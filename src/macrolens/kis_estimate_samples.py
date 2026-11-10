"""Offline XLC/SPY 2025 estimates, with scoped checks against stored evidence."""

import json
import re
from pathlib import Path

from . import kis_estimate, kis_ledger
from .distribution_evidence import load_catalog
from .kis_client import KISError
from .kis_collect import day as market_day
from .kis_collect import verify_archive
from .ledger import blocker, encoded, number
from .price_audit import dump, new_folder, sha
from .price_quality import AuditError, trading_sessions

SYMBOLS = ("XLC", "SPY")
START, END = kis_estimate.START, kis_estimate.END
SCENARIOS = {"reported_amounts": ("표의 배당액을 당시 1주 기준으로 가정", 1)}
ASSUMPTIONS = {
    "price_basis": kis_estimate.ASSUMPTIONS["price_basis"],
    "distribution_coverage": "해당 상품의 공식 2025년 4건 외 추가 분배는 없다고 가정",
    "split_coverage": "2025년에 분할이 없다고 가정; 빈 권리 응답은 무분할의 증거가 아님",
    "share_basis": "표의 배당액을 해당 배당락일 당시 1주 기준으로 가정; 환산 배율 1",
}


def expected_events(ticker):
    if ticker not in SYMBOLS:
        raise AuditError("새 추정 명령의 대상은 XLC·SPY뿐입니다")
    catalog, _ = load_catalog()
    return {
        tuple(c[k] for k in ("cusip", "ex_date", "record_date", "payable_date"))
        for c in catalog["claims"]
        if c["ticker"] == ticker and c["ex_date"].startswith("2025-")
    }


