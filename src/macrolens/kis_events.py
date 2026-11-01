"""Offline event evidence only: never adjust prices or approve total returns."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .price_quality import trading_sessions

KNOWN_MISSING_POLICY = {
    "id": "xlc-20230605-exclude-required-samples-v1",
    "agreed_on": "2026-11-01",
    "ticker": "XLC",
    "date": "2023-06-05",
    "reason": "low_above_open_or_close",
    "modp": ["0", "1"],
    "price_quality_override": False,
    "fill_or_shift_dates": False,
    "future_rule": "exclude_calculations_requiring_this_price; exclude_entire_cross_sector_sample",
    "exclusion_engine_implemented": False,
    "other_errors_allowed": False,
    "research_eligibility": "not_granted_by_this_policy",
}


def numeric_candidate(field, value, currency, dividend):
    item = {"field": field, "currency": currency, "status": "unverified", "share_basis": "unknown"}
    try:
        number, reference = Decimal(str(value)), Decimal(str(dividend))
        if not number.is_finite() or not reference.is_finite() or number < 0 or reference < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        item["status"] = "missing_or_invalid_amount"
        return item
    # Numerical equality is evidence, not proof of the meaning of this KIS field.
    item["value"] = str(number)
    if currency != "USD":
        item["status"] = "currency_unverified_or_not_usd"
        return item
    item.update(
        status="numeric_equal_basis_unverified" if number == reference else "numeric_difference",
        delta_from_issuer_dividend=str(number - reference),
        ratio_to_issuer_dividend=str(number / reference) if reference else None,
        precision_relation=(
            "exact"
            if number == reference
            else "within_one_5dp_unit"
            if abs(number - reference) <= Decimal("0.00001")
            else "outside_one_5dp_unit"
        ),
        diagnostic_unit_usd="0.00001",
        provider_rounding_rule_confirmed=False,
    )
    return item


def primary_amount_relation(event, issuer_quality_issues):
    """Describe one confirmed USD amount without requiring secondary ICE coverage."""
    if issuer_quality_issues or event["period_record_date_candidates"] != 1:
        return "unverified_or_ambiguous"
    row = event["amount_candidates"][0]
    if row["confirmation_code"] != "Y":
        return "unverified_or_ambiguous"
    candidates = [c for c in row["candidates"] if "precision_relation" in c]
    if len(candidates) != 1:
        return "unverified_or_ambiguous"
    return candidates[0]["precision_relation"]


def price_pair(prices, ex_date):
    """Use the immediately preceding exchange session, never the last available row."""
    event_day = date.fromisoformat(ex_date)
    sessions, _ = trading_sessions((event_day - timedelta(days=10)).isoformat(), ex_date)
    if ex_date not in sessions or len(sessions) < 2:
        return {"status": "event_not_trading_session", "ex_date": ex_date}
    previous = sorted(sessions)[-2]
    selected = {}
    for modp in ("0", "1"):
        selected[modp] = {}
        for day in (previous, ex_date):
            candidates = [r for r in prices[modp] if r["date"] == day]
            if len(candidates) != 1:
                return {
                    "status": "missing_or_duplicate_price",
                    "ex_date": ex_date,
                    "previous_session": previous,
                }
            selected[modp][day] = candidates[0]
    p0, p1 = (selected["0"][d]["clos"] for d in (previous, ex_date))
    a0, a1 = (selected["1"][d]["clos"] for d in (previous, ex_date))
    return {
        "status": "observed_not_total_return_validation",
        "ex_date": ex_date,
        "previous_session": previous,
        "source_files": {
            m: {d: r["source_file"] for d, r in records.items()} for m, records in selected.items()
        },
        "modp0_close_ratio": p1 / p0,
        "modp1_close_ratio": a1 / a0,
        "factor_before": a0 / p0,
        "factor_after": a1 / p1,
        "factor_step": (a1 / p1) / (a0 / p0),
        "dividend_added": False,
        "split_applied_to_modp1": False,
    }


def review_events(prices, rights, ice, issuer):
    result = {"status": "issuer_not_attached", "events": [], "total_return_eligible": False}
    if issuer is None:
        return result
    events = issuer["events_in_source_order"]
    dates = [e["ex_date"] for e in events]
    quality = [i for i in issuer.get("issues", []) if i != "일별 가격·공급자 사건과의 대조 미완료"]
    if len(set(dates)) != len(dates):
        quality.append("duplicate_issuer_ex_dates")
    if not events:
        quality.append("no_issuer_events")
    result.update(status="evidence_only_basis_unverified", issuer_quality_issues=quality)
    for event in events:
        rec, ex, pay = (
            event[k].replace("-", "") for k in ("record_date", "ex_date", "payable_date")
        )
        period = [
            r
            for r in rights
            if r.get("acpl_bass_dt") == rec and r.get("rght_type_cd") in ("03", "75")
        ]
        matched = [
            r
            for r in ice
            if r.get("div_lock_dt") == ex and r.get("record_dt") == rec and r.get("pay_dt") == pay
        ]
        amounts = []
        for row in period:
            candidates = [
                numeric_candidate(
                    "alct_frcr_unpr",
                    row.get("alct_frcr_unpr"),
                    row.get("crcy_cd"),
                    event["dividend"],
                )
            ]
            for n in (2, 3, 4):
                key = f"stkp_dvdn_frcr_amt{n}"
                if key in row or f"crcy_cd{n}" in row:
                    candidates.append(
                        numeric_candidate(
                            key, row.get(key), row.get(f"crcy_cd{n}"), event["dividend"]
                        )
                    )
            amounts.append({"confirmation_code": row.get("dfnt_yn"), "candidates": candidates})
        result["events"].append(
            {
                "issuer": event,
                "period_record_date_candidates": len(period),
                "ice_three_date_matches": len(matched),
                "date_match_only": len(period) == 1 and len(matched) == 1 and not quality,
                "amount_candidates": amounts,
                "capital_gains_complete": all(
                    event.get(k) is not None for k in ("short_term_gain", "long_term_gain")
                ),
                "amount_and_share_basis": "unverified",
                "price_pair": price_pair(prices, event["ex_date"]),
            }
        )
    result["summary"] = {
        "issuer_events": len(events),
        "one_to_one_date_matches": sum(e["date_match_only"] for e in result["events"]),
        "unique_confirmed_usd_numeric_matches_basis_unverified": sum(
            e["date_match_only"]
            and e["amount_candidates"][0]["confirmation_code"] == "Y"
            and sum(
                c["status"] == "numeric_equal_basis_unverified"
                for c in e["amount_candidates"][0]["candidates"]
            )
            == 1
            for e in result["events"]
        ),
        "price_pairs_available": sum(
            e["price_pair"]["status"] == "observed_not_total_return_validation"
            for e in result["events"]
        ),
        "capital_gains_unknown_events": sum(
            not e["capital_gains_complete"] for e in result["events"]
        ),
    }
    for event in result["events"]:
        event["primary_amount_relation_basis_unverified"] = primary_amount_relation(event, quality)
    result["summary"]["period_record_date_matches"] = sum(
        e["period_record_date_candidates"] == 1 and not quality for e in result["events"]
    )
    result["summary"]["ice_three_date_matches"] = sum(
        e["ice_three_date_matches"] == 1 and not quality for e in result["events"]
    )
    result["summary"]["primary_amount_relations_basis_unverified"] = {
        relation: sum(
            e["primary_amount_relation_basis_unverified"] == relation for e in result["events"]
        )
        for relation in (
            "exact",
            "within_one_5dp_unit",
            "outside_one_5dp_unit",
            "unverified_or_ambiguous",
        )
    }
    return result
