"""Research acceptance: economic coefficients are hypotheses, not fitted values."""

from copy import deepcopy
from itertools import product
from pathlib import Path

import pytest

from macrolens import cli, ingest
from macrolens.history import compare_runs
from macrolens.ingest import DataError
from macrolens.reporting import comparison_lines, markdown
from macrolens.research import FROZEN_CONFIG_HASH, research_configuration, validate_research
from macrolens.scoring import score_sectors
from macrolens.storage import digest, list_runs, load_run, new_run

FACTORS = ("growth", "employment", "inflation", "rates")


def factors(values):
    return {k: {"status": "valid", "value": v} for k, v in zip(FACTORS, values, strict=True)}


def sample(root, at="2026-10-05T09:00:00+09:00", **kwargs):
    return cli.run("sample", root, at, model="research-v1", **kwargs)


def test_frozen_matrix_matches_all_44_rationales_in_document():
    config = research_configuration()
    assert digest(config) == FROZEN_CONFIG_HASH
    card = (Path(__file__).resolve().parents[1] / "docs/model_card.md").read_text()
    model = config["model"]
    for sector, weights in model["sectors"].items():
        for key, value in weights.items():
            rid = model["sector_metadata"][sector]["rationale_ids"][key]
            row = next(line for line in card.splitlines() if line.startswith("| " + rid + " "))
            assert int(row.split("|")[2].strip().split(" / ")[0]) == value
    config["model"]["sectors"]["금융"]["rates"] = 1
    with pytest.raises(DataError, match="동결"):
        validate_research(config)
    assert research_configuration()["model"]["sectors"]["금융"]["rates"] == 0


@pytest.mark.parametrize("mutation", ["missing", "zero", "range", "bool", "rationale", "window"])
def test_malformed_research_config_rejected_even_when_inputs_missing(mutation):
    config = research_configuration()
    model = config["model"]
    if mutation == "missing":
        model["sectors"].pop("금융")
    elif mutation == "zero":
        model["sectors"]["금융"] = dict.fromkeys(FACTORS, 0)
    elif mutation == "range":
        model["sectors"]["금융"]["rates"] = 3
    elif mutation == "bool":
        model["sectors"]["금융"]["rates"] = False
    elif mutation == "rationale":
        model["sector_metadata"]["금융"]["rationale_ids"]["rates"] = "wrong"
    else:
        config["indicators"]["INDPRO"]["window"] = 61
    with pytest.raises(DataError, match="동결"):
        cli.build_result({}, "invalid", config=config)


def test_hand_calculation_normalization_and_zero_contribution():
    config = research_configuration()
    scores = score_sectors(factors((0.6, 0.3, 0.2, 0.3)), config["model"]["sectors"])
    by_name = {x["sector"]: x for x in scores}
    assert by_name["산업재"]["score"] == 60
    assert by_name["산업재"]["contributions"] == dict(zip(FACTORS, (12, 3, -2, -3), strict=True))
    assert by_name["금융"]["score"] == 72.5
    assert by_name["금융"]["contributions"]["rates"] == 0
    assert by_name["금융"]["contributions"]["inflation"] == 0


def test_all_extreme_factor_combinations_stay_bounded_and_reconcile():
    model = research_configuration()["model"]
    for values in product((-1, 1), repeat=4):
        for score in score_sectors(factors(values), model["sectors"]):
            assert 0 <= score["score"] <= 100
            assert 50 + sum(score["contributions"].values()) == pytest.approx(score["score"])


def test_average_ties_and_gics_order_without_rounding_scores():
    model = research_configuration()["model"]
    scores = score_sectors(
        factors((0, 0, 0, 0)),
        model["sectors"],
        ranking_policy="average",
        tie_order={k: v["gics"] for k, v in model["sector_metadata"].items()},
    )
    assert [x["rank"] for x in scores] == [6] * 11
    assert scores[0]["sector"] == "에너지" and scores[-1]["sector"] == "부동산"
    weights = {"A": {"x": 1}, "B": {"x": 1}, "C": {"x": -1}}
    rows = score_sectors(
        {"x": {"status": "valid", "value": 0.1}}, weights, ranking_policy="average"
    )
    assert [x["rank"] for x in rows] == [1.5, 1.5, 3]
    rows = score_sectors(
        {"x": {"status": "valid", "value": 1e-9}}, weights, ranking_policy="average"
    )
    assert rows[0]["score"] != rows[-1]["score"]
    assert [x["rank"] for x in rows] == [1.5, 1.5, 3]


