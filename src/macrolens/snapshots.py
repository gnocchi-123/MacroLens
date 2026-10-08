"""Explicit time policy and the actual input selected for a calculation."""

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .ingest import DataError

ENGINE_VERSION = "m2-v1"


def instant(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise DataError("시간대가 있는 ISO 8601 시각을 지정하세요.") from None


def week_slot(as_of):
    day = instant(as_of).astimezone(ZoneInfo("Asia/Seoul")).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def time_policy(snapshot):
    if snapshot["mode"] == "sample":
        return "synthetic-v1"
    return snapshot.get("time_policy", "collected-at-v1")


def select_inputs(snapshot, indicators):
    # Lazy import avoids the ingest/features dependency cycle.
    from .features import eligible_rows

    selected = {k: deepcopy(v) for k, v in snapshot.items() if k not in ("series", "raw")}
    selected["series"] = {}
    selected["selection"] = {}
    as_of = instant(snapshot["as_of"])
    for sid in indicators:
        if sid not in snapshot["series"] or sid in selected["errors"]:
            continue
        source = snapshot["series"][sid]
        try:
            rows = eligible_rows(source, as_of)
        except (DataError, KeyError, TypeError, ValueError, OverflowError):
            selected["errors"][sid] = "입력 시점·중복·형식 검증 실패"
            continue
        selected["series"][sid] = {
            k: deepcopy(v) for k, v in source.items() if k not in ("observations", "raw_response")
        }
        selected["series"][sid]["observations"] = deepcopy(rows)
        selected["selection"][sid] = {
            "received": len(source["observations"]),
            "selected": len(rows),
            "excluded": len(source["observations"]) - len(rows),
        }
    return selected