def evidence_review(saved, meta, ticker, inputs):
    """Detect explicit contradictions; no claim of complete corporate-action coverage."""
    problems, observations, notes = [], [], []
    events = inputs["events"]
    counts = {"period-rights": 0, "rights-by-ice": 0}

    def hold(field, code, source, row=None):
        # Record/announcement dates are not necessarily effective dates. Stop from start.
        item = blocker(ticker, START, field, code, source + "; 저장 원문·해당 사건 대조 필요")
        if row:
            item["source"] = row
        problems.append(item)

    def day(value):
        return None if value in (None, "", "00000000") else market_day(value)

    for record in meta["pages"]:
        endpoint, params = record["endpoint"], record["params"]
        if endpoint not in counts or params.get("SYMB", params.get("PDNO")) != ticker:
            continue
        body = json.loads((Path(saved) / record["file"]).read_text())
        rows = body.get("output" if endpoint == "period-rights" else "output1")
        if not isinstance(rows, list):
            hold("rights", "invalid_rights_array", record["file"])
            continue
        for index, row in enumerate(rows, 1):
            source = {"file": record["file"], "sha256": record["sha256"], "row_1based": index}
            try:
                dates = (
                    {"record_date": day(row.get("acpl_bass_dt"))}
                    if endpoint == "period-rights"
                    else {
                        name: day(row.get(key))
                        for name, key in (
                            ("ex_date", "div_lock_dt"),
                            ("record_date", "record_dt"),
                            ("payable_date", "pay_dt"),
                            ("effective_date", "effective_dt"),
                            ("rights_ex_date", "lock_dt"),
                        )
                    }
                )
            except (KISError, ValueError, TypeError, AttributeError):
                hold("rights", "invalid_rights_date", record["file"], source)
                continue
            action = endpoint == "rights-by-ice" and re.search(
                r"\bsplit\b|분할|병합|stock\s+dividend", str(row.get("ca_title", "")), re.I
            )
            # Prior-year ex dates do not create entitlement on the 2025 start close,
            # even if their payment/effective metadata happens to be in 2025.
            order = (
                ("effective_date", "rights_ex_date", "record_date", "ex_date")
                if action
                else ("ex_date", "record_date", "effective_date", "rights_ex_date")
            )
            scope_day = next((dates.get(k) for k in order if dates.get(k)), None)
            relevant = scope_day is not None and "2025-01-01" <= scope_day <= END
            if not relevant:
                if not any(dates.values()):
                    notes.append({"code": "undated_rights_unknown", "source": source})
                    if endpoint == "period-rights" and row.get("rght_type_cd") not in ("03", "75"):
                        hold("rights.type", "undated_corporate_action", record["file"], source)
                    elif action:
                        hold("ice.action", "undated_corporate_action", record["file"], source)
                continue
            counts[endpoint] += 1
            observations.append({"endpoint": endpoint, "dates": dates, "source": source})
            if endpoint == "period-rights":
                if row.get("pdno") != ticker or row.get("prdt_type_cd") != meta["product_type"]:
                    hold("rights.identity", "rights_symbol_conflict", record["file"], source)
                if row.get("rght_type_cd") not in ("03", "75"):
                    hold("rights.type", "unmodeled_corporate_action", record["file"], source)
                    continue
                matching = [e for e in events if e["record_date"] == dates["record_date"]]
                if len(matching) != 1:
                    hold(
                        "rights.record_date",
                        "unexpected_distribution_record",
                        record["file"],
                        source,
                    )
                    continue
                fields = [("alct_frcr_unpr", "crcy_cd")] + [
                    (f"stkp_dvdn_frcr_amt{i}", f"crcy_cd{i}") for i in (2, 3, 4)
                ]
                for amount_key, currency_key in fields:
                    if row.get("dfnt_yn") != "Y" or row.get(currency_key) != "USD":
                        continue
                    try:
                        delta = number(row.get(amount_key)) - number(matching[0]["dividend"])
                    except (ValueError, TypeError, ZeroDivisionError):
                        notes.append({"code": "rights_amount_unknown", "source": source})
                        continue
                    observations.append(
                        {"source": source, "field": amount_key, "delta_usd": str(delta)}
                    )
                    if abs(delta) > number("0.00001"):
                        hold(amount_key, "distribution_amount_conflict", record["file"], source)
            else:
                if action:
                    hold("ice.action", "unmodeled_corporate_action", record["file"], source)
                known_dates = {
                    k: dates[k] for k in ("ex_date", "record_date", "payable_date") if dates[k]
                }
                if not known_dates:
                    hold("ice.action", "unclassified_ice_action", record["file"], source)
                if known_dates and not any(
                    all(e[k] == v for k, v in known_dates.items()) for e in events
                ):
                    hold("ice.dates", "ice_distribution_date_conflict", record["file"], source)
    for endpoint, count in counts.items():
        if not count:
            notes.append({"code": "no_2025_rights_evidence_not_no_events", "endpoint": endpoint})
    raw, adjusted = inputs["prices_modp0"], inputs["prices_modp1"]
    sessions, _ = trading_sessions(START, END)
    pairs, unusual = 0, []
    for previous, current in zip(sessions, sessions[1:]):
        try:
            p0, p1, a0, a1 = [number(v[d]) for v in (raw, adjusted) for d in (previous, current)]
            step = (a1 / p1) / (a0 / p0)
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            continue
        pairs += 1
        if abs(step - 1) > number("0.2"):
            unusual.append(
                {"date": current, "previous_session": previous, "factor_step": str(step)}
            )
            hold("price.adjustment", "unexplained_adjustment_step", current + " MODP=0/1")
    if pairs != len(sessions) - 1:
        notes.append({"code": "price_diagnostic_pairs_incomplete", "pairs": pairs})
    return {
        "status": "held" if problems else "no_observed_conflict_not_complete_validation",
        "scope": {"ticker": ticker, "start": START, "end": END},
        "blockers": problems,
        "observations": observations,
        "limitations": notes,
        "rights_rows_in_scope": counts,
        "price_pairs_compared": pairs,
        "unusual_price_steps": unusual,
        "price_step_diagnostic_threshold": "0.2",
        "notice": "20%는 조정계수 급변 진단 기준, 분할 확정/오차 허용/가격 보정 기준이 아님",
    }


def scenarios(strict, inputs, review):
    ticker = strict["ticker"]
    result, effective = kis_estimate.symbol_scenarios(
        strict,
        inputs,
        ticker,
        ticker.lower() + "-2025-reported-dividends-v1",
        expected_events(ticker),
        SCENARIOS,
        ASSUMPTIONS,
        review["blockers"],
    )
    result["stored_evidence_review"] = review
    return result, effective


