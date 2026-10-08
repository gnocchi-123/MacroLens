"""Historical API semantics validated with mock responses, not real FRED data."""

import io
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from macrolens import cli, ingest
from macrolens.config import INDICATORS
from macrolens.features import calculate_feature
from macrolens.ingest import SAMPLE_AS_OF, fred_snapshot, sample_snapshot
from macrolens.snapshots import select_inputs
from macrolens.storage import load_run, read_json

AS_OF = datetime.fromisoformat("2026-10-05T09:00:00+09:00")


def mock_api(calls, fault=None):
    sample = sample_snapshot(AS_OF)

    def api(endpoint, params, key):
        calls.append((endpoint, deepcopy(params)))
        sid = params["series_id"]
        vintage = params["realtime_start"]
        assert params["realtime_end"] == vintage
        bounds = {"realtime_start": vintage, "realtime_end": vintage}
        if endpoint == "series":
            meta = {"id": sid, **sample["series"][sid]["metadata"], **bounds}
            payload = {**bounds, "seriess": [meta]}
        else:
            rows = [
                {"date": row["observation_date"], "value": str(row["value"]), **bounds}
                for row in sample["series"][sid]["observations"]
            ]
            payload = {**bounds, "count": len(rows), "observations": rows}
        if fault:
            fault(endpoint, sid, payload)
        return payload

    return api


def test_historical_uses_real_time_date_not_latest_date_filter(monkeypatch):
    calls = []
    monkeypatch.setattr(ingest, "request_json", mock_api(calls))
    snapshot = fred_snapshot("test-key", AS_OF)
    assert not snapshot["errors"]
    assert len(calls) == 8
    assert snapshot["vintage_date"] == "2026-10-03"  # Sunday evening in Chicago -> Saturday.
    for endpoint, params in calls:
        assert params["realtime_start"] == params["realtime_end"] == "2026-10-03"
        if endpoint == "series/observations":
            assert params["output_type"] == 1 and params["units"] == "lin"
            assert params["observation_end"] == "2026-10-03"
    row = snapshot["series"]["UNRATE"]["observations"][-1]
    assert row["available_at"] == "2026-10-04T00:00:00-05:00"
    assert datetime.fromisoformat(row["retrieved_at"]) > AS_OF
    assert row["published_at"] is None
    result = cli.build_result(snapshot, "historical")
    assert result["status"] == "demo_complete"
    assert result["point_in_time_status"] == "date_only_conservative"
    assert not result["strict_point_in_time"]


@pytest.mark.parametrize(
    "fault_kind", ["revision", "future_observation", "missing_period", "metadata"]
)
def test_future_revision_observation_and_unverifiable_vintage_hold(monkeypatch, fault_kind):
    def fault(endpoint, sid, payload):
        if sid != "INDPRO":
            return
        if endpoint == "series" and fault_kind == "metadata":
            payload["seriess"][0]["realtime_start"] = "2026-10-06"
        if endpoint != "series/observations":
            return
        if fault_kind == "revision":
            payload["observations"][-1]["realtime_start"] = "2026-10-06"
        elif fault_kind == "future_observation":
            payload["observations"][-1]["date"] = "2026-10-06"
        elif fault_kind == "missing_period":
            del payload["observations"][-1]["realtime_start"]

    monkeypatch.setattr(ingest, "request_json", mock_api([], fault))
    snapshot = fred_snapshot("test-key", AS_OF)
    assert "INDPRO" in snapshot["errors"]
    assert cli.build_result(snapshot, "bad")["scores"] == []
    assert "INDPRO" in snapshot["raw"]  # Response retained even when rejected.


def test_future_vintage_not_used_even_when_available_at_is_old():
    snapshot = sample_snapshot()
    series = snapshot["series"]["UNRATE"]
    baseline = calculate_feature(series, INDICATORS["UNRATE"], SAMPLE_AS_OF)
    revised = deepcopy(series["observations"][-1])
    revised.update(value=9999, vintage_date="2026-10-06", available_at="2026-09-01T00:00:00Z")
    series["observations"].append(revised)
    selected = select_inputs(snapshot, INDICATORS)
    assert selected["selection"]["UNRATE"]["excluded"] == 1
    assert (
        calculate_feature(selected["series"]["UNRATE"], INDICATORS["UNRATE"], SAMPLE_AS_OF)
        == baseline
    )


