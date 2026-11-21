"""Offline twelve-ETF data inventory. No collection, estimates or eligibility approval."""

import json
from collections import Counter
from datetime import date
from pathlib import Path

from .distribution_evidence import CATALOG, interpret, load_catalog, summary
from .kis_audit import audit_archive, split_diagnostic
from .kis_client import KISError
from .kis_collect import archive_symbols, verify_archive
from .kis_events import review_events
from .price_audit import UNIVERSE, dump, new_folder, sha
from .price_issuer import parse_distributions
from .price_quality import trading_sessions

EXPANSION = CATALOG.with_name("ssga_distribution_expansion_v1.json")
SPLITS = CATALOG.with_name("ssga_splits_v1.json")


def combined_catalog():
    """Separate new nine-ETF facts; the three-ETF ledger's default catalog is unchanged."""
    original, first = load_catalog()
    extra, second = load_catalog(EXPANSION)
    if set(original["documents"]) & set(extra["documents"]):
        raise KISError("duplicate_evidence_document")
    catalog = {
        "schema_version": 1,
        "id": "twelve-etf-inventory-evidence-v1",
        "documents": original["documents"] | extra["documents"],
        "claims": original["claims"] + extra["claims"],
    }
    if len({c["id"] for c in catalog["claims"]}) != len(catalog["claims"]):
        raise KISError("duplicate_evidence_claim")
    return catalog, {CATALOG.name: sha(first), EXPANSION.name: sha(second)}


def split_review(ticker, normalized, start, end, catalog):
    result = {"history_completeness": "unknown", "events": [], "total_return": False}
    for event in catalog["events"]:
        if event["ticker"] != ticker or not start <= event["effective_date"] <= end:
            continue
        row = {**event, "status": "prices_not_available", "document": catalog["document"]}
        if normalized:
            row["period_record_date_candidates"] = [
                r
                for r in normalized["period_rights"]
                if r.get("rght_type_cd") == "14"
                and r.get("acpl_bass_dt") == event["record_date"].replace("-", "")
            ]
            row["ice_effective_date_candidates"] = [
                r
                for r in normalized["ice_rights"]
                if r.get("effective_dt") == event["effective_date"].replace("-", "")
            ]
            values = []
            for modp in ("0", "1"):
                for d in (event["previous_session"], event["effective_date"]):
                    matches = [r for r in normalized["prices_by_modp"][modp] if r["date"] == d]
                    if len(matches) == 1:
                        values.append(matches[0]["clos"])
            if len(values) == 4:
                row["diagnostic"] = split_diagnostic(*values, ratio=event["new_per_old"])
                difference = abs(
                    row["diagnostic"]["official_split_neutral_ratio"]
                    - row["diagnostic"]["provider_modp1_ratio"]
                )
                row["ratio_difference"] = difference
                row["status"] = (
                    "difference_requires_review" if difference > 0.01 else "observed_consistent"
                )
                # This is the existing XLK diagnostic threshold, not a return/eligibility test.
        result["events"].append(row)
    return result


def continuous_segments(expected, actual):
    segments, current = [], []
    for d in expected:
        if d in actual:
            current.append(d)
        elif current:
            segments.append({"start": current[0], "end": current[-1], "sessions": len(current)})
            current = []
    if current:
        segments.append({"start": current[0], "end": current[-1], "sessions": len(current)})
    return segments


