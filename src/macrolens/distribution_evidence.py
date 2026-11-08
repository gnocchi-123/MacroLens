"""Scoped official distribution interpretations; never repair the source workbook.

Only an explicitly zero, final, event-level tax component can resolve a blank.
Tax classifications and annual totals are never additional cash distributions.
"""

import copy
import json
import re
from collections import Counter
from datetime import date
from fractions import Fraction
from pathlib import Path

from .price_audit import dump, new_folder, sha
from .price_issuer import parse_distributions
from .price_quality import AuditError

CATALOG = Path(__file__).with_name("evidence") / "ssga_distribution_v1.json"
FIELDS = ("short_term_gain", "long_term_gain")
KEY_FIELDS = ("cusip", "ex_date", "record_date", "payable_date")
SYMBOLS = ("XLC", "XLK", "SPY")


def load_catalog(path=None):
    blob = (Path(path) if path else CATALOG).read_bytes()
    catalog = json.loads(blob)
    if catalog.get("schema_version") != 1 or not isinstance(catalog.get("documents"), dict):
        raise AuditError("분배 근거 목록 형식 오류")
    claims = catalog.get("claims")
    if not isinstance(claims, list) or len({c["id"] for c in claims}) != len(claims):
        raise AuditError("분배 근거 ID 중복/형식 오류")
    return catalog, blob


def applicability(claim, document, event, field):
    """A date/CUSIP match alone does not validate a document's field or finality."""
    if document.get("kind") != "final_event_tax_summary":
        return "not_final_event_evidence"
    year = event["ex_date"][:4]
    if str(document.get("tax_year")) != year:
        return "different_tax_year"
    if not document.get("start", "9999") <= event["ex_date"] <= document.get("end", ""):
        return "outside_document_period"
    if claim.get("estimated") is not False or claim.get("scope") != "exact_event":
        return "estimated_or_aggregate_evidence"
    definition = document.get("fields", {}).get(field, {})
    if definition.get("role") != "tax_component_in_total":
        return "field_not_established_by_document"
    expected = definition.get("column", "") + str(claim.get("row", ""))
    if (
        not re.fullmatch(r"[A-Z]+[1-9][0-9]*", expected)
        or claim.get("cells", {}).get(field) != expected
    ):
        return "field_location_mismatch"
    if not all(document.get(k) for k in ("title", "url", "sha256", "checked_at", "sheet")):
        return "incomplete_reference"
    return None


def interpret(ticker, events, catalog):
    """Return a separate effective view plus per-field provenance. No source mutation."""
    effective = copy.deepcopy(events)
    review = []
    counts = Counter(tuple(e.get(k) for k in KEY_FIELDS) for e in events)
    for source, output in zip(events, effective, strict=True):
        item = {"ticker": ticker, **{k: source.get(k) for k in KEY_FIELDS}, "fields": {}}
        candidates = [
            c
            for c in catalog["claims"]
            if c.get("ticker") == ticker
            and all(c.get(k) == source.get(k) and source.get(k) for k in KEY_FIELDS)
        ]
        for field in FIELDS:
            original = source.get(field)
            found, rejected = [], []
            for claim in candidates:
                if field not in claim.get("values", {}):
                    continue
                doc = catalog["documents"].get(claim.get("document"), {})
                reason = applicability(claim, doc, source, field)
                if counts[tuple(source.get(k) for k in KEY_FIELDS)] != 1:
                    reason = "duplicate_issuer_event"
                try:
                    amount = Fraction(claim["values"][field])
                    if amount < 0:
                        reason = "invalid_tax_component"
                except (ValueError, TypeError, ZeroDivisionError):
                    reason = "invalid_tax_component"
                if reason:
                    rejected.append({"claim_id": claim["id"], "reason": reason})
                else:
                    found.append((amount, claim))
            values = {amount for amount, _ in found}
            state = "source_reported" if original is not None else "unknown"
            if len(values) > 1:
                state = "conflict"
            elif values == {Fraction(0)}:
                if original is None:
                    output[field], state = "0", "resolved_explicit_event_zero"
                elif Fraction(original) != 0:
                    state = "conflict"
                else:
                    state = "corroborated_source_zero"
            elif values:
                # An amount inside Total Distribution Per Share must not be added a second time.
                state = "unsupported_nonzero_tax_component"
            item["fields"][field] = {
                "original": original,
                "effective": output.get(field),
                "status": state,
                "method": "separate_official_event_fact_not_blank_equals_zero",
                "claims": [c["id"] for _, c in found],
                "rejected": rejected,
                "locations": [
                    {"document": c["document"], "cell": c["cells"][field]} for _, c in found
                ],
                "additional_cash_from_tax_classification": False,
            }
        item["share_basis"] = output.get("share_basis", "unknown")
        review.append(item)
    return effective, review


