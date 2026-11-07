"""Exact, offline USD ETF accounting. No signals, orders or strategy evaluation."""

from datetime import date, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction

RULES_VERSION = "etf-cash-receivable-v1"


def number(value):
    """Source decimal text -> rational; never round quantities or intermediate balances."""
    if isinstance(value, float):
        raise ValueError("ledger requires decimal text, not binary floats")
    return Fraction(value)


def display(value):
    with localcontext() as ctx:
        ctx.prec = 50
        return format(Decimal(value.numerator) / Decimal(value.denominator), ".12f")


def encoded(value):
    return {"exact": str(value), "display": display(value)}


def blocker(ticker, day, field, code, resolution):
    return dict(ticker=ticker, date=day, input=field, code=code, resolution=resolution)


def calculate(ticker, start, end, prices, sessions, events, splits, blockers=()):
    """Inputs are reviewed upstream. Unknowns stop the prefix, never disappear from totals.

    sessions must extend beyond all payable dates to specify the exact reinvestment day.
    Price keys are trading dates; weekends have an explicit last-market-close valuation.
    """
    start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
    if end_day < start_day:
        raise ValueError("reversed period")
    session_set = set(sessions)
    problems = list(blockers)
    if start not in session_set:
        problems.append(blocker(ticker, start, "start", "non_session_start", "거래일 시작일 지정"))
    close = {}
    for day in sorted(session_set):
        if not start <= day <= end:
            continue
        try:
            value = number(prices[day])
            if value <= 0:
                raise ValueError
            close[day] = value
        except (KeyError, ValueError, ZeroDivisionError, TypeError):
            known = ticker == "XLC" and day == "2023-06-05"
            problems.append(
                blocker(
                    ticker,
                    day,
                    "MODP=0.close",
                    "known_xlc_missing" if known else "missing_or_invalid_close",
                    "해당 가격 의존 계산 제외; 보간·종가만 채택·재투자일 이동 금지"
                    if known
                    else "유효한 해당 거래일 원본과 검수 근거 필요; 채움/날짜 이동 금지",
                )
            )
    split_by_day = {}
    for split in splits:
        day = split["date"]
        if not start < day <= end:
            continue
        ratio = number(split["new_per_old"])
        if day not in session_set or ratio <= 0 or day in split_by_day:
            problems.append(blocker(ticker, day, "split", "invalid_split", "공식 적용일·비율 대조"))
        else:
            split_by_day[day] = ratio
    eligible_events, seen = [], set()
    for event in sorted(events, key=lambda e: e["ex_date"]):
        ex = event["ex_date"]
        # Buying at the start close creates no entitlement to today's or older distributions.
        if not start < ex <= end:
            continue
        pay, record = event["payable_date"], event["record_date"]
        valid = True
        if ex not in session_set or not ex <= record <= pay or ex in seen:
            problems.append(
                blocker(
                    ticker,
                    ex,
                    "distribution_dates",
                    "invalid_event_dates",
                    "공식 배당락·기준·지급일/중복 확인",
                )
            )
            valid = False
        seen.add(ex)
        amounts = []
        for field in ("dividend", "short_term_gain", "long_term_gain"):
            try:
                value = number(event.get(field))
                if value < 0:
                    raise ValueError
                amounts.append(value)
            except (ValueError, TypeError, ZeroDivisionError):
                problems.append(
                    blocker(
                        ticker,
                        ex,
                        field,
                        "unknown_distribution_amount",
                        "운용사 명시 금액 필요; 빈 자본이득은 0이 아님",
                    )
                )
                valid = False
        if event.get("share_basis") != "per_ex_date_share":
            problems.append(
                blocker(
                    ticker,
                    ex,
                    "share_basis",
                    "unknown_share_basis",
                    "해당 배당락일 당시 1주 기준의 분배금임을 확인하는 공식 근거",
                )
            )
            valid = False
        later = [s for s in sessions if s > pay]
        if not later:
            problems.append(
                blocker(
                    ticker,
                    ex,
                    "calendar",
                    "reinvestment_session_unknown",
                    "지급일 뒤 첫 정규 거래일을 포함하는 달력 필요",
                )
            )
            valid = False
        if valid:
            eligible_events.append(
                {**event, "amount": sum(amounts, Fraction()), "reinvest_date": min(later)}
            )
    problems.sort(key=lambda p: (p["date"], p["input"], p["code"]))
    stop = min((p["date"] for p in problems), default="9999-12-31")
    holdings, cash, receivable = Fraction(), Fraction(), Fraction()
    claims, rows = [], []
    last_close, close_date, initial = None, None, None
    current = start_day
    while current <= end_day and current.isoformat() < stop:
        day = current.isoformat()
        before = holdings
        ratio = split_by_day.get(day, Fraction(1))
        holdings *= ratio
        accrued = paid = bought = spent = external = Fraction()
        if day in session_set:
            last_close, close_date = close[day], day
        for event in eligible_events:
            if event["ex_date"] == day:
                # Prior-close ownership expressed in ex-date shares (same-day split if any).
                entitled = before * ratio
                amount = entitled * event["amount"]
                accrued += amount
                claims.append(
                    {
                        "ex_date": day,
                        "record_date": event["record_date"],
                        "payable_date": event["payable_date"],
                        "reinvest_date": event["reinvest_date"],
                        "entitled_shares": entitled,
                        "amount": amount,
                        "state": "receivable",
                    }
                )
        receivable += accrued
        for claim in claims:
            if claim["payable_date"] == day and claim["state"] == "receivable":
                paid += claim["amount"]
                claim["state"] = "cash"
        cash += paid
        receivable -= paid
        if day == start:
            initial = external = last_close
            holdings = Fraction(1)
        for claim in claims:
            if claim["reinvest_date"] == day and claim["state"] == "cash":
                spent += claim["amount"]
                claim["state"] = "reinvested"
        if spent:
            bought = spent / last_close
            holdings += bought
            cash -= spent
        nav = holdings * last_close + cash + receivable
        values = dict(
            shares_before=before,
            split_ratio=ratio,
            shares=holdings,
            close=last_close,
            receivable_added=accrued,
            dividend_paid=paid,
            cash=cash,
            receivable=receivable,
            reinvested_cash=spent,
            reinvested_shares=bought,
            external_contribution=external,
            nav=nav,
        )
        rows.append(
            {
                "date": day,
                "is_session": day in session_set,
                "valuation_close_date": close_date,
                **{key: encoded(value) for key, value in values.items()},
            }
        )
        current += timedelta(days=1)
    complete = not problems and bool(rows)
    return {
        "ticker": ticker,
        "start": start,
        "end_inclusive": end,
        "status": "calculated_under_reviewed_inputs" if complete else "held",
        "diagnostic_prefix_only": not complete,
        "blockers": problems,
        "rows": rows,
        "initial_value": encoded(initial) if initial is not None else None,
        "terminal_value": rows[-1]["nav"] if complete else None,
        "total_return": encoded(nav / initial - 1) if complete else None,
        "claims": [
            {**c, "amount": encoded(c["amount"]), "entitled_shares": encoded(c["entitled_shares"])}
            for c in claims
        ],
        "rules_version": RULES_VERSION,
        "strict_point_in_time": False,
        "validated_total_return": False,
    }