@pytest.mark.parametrize("missing", ["INDPRO", "UNRATE", "CPIAUCSL", "DGS10"])
def test_any_missing_active_input_holds_entire_research_model(tmp_path, monkeypatch, missing):
    original = cli.sample_snapshot

    def broken(at):
        snapshot = original(at)
        del snapshot["series"][missing]
        return snapshot

    monkeypatch.setattr(cli, "sample_snapshot", broken)
    result, folder = sample(tmp_path)
    assert result["status"] == "held" and result["scores"] == []
    assert result["model_kind"] == "research"
    assert "모든 점수·순위를 보류" in (folder / "report.md").read_text()


def test_research_comparison_replay_and_reports_are_offline_and_immutable(tmp_path, monkeypatch):
    before, _ = sample(tmp_path, "2026-09-28T09:00:00+09:00")
    after, folder = sample(tmp_path)
    saved = load_run(tmp_path, after["run_id"])
    original = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    assert after["status"] == "research_complete"
    assert len(after["scores"]) == 11
    assert after["strict_point_in_time"] is False
    comparison = compare_runs(tmp_path, before["run_id"], after["run_id"])
    assert comparison["status"] == "comparable"
    assert after["previous_run_id"] == before["run_id"]
    for row in comparison["sectors"].values():
        assert sum(row["contribution_deltas"].values()) == pytest.approx(row["score_delta"])
    for row in after["scores"]:
        assert sum(abs(v) for v in row["normalized_weights"].values()) == pytest.approx(1)
        assert len(row["rationale_ids"]) == 4
    report = (folder / "report.md").read_text()
    assert "# MacroLens M3" in report and "합성 샘플" in report
    assert "가상 섹터" not in report and "전주 비교 없음" not in report
    assert markdown(saved["result"]) == report

    def forbidden(*args, **kwargs):
        pytest.fail("replay must use saved inputs/config, not network or live defaults")

    monkeypatch.setattr(cli, "configuration", forbidden)
    monkeypatch.setattr(cli, "sample_snapshot", forbidden)
    monkeypatch.setattr(ingest, "urlopen", forbidden)
    replay, _ = cli.run(
        "sample", tmp_path, parent_run_id=after["run_id"], kind="replay", replay=saved
    )
    assert replay["replay_verification"] == "matched"
    assert replay["config_hash"] == after["config_hash"]
    assert replay["history"] == after["history"]
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == original
    fractional = deepcopy(comparison)
    next(iter(fractional["sectors"].values()))["rank_improvement"] = 0.5
    assert "+0.5" in "\n".join(comparison_lines(fractional, research=True))


def test_mixed_demo_and_research_do_not_steal_previous_week(tmp_path):
    research, _ = sample(tmp_path, "2026-09-28T09:00:00+09:00")
    demo, _ = cli.run("sample", tmp_path, "2026-09-28T09:00:00+09:00")
    current, _ = sample(tmp_path)
    assert current["previous_run_id"] == research["run_id"]
    comparison = compare_runs(tmp_path, demo["run_id"], current["run_id"])
    assert comparison["status"] == "not_comparable" and not comparison["sectors"]


def test_correction_inherits_frozen_config_and_rejects_model_switch(tmp_path):
    first, _ = sample(tmp_path)
    second, _ = cli.run("sample", tmp_path, parent_run_id=first["run_id"], kind="correction")
    assert second["model_version"] == first["model_version"]
    assert second["config_hash"] == first["config_hash"]
    with pytest.raises(DataError, match="다른 모델"):
        cli.run(
            "sample", tmp_path, parent_run_id=first["run_id"], kind="correction", model="demo-v1"
        )


def test_rerun_interrupted_before_config_write_retains_research_model(tmp_path):
    run_id, _ = new_run(
        tmp_path,
        {
            "kind": "original",
            "mode": "sample",
            "model_selector": "research-v1",
            "config_hash": FROZEN_CONFIG_HASH,
        },
    )
    result, _ = cli.run(
        "sample", tmp_path, "2026-10-05T09:00:00+09:00", parent_run_id=run_id, kind="rerun"
    )
    assert result["status"] == "research_complete" and result["config_hash"] == FROZEN_CONFIG_HASH


def test_cli_research_selection_and_failed_rerun_inherits_model(tmp_path, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert (
        cli.main(["weekly", "--mode", "fred", "--model", "research-v1", "--output", str(tmp_path)])
        == 1
    )
    first = list_runs(tmp_path)[0]
    assert (
        cli.main(
            ["weekly", "--mode", "fred", "--rerun-of", first["run_id"], "--output", str(tmp_path)]
        )
        == 1
    )
    import json

    configs = [json.loads(p.read_text()) for p in tmp_path.glob("*/config.json")]
    assert len(configs) == 2 and configs[0] == configs[1] == research_configuration()
    assert (
        cli.main(
            [
                "weekly",
                "--model",
                "research-v1",
                "--as-of",
                "2026-10-05T09:00:00+09:00",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert any(x["status"] == "research_complete" for x in list_runs(tmp_path))
