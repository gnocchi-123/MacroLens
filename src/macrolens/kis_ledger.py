"""Offline adapter for immutable KIS archives and official issuer distributions."""

import csv
import json
from datetime import date, timedelta
from pathlib import Path

from .distribution_evidence import interpret, load_catalog, summary
from .kis_audit import audit_archive
from .kis_client import ENDPOINT_PATHS, ENDPOINTS
from .kis_collect import SAMPLE, verify_archive
from .kis_events import KNOWN_MISSING_POLICY, review_events
from .ledger import RULES_VERSION, blocker, calculate, number
from .price_audit import dump, new_folder, sha
from .price_issuer import parse_distributions
from .price_quality import AuditError, trading_sessions

SPLIT = {
    "date": "2025-12-05",
    "new_per_old": "2",
    "source": "https://www.nasdaq.com/press-release/"
    "state-street-investment-management-announces-share-splits-five-select-sector-spdrr",
    "basis": "State Street announcement; record 12/02, payable 12/04 after close",
}
CLAIMS = {
    "price_basis": ("as_traded_modp0", "기간 전체 MODP=0이 당시 거래가격이라는 근거 필요"),
    "split_coverage": ("complete", "해당 기간 공식 분할 목록의 완전성 근거 필요"),
    "distribution_coverage": ("complete", "공식 분배금 자료의 해당 기간 완전성 근거 필요"),
}
PRICE_CONTRACT_REFERENCE = {
    "id": "kis-dailyprice-option-definition-20261108",
    "title": "KIS official chk_dailyprice.py: Parameters and COLUMN_MAPPING",
    "url": "https://github.com/koreainvestment/open-trading-api/blob/"
    "3a9be59a3ed8745720dc3aa9d584bdf3b7830362/"
    "examples_llm/overseas_stock/dailyprice/chk_dailyprice.py#L24-L57",
    "sha256": "ea386811dfadb5d37314aefbbf39b541298b364ecb04ce83ac245f39906ff51c",
    "checked_at": "2026-11-08T11:05:58.975350+00:00",
    "definitions": {"MODP=0": "수정주가 미반영", "MODP=1": "수정주가 반영", "clos": "종가"},
    "scope": "request_option_and_field_definition_only",
    "not_established": ["regular_session_close_definition", "full_history_as_traded_verification"],
}


def price_contract_evidence(meta, ticker, price_sources, start, end):
    """Link the documented option to used, hash-checked archive pages, not to eligibility."""
    names = sorted({name for day, name in price_sources.items() if start <= day <= end})
    pages = []
    for name in names:
        records = [record for record in meta["pages"] if record["file"] == name]
        record = records[0] if len(records) == 1 else {}
        params = record.get("params", {})
        matched = (
            meta.get("provider") == "kis"
            and record.get("endpoint") == "dailyprice"
            and record.get("path") == ENDPOINT_PATHS["dailyprice"]
            and record.get("tr_id") == ENDPOINTS["dailyprice"][0]
            and all(
                params.get(k) == v
                for k, v in {"SYMB": ticker, "EXCD": "AMS", "GUBN": "0", "MODP": "0"}.items()
            )
        )
        pages.append({"file": name, "sha256": record.get("sha256"), "request_matches": matched})
    return {
        "reference_id": PRICE_CONTRACT_REFERENCE["id"],
        "ticker": ticker,
        "start": start,
        "end_inclusive": end,
        "status": (
            "matched_archived_requests"
            if pages and all(p["request_matches"] for p in pages)
            else "request_not_matched"
            if pages
            else "no_usable_price_pages"
        ),
        "pages": pages,
        "automatically_approves_price_basis": False,
        "notice": "확인된 요청 옵션 정의. 실제 가격 기준·정규장 종가·구간 완전성 승인이 아님",
    }


def review_template(bindings, events, start, end):
    def unknown():
        return {"value": "unknown", "start": start, "end": end, "references": []}

    return {
        "schema_version": 1,
        "input_sha256": bindings,
        "notice": "검토용 양식; unknown을 근거 없이 변경하지 마세요. 금액 수정 입력이 아닙니다.",
        "symbols": {
            t: {
                **{key: unknown() for key in CLAIMS},
                "event_share_basis": {e["ex_date"]: unknown() for e in events.get(t, [])},
                "additional_splits": [],
            }
            for t in SAMPLE
        },
    }


