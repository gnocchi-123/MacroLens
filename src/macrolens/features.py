"""원래 관측 빈도를 유지하는 변환 및 과거 창 표준화."""

import math
from bisect import bisect_right
from datetime import date, datetime, timedelta
from statistics import mean, stdev

from .ingest import DataError, month_shift


def standardize(current, history, window):
    if window < 2 or len(history) < window:
        raise DataError("표준화 이력 부족")
    values = history[-window:]
    if not all(math.isfinite(x) for x in [current, *values]):
        raise DataError("비유한 변환값")
    average, sigma = mean(values), stdev(values)
    if sigma == 0:
        raise DataError("표준편차 0")
    z = (current - average) / sigma
    if not math.isfinite(z):
        raise DataError("비유한 표준화값")
    return {
        "value": max(-3, min(3, z)) / 3,
        "z": z,
        "mean": average,
        "std": sigma,
        "window": window,
    }


def transform_values(values, transform):
    dates = sorted(values)
    transformed = {}
    for day in dates:
        if transform == "difference_28d":
            target = day - timedelta(days=28)
            index = bisect_right(dates, target) - 1
            if index < 0 or (target - dates[index]).days > 7:
                continue
            base = values[dates[index]]
            result = values[day] - base
        else:
            lag = 12 if transform == "yoy" else 3
            previous = month_shift(day, -lag)
            if previous not in values:
                continue
            base = values[previous]
            if transform == "yoy":
                if base <= 0:
                    raise DataError("전년 대비 기준값이 0 이하")
                result = (values[day] / base - 1) * 100
            elif transform == "negative_3m":
                result = -(values[day] - base)
            else:
                raise DataError("알 수 없는 변환")
        transformed[day] = result
    return transformed


def calculate_feature(series, spec, as_of):
    for key in ("units", "frequency", "seasonal_adjustment"):
        if series["metadata"].get(key) != spec[key]:
            raise DataError(f"메타데이터 불일치: {key}")
    eligible = {}
    for row in series["observations"]:
        day = date.fromisoformat(row["observation_date"])
        available = datetime.fromisoformat(row["available_at"])
        if available.tzinfo is None:
            raise DataError("시간대 없는 이용 가능 시각")
        if available > as_of or day > as_of.date():
            continue
        if day in eligible:
            raise DataError("같은 관측일의 중복 버전: M2 시점 선택 필요")
        if spec["frequency"] == "Monthly" and day.day != 1:
            raise DataError("월별 관측일 형식 불일치")
        eligible[day] = row
    if not eligible:
        raise DataError("이용 가능한 관측 없음")
    if spec["frequency"] == "Monthly" and eligible[max(eligible)]["value"] is None:
        raise DataError("최신 월 관측값 누락")
    values = {}
    for day, row in eligible.items():
        value = row["value"]
        if value is None:
            continue
        if not math.isfinite(value):
            raise DataError("비유한 관측값")
        values[day] = value
    if not values:
        raise DataError("유효 관측 없음")
    latest = max(values)
    if (as_of.date() - latest).days > spec["max_age_days"]:
        raise DataError("관측일 기준 잠정 지연 한도 초과")
    transformed = transform_values(values, spec["transform"])
    if latest not in transformed:
        raise DataError("최신 관측의 변환 기준값 누락")
    history = [transformed[day] for day in sorted(transformed) if day < latest]
    result = standardize(transformed[latest], history, spec["window"])
    return {
        **result,
        "status": "valid",
        "raw_value": values[latest],
        "transformed_value": transformed[latest],
        "transform": spec["transform"],
        "source": series["source"],
        "observation": eligible[latest],
        "availability_policy": series["availability_policy"],
    }
