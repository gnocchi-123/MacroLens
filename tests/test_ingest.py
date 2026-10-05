import io
import json
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from macrolens import ingest
from macrolens.cli import build_result
from macrolens.config import INDICATORS
from macrolens.ingest import SAMPLE_AS_OF, DataError, fred_snapshot, parse_rows, request_json


def test_fred_request_and_dot_missing(monkeypatch):
    def open_fake(request, timeout):
        params = parse_qs(urlparse(request.full_url).query)
        assert params["api_key"] == ["not-a-real-key"]
        assert params["file_type"] == ["json"]
        assert timeout == 30
        return io.BytesIO(
            json.dumps(
                {
                    "count": 2,
                    "observations": [
                        {"date": "2026-10-01", "value": "4.25"},
                        {"date": "2026-10-02", "value": "."},
                    ],
                }
            ).encode()
        )

    monkeypatch.setattr(ingest, "urlopen", open_fake)
    payload = request_json("series/observations", {"series_id": "DGS10"}, "not-a-real-key")
    rows = parse_rows(payload, SAMPLE_AS_OF, "2026-10-04")
    assert rows[0]["value"] == 4.25
    assert rows[1]["value"] is None
    assert rows[0]["published_at"] is None
    assert rows[0]["available_at"] == SAMPLE_AS_OF.isoformat()


def test_http_error_never_leaks_key(monkeypatch):
    secret = "SUPER_SECRET_TEST_KEY"

    def failing(*args, **kwargs):
        raise HTTPError(f"https://example.test?api_key={secret}", 400, secret, {}, None)

    monkeypatch.setattr(ingest, "urlopen", failing)
    with pytest.raises(DataError) as error:
        request_json("series", {}, secret)
    assert secret not in str(error.value)
    assert "400" in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"count": 3, "observations": []},
        {"count": 1, "observations": [{"date": "2026-10-01", "value": "NaN"}]},
    ],
)
def test_invalid_fred_payload(payload):
    with pytest.raises(DataError):
        parse_rows(payload, SAMPLE_AS_OF, "2026-10-04")


def test_live_pipeline_with_fake_api_and_no_sample_fallback(monkeypatch):
    calls = []

    def api(endpoint, params, key):
        calls.append((endpoint, params))
        sid = params["series_id"]
        assert params["realtime_start"] == params["realtime_end"]
        if sid == "UNRATE":
            raise DataError("수집 실패")
        if endpoint == "series":
            return {"seriess": [{"id": sid, **INDICATORS[sid]}]}
        return {"count": 1, "observations": [{"date": params["observation_end"], "value": "4"}]}

    monkeypatch.setattr(ingest, "request_json", api)
    snapshot = fred_snapshot("test-key")
    assert snapshot["mode"] == "fred"
    assert "UNRATE" not in snapshot["series"]
    assert snapshot["errors"]["UNRATE"] == "메타데이터: 수집 실패"
    assert "test-key" not in json.dumps(snapshot)
    result = build_result(snapshot, "test")
    assert result["status"] == "held"
    assert not result["scores"]
    assert len(calls) == 7


def test_complete_fred_pipeline_with_synthetic_api_responses(monkeypatch):
    sample = ingest.sample_snapshot()

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 5, 12, tzinfo=UTC)

    def api(endpoint, params, key):
        sid = params["series_id"]
        assert params["realtime_start"] == params["realtime_end"] == "2026-10-04"
        if endpoint == "series":
            return {"seriess": [{"id": sid, **sample["series"][sid]["metadata"]}]}
        assert params["units"] == "lin"
        assert params["observation_end"] == "2026-10-04"
        rows = [
            {"date": row["observation_date"], "value": str(row["value"])}
            for row in sample["series"][sid]["observations"]
        ]
        return {"count": len(rows), "observations": rows}

    monkeypatch.setattr(ingest, "datetime", Clock)
    monkeypatch.setattr(ingest, "request_json", api)
    snapshot = fred_snapshot("test-key")
    result = build_result(snapshot, "fake-live-test")
    assert result["mode"] == "fred"
    assert result["status"] == "demo_complete"
    assert len(result["scores"]) == 3
    assert not result["strict_point_in_time"]
    assert all(
        item["observation"]["published_at"] is None for item in result["indicators"].values()
    )


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("timeout", "시간 초과"),
        ("dns", "DNS"),
        ("certificate", "인증서"),
        ("reset", "연결 거부·중단"),
        ("json", "JSON"),
    ],
)
def test_detailed_errors_do_not_expose_secrets(monkeypatch, kind, expected):
    import socket
    import ssl

    secret = "never-print-this-key"

    def fake_open(*args, **kwargs):
        if kind == "json":
            return io.BytesIO(b"<html>never-print-this-key</html>")
        reason = {
            "timeout": TimeoutError(secret),
            "dns": socket.gaierror(secret),
            "certificate": ssl.SSLCertVerificationError(secret),
            "reset": ConnectionResetError(secret),
        }[kind]
        raise URLError(reason)

    from urllib.error import URLError

    monkeypatch.setattr(ingest, "urlopen", fake_open)
    with pytest.raises(DataError) as error:
        request_json("series", {}, secret)
    assert expected in str(error.value)
    assert secret not in str(error.value)


def test_progress_names_failed_stage_without_key(monkeypatch, capsys):
    def fake_request(endpoint, params, key):
        raise DataError("FRED 시간 초과")

    monkeypatch.setattr(ingest, "request_json", fake_request)
    snapshot = fred_snapshot("test-secret")
    output = capsys.readouterr().err
    assert "[1/4] INDPRO 메타데이터 요청 중" in output
    assert "[4/4] DGS10 실패" in output
    assert "test-secret" not in output
    assert "메타데이터: FRED 시간 초과" == snapshot["errors"]["INDPRO"]