def load_review(path, bindings):
    if path is None:
        return {}, {}
    path = Path(path)
    blob = path.read_bytes()
    review = json.loads(blob)
    if review.get("schema_version") != 1 or review.get("input_sha256") != bindings:
        raise AuditError("원장 근거서의 입력 해시 불일치")
    # Hash-checked references are a human review trail, NOT automatic truth verification.
    files = {"review-source.json": blob}

    def visit(value):
        if isinstance(value, dict):
            if "references" in value:
                refs = value["references"]
                if not isinstance(refs, list):
                    raise AuditError("원장 근거 참조 형식 오류")
                for ref in refs:
                    if not ref.get("reason", "").strip():
                        raise AuditError("원장 근거의 판단 이유 필요")
                    content = (path.parent / ref["file"]).read_bytes()
                    if sha(content) != ref["sha256"]:
                        raise AuditError("원장 근거 파일 해시 불일치")
                    name = "evidence-" + ref["sha256"] + ".bin"
                    files[name] = content
                    ref["file"] = name  # Copied review can replay using copied references.
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(review)
    files["review.json"] = json.dumps(review, ensure_ascii=False, sort_keys=True).encode()
    return review.get("symbols", {}), files


def confirmed(claim, value, start, end):
    return (
        isinstance(claim, dict)
        and claim.get("value") == value
        and claim.get("start", "9999") <= start
        and claim.get("end", "") >= end
        and bool(claim.get("references"))
    )


def exact_prices(saved, normalized, modp):
    """Use only audit-accepted rows, but recover close text from its original raw page."""
    result, sources, duplicates, cache = {}, {}, [], {}
    for row in normalized["prices_by_modp"][modp]:
        name, day = row["source_file"], row["date"]
        if name not in cache:
            cache[name] = json.loads((saved / name).read_text())["output2"]
        candidates = [r for r in cache[name] if r.get("xymd") == day.replace("-", "")]
        if day in result or len(candidates) != 1:
            duplicates.append(day)
            continue
        result[day] = candidates[0]["clos"]
        sources[day] = name
    # A conflicting/duplicate day cannot be used even when one row looks plausible.
    for day in duplicates:
        result.pop(day, None)
        sources.pop(day, None)
    return result, sources, duplicates


def split_check(split, prices, adjusted):
    day = split["date"]
    sessions, _ = trading_sessions((date.fromisoformat(day) - timedelta(days=10)).isoformat(), day)
    previous = max(s for s in sessions if s < day)
    result = {
        "date": day,
        "previous_session": previous,
        "status": "unverified",
        "scope": "single_event_diagnostic_not_full_history_approval",
    }
    try:
        raw_ratio = number(prices[day]) / number(prices[previous])
        adj_ratio = number(adjusted[day]) / number(adjusted[previous])
        relative = raw_ratio / adj_ratio
        ratio = number(split["new_per_old"])
        # Diagnostic threshold only; never alters either series or approves its full basis.
        threshold = number("0.001")
        if abs(relative - 1) <= threshold:
            result["status"] = "appears_already_split_adjusted"
        elif abs(relative * ratio - 1) <= threshold:
            result["status"] = "consistent_with_as_traded_at_this_split"
        result.update(
            raw_ratio=str(raw_ratio),
            modp1_ratio=str(adj_ratio),
            threshold=str(threshold),
            official_new_per_old=str(ratio),
        )
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        pass
    return result


def identity_ok(meta, result, ticker):
    identity = result["symbols"][ticker]["identity"]
    if meta.get("identity_source") == "basic-quote":
        basic, quote = identity.get("basic_info", {}), identity.get("price_detail", {})
        return (
            basic.get("pdno") == ticker
            and basic.get("prdt_type_cd") == meta["product_type"]
            and quote.get("curr") == "USD"
            and quote.get("rsym") == "DAMS" + ticker
        )
    return (
        identity.get("tr_crcy_cd") == "USD"
        and identity.get("ovrs_excg_cd") in ("AMS", "AMEX")
        and identity.get("pdno", ticker) == ticker
    )