def run(saved, output, issuer_file=None):
    strict, strict_folder = kis_ledger.run(
        saved, Path(output) / "strict", START, END, issuer_file=issuer_file
    )
    meta = verify_archive(saved)
    inputs = json.loads((strict_folder / "inputs.json").read_text())
    symbols, scenario_inputs = {}, {}
    for ticker in SYMBOLS:
        review = evidence_review(saved, meta, ticker, inputs[ticker])
        symbols[ticker], scenario_inputs[ticker] = scenarios(
            strict["symbols"][ticker], inputs[ticker], review
        )
    result = {
        "status": "held"
        if any(s["status"] == "held" for s in symbols.values())
        else "estimated_not_validated",
        "evidence_kind": strict["evidence_kind"],
        "start": START,
        "end_inclusive": END,
        "source_archive_id": strict["source_archive_id"],
        "input_sha256": strict["input_sha256"],
        "strict_result_sha256": sha((strict_folder / "result.json").read_bytes()),
        "symbols": symbols,
        "distribution_evidence": strict["distribution_evidence"],
        "price_contract_reference": strict["price_contract_reference"],
        "strict_point_in_time": False,
        "validated_total_return": False,
        "total_return_eligible": False,
        "source_sha256": {
            **strict["source_sha256"],
            "kis_estimate.py": sha(Path(kis_estimate.__file__).read_bytes()),
            "kis_estimate_samples.py": sha(Path(__file__).read_bytes()),
        },
    }
    folder = new_folder(output, "xlc_spy_2025_assumption_estimate")
    dump(folder / "result.json", result)
    dump(folder / "scenario-events.json", scenario_inputs)
    dump(folder / "strict-output-location.json", {"path": str(strict_folder)})
    for source, dest in (
        ("result.json", "strict-result.json"),
        ("inputs.json", "strict-inputs.json"),
    ):
        (folder / dest).write_bytes((strict_folder / source).read_bytes())
    for ticker, item in symbols.items():
        kis_ledger.write_csv(
            folder / (ticker + "-ledger.csv"), item["scenarios"]["reported_amounts"]["rows"]
        )
    (folder / "report.md").write_text(render(result), encoding="utf-8")
    dump(folder / "checksums.json", {p.name: sha(p.read_bytes()) for p in folder.iterdir()})
    (folder / "COMPLETE").write_text("saved assumptions; not verified returns\n", encoding="utf-8")
    return result, folder


def render(result):
    lines = [
        "# XLC·SPY 2025 가정에 따른 총수익 추정",
        "",
        "상태: " + result["status"],
        "자료: " + result["evidence_kind"],
        "2025-01-02 종가 1주 매수~2025-12-31, USD 세전. 검증된 총수익률·전략 성과가 아닙니다.",
        "배당액은 표 그대로, 배율1. XLK의 분할·2배 환산은 적용하지 않습니다.",
        "",
        "| 상품 | 원본 판정 | 추정 상태 | 달력 행 수 | 추정 총수익률(%) | 연말 미수 배당(USD) |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for ticker, symbol in result["symbols"].items():
        item = symbol["scenarios"]["reported_amounts"]
        value = item["estimated_total_return"]
        percent = encoded(number(value["exact"]) * 100)["display"] if value else "보류"
        receivable = item["rows"][-1]["receivable"]["display"] if value else "보류"
        lines.append(
            f"| {ticker} | {symbol['source_price_status']} | {symbol['status']} | "
            f"{len(item['rows'])} | {percent} | {receivable} |"
        )
    lines += ["", "공통 가정:", ""] + ["- " + v for v in ASSUMPTIONS.values()]
    for ticker, symbol in result["symbols"].items():
        lines += ["", ticker + " 보류 사유:", ""]
        problems = symbol["scenarios"]["reported_amounts"]["blockers"]
        lines.extend(
            f"- {b['date']} / {b['input']} / {b['code']}: {b['resolution']}" for b in problems
        )
        if not problems:
            lines.append("- 없음. 위 가정 아래 추정 계산 완료이며 공식 확인은 아닙니다.")
        check = symbol["stored_evidence_review"]
        lines.append(
            f"- 저장 근거 대조: {check['status']}; 가격 전후 쌍 {check['price_pairs_compared']}건."
        )
        lines.append("- 빈 권리·누락 필드 및 한계는 result.json의 stored_evidence_review 참조.")
    lines += [
        "",
        "SPY 12월 배당은 2026-01-30 지급 예정 미수금으로 연말 평가액에 남깁니다.",
        "2026-02-02 재투자 예정일만 기록하며 2025년 원장에서 지급/재투자를 실행하지 않습니다.",
        "엄밀 판정·원본 입력은 strict-result.json/strict-inputs.json에 별도 보존합니다.",
        "XLC 2023-06-05 결측의 원본 held를 유지하며 "
        "이번 2025년 가격을 그 이유만으로 보류하지 않습니다.",
        "strict_point_in_time=false / total_return_eligible=false. "
        "오차 범위는 산출하지 않았습니다.",
        "",
    ]
    return "\n".join(lines)
