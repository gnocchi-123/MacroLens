import copy
from datetime import date, timedelta
from statistics import mean, stdev

import pytest

from macrolens.config import INDICATORS
from macrolens.features import calculate_feature, standardize, transform_values
from macrolens.ingest import SAMPLE_AS_OF, DataError, sample_snapshot


def test_standardization_hand_calculation_and_clip():
    result = standardize(4, [1, 2, 3], 3)
    assert result["mean"] == 2
    assert result["std"] == 1  # sample standard deviation (ddof=1)
    assert result["value"] == pytest.approx(2 / 3)
    assert standardize(100, [1, 2, 3], 3)["value"] == 1
    assert standardize(-100, [1, 2, 3], 3)["value"] == -1


@pytest.mark.parametrize(
    "current,history,window",
    [
        (3, [1, 2], 3),
        (3, [1, 1, 1], 3),
        (float("nan"), [1, 2, 3], 3),
        (3, [1, float("inf"), 2], 3),
    ],
)
def test_bad_standardization_holds(current, history, window):
    with pytest.raises(DataError):
        standardize(current, history, window)


def test_month_lag_uses_calendar_not_row_offset():
    values = {date(2024, 1, 1): 100, date(2025, 1, 1): 110, date(2025, 2, 1): 120}
    assert transform_values(values, "yoy") == {date(2025, 1, 1): pytest.approx(10)}
    values = {date(2024, 1, 1): 4, date(2024, 4, 1): 3.5}
    assert transform_values(values, "negative_3m") == {date(2024, 4, 1): 0.5}


def test_rate_lag_uses_28_calendar_days_and_prior_valid_day():
    values = {date(2025, 1, 3): 4, date(2025, 2, 2): 4.5}
    assert transform_values(values, "difference_28d")[date(2025, 2, 2)] == 0.5
    assert not transform_values({date(2025, 1, 1): 4, date(2025, 3, 1): 5}, "difference_28d")


def test_sample_window_excludes_current():
    series = sample_snapshot()["series"]["UNRATE"]
    values = {date.fromisoformat(x["observation_date"]): x["value"] for x in series["observations"]}
    transformed = transform_values(values, "negative_3m")
    history = list(transformed.values())[-61:-1]
    result = calculate_feature(series, INDICATORS["UNRATE"], SAMPLE_AS_OF)
    assert result["mean"] == pytest.approx(mean(history))
    assert result["std"] == pytest.approx(stdev(history))


def test_future_available_and_future_observation_excluded():
    series = sample_snapshot()["series"]["UNRATE"]
    baseline = calculate_feature(series, INDICATORS["UNRATE"], SAMPLE_AS_OF)
    row = copy.deepcopy(series["observations"][-1])
    row["value"] = 99
    row["available_at"] = (SAMPLE_AS_OF + timedelta(seconds=1)).isoformat()
    series["observations"].append(row)
    assert calculate_feature(series, INDICATORS["UNRATE"], SAMPLE_AS_OF) == baseline
    row["available_at"] = SAMPLE_AS_OF.isoformat()
    row["observation_date"] = "2026-11-01"
    assert calculate_feature(series, INDICATORS["UNRATE"], SAMPLE_AS_OF) == baseline


@pytest.mark.parametrize("problem", ["units", "stale", "missing", "duplicate", "nan"])
def test_bad_series_holds(problem):
    series = sample_snapshot()["series"]["UNRATE"]
    as_of = SAMPLE_AS_OF
    if problem == "units":
        series["metadata"]["units"] = "Index"
    elif problem == "stale":
        as_of += timedelta(days=100)
    elif problem == "missing":
        series["observations"][-1]["value"] = None
    elif problem == "duplicate":
        series["observations"].append(series["observations"][-1].copy())
    else:
        series["observations"][-1]["value"] = float("nan")
    with pytest.raises(DataError):
        calculate_feature(series, INDICATORS["UNRATE"], as_of)