def run(saved, output, start, end, issuer_file=None):
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise KISError("invalid_inventory_period")
    inputs, candidates, issuer_blobs = [], {t: [] for t in UNIVERSE}, {}
    # Verify immutable input hashes before reading any prices. Never pick a latest folder silently.
    for path in saved:
        path = Path(path)
        meta = verify_archive(path)
        audit, normalized = audit_archive(path, meta)
        source_audit = json.loads((path / "audit.json").read_text())
        ref = {
            "archive": path.name,
            "manifest_sha256": sha((path / "manifest.json").read_bytes()),
            "checksums_sha256": sha((path / "checksums.json").read_bytes()),
            "mode": meta["mode"],
            "evidence_kind": meta["evidence_kind"],
            "symbols": list(archive_symbols(meta)),
            "start": meta["start"],
            "end_inclusive": meta["end_inclusive"],
        }
        inputs.append(ref)
        if meta.get("issuer_file"):
            blob = (path / meta["issuer_file"]).read_bytes()
            issuer_blobs[sha(blob)] = blob
        for t in archive_symbols(meta):
            candidates[t].append((ref, meta, audit, normalized[t], source_audit["symbols"][t]))
    if issuer_file:
        issuer_blob = Path(issuer_file).read_bytes()
    elif len(issuer_blobs) > 1:
        raise KISError("different_issuer_files_choose_explicit_issuer_file")
    else:
        issuer_blob = next(iter(issuer_blobs.values()), None)
    official = (
        parse_distributions(issuer_blob, UNIVERSE, start, end, exact_amounts=True)
        if issuer_blob
        else {}
    )
    catalog, catalog_hashes = combined_catalog()
    split_blob = SPLITS.read_bytes()
    splits = json.loads(split_blob)
    expected, calendar = trading_sessions(start, end)
    result = {
        "version": "kis-twelve-etf-inventory-v1",
        "start": start,
        "end_inclusive": end,
        "evaluation_candidate": ["2021-06-07", "2026-09-30"],
        "archives": inputs,
        "symbols": {},
        "calendar": calendar,
        "issuer_sha256": sha(issuer_blob) if issuer_blob else None,
        "catalog_sha256": catalog_hashes,
        "split_catalog_sha256": sha(split_blob),
        "strict_point_in_time": False,
        "total_return_eligible": False,
        "returns_calculated": False,
        "audit_source_sha256": {
            name: sha(Path(__file__).with_name(name).read_bytes())
            for name in (
                "kis_audit.py",
                "kis_events.py",
                "kis_collect.py",
                "price_issuer.py",
                "distribution_evidence.py",
                "price_quality.py",
            )
        },
        "tool_sha256": sha(Path(__file__).read_bytes()),
    }
    common_sets = []
    for ticker in UNIVERSE:
        source = official.get(ticker)
        events = (
            [
                dict(e, **e["source_amounts"], share_basis="unknown")
                for e in source["events_in_source_order"]
            ]
            if source
            else []
        )
        effective, interpretation = interpret(ticker, events, catalog)
        item = {
            "status": "not_run",
            "price_status": "not_run",
            "price_rows": {"0": 0, "1": 0},
            "actual_range": {},
            "source_status": None,
            "blocking_inputs": [],
            "archives": [c[0] for c in candidates[ticker]],
            "distributions": {
                "status": "supplied_issuer_file_parsed" if source else "not_available",
                "rows": len(events),
                "by_year": summary(interpretation),
                "source_events": events,
                "effective_events": effective,
                "event_interpretations": interpretation,
                "source_issues": source["issues"] if source else [],
                "history_completeness": "unknown",
                "share_basis": "unknown",
            },
            "proposed_assumptions_not_approved": [
                "MODP=0을 당시 정규장 종가로 사용할 근거 또는 범위별 가정",
                "분배·분할 목록의 충분성 및 배당의 당시 1주 기준",
                "미해소 자본이득 빈칸의 개별 근거; 자동 0 금지",
            ],
        }
        normalized = None
        if not candidates[ticker]:
            item["blocking_inputs"].append("no_saved_price_archive_in_this_run")
        elif len(candidates[ticker]) > 1:
            item["status"] = item["price_status"] = "held"
            item["blocking_inputs"].append("multiple_archives_for_symbol_select_one; no_merge")
        else:
            ref, meta, audit, normalized, old = candidates[ticker][0]
            item["source_status"] = old["status"]
            item["current_audit"] = audit["symbols"][ticker]
            item["price_row_errors"] = [
                r for r in audit["price_row_errors"] if r["ticker"] == ticker
            ]
            ready = audit["price_readiness"][ticker]["ready"]
            item["price_status"] = "conditional" if ready else "held"
            item["status"] = audit["symbols"][ticker]["status"]
            valid = {}
            for modp in ("0", "1"):
                rows = [r for r in normalized["prices_by_modp"][modp] if start <= r["date"] <= end]
                counts = Counter(r["date"] for r in rows)
                valid[modp] = {d for d, n in counts.items() if n == 1}
                item["price_rows"][modp] = len(rows)
                item["actual_range"][modp] = [min(counts, default=None), max(counts, default=None)]
            item["missing_sessions"] = {
                m: sorted(set(expected) - dates) for m, dates in valid.items()
            }
            if any(item["missing_sessions"].values()):
                item["status"] = item["price_status"] = "held"
                item["blocking_inputs"].append("requested_session_prices_missing")
            complete_scope = (
                meta["mode"] == "collect" and meta["start"] <= start <= end <= meta["end_inclusive"]
            )
            if not complete_scope:
                item["status"] = item["price_status"] = "held"
                item["blocking_inputs"].append("probe_or_incomplete_requested_scope")
            if meta["evidence_kind"] != "real":
                item["status"] = item["price_status"] = "synthetic_only"
            elif complete_scope:
                common_sets.append(valid["0"] & valid["1"] & set(expected))
            item["event_review"] = review_events(
                normalized["prices_by_modp"],
                normalized["period_rights"],
                normalized["ice_rights"],
                source,
            )
            item["rights"] = {
                "period_rows": len(normalized["period_rights"]),
                "ice_rows": len(normalized["ice_rights"]),
                "history_completeness": "unknown",
                "semantics": normalized["rights_semantics"],
                "empty_means": "unknown_not_no_events",
            }
        item["splits"] = split_review(ticker, normalized, start, end, splits)
        if any(e["status"] == "difference_requires_review" for e in item["splits"]["events"]):
            if item["status"] != "synthetic_only":
                item["status"] = "held"
            item["blocking_inputs"].append("official_split_price_difference_requires_review")
        item["blocking_inputs"] += [
            "price_basis_unverified",
            "distribution_and_split_coverage_unknown",
            "share_basis_unknown",
        ]
        if not events or any(
            s["unknown_short_term_gain"] or s["unknown_long_term_gain"] or s["conflicts"]
            for s in item["distributions"]["by_year"].values()
        ):
            item["blocking_inputs"].append("capital_gains_unknown_or_conflicting")
        result["symbols"][ticker] = item
    common = {
        "status": "not_established",
        "missing_real_full_scope_symbols": [
            t
            for t in UNIVERSE
            if len(candidates[t]) != 1
            or candidates[t][0][1]["evidence_kind"] != "real"
            or candidates[t][0][1]["mode"] != "collect"
            or not candidates[t][0][1]["start"]
            <= start
            <= end
            <= candidates[t][0][1]["end_inclusive"]
        ],
    }
    if len(common_sets) == len(UNIVERSE):
        dates = set.intersection(*common_sets)
        common.update(
            status="observed_dates_only_not_eligibility",
            sessions=len(dates),
            first=min(dates, default=None),
            last=max(dates, default=None),
            missing_sessions=sorted(set(expected) - dates),
            continuous_segments=continuous_segments(expected, dates),
            preparation_sessions=sum(d < "2021-06-07" for d in dates & set(expected)),
        )
    result["common_prices"] = common
    folder = new_folder(output, "kis_offline_inventory")
    dump(folder / "inventory.json", result)
    dump(folder / "distribution-evidence.json", catalog)
    (folder / "split-evidence.json").write_bytes(split_blob)
    if issuer_blob:
        (folder / "issuer-distributions.xlsx").write_bytes(issuer_blob)
    lines = [
        "# 12상품 자료·근거 현황",
        "",
        f"요청: {start}~{end}, 양끝 포함.",
        "수익률·전략 계산 없음. 원본 판정과 검수 대상 기간을 구분합니다.",
        "not_run=이 실행에 가격 원본 없음; synthetic_only=합성 테스트; "
        "held=보류; conditional=조건부 후보.",
        "",
        "| 상품 | 요청 | 실제 범위·MODP0/1 행 | 가격 판정 / 전체 | "
        "분배·분할 | 계산 차단·다음 조치 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for t, item in result["symbols"].items():
        splits_text = (
            ", ".join(
                f"{e['effective_date']} {e['new_per_old']}:1/{e['status']}"
                for e in item["splits"]["events"]
            )
            or "목록 완전성 미확인"
        )
        lines.append(
            f"| {t} | {start}~{end} | {item['actual_range']} / {item['price_rows']} | "
            f"{item['price_status']} / {item['status']} | "
            f"공식 분배 {item['distributions']['rows']}건; {splits_text} | "
            f"{'; '.join(item['blocking_inputs'])} |"
        )
    lines += [
        "",
        "## 상품·연도별 공식 분배 근거",
        "",
        "| 상품 | 연도 | 사건 | 해소 빈 셀 | 남은 단기/장기 | 주당 기준 미확인 |",
        "| --- | --- | ---: | ---: | --- | ---: |",
    ]
    for t, item in result["symbols"].items():
        for y, s in item["distributions"]["by_year"].items():
            lines.append(
                f"| {t} | {y} | {s['events']} | {s['resolved_blank_fields']} | "
                f"{s['unknown_short_term_gain']}/{s['unknown_long_term_gain']} | "
                f"{s['unknown_share_basis']} |"
            )
    lines += [
        "",
        "공통 기간: " + common["status"],
        "가정 제안은 미승인입니다. 기존 세 상품 2025 가정을 자동 확대하지 않습니다.",
        "사건 날짜·원본 빈칸·별도 해석·문서/셀 위치·권리·구간은 inventory.json을 확인하세요.",
        "빈 권리/목록에 없는 분할은 사건 없음의 근거가 아닙니다. MODP=1에 배당을 더하지 않습니다.",
        "strict_point_in_time=false / total_return_eligible=false. COMPLETE는 저장 완료입니다.",
        "",
    ]
    (folder / "report.md").write_text("\n".join(lines), encoding="utf-8")
    dump(folder / "checksums.json", {p.name: sha(p.read_bytes()) for p in folder.iterdir()})
    (folder / "COMPLETE").write_text("inventory saved; no return calculation\n", encoding="utf-8")
    return result, folder
