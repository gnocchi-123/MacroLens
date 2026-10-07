"""M2 history tests use synthetic data only; never a real FRED key."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from macrolens import cli, ingest
from macrolens.history import compare_loaded, compare_runs
from macrolens.ingest import DataError
from macrolens.snapshots import week_slot
from macrolens.storage import digest, list_runs, load_run, read_json, write_json


def sample(root, at="2026-10-05T09:00:00+09:00", **kwargs):
    return cli.run("sample", root, at, **kwargs)


def bytes_in(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_run_records_config_input_raw_and_integrity(tmp_path):
    result, folder = sample(tmp_path)
    saved = load_run(tmp_path, result["run_id"])
    assert result["schema_version"] == 2
    assert result["input_hash"] == digest(saved["inputs"])
    assert result["config_hash"] == digest(saved["config"])
    assert result["snapshot_hash"] == digest(saved["snapshot"])
    assert saved["record"]["kind"] == "original"
    assert read_json(folder / "status.json")["status"] == "demo_complete"
    (folder / "inputs.json").write_text("{}")
    with pytest.raises(DataError, match="무결성"):
        load_run(tmp_path, result["run_id"])
    assert list_runs(tmp_path)[0]["status"] == "unreadable"


def test_replay_and_regeneration_offline_with_saved_config(tmp_path, monkeypatch):
    result, folder = sample(tmp_path)
    original = bytes_in(folder)

    def forbidden(*args, **kwargs):
        raise AssertionError("network/collection must not be used")

    monkeypatch.setattr(ingest, "urlopen", forbidden)
    monkeypatch.setattr(cli, "fred_snapshot", forbidden)
    monkeypatch.setattr(cli, "sample_snapshot", forbidden)
    monkeypatch.setattr(cli, "INDICATORS", {})
    monkeypatch.setattr(cli, "DEMO_MODEL", {})
    assert cli.main(["replay", result["run_id"], "--root", str(tmp_path)]) == 0
    replay = next(x for x in list_runs(tmp_path) if x["run_id"] != result["run_id"])
    saved = load_run(tmp_path, replay["run_id"])
    assert saved["result"]["replay_verification"] == "matched"
    assert saved["result"]["scores"] == result["scores"]
    assert saved["result"]["input_hash"] == result["input_hash"]
    assert saved["record"]["kind"] == "replay"
    assert saved["record"]["parent_run_id"] == result["run_id"]
    output = tmp_path / "regenerated.md"
    assert (
        cli.main(["report", result["run_id"], "--root", str(tmp_path), "--output", str(output)])
        == 0
    )
    assert output.read_bytes() == original["report.md"]
    assert (
        cli.main(
            [
                "report",
                result["run_id"],
                "--root",
                str(tmp_path),
                "--output",
                str(folder / "report.md"),
            ]
        )
        == 1
    )
    assert bytes_in(folder) == original


def test_two_run_deltas_are_nonzero_and_sum_to_score(tmp_path):
    left, _ = sample(tmp_path, "2026-09-28T09:00:00+09:00")
    right, _ = sample(tmp_path)
    compared = compare_runs(tmp_path, left["run_id"], right["run_id"])
    assert compared["status"] == "comparable"
    assert compared["interval_days"] == 7
    assert compared["indicators"]["DGS10"]["raw_value_delta"] != 0
    assert any(x["score_delta"] != 0 for x in compared["sectors"].values())
    for sector in compared["sectors"].values():
        assert sum(sector["contribution_deltas"].values()) == pytest.approx(sector["score_delta"])
    assert right["previous_run_id"] == left["run_id"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_version", "changed"),
        ("mode", "fred"),
        ("time_policy", "other"),
        ("engine_version", "other"),
        ("config_hash", "different"),
        ("status", "held"),
    ],
)
def test_incomparable_has_reasons_and_no_numeric_delta(tmp_path, field, value):
    result, _ = sample(tmp_path)
    before = load_run(tmp_path, result["run_id"])
    after = deepcopy(before)
    after["result"][field] = value
    compared = compare_loaded(before, after)
    assert compared["status"] == "not_comparable"
    assert compared["reasons"]
    assert compared["sectors"] == compared["indicators"] == {}


def test_reject_inconsistent_contributions(tmp_path):
    result, _ = sample(tmp_path)
    before = load_run(tmp_path, result["run_id"])
    after = deepcopy(before)
    after["result"]["scores"][0]["score"] += 1
    compared = compare_loaded(before, after)
    assert compared["status"] == "not_comparable"
    assert "합 불일치" in compared["reasons"][0]


def test_missing_week_separates_last_available_and_actual_gap(tmp_path):
    earlier, _ = sample(tmp_path, "2026-09-21T09:00:00+09:00")
    current, folder = sample(tmp_path)
    assert current["previous_run_id"] is None
    assert current["history"]["previous_week"] is None
    last = current["history"]["last_available"]
    assert last["before_run_id"] == earlier["run_id"]
    assert last["interval_days"] == 14
    report = (folder / "report.md").read_text()
    assert "전주 비교 없음" in report and "14.000000일" in report
    assert "2026-09-21" in report and "마지막 가용 기록" in report


def test_same_week_rerun_correction_and_replay_are_one_slot(tmp_path):
    first, folder = sample(tmp_path, "2026-09-28T09:00:00+09:00")
    original = bytes_in(folder)
    second, _ = sample(
        tmp_path, "2026-09-29T09:00:00+09:00", parent_run_id=first["run_id"], kind="rerun"
    )
    corrected, _ = sample(
        tmp_path, "2026-09-28T09:00:00+09:00", parent_run_id=first["run_id"], kind="correction"
    )
    assert second["previous_run_id"] is None
    assert cli.main(["replay", first["run_id"], "--root", str(tmp_path)]) == 0
    current, _ = sample(tmp_path)
    assert current["previous_run_id"] == corrected["run_id"]
    assert bytes_in(folder) == original
    with pytest.raises(DataError, match="같은 한국시간 주간"):
        sample(tmp_path, parent_run_id=first["run_id"], kind="correction")


def test_latest_held_representative_not_replaced_by_older_success(tmp_path, monkeypatch):
    sample(tmp_path, "2026-09-28T09:00:00+09:00")
    original = cli.sample_snapshot

    def broken(at):
        snapshot = original(at)
        del snapshot["series"]["INDPRO"]
        return snapshot

    monkeypatch.setattr(cli, "sample_snapshot", broken)
    held, _ = sample(tmp_path, "2026-09-28T09:00:00+09:00")
    monkeypatch.setattr(cli, "sample_snapshot", original)
    result, folder = sample(tmp_path)
    assert result["previous_run_id"] == held["run_id"]
    assert result["history"]["previous_week"]["status"] == "not_comparable"
    assert "비교 불가" in (folder / "report.md").read_text()


@pytest.mark.parametrize(
    "at,expected",
    [
        ("2026-10-04T14:59:59+00:00", "2026-09-28"),
        ("2026-10-04T15:00:00+00:00", "2026-10-05"),
        ("2026-10-05T00:00:00+09:00", "2026-10-05"),
        ("2025-12-31T12:00:00+09:00", "2025-12-29"),
    ],
)
def test_korean_week_boundaries(at, expected):
    assert week_slot(at) == expected


def test_naive_and_future_judgment_times_rejected(tmp_path):
    assert cli.main(["weekly", "--as-of", "2026-10-05", "--output", str(tmp_path)]) == 1
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert cli.main(["weekly", "--as-of", future, "--output", str(tmp_path)]) == 1
    assert not list(tmp_path.iterdir())


def test_failed_attempt_is_retained_and_rerunnable(tmp_path, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert cli.main(["weekly", "--mode", "fred", "--output", str(tmp_path)]) == 1
    record = list_runs(tmp_path)[0]
    folder = tmp_path / record["run_id"]
    original = bytes_in(folder)
    assert record["status"] == "failed"
    assert (
        cli.main(
            ["weekly", "--mode", "fred", "--output", str(tmp_path), "--rerun-of", record["run_id"]]
        )
        == 1
    )
    assert len(list_runs(tmp_path)) == 2
    assert bytes_in(folder) == original
    assert any(x["parent_run_id"] == record["run_id"] for x in list_runs(tmp_path))
    assert compare_runs(tmp_path, record["run_id"], record["run_id"])["status"] == "not_comparable"


def test_interruption_and_write_failure_are_not_complete(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("sensitive request URL must not be printed")

    monkeypatch.setattr(cli, "sample_snapshot", broken)
    assert cli.main(["weekly", "--output", str(tmp_path)]) == 1
    record = list_runs(tmp_path)[0]
    assert record["status"] == "failed"
    assert "sensitive" not in record["error"]
    folder = tmp_path / "interrupted"
    folder.mkdir()
    write_json(folder / "run.json", {"kind": "original"})
    assert any(x["status"] == "incomplete" for x in list_runs(tmp_path))


def test_m1_is_read_only_without_invented_configuration(tmp_path):
    snapshot = ingest.sample_snapshot()
    legacy = cli.build_result(snapshot, "legacy")
    legacy["schema_version"] = 1
    for key in ("engine_version", "input_hash", "week_slot", "time_policy", "point_in_time_status"):
        legacy.pop(key)
    folder = tmp_path / "legacy"
    folder.mkdir()
    write_json(folder / "snapshot.json", snapshot)
    write_json(folder / "result.json", legacy)
    (folder / "COMPLETE").write_text("demo_complete\n")
    original = bytes_in(folder)
    assert load_run(tmp_path, "legacy")["config"] is None
    assert cli.main(["report", "legacy", "--root", str(tmp_path)]) == 0
    assert cli.main(["replay", "legacy", "--root", str(tmp_path)]) == 1
    current, _ = sample(tmp_path)
    comparison = compare_runs(tmp_path, "legacy", current["run_id"])
    assert comparison["status"] == "not_comparable"
    assert any("M1" in x for x in comparison["reasons"])
    assert bytes_in(folder) == original


def test_path_traversal_rejected(tmp_path):
    with pytest.raises(DataError):
        load_run(tmp_path, "../outside")


def test_regenerate_when_only_original_report_is_missing(tmp_path):
    result, folder = sample(tmp_path)
    text = (folder / "report.md").read_bytes()
    (folder / "report.md").unlink()
    target = tmp_path / "restored.md"
    assert (
        cli.main(["report", result["run_id"], "--root", str(tmp_path), "--output", str(target)])
        == 0
    )
    assert target.read_bytes() == text
    assert not (folder / "report.md").exists()


def test_failed_final_write_is_incomplete_even_if_status_was_written(tmp_path, monkeypatch):
    from macrolens import storage

    original = storage.write_json

    def disk_error(path, value):
        if path.name == "manifest.json":
            raise OSError("disk full")
        original(path, value)

    monkeypatch.setattr(storage, "write_json", disk_error)
    with pytest.raises(DataError):
        sample(tmp_path)
    record = list_runs(tmp_path)[0]
    assert record["status"] == "incomplete"
    assert not (tmp_path / record["run_id"] / "COMPLETE").exists()


def test_later_correction_does_not_change_frozen_links_or_replay(tmp_path):
    before, _ = sample(tmp_path, "2026-09-28T09:00:00+09:00")
    current, folder = sample(tmp_path)
    original = bytes_in(folder)
    sample(tmp_path, "2026-09-28T10:00:00+09:00", kind="correction", parent_run_id=before["run_id"])
    saved = load_run(tmp_path, current["run_id"])
    replayed, _ = cli.run(
        "sample", tmp_path, parent_run_id=current["run_id"], kind="replay", replay=saved
    )
    assert replayed["history"] == current["history"]
    assert replayed["previous_run_id"] == before["run_id"]
    assert bytes_in(folder) == original
