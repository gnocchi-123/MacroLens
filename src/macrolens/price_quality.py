"""Price-file checks and event-local arithmetic only; no strategy evaluation."""

import csv
import io
import math
from datetime import date, datetime, timedelta
from importlib.metadata import version
from zoneinfo import ZoneInfo

COLUMNS = (
    "Date",
    "Open",
    "High",
    "Low",
    "Close",
    "Adj Close",
    "Volume",
    "Dividends",
    "Stock Splits",
)
NUMERIC = COLUMNS[1:]
PRICE_FIELDS = COLUMNS[1:6]


class AuditError(ValueError):
    """Safe diagnostic: never include raw provider response or secret values."""


def trading_sessions(start, end):
    try:
        import exchange_calendars as xcals
    except ImportError:
        raise AuditError("가격 검수 의존성 필요: uv sync --locked --extra price-audit") from None
    # Calendar bounds are session dates; include padding for weekend/holiday endpoints.
    calendar = xcals.get_calendar(
        "XNYS",
        start=date.fromisoformat(start) - timedelta(days=14),
        end=date.fromisoformat(end) + timedelta(days=14),
    )
    days = calendar.sessions_in_range(start, end)
    return [x.date().isoformat() for x in days], {
        "name": "XNYS",
        "package": "exchange-calendars",
        "version": version("exchange-calendars"),
        "scope": "미국 주식 정규장 날짜; 조기 종료일도 거래일. NYSE Arca ETF에 적용하는 달력 가정",
    }


def market_date(text, timezone):
    if len(text) == 10:
        return date.fromisoformat(text).isoformat()
    timestamp = datetime.fromisoformat(text)
    if timestamp.tzinfo is None:
        raise AuditError("일중 형식의 Date에는 시간대가 필요합니다.")
    return timestamp.astimezone(ZoneInfo(timezone)).date().isoformat()


def read_prices(blob, timezone):
    """Preserve order and missing values; never deduplicate, forward-fill or repair."""
    reader = csv.DictReader(io.StringIO(blob.decode("utf-8-sig")))
    if reader.fieldnames != list(COLUMNS):
        raise AuditError(
            "CSV 열 순서: Date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits"
        )
    rows = []
    for raw in reader:
        if None in raw or any(value is None for value in raw.values()):
            raise AuditError("CSV 열 수 불일치")
        try:
            row = {"date": market_date(raw["Date"], timezone)}
            for field in NUMERIC:
                row[field] = (
                    None if raw[field].strip() in ("", "null", "NaN", "nan") else float(raw[field])
                )
            rows.append(row)
        except (ValueError, TypeError, OverflowError):
            raise AuditError("CSV 날짜 또는 숫자 형식 오류") from None
    return rows


def event_returns(previous, current, close_basis, dividend_basis):
    """Returns local gross factors, not a portfolio or full-period performance."""
    if close_basis not in ("unadjusted", "split_adjusted"):
        raise AuditError("Close 조정 의미 미확정")
    if dividend_basis != "same_share_basis_as_close":
        raise AuditError("배당과 Close의 주당 기준이 다르거나 미확정")
    ratio = (current["Stock Splits"] or 1.0) if close_basis == "unadjusted" else 1.0
    p0, p1, dividend = previous["Close"], current["Close"], current["Dividends"]
    # On a raw split day, cash distribution must be per NEW share, matching today's close.
    price_growth = ratio * p1 / p0
    reinvest_at_close = ratio * (p1 + dividend) / p0
    denominator = p0 / ratio - dividend
    backward_factor_growth = p1 / denominator if denominator > 0 else None
    return {
        "price_return": price_growth - 1,
        "cash_reinvest_at_ex_date_close_approx": reinvest_at_close - 1,
        "backward_dividend_factor_return": (
            backward_factor_growth - 1 if backward_factor_growth is not None else None
        ),
        "vendor_adjusted_return": current["Adj Close"] / previous["Adj Close"] - 1,
        "cash_added_to_adjusted_series": False,
    }