def run(saved, output, start, end, issuer_file=None, review_file=None, distribution_evidence=None):
    saved = Path(saved)
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise AuditError("시작일이 종료일보다 늦음")
    meta = verify_archive(saved)
    source_audit = json.loads((saved / "audit.json").read_text())
    audit, normalized = audit_archive(saved, meta)
    issuer = (
        Path(issuer_file)
        if issuer_file
        else (saved / meta["issuer_file"] if meta.get("issuer_file") else None)
    )
    issuer_blob = issuer.read_bytes() if issuer else None
    official = (
        parse_distributions(issuer_blob, SAMPLE, start, end, exact_amounts=True) if issuer else {}
    )
    events = {
        t: [
            dict(e, **e["source_amounts"])
            for e in official.get(t, {}).get("events_in_source_order", [])
        ]
        for t in SAMPLE
    }
    bindings = {
        "archive_checksums": sha((saved / "checksums.json").read_bytes()),
        "manifest": sha((saved / "manifest.json").read_bytes()),
        "issuer": sha(issuer_blob) if issuer_blob is not None else None,
    }
    reviewed, evidence_files = load_review(review_file, bindings)
    distribution_catalog, distribution_blob = load_catalog(distribution_evidence)
    last_pay = max([end] + [e["payable_date"] for group in events.values() for e in group])
    sessions, calendar = trading_sessions(
        start, (date.fromisoformat(last_pay) + timedelta(days=14)).isoformat()
    )
    symbols, prepared = {}, {}
    for ticker in SAMPLE:
        problems = []

        def hold(day, field, code, resolution):
            problems.append(blocker(ticker, day, field, code, resolution))

        if meta["mode"] != "collect" or not meta["start"] <= start <= end <= meta["end_inclusive"]:
            hold(start, "archive", "period_not_covered", "전체 수집 기록 내 요청 구간 지정 필요")
        if meta["exchange"] != "AMS" or not identity_ok(meta, audit, ticker):
            hold(
                start, "identity", "identity_unverified", "저장된 상품 식별·USD·NYSE Arca 근거 필요"
            )
        review = reviewed.get(ticker, {})
        for key, (value, resolution) in CLAIMS.items():
            if not confirmed(review.get(key), value, start, end):
                hold(start, key, "unknown_" + key, resolution)
        if not issuer:
            hold(start, "issuer_file", "issuer_file_missing", "운용사 공식 배당 XLSX 지정 필요")
        for issue in official.get(ticker, {}).get("issues", []):
            if issue.startswith(("결측/비정상", "중복")):
                hold(start, "issuer_file", "invalid_issuer_event", issue + "; 공식 원본 확인 필요")
        # Core independently blocks blanks, unknown event basis and malformed event dates.
        for event in events[ticker]:
            claim = review.get("event_share_basis", {}).get(event["ex_date"])
            event["share_basis"] = (
                "per_ex_date_share"
                if confirmed(claim, "per_ex_date_share", event["ex_date"], event["ex_date"])
                else "unknown"
            )
        source_events = events[ticker]
        events[ticker], interpretations = interpret(ticker, source_events, distribution_catalog)
        for event_review in interpretations:
            for field, resolution in event_review["fields"].items():
                if resolution["status"] in ("conflict", "unsupported_nonzero_tax_component"):
                    if start < event_review["ex_date"] <= end:
                        hold(
                            event_review["ex_date"],
                            field,
                            "distribution_evidence_conflict",
                            "공식 사건 자료의 충돌/비영 세무 분류 확인 필요; "
                            "총분배액에 재가산 금지",
                        )
        prices, price_sources, duplicates = exact_prices(saved, normalized[ticker], "0")
        adjusted, _, _ = exact_prices(saved, normalized[ticker], "1")
        for issue in audit["symbols"][ticker]["issues"]:
            if issue.startswith(("가격 페이지 진행", "가격 응답 종목", "가격 배열 형식")):
                hold(start, "price_pages", "price_page_contract_failed", issue + "; 원본 대조 필요")
        for failure in meta["failures"]:
            if failure["ticker"] in (ticker, "ALL") and (
                failure["ticker"] == "ALL"
                or "modp0" in failure["stream"]
                or failure["stream"].startswith("identity")
            ):
                hold(
                    start,
                    "price_pages",
                    "incomplete_source_stream",
                    failure["stream"] + ": " + failure["code"] + "; 수집 무결성 확인 필요",
                )
        for error in audit["price_row_errors"]:
            if error["ticker"] != ticker or error["modp"] != "0":
                continue
            day = error["date"]
            if day is None or start <= day <= end:
                prices.pop(day, None)  # Never select a good close alongside a rejected OHLC row.
                price_sources.pop(day, None)
                hold(
                    day or start,
                    "MODP=0.OHLC",
                    "rejected_source_row",
                    error["source_file"]
                    + ": "
                    + ", ".join(error["reasons"])
                    + "; 원문 보존. 오류 없는 OHLC 근거 없이는 사용 불가",
                )
        for day in prices:
            if start <= day <= end and day not in sessions:
                hold(day, "MODP=0.date", "non_session_price", "공식 거래일·자료 날짜 대조 필요")
        for day in duplicates:
            if start <= day <= end:
                hold(day, "MODP=0", "duplicate_prices", "중복·충돌 없는 검수 원본 필요")
        splits = [dict(SPLIT)] if ticker == "XLK" else []
        for split in review.get("additional_splits", []):
            if not confirmed(split, "official_split", split["date"], split["date"]):
                hold(start, "splits", "unverified_split", "공식 분할 적용일·비율 근거 필요")
            else:
                splits.append(split)
        diagnostics = [
            split_check(s, prices, adjusted)
            for s in splits
            if start < s["date"] <= meta["end_inclusive"]
        ]
        for diag in diagnostics:
            # A later back-adjustment can contaminate an earlier selected period too.
            if (
                diag["date"] <= end and diag["status"] != "consistent_with_as_traded_at_this_split"
            ) or (diag["status"] == "appears_already_split_adjusted"):
                hold(
                    start,
                    "MODP=0.split_basis",
                    diag["status"],
                    f"{diag['date']} 전후 MODP=0/1 및 공식 분할 대조 필요; 이중 분할 적용 금지",
                )
        item = calculate(ticker, start, end, prices, sessions, events[ticker], splits, problems)
        item.update(
            source_price_status=source_audit["symbols"][ticker]["status"],
            current_price_status=audit["symbols"][ticker]["status"],
            source_total_return_eligible=source_audit.get("total_return_eligible", False),
            price_contract_evidence=price_contract_evidence(
                meta, ticker, price_sources, start, end
            ),
            split_diagnostics=diagnostics,
            source_price_errors=[x for x in audit["price_row_errors"] if x["ticker"] == ticker],
            issuer_kis_crosscheck=review_events(
                normalized[ticker]["prices_by_modp"],
                normalized[ticker]["period_rights"],
                normalized[ticker]["ice_rights"],
                official.get(ticker),
            ),
            distribution_interpretations=interpretations,
            distribution_evidence_by_year=summary(interpretations),
        )
        symbols[ticker] = item
        prepared[ticker] = {
            "prices_modp0": prices,
            "price_source_files": price_sources,
            "events": events[ticker],
            "source_events": source_events,
            "splits": splits,
        }
    result = {
        "rules_version": RULES_VERSION,
        "evidence_kind": meta["evidence_kind"],
        "source_archive_id": saved.name,
        "input_sha256": bindings,
        "calendar": calendar,
        "source_total_return_eligible": source_audit.get("total_return_eligible", False),
        "strict_point_in_time": False,
        "validated_total_return": False,
        "research_missing_policy": KNOWN_MISSING_POLICY,
        "start": start,
        "end_inclusive": end,
        "symbols": symbols,
        "review_semantics": "hash-checked human evidence; content not automatically certified",
        "review_sha256": sha(evidence_files["review.json"]) if evidence_files else None,
        "distribution_evidence": {
            "catalog_id": distribution_catalog["id"],
            "sha256": sha(distribution_blob),
            "documents": distribution_catalog["documents"],
            "source_verification": "reviewed catalogue; no network or fresh document verification",
        },
        "price_contract_reference": PRICE_CONTRACT_REFERENCE,
        "status": "held"
        if any(s["status"] == "held" for s in symbols.values())
        else "calculated_under_reviewed_inputs",
        "source_sha256": {
            n: sha(Path(__file__).with_name(n).read_bytes())
            for n in (
                "ledger.py",
                "kis_ledger.py",
                "price_issuer.py",
                "kis_audit.py",
                "kis_client.py",
                "kis_collect.py",
                "kis_events.py",
                "distribution_evidence.py",
                "price_quality.py",
            )
        },
    }
    folder = new_folder(output, "offline_etf_ledger")
    dump(folder / "result.json", result)
    dump(folder / "inputs.json", prepared)
    (folder / "distribution-evidence.json").write_bytes(distribution_blob)
    dump(folder / "review-template.json", review_template(bindings, events, start, end))
    dump(folder / "source-checksums.json", json.loads((saved / "checksums.json").read_text()))
    if issuer_blob is not None:
        (folder / "issuer-distributions.xlsx").write_bytes(issuer_blob)
    for name, blob in evidence_files.items():
        (folder / name).write_bytes(blob)
    for ticker, item in symbols.items():
        write_csv(folder / (ticker + "-ledger.csv"), item["rows"])
    (folder / "report.md").write_text(render(result), encoding="utf-8")
    dump(folder / "checksums.json", {p.name: sha(p.read_bytes()) for p in folder.iterdir()})
    (folder / "COMPLETE").write_text("saved; not an eligibility approval\n", encoding="utf-8")
    return result, folder


