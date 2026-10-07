import pytest

from macrolens.ingest import DataError
from macrolens.scoring import score_sectors


def test_plan_example_contributions_and_scale_invariance():
    factors = {
        "growth": {"status": "valid", "value": 0.6},
        "rates": {"status": "valid", "value": 0.3},
    }
    weights = {"example": {"growth": 2, "rates": -1}}
    item = score_sectors(factors, weights)[0]
    assert item["score"] == 65
    assert item["contributions"] == {"growth": 20, "rates": -5}
    assert score_sectors(factors, {"example": {"growth": 4, "rates": -2}})[0] == item


def test_hold_even_if_invalid_factor_weight_zero():
    factors = {"a": {"status": "valid", "value": 1}, "b": {"status": "held"}}
    assert score_sectors(factors, {"example": {"a": 1, "b": 0}}) == []


def test_bounds_and_tied_ranks():
    factors = {"a": {"status": "valid", "value": 1}}
    result = score_sectors(factors, {"A": {"a": 1}, "B": {"a": 2}, "C": {"a": -1}})
    assert [x["score"] for x in result] == [100, 100, 0]
    assert [x["rank"] for x in result] == [1, 1, 3]


@pytest.mark.parametrize("weights", [{"a": 0}, {"b": 1}, {"a": float("nan")}])
def test_invalid_coefficients(weights):
    with pytest.raises(DataError):
        score_sectors({"a": {"status": "valid", "value": 0}}, {"A": weights})
