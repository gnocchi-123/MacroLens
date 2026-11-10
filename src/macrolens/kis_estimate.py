"""Explicit XLK 2025 scenarios. Assumptions never become verified source facts."""

import copy
import json
from datetime import date, timedelta
from pathlib import Path

from . import kis_ledger
from .ledger import RULES_VERSION, blocker, calculate, encoded, number
from .price_audit import dump, new_folder, sha
from .price_quality import AuditError, trading_sessions

PROFILE = "xlk-2025-dividend-basis-scenarios-v1"
START, END = "2025-01-02", "2025-12-31"
EXPECTED_EVENTS = {
    ("81369Y803", "2025-03-24", "2025-03-24", "2025-03-26"),
    ("81369Y803", "2025-06-23", "2025-06-23", "2025-06-25"),
    ("81369Y803", "2025-09-22", "2025-09-22", "2025-09-24"),
    ("81369Y803", "2025-12-22", "2025-12-22", "2025-12-24"),
}
SCENARIOS = {
    "reported_amounts": ("A: 표의 배당액을 당시 1주 기준으로 가정", 1),
    "pre_split_dividends_x2": ("B: 분할 전 3건은 분할 후 기준 표시로 가정하여 2배 환산", 2),
}
ASSUMPTIONS = {
    "price_basis": "MODP=0을 당시 정규장 종가로 사용한다고 가정; 공식 정의는 미수정/종가까지",
    "distribution_coverage": "공식 이력의 2025년 확인된 4건 이외 추가 분배는 없다고 가정",
    "split_coverage": "2025년 분할은 공식 12-05의 2:1 한 건이라고 가정",
    "share_basis": "A/B의 주당 기준을 각각 가정; 공식 확인으로 승격하지 않음",
}


def scenarios(strict, inputs):
    return symbol_scenarios(strict, inputs, "XLK", PROFILE, EXPECTED_EVENTS, SCENARIOS, ASSUMPTIONS)


def symbol_scenarios(
    strict,
    inputs,
    ticker,
    profile,
    expected_events,
    scenario_definitions,
    assumptions,
    extra_blockers=(),
):
    """Reuse exact accounting, relaxing only named semantic unknowns in this fixed scope."""
    if (strict["ticker"], strict["start"], strict["end_inclusive"]) != (ticker, START, END):
        raise AuditError(f"추정 프로필은 {ticker} 2025-01-02~2025-12-31에만 적용됩니다")
    events = inputs["events"]
    event_keys = [
        tuple(e.get(k) for k in ("cusip", "ex_date", "record_date", "payable_date")) for e in events
    ]
    problems, assumed = copy.deepcopy(list(extra_blockers)), []
    if len(event_keys) != 4 or set(event_keys) != expected_events:
        problems.append(
            blocker(ticker, START, "events", "estimate_event_set_mismatch", "알려진 공식 4건 확인")
        )
    if strict["price_contract_evidence"]["status"] != "matched_archived_requests":
        problems.append(
            blocker(ticker, START, "MODP=0", "estimate_request_mismatch", "저장 요청 계약 확인")
        )
    # A missing/contradictory split check is not a reasonable price-basis assumption.
    if ticker == "XLK" and not any(
        d["date"] == kis_ledger.SPLIT["date"]
        and d["status"] == "consistent_with_as_traded_at_this_split"
        for d in strict["split_diagnostics"]
    ):
        problems.append(
            blocker(
                ticker, START, "split", "estimate_split_unverified", "저장된 분할 전후 대조 필요"
            )
        )
    if ticker != "XLK" and inputs["splits"]:
        problems.append(
            blocker(ticker, START, "split", "unexpected_split_input", "무분할 가정과 입력 충돌")
        )
    for issue in strict["blockers"]:
        field = issue["input"]
        allowed = (
            issue["ticker"] == ticker
            and field in assumptions
            and issue["code"] == "unknown_" + field
        )
        if field == "share_basis":
            allowed = allowed and issue["date"] in {e[1] for e in expected_events}
        (assumed if allowed else problems).append(copy.deepcopy(issue))
    last_pay = max([END] + [e["payable_date"] for e in events])
    sessions, calendar = trading_sessions(
        START, (date.fromisoformat(last_pay) + timedelta(days=14)).isoformat()
    )
    results, scenario_inputs = {}, {}
    for key, (label, pre_multiplier) in scenario_definitions.items():
        effective, changes = copy.deepcopy(events), []
        for event in effective:
            multiplier = (
                pre_multiplier
                if ticker == "XLK" and event["ex_date"] < kis_ledger.SPLIT["date"]
                else 1
            )
            original = event["dividend"]
            try:
                event["dividend"] = str(number(original) * multiplier)
            except (ValueError, TypeError, ZeroDivisionError):
                pass  # Core still blocks missing/invalid amounts. Never invent zero.
            event["share_basis"] = "per_ex_date_share"
            event["share_basis_status"] = "assumed_not_verified"
            event["assumption_profile"] = profile
            changes.append(
                {
                    "ex_date": event["ex_date"],
                    "source_dividend": original,
                    "multiplier": multiplier,
                    "scenario_dividend": event["dividend"],
                    "share_basis_status": "assumed_not_verified",
                }
            )
        result = calculate(
            ticker,
            START,
            END,
            inputs["prices_modp0"],
            sessions,
            effective,
            inputs["splits"],
            problems,
        )
        result["estimated_total_return"] = result.pop("total_return")
        if result["status"] == "calculated_under_reviewed_inputs":
            result["status"] = "estimated_not_validated"
        result.update(label=label, assumptions=assumptions, dividend_basis_changes=changes)
        results[key] = result
        scenario_inputs[key] = effective
    complete = all(r["status"] == "estimated_not_validated" for r in results.values())
    difference = None
    if complete and ticker == "XLK":
        a, b = [number(r["estimated_total_return"]["exact"]) for r in results.values()]
        difference = encoded((b - a) * 100)
    return {
        "profile": profile,
        "ticker": ticker,
        "start": START,
        "end_inclusive": END,
        "status": "estimated_not_validated" if complete else "held",
        "strict_ledger_status": strict["status"],
        "source_price_status": strict["source_price_status"],
        "source_total_return_eligible": strict["source_total_return_eligible"],
        "total_return_eligible": False,
        "validated_total_return": False,
        "strict_point_in_time": False,
        "rules_version": RULES_VERSION,
        "calendar": calendar,
        "assumptions": assumptions,
        "assumed_unresolved_conditions": assumed,
        "scenarios": results,
        "scenario_difference_percentage_points": difference,
        "sensitivity_notice": (
            "B-A는 두 가정의 차이이며 오차 상한·신뢰구간·전략 비교가 아닙니다"
            if ticker == "XLK"
            else "단일 주당 기준 가정의 추정이며 오차 범위를 계산하지 않습니다"
        ),
    }, scenario_inputs