def test_raw_checkpoint_and_key_redaction_on_partial_failure(tmp_path, monkeypatch, capsys):
    secret = "fictional-secret-not-a-credential"
    monkeypatch.setenv("FRED_API_KEY", secret)

    def fault(endpoint, sid, payload):
        if endpoint == "series":
            payload["seriess"][0]["notes"] = "echo " + secret
            payload["api_key"] = secret
        elif sid == "INDPRO":
            payload["count"] += 1

    monkeypatch.setattr(ingest, "request_json", mock_api([], fault))
    result, folder = cli.run("fred", tmp_path, AS_OF)
    assert result["status"] == "held"
    assert (folder / "raw" / "INDPRO-observations.json").exists()
    assert len(list((folder / "raw").iterdir())) == 8
    for path in folder.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text()
    assert secret not in capsys.readouterr().err
    saved = load_run(tmp_path, result["run_id"])
    assert "INDPRO" not in saved["inputs"]["series"]
    assert "INDPRO" in saved["snapshot"]["raw"]
    raw = read_json(folder / "raw" / "INDPRO-metadata.json")
    assert "api_key" not in raw["params"]
    assert "url" not in raw


def test_request_json_sanitizes_echoed_key(monkeypatch):
    key = "fictional-key"
    response = {"notes": "https://example.test/?api_key=" + key, "api_key": key}
    monkeypatch.setattr(
        ingest, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(response).encode())
    )
    result = ingest.request_json("series", {}, key)
    assert key not in json.dumps(result)
    assert result["api_key"] == "[REDACTED]"


@pytest.mark.parametrize(
    "at,vintage,offset",
    [
        ("2026-03-09T09:00:00+09:00", "2026-03-07", "-06:00"),
        ("2026-03-10T09:00:00+09:00", "2026-03-08", "-05:00"),
    ],
)
def test_chicago_dst_conservative_cutoff(monkeypatch, at, vintage, offset):
    calls = []
    monkeypatch.setattr(ingest, "request_json", mock_api(calls))
    snapshot = fred_snapshot("test-key", datetime.fromisoformat(at))
    # Mock October observations are rejected, but the request date still demonstrates DST handling.
    assert snapshot["vintage_date"] == vintage
    assert all(params["realtime_start"] == vintage for _, params in calls)
    gate = datetime.combine(
        datetime.fromisoformat(vintage).date() + timedelta(days=1),
        datetime.min.time(),
        ingest.ZoneInfo("America/Chicago"),
    )
    assert gate.isoformat().endswith(offset)


def test_future_asof_never_calls_api(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("future cutoff must be rejected before the network")

    monkeypatch.setattr(ingest, "request_json", forbidden)
    with pytest.raises(ingest.DataError):
        fred_snapshot("test-key", datetime.now(UTC) + timedelta(days=1))


def test_historical_fred_replay_does_not_request_new_vintage(tmp_path, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fictional-key")
    calls = []
    monkeypatch.setattr(ingest, "request_json", mock_api(calls))
    result, _ = cli.run("fred", tmp_path, AS_OF)
    assert result["status"] == "demo_complete"
    assert len(calls) == 8

    def forbidden(*args, **kwargs):
        pytest.fail("saved FRED input replay must not request a new vintage")

    monkeypatch.setattr(ingest, "request_json", forbidden)
    monkeypatch.setattr(ingest, "urlopen", forbidden)
    monkeypatch.delenv("FRED_API_KEY")
    saved = load_run(tmp_path, result["run_id"])
    replayed, _ = cli.run(
        "fred", tmp_path, parent_run_id=result["run_id"], kind="replay", replay=saved
    )
    assert replayed["replay_verification"] == "matched"
    assert replayed["input_hash"] == result["input_hash"]
    assert replayed["point_in_time_status"] == "date_only_conservative"
