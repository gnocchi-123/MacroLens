"""KIS-specific adapter: no Yahoo options, invented Adj Close or dividend reinvestment."""

import json
import math
from pathlib import Path

from .kis_client import KISError, public_payload
from .kis_collect import SAMPLE, day, rows
from .price_issuer import parse_distributions
from .price_quality import trading_sessions


def optional_day(value):
    return None if value in (None, "", "00000000") else day(value)


def normalize_rights(period, ice):
    """Map only dates explicitly named in the official column definitions."""
    return {
        "period": [
            {
                "kind_code": r.get("rght_type_cd"),
                "record_date": optional_day(r.get("acpl_bass_dt")),
                "ex_date": None,
                "payable_date": None,
                "split_effective_date": None,
                "allocation_unit_price": r.get("alct_frcr_unpr"),
                "stock_allocation_ratio": r.get("stck_alct_rt"),
                "cash_allocation_ratio": r.get("cash_alct_rt"),
                "cash_dividend_per_share": None,
                "split_new_per_old": None,
                "currency": r.get("crcy_cd"),
                "confirmation_code": r.get("dfnt_yn"),
                "share_basis": "unknown",
            }
            for r in period
        ],
        "ice": [
            {
                "title": r.get("ca_title"),
                **{
                    name: optional_day(r.get(field))
                    for name, field in (
                        ("announcement_date", "anno_dt"),
                        ("ex_date", "div_lock_dt"),
                        ("payable_date", "pay_dt"),
                        ("record_date", "record_dt"),
                        ("validity_date", "validity_dt"),
                        ("effective_date", "effective_dt"),
                        ("rights_ex_date", "lock_dt"),
                    )
                },
            }
            for r in ice
        ],
    }


def split_diagnostic(before, after, adjusted_before, adjusted_after, *, modp="0", ratio=2):
    if modp != "0":
        raise KISError("split_must_not_be_applied_to_adjusted_price")
    return {
        "raw_price_ratio": after / before,
        "official_split_neutral_ratio": ratio * after / before,
        "provider_modp1_ratio": adjusted_after / adjusted_before,
        "dividend_added": False,
        "total_return": False,
    }