def run(saved, output, issuer_file=None):
    # The normal ledger remains held. Its immutable result is retained separately.
    strict, strict_folder = kis_ledger.run(
        saved, Path(output) / "strict", START, END, issuer_file=issuer_file
    )
    inputs = json.loads((strict_folder / "inputs.json").read_text())
    result, scenario_inputs = scenarios(strict["symbols"]["XLK"], inputs["XLK"])
    result.update(
        evidence_kind=strict["evidence_kind"],
        source_archive_id=strict["source_archive_id"],
        input_sha256=strict["input_sha256"],
        strict_result_sha256=sha((strict_folder / "result.json").read_bytes()),
        distribution_evidence=strict["distribution_evidence"],
        price_contract_reference=strict["price_contract_reference"],
        source_sha256={
            **strict["source_sha256"],
            "kis_estimate.py": sha(Path(__file__).read_bytes()),
        },
    )
    folder = new_folder(output, "xlk_2025_assumption_estimate")
    dump(folder / "result.json", result)
    dump(folder / "scenario-events.json", scenario_inputs)
    dump(folder / "strict-output-location.json", {"path": str(strict_folder)})
    for source, dest in (
        ("result.json", "strict-result.json"),
        ("inputs.json", "strict-inputs.json"),
    ):
        (folder / dest).write_bytes((strict_folder / source).read_bytes())
    for key, item in result["scenarios"].items():
        kis_ledger.write_csv(folder / (key + "-ledger.csv"), item["rows"])
    (folder / "report.md").write_text(render(result), encoding="utf-8")
    dump(folder / "checksums.json", {p.name: sha(p.read_bytes()) for p in folder.iterdir()})
    (folder / "COMPLETE").write_text("saved assumptions; not verified returns\n", encoding="utf-8")
    return result, folder


def render(result):
    lines = [
        "# XLK 2025 가정에 따른 총수익 추정",
        "",
        "상태: " + result["status"],
        "자료: " + result["evidence_kind"],
        "2025-01-02 종가 1주 매수 ~ 2025-12-31. USD 세전. 검증된 총수익률이 아닙니다.",
        "배당락 미수금 → 지급일 현금 → 지급일보다 뒤 첫 거래일 재투자. 분할은 수량에 한 번만 반영.",
        "MODP=1에 배당을 더하지 않습니다. 원본 금액·기존 held/conditional 판정을 바꾸지 않습니다.",
        "",
        "| 가정 | 상태 | 원장 행 수 | 추정 총수익률(%) |",
        "| --- | --- | ---: | ---: |",
    ]
    for item in result["scenarios"].values():
        value = item["estimated_total_return"]
        percent = encoded(number(value["exact"]) * 100)["display"] if value else "보류"
        lines.append(f"| {item['label']} | {item['status']} | {len(item['rows'])} | {percent} |")
    lines += ["", "계산을 위해 둔 가정:", ""]
    lines.extend("- " + value for value in result["assumptions"].values())
    delta = result["scenario_difference_percentage_points"]
    lines += [
        "",
        "두 가정의 차이(B-A): " + (delta["display"] + "%p" if delta else "보류"),
        result["sensitivity_notice"],
        "가격 기준·누락 사건·12월 주당 기준 등 공통 가정의 오차는 이 차이에 포함되지 않습니다.",
        "",
    ]
    for key, item in result["scenarios"].items():
        lines.append(key + " 보류 사유:")
        lines.extend(
            f"- {b['date']} / {b['input']} / {b['code']}: {b['resolution']}"
            for b in item["blockers"]
        )
        if not item["blockers"]:
            lines.append("- 없음. 위 가정 아래 추정 계산 완료이며 공식 확인은 아닙니다.")
        lines.append("")
    lines += [
        "strict-result.json에 기존 판정, strict-inputs.json에 변경 전 입력을 보존합니다.",
        "scenario-events.json의 계산용 환산값은 원자료 수정이 아닙니다. 원본 null도 유지합니다.",
        "strict_point_in_time=false / total_return_eligible=false. 전략 백테스트가 아닙니다.",
        "",
    ]
    return "\n".join(lines)