def audit_symbol(blob, metadata, request, adjustment, reference_ids):
    issues, warnings = [], []
    output = {
        "status": "held",
        "issues": issues,
        "warnings": warnings,
        "rows": 0,
        "first_date": None,
        "last_date": None,
        "event_windows": [],
        "official_matches": [],
        "adjustment": adjustment,
    }
    try:
        start = date.fromisoformat(request["start"])
        end = date.fromisoformat(request["end_inclusive"])
        if start > end:
            raise AuditError("요청 시작일이 종료일 이후")
        expected, calendar = trading_sessions(start.isoformat(), end.isoformat())
        output["calendar"] = calendar
        output["requested_range"] = [start.isoformat(), end.isoformat()]
        output["expected_sessions"] = len(expected)
        if metadata.get("timezone") != "America/New_York":
            issues.append("상품 시간대 미확인/불일치")
        rows = read_prices(blob, metadata.get("timezone") or "America/New_York")
    except (AuditError, ValueError, KeyError) as exc:
        issues.append(str(exc) if isinstance(exc, AuditError) else "요청 기간/달력 형식 오류")
        return output
    output["rows"] = len(rows)
    dates = [row["date"] for row in rows]
    if dates:
        output.update(first_date=min(dates), last_date=max(dates))
    else:
        issues.append("빈 가격 응답/파일; 사건 없음으로 해석하지 않음")
    if dates != sorted(dates):
        issues.append("날짜 정렬 오류; 자동 정렬하지 않음")
    if len(set(dates)) != len(dates):
        issues.append("중복 거래일; 자동 제거하지 않음")
    expected_set, received = set(expected), set(dates)
    output["missing_sessions"] = sorted(expected_set - received)
    output["unexpected_dates"] = sorted(received - expected_set)
    if output["missing_sessions"]:
        issues.append("거래일 가격 누락 (휴장 제외)")
    if output["unexpected_dates"]:
        issues.append("휴장일 또는 요청 범위 밖의 가격")
    if metadata.get("currency") != "USD" or metadata.get("instrument_type") != "ETF":
        issues.append("USD ETF 상품 식별 미확인/불일치")
    if metadata.get("exchange") not in ("PCX", "NYSE ARCA", "NYSEArca", "NYSE Arca"):
        issues.append("거래소 식별 미확인/불일치")
    identity_verified = metadata.get("identity_reference") in reference_ids
    for field in ("inception_date", "listing_date"):
        try:
            date.fromisoformat(metadata[field])
        except (TypeError, ValueError, KeyError):
            warnings.append(field + " 공식 확인 없음")
            identity_verified = False
    if identity_verified:
        if metadata["inception_date"] > metadata["listing_date"]:
            issues.append("설정일·상장일 순서 불일치")
        if dates and min(dates) < metadata["listing_date"]:
            issues.append("공식 상장일 이전 가격")
    else:
        warnings.append("상품 식별·설정일·상장일 공식 대조 미완료")
    for row in rows:
        if any(row[k] is None or not math.isfinite(row[k]) for k in NUMERIC):
            issues.append("결측/비유한 숫자: " + row["date"])
            continue
        if any(row[k] <= 0 for k in PRICE_FIELDS) or row["Volume"] < 0:
            issues.append("0 이하 가격/음수 거래량: " + row["date"])
        if row["Low"] > min(row["Open"], row["Close"], row["High"]) or row["High"] < max(
            row["Open"], row["Close"]
        ):
            issues.append("OHLC 가격 범위 불일치: " + row["date"])
        if row["Dividends"] < 0 or row["Stock Splits"] < 0:
            issues.append("음수 배당/분할: " + row["date"])
    for column, key in (("Dividends", "dividend_status"), ("Stock Splits", "split_status")):
        status = metadata.get(key, "unknown")
        count = sum(row[column] is not None and row[column] > 0 for row in rows)
        output[key] = {"source_status": status, "positive_event_rows": count}
        if status not in ("reported", "explicit_none"):
            issues.append(key + ": 자료 유무 미확인; 0을 사건 없음으로 추정하지 않음")
        elif status == "explicit_none" and count or status == "reported" and not count:
            issues.append(key + ": 사건 선언과 데이터 불일치")
    if adjustment.get("close") not in ("unadjusted", "split_adjusted"):
        issues.append("Close가 비조정/분할 조정 중 무엇인지 미확정")
    if adjustment.get("adj_close") != "dividend_and_split_adjusted":
        issues.append("Adj Close의 배당·분할 조정 의미 미확정")
    if adjustment.get("dividends") != "same_share_basis_as_close":
        issues.append("배당 주당 기준 미확정/가격과 불일치")
    if (
        metadata.get("capital_gains_status") != "explicit_none_verified"
        or metadata.get("capital_gains_reference") not in reference_ids
    ):
        warnings.append("자본이득 분배 유무 미확인; 배당만으로 총분배금을 확정하지 않음")
    if issues:
        return output
    by_date = {row["date"]: row for row in rows}
    confirmed_kinds = set()
    verified_events = set()
    for official in metadata.get("official_events", []):
        kind, day = official.get("kind"), official.get("date")
        if official.get("reference") not in reference_ids:
            warnings.append("공식 사건 출처 파일·검토 기록 누락")
            continue
        if kind not in ("dividend", "split") or day not in by_date:
            warnings.append("공식 사건 종류/검수 기간 확인 필요")
            continue
        column = "Dividends" if kind == "dividend" else "Stock Splits"
        if kind == "dividend" and official.get("basis") != adjustment["dividends"]:
            warnings.append("공식 배당과 가격 제공자 주당 기준 차이; 금액을 직접 비교하지 않음")
            continue
        try:
            value = float(official["value"])
            if not math.isfinite(value) or value <= 0:
                raise ValueError
            matched = math.isclose(by_date[day][column], value, rel_tol=0, abs_tol=1e-6)
        except (TypeError, ValueError, KeyError):
            issues.append("공식 사건 값 형식 오류")
            continue
        output["official_matches"].append(
            {"kind": kind, "date": day, "matched": matched, "reference": official["reference"]}
        )
        if not matched:
            issues.append("공식 사건과 공급자 불일치: " + day)
        else:
            confirmed_kinds.add(kind)
            verified_events.add((kind, day))
    if "dividend" not in confirmed_kinds:
        warnings.append("공식 배당 표본 대조 미완료")
    for index, row in enumerate(rows):
        if row["Stock Splits"] and ("split", row["date"]) not in verified_events:
            warnings.append("분할 공식 대조 미완료: " + row["date"])
        if not row["Dividends"] and not row["Stock Splits"]:
            if index:
                previous = rows[index - 1]
                if not math.isclose(
                    row["Close"] / previous["Close"],
                    row["Adj Close"] / previous["Adj Close"],
                    rel_tol=0,
                    abs_tol=1e-4,
                ):
                    issues.append("미기록 사건/설명되지 않은 조정률 변화 조사 필요: " + row["date"])
            continue
        if not index:
            warnings.append("사건 전 거래일 가격 없음: " + row["date"])
            continue
        window = {
            "date": row["date"],
            "previous_date": rows[index - 1]["date"],
            **event_returns(rows[index - 1], row, adjustment["close"], adjustment["dividends"]),
        }
        output["event_windows"].append(window)
        if adjustment.get("method") == "backward_dividend_factor":
            expected_return = window["backward_dividend_factor_return"]
        elif adjustment.get("method") == "cash_reinvest_ex_date_close":
            expected_return = window["cash_reinvest_at_ex_date_close_approx"]
        else:
            warnings.append("배당 재투자/역산 조정 산식 미확인")
            continue
        if expected_return is None or not math.isclose(
            expected_return, window["vendor_adjusted_return"], rel_tol=0, abs_tol=1e-4
        ):
            issues.append("사건 전후 조정률 차이 조사 필요: " + row["date"])
    # Prespecified review target from the user/draft, not an invented official event.
    if metadata.get("ticker") == "XLK" and start <= date(2025, 12, 31) and end >= date(2025, 12, 1):
        if not any(kind == "split" and day.startswith("2025-12-") for kind, day in verified_events):
            warnings.append("XLK 2025년 12월 분할 공식 확인 및 대조 미완료")
    output["status"] = "held" if issues else "conditional" if warnings else "usable"
    return output