def audit_archive(folder, meta):
    folder = Path(folder)
    identity_source = meta.get("identity_source", "overseas")
    if identity_source not in ("overseas", "basic-quote"):
        raise KISError("unknown_identity_source")
    pages = []
    for record in meta["pages"]:
        pages.append((record, public_payload((folder / record["file"]).read_bytes())))
    issuer = None
    if meta.get("issuer_file"):
        issuer = parse_distributions(
            (folder / meta["issuer_file"]).read_bytes(),
            SAMPLE,
            meta["start"],
            meta["end_inclusive"],
        )
    result = {
        "status": "held",
        "evidence_kind": meta["evidence_kind"],
        "symbols": {},
        "can_collect": False,
        "strict_point_in_time": False,
        "total_return_eligible": False,
        "automatic_corrections": [],
    }
    probe_prices_ok = True
    normalized = {}
    for ticker in SAMPLE:
        issues, limits = (
            [],
            [
                "MODP=1 조정 범위·기준일·정규장/시각 계약 미확인; 총수익 계열 아님",
                "배당 금액·주당 기준·자본이득·권리 역사 완전성 추가 검토 필요",
                "개인 보존·무료 이용 범위는 본인 계정 계약 확인 필요",
            ],
        )
        prices, price_groups, identity, rights, ice = {"0": [], "1": []}, {}, {}, [], []
        basic, quote = {}, {}
        for record, body in pages:
            params = record["params"]
            if params.get("SYMB", params.get("PDNO")) != ticker:
                continue
            endpoint = record["endpoint"]
            if endpoint == "search-info":
                identity = body.get("output", {})
                if isinstance(identity, list):
                    identity = identity[0] if len(identity) == 1 else {}
            elif endpoint in ("basic-info", "price-detail"):
                output = body.get("output", {})
                if isinstance(output, list):
                    output = output[0] if len(output) == 1 else {}
                if not isinstance(output, dict):
                    output = {}
                if endpoint == "basic-info":
                    basic = output
                else:
                    quote = output
            elif endpoint == "dailyprice":
                modp, label = params["MODP"], record["label"]
                group = price_groups.setdefault((modp, label), [])
                try:
                    source_rows = rows(body, "output2")
                except KISError:
                    issues.append("가격 배열 형식 오류")
                    continue
                header = body.get("output1")
                rsym = header.get("rsym", "") if isinstance(header, dict) else ""
                if rsym != "D" + meta["exchange"] + ticker:
                    issues.append("가격 응답 종목 식별 미확인")
                for row in source_rows:
                    try:
                        item = {"date": day(row.get("xymd")), "source_file": record["file"]}
                        for field in ("open", "high", "low", "clos", "tvol"):
                            item[field] = float(row[field])
                            if not math.isfinite(item[field]) or item[field] < 0:
                                raise ValueError
                        if (
                            min(item[f] for f in ("open", "high", "low", "clos")) <= 0
                            or item["low"] > min(item["open"], item["clos"])
                            or item["high"] < max(item["open"], item["clos"])
                        ):
                            raise ValueError
                        group.append(item)
                    except (KISError, ValueError, KeyError, TypeError):
                        issues.append("가격 날짜/숫자/OHLC 비정상; 원문 보존")
            elif endpoint == "period-rights":
                try:
                    rights.extend(rows(body, "output"))
                except KISError:
                    issues.append("기간별 권리 배열 형식 오류")
            elif endpoint == "rights-by-ice":
                try:
                    ice.extend(rows(body, "output1"))
                except KISError:
                    issues.append("ICE 권리 배열 형식 오류")
        if identity_source == "basic-quote":
            # Both independent official responses must agree; never fill identity from a ticker.
            identity = {"basic_info": basic, "price_detail": quote}
            if basic.get("pdno") != ticker or basic.get("prdt_type_cd") != meta["product_type"]:
                issues.append("공통 상품기본조회 상품번호/상품유형 불일치 또는 미확인")
            if quote.get("curr") != "USD":
                issues.append("현재가상세 USD 통화 미확인")
            if quote.get("rsym") != "D" + meta["exchange"] + ticker:
                issues.append("현재가상세 거래소/상품 식별 불일치 또는 미확인")
            limits.append(
                "현재 식별 자료: 과거 상장/분류 보증 아님; 현재가를 과거 가격에 사용하지 않음"
            )
        elif not isinstance(identity, dict) or identity.get("tr_crcy_cd") != "USD":
            issues.append("상품 식별 또는 USD 통화 미확인")
        elif (
            identity.get("ovrs_excg_cd")
            not in {"AMS": ("AMS", "AMEX"), "NYS": ("NYS", "NYSE"), "NAS": ("NAS", "NASD")}[
                meta["exchange"]
            ]
        ):
            issues.append("요청 거래소와 상품 응답 거래소 대조 필요")
        ranges = [("full", meta["start"], meta["end_inclusive"])]
        if meta["mode"] == "probe":
            ranges = [("recent", "2026-09-21", "2026-09-30"), ("old", "2020-06-01", "2020-06-10")]
        coverage = []
        for modp in ("0", "1"):
            for label, start, end in ranges:
                group = price_groups.get((modp, label), [])
                dates = [r["date"] for r in group]
                if len(dates) != len(set(dates)):
                    issues.append("동일 일자 중복/충돌; 자동 제거하지 않음")
                # Pagination records retain source order; normalized rows also retain that order.
                chosen = [r for r in group if start <= r["date"] <= end]
                actual = {r["date"] for r in chosen}
                expected, calendar = trading_sessions(start, end)
                missing, unexpected = sorted(set(expected) - actual), sorted(actual - set(expected))
                coverage.append(
                    {
                        "modp": modp,
                        "label": label,
                        "start": start,
                        "end": end,
                        "missing_sessions": missing,
                        "unexpected_dates": unexpected,
                        "calendar": calendar,
                    }
                )
                if missing or unexpected:
                    issues.append(
                        f"{label}/MODP={modp}: 결측 {len(missing)}, 비거래일 {len(unexpected)}"
                    )
                prices[modp].extend(chosen)
        # Price preflight allows further evidence collection, never total-return approval.
        price_ready = not issues and not any(
            f["ticker"] == ticker and ("modp" in f["stream"] or f["stream"].startswith("identity"))
            for f in meta["failures"]
        )
        probe_prices_ok &= price_ready
        for failure in meta["failures"]:
            if failure["ticker"] in (ticker, "ALL"):
                issues.append(failure["stream"] + ": " + failure["code"])
        if not rights:
            issues.append("빈 기간별 권리: 사건 없음이 아닌 unknown")
        if not ice:
            issues.append("빈 ICE 권리: 날짜 대조 불가")
        try:
            normalized_rights = normalize_rights(rights, ice)
        except KISError:
            normalized_rights = None
            issues.append("권리 날짜 형식 오류; 원문 보존")
        for records in (rights, ice):
            fingerprints = [json.dumps(r, sort_keys=True) for r in records]
            if len(fingerprints) != len(set(fingerprints)):
                issues.append("중복 권리 행; 원문 유지하고 완전성 보류")
        for row in rights:
            if row.get("pdno") != ticker or row.get("prdt_type_cd") != meta["product_type"]:
                issues.append("다른 상품/유형 권리 혼입")
            if row.get("dfnt_yn") != "Y":
                limits.append("예정/확정 코드 미확인 권리 존재; 소급 신호 사용 금지")
        comparisons = []
        if issuer:
            for event in issuer[ticker]["events_in_source_order"]:
                record_day = event["record_date"].replace("-", "")
                period = [
                    r
                    for r in rights
                    if r.get("acpl_bass_dt") == record_day and r.get("rght_type_cd") in ("03", "75")
                ]
                dates = [
                    r
                    for r in ice
                    if r.get("div_lock_dt") == event["ex_date"].replace("-", "")
                    and r.get("pay_dt") == event["payable_date"].replace("-", "")
                    and r.get("record_dt") == record_day
                ]
                comparisons.append(
                    {
                        "issuer_ex_date": event["ex_date"],
                        "issuer_record_date": event["record_date"],
                        "issuer_payable_date": event["payable_date"],
                        "period_record_date_candidates": len(period),
                        "ice_date_matches": len(dates),
                        "amount_and_share_basis": "unverified",
                        "issuer_dividend": event["dividend"],
                        "kis_allocation_unit_candidates": [r.get("alct_frcr_unpr") for r in period],
                    }
                )
            if any(
                x["ice_date_matches"] != 1 or x["period_record_date_candidates"] != 1
                for x in comparisons
            ):
                limits.append("운용사 배당 날짜와 KIS 권리 1:1 대조 미완료")
        else:
            limits.append("운용사 공식 XLSX 미첨부; --issuer-file로 오프라인 대조 가능")
        split = {"status": "not_in_range_or_not_xlk"}
        if ticker == "XLK" and meta["start"] <= "2025-12-05" <= meta["end_inclusive"]:
            split = {
                "status": "unverified",
                "official_effective_date": "2025-12-05",
                "official_record_date": "2025-12-02",
                "official_new_per_old": 2,
                "period_candidates": [
                    r
                    for r in rights
                    if r.get("rght_type_cd") == "14" and r.get("acpl_bass_dt") == "20251202"
                ],
                "ratio_basis": "stck_alct_rt interpretation not yet verified",
            }
            byday = {modp: {r["date"]: r for r in prices[modp]} for modp in ("0", "1")}
            if all(d in byday[m] for m in byday for d in ("2025-12-04", "2025-12-05")):
                split["price_diagnostic"] = split_diagnostic(
                    *[byday[m][d]["clos"] for m in ("0", "1") for d in ("2025-12-04", "2025-12-05")]
                )
                v = split["price_diagnostic"]
                split["price_ratio_difference"] = abs(
                    v["official_split_neutral_ratio"] - v["provider_modp1_ratio"]
                )
                if split["price_ratio_difference"] > 0.01:
                    issues.append("XLK 분할 전후 MODP 두 계열 차이 조사 필요")
        status = "held" if issues else "conditional"
        normalized[ticker] = {
            "prices_by_modp": prices,
            "period_rights": rights,
            "ice_rights": ice,
            "rights_semantics": normalized_rights,
        }
        result["symbols"][ticker] = {
            "status": status,
            "issues": list(dict.fromkeys(issues)),
            "limits": list(dict.fromkeys(limits)),
            "identity": identity,
            "prices": {
                m: {
                    "rows": len(p),
                    "first": min((r["date"] for r in p), default=None),
                    "last": max((r["date"] for r in p), default=None),
                }
                for m, p in prices.items()
            },
            "coverage": coverage,
            "rights_rows": len(rights),
            "ice_rows": len(ice),
            "rights_completeness": "unknown",
            "issuer_comparisons": comparisons,
            "xlk_split": split,
            "adj_close": "not_provided_not_created",
            "modp0": "provider_adjustment_off",
            "modp1": "provider_adjusted_scope_unverified",
        }
    result["can_collect"] = meta["mode"] == "probe" and probe_prices_ok
    if any(f["ticker"] == "ALL" for f in meta["failures"]):
        result["can_collect"] = False
    if all(r["status"] == "conditional" for r in result["symbols"].values()):
        result["status"] = "conditional"
    if meta["evidence_kind"] != "real":
        result["status"] = "synthetic_only"
        result["can_collect"] = False
    return result, normalized