def write_csv(path, rows):
    fields = [
        "date",
        "is_session",
        "valuation_close_date",
        "shares_before",
        "split_ratio",
        "shares",
        "close",
        "receivable_added",
        "dividend_paid",
        "cash",
        "receivable",
        "reinvested_cash",
        "reinvested_shares",
        "external_contribution",
        "nav",
    ]
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: v["display"] if isinstance(v, dict) else v for k, v in row.items()})


def render(result):
    lines = [
        "# ETF 배당·현금·수량 원장",
        "",
        "상태: " + result["status"],
        "ETF 자체 USD 세전 계산. 전략 백테스트·검증된 총수익 인증이 아닙니다.",
        "시작일 종가 1주 매수. 지급일보다 뒤 첫 정규 거래일 종가에 재투자합니다.",
        "CSV는 표시용 12자리 반올림이며 result.json의 exact는 반올림 없는 분수입니다.",
        "휴장일 평가는 valuation_close_date를 표시합니다. 거래일 결측을 채우지 않습니다.",
        "held의 원장은 첫 보류일 전 진단용 접두 구간뿐이며 전체 구간 수익률은 없습니다.",
        "",
        "| 상품 | 원본 판정 | 원장 판정 | 날짜 행 수 | 세전 총수익률 |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for t, s in result["symbols"].items():
        value = s["total_return"]["display"] if s["total_return"] else "보류"
        lines.append(
            f"| {t} | {s['source_price_status']} | {s['status']} | {len(s['rows'])} | {value} |"
        )
    for t, s in result["symbols"].items():
        resolved = sum(
            y["resolved_blank_fields"] for y in s["distribution_evidence_by_year"].values()
        )
        lines += [
            "",
            f"{t}: 공식 사건별 명시 0으로 해석한 빈 자본이득 셀 {resolved}개.",
            "원본 빈칸은 보존. 주당 기준·다른 연도·전체 적격성 승인은 별도입니다.",
        ]
        contract = s["price_contract_evidence"]
        lines += [
            f"{t}: KIS 공식 MODP=0 미수정 옵션과 사용 후보 원본 요청 대조: "
            f"{contract['status']} / {len(contract['pages'])}페이지.",
            "옵션 정의 확인은 당시 거래가격·정규장 종가·기간 완전성의 자동 승인이 아닙니다.",
        ]
        lines += ["", f"{t} 보류 사유 (입력·해소 근거):"]
        lines.extend(
            f"- {b['date']} / {b['input']} / {b['code']}: {b['resolution']}" for b in s["blockers"]
        )
        if not s["blockers"]:
            lines.append("- 입력 근거서 범위에서 계산 완료. 근거 내용의 독립 인증은 아닙니다.")
    lines += [
        "",
        "원본 held/conditional 및 total_return_eligible는 변경하지 않습니다.",
        "strict_point_in_time=false. 빈 자본이득/미확인 주당 기준은 unknown입니다.",
        "MODP=1에는 배당을 더하거나 분할비를 다시 적용하지 않았습니다.",
        "KIS 옵션 정의 출처: " + result["price_contract_reference"]["url"],
        "COMPLETE는 보존 완료이며 적격성 승인이 아닙니다.",
        "",
    ]
    return "\n".join(lines)
