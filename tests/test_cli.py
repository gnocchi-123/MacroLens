import json

import pytest

from macrolens import cli
from macrolens.cli import build_result, main
from macrolens.ingest import sample_snapshot


def test_sample_end_to_end_and_no_overwrite(tmp_path):
    assert main(["weekly", "--output", str(tmp_path)]) == 0
    assert main(["weekly", "--output", str(tmp_path)]) == 0
    folders = list(tmp_path.iterdir())
    assert len(folders) == 2
    results = []
    for folder in folders:
        result = json.loads((folder / "result.json").read_text())
        results.append(result)
        report = (folder / "report.md").read_text()
        assert "합성 샘플" in report and "미검증 연구용" in report and "가상 계수" in report
        assert (folder / "COMPLETE").is_file()
        assert len(result["indicators"]) == 4
        for item in result["scores"]:
            assert item["score"] == pytest.approx(50 + sum(item["contributions"].values()))
    assert results[0]["snapshot_hash"] == results[1]["snapshot_hash"]
    assert results[0]["scores"] == results[1]["scores"]


def test_no_key_preserves_failed_attempt(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert main(["weekly", "--mode", "fred", "--output", str(tmp_path)]) == 1
    assert "FRED_API_KEY" in capsys.readouterr().err
    folder = next(tmp_path.iterdir())
    status = json.loads((folder / "status.json").read_text())
    assert status["status"] == "failed"
    assert not (folder / "COMPLETE").exists()
    assert not (folder / "result.json").exists()


def test_held_outputs_report_and_exit_two(tmp_path, monkeypatch):
    snapshot = sample_snapshot()
    del snapshot["series"]["INDPRO"]
    monkeypatch.setattr(cli, "sample_snapshot", lambda: snapshot)
    assert main(["weekly", "--output", str(tmp_path)]) == 2
    folder = next(tmp_path.iterdir())
    assert "입력 불완전" in (folder / "report.md").read_text()
    result = json.loads((folder / "result.json").read_text())
    assert result["scores"] == []
    assert result["indicators"]["INDPRO"]["status"] == "held"


def test_insufficient_history_blocks_all_scores():
    snapshot = sample_snapshot()
    snapshot["series"]["DGS10"]["observations"] = snapshot["series"]["DGS10"]["observations"][-50:]
    result = build_result(snapshot, "test")
    assert result["status"] == "held"
    assert result["indicators"]["DGS10"]["reason"] == "표준화 이력 부족"