def summary(review):
    years = {}
    for event in review:
        year = event["ex_date"][:4]
        row = years.setdefault(
            year,
            dict(
                events=0,
                resolved_blank_fields=0,
                unknown_short_term_gain=0,
                unknown_long_term_gain=0,
                unknown_share_basis=0,
                conflicts=0,
            ),
        )
        row["events"] += 1
        row["unknown_share_basis"] += event["share_basis"] != "per_ex_date_share"
        for field, result in event["fields"].items():
            row["resolved_blank_fields"] += result["status"] == "resolved_explicit_event_zero"
            row["unknown_" + field] += result["effective"] is None
            row["conflicts"] += result["status"] in (
                "conflict",
                "unsupported_nonzero_tax_component",
            )
    return dict(sorted(years.items()))


def inventory(issuer_file, output, start, end, evidence_file=None):
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise AuditError("분배 근거 요청 기간 오류")
    blob = Path(issuer_file).read_bytes()
    parsed = parse_distributions(blob, SYMBOLS, start, end, exact_amounts=True)
    catalog, evidence_blob = load_catalog(evidence_file)
    symbols = {}
    for ticker, source in parsed.items():
        events = [
            {**e, **e["source_amounts"], "share_basis": "unknown"}
            for e in source["events_in_source_order"]
        ]
        effective, review = interpret(ticker, events, catalog)
        symbols[ticker] = {
            "source_events": events,
            "effective_events": effective,
            "event_interpretations": review,
            "by_year": summary(review),
            "source_issues": source["issues"],
            "calculation_status": "not_run",
            "share_basis_status": "unknown",
        }
    result = {
        "kind": "official_distribution_evidence_inventory",
        "start": start,
        "end_inclusive": end,
        "issuer_sha256": sha(blob),
        "symbols": symbols,
        "catalog_id": catalog["id"],
        "catalog_sha256": sha(evidence_blob),
        "documents": catalog["documents"],
        "strict_point_in_time": False,
        "actual_price_ledger_run": False,
        "total_return_eligible": False,
        "interpretation_version": "scoped-explicit-event-zero-v1",
        "tool_sha256": sha(Path(__file__).read_bytes()),
        "source_verification": "reviewed catalogue; no network or new document verification",
    }
    folder = new_folder(output, "offline_distribution_evidence")
    dump(folder / "evidence.json", result)
    (folder / "issuer-distributions.xlsx").write_bytes(blob)
    (folder / "distribution-evidence.json").write_bytes(evidence_blob)
    lines = [
        "# 공식 분배 근거 적용 결과",
        "",
        "가격 원장 실행·총수익률 산출 결과가 아닙니다.",
        "원본의 빈칸과 금액은 보존합니다. 사건별 명시 0만 별도 해석으로 사용합니다.",
        "",
        "| 상품 | 연도 | 사건 | 해소한 빈 셀 | 남은 단기 | 남은 장기 | 주당 기준 미확인 | 충돌 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for ticker, item in symbols.items():
        for year, s in item["by_year"].items():
            lines.append(
                f"| {ticker} | {year} | {s['events']} | {s['resolved_blank_fields']} | "
                f"{s['unknown_short_term_gain']} | {s['unknown_long_term_gain']} | "
                f"{s['unknown_share_basis']} | {s['conflicts']} |"
            )
    lines += [
        "",
        "사건별 원본·해석·문서·셀 위치는 evidence.json을 확인하세요.",
        "2025년 근거는 다른 연도/상품/날짜에 적용하지 않습니다.",
        "2026년 추정치·연간 총액·세무 분류를 새 현금흐름으로 쓰지 않습니다.",
        "strict_point_in_time=false. COMPLETE는 저장 완료입니다.",
        "",
    ]
    (folder / "report.md").write_text("\n".join(lines), encoding="utf-8")
    dump(folder / "checksums.json", {p.name: sha(p.read_bytes()) for p in folder.iterdir()})
    (folder / "COMPLETE").write_text("saved; no total return certification\n", encoding="utf-8")
    return result, folder
