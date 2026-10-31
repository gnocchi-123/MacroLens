"""Synthetic diagnostic responses; no actual KIS service requests."""

import io
import json
import socket
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from macrolens import kis, kis_client, kis_diagnostic

KEY = "SYNTHETIC_DIAGNOSTIC_APP_KEY"
SECRET = "SYNTHETIC_DIAGNOSTIC_APP_SECRET"
TOKEN = "SYNTHETIC_DIAGNOSTIC_TOKEN"
MESSAGE = "합성 오류 설명입니다. TR ID를 확인하세요."


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("No network allowed")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setenv("KIS_APP_KEY", KEY)
    monkeypatch.setenv("KIS_APP_SECRET", SECRET)


@pytest.mark.parametrize("status", [500, 429, 200])
def test_diagnostic_get_once_no_probe_or_eligibility(tmp_path, monkeypatch, capsys, status):
    calls = []

    def send(method, url, headers, body, timeout):
        calls.append((method, url))
        if method == "POST":
            return 200, {}, json.dumps({"access_token": TOKEN, "expires_in": 86400}).encode()
        return (
            status,
            {},
            json.dumps({"rt_cd": "1", "msg_cd": "EGW02004", "msg1": MESSAGE}).encode(),
        )

    client = kis_client.KISClient("real", tmp_path / "cache", send=send, sleep=lambda _: None)
    monkeypatch.setattr(kis_diagnostic, "KISClient", lambda env: client)
    output = tmp_path / "diagnostic"
    assert kis.main(["diagnose", "--env", "real", "--output", str(output)]) == 2
    assert len([call for call in calls if call[0] == "GET"]) == 1
    assert all("price-detail" in url for method, url in calls if method == "GET")
    saved = next(output.glob("*/diagnostic.json"))
    record = json.loads(saved.read_text())
    assert record["provider_message"] == MESSAGE
    assert record["max_get_attempts"] == 1
    assert not record["can_collect"]
    assert not (saved.parent / "response.json").exists()
    text = capsys.readouterr().out
    assert MESSAGE in text
    for path in saved.parent.iterdir():
        assert all(s not in path.read_text() for s in (KEY, SECRET, TOKEN))


@pytest.mark.parametrize(
    "message",
    [
        "키 " + KEY,
        "토큰 " + TOKEN,
        "시크릿 " + SECRET,
        "설명\x1b[31m오류",
        "설명\u202e오류",
    ],
)
def test_secret_echo_or_control_character_message_is_omitted(message):
    raw = json.dumps({"msg_cd": "EGW02004", "msg1": message}).encode()
    assert kis_client.diagnostic_message(raw, (KEY, SECRET, TOKEN)) is None


def test_diagnostic_masks_urls_and_free_identifiers():
    raw = json.dumps(
        {
            "msg1": "사용자 ABCDEFGHIJKLMNOP 계좌 12345678 "
            "https://example.test/private?q=unknown 토큰 OTHER_PRIVATE_TOKEN "
            "TR CTPF1702R 오류"
        }
    ).encode()
    message = kis_client.diagnostic_message(raw)
    for value in ("ABCDEFGHIJKLMNOP", "12345678", "https", "private", "OTHER_PRIVATE_TOKEN"):
        assert value not in message
    assert "TR CTPF1702R 오류" in message


def test_transport_diagnostic_is_opt_in_and_default_keeps_no_message(monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raw = json.dumps({"msg_cd": "EGW02004", "msg1": MESSAGE}).encode()
            raise HTTPError(
                "https://example.test",
                500,
                "PRIVATE_REASON",
                {"authorization": TOKEN},
                io.BytesIO(raw),
            )

    monkeypatch.setattr(kis_client, "build_opener", lambda *args: Opener())
    for enabled in (False, True):
        status, headers, raw = kis_client.transport(
            "GET",
            "https://example.test",
            {"appkey": KEY, "appsecret": SECRET, "authorization": "Bearer " + TOKEN},
            None,
            15,
            diagnostics=enabled,
        )
        assert status == 500 and headers == {}
        result = json.loads(raw)
        assert result.get("msg1") == (MESSAGE if enabled else None)
        assert "PRIVATE_REASON" not in raw.decode()


def test_successful_diagnostic_saves_raw_but_does_not_unlock_collect(tmp_path, monkeypatch, capsys):
    raw = b'{"rt_cd":"0","output":{"curr":"USD","rsym":"DAMSSPY"}}'

    def send(method, *args):
        if method == "POST":
            return 200, {}, json.dumps({"access_token": TOKEN, "expires_in": 86400}).encode()
        return 200, {}, raw

    client = kis_client.KISClient("real", tmp_path / "cache", send=send, sleep=lambda _: None)
    monkeypatch.setattr(kis_diagnostic, "KISClient", lambda env: client)
    output = tmp_path / "diagnostic"
    assert kis.main(["diagnose", "--env", "real", "--output", str(output)]) == 0
    saved = next(output.glob("*/diagnostic.json"))
    assert (saved.parent / "response.json").read_bytes() == raw
    record = json.loads(saved.read_text())
    assert not record["can_collect"]
    assert "can_collect: false" in capsys.readouterr().out


def test_auth_error_text_never_exposed_even_in_diagnostic_mode(monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError(
                "https://example.test",
                500,
                "private",
                {},
                io.BytesIO(json.dumps({"msg1": MESSAGE}).encode()),
            )

    monkeypatch.setattr(kis_client, "build_opener", lambda *args: Opener())
    _, _, raw = kis_client.transport(
        "POST", "https://example.test", {}, b"{}", 15, diagnostics=True
    )
    assert raw == b"{}"


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("valid", "row_checks_passed"),
        ("invalid", "invalid_price_row"),
        ("missing", "target_missing"),
        ("duplicate", "duplicate_target_rows"),
        ("wrong_symbol", "symbol_unverified"),
        ("bad_shape", "invalid_response_shape"),
    ],
)
def test_target_date_diagnostic_one_get_preserves_raw_without_repair(
    tmp_path, monkeypatch, capsys, kind, expected
):
    calls = []
    # Deliberately synthetic prices; not the user's observed prices.
    row = {
        "xymd": "20230605",
        "open": "100",
        "high": "102",
        "low": "99",
        "clos": "101",
        "tvol": "10",
    }
    if kind == "invalid":
        row["low"] = "100.5"
    payload = {"rt_cd": "0", "output1": {"rsym": "DAMSXLC"}, "output2": [row]}
    if kind == "missing":
        payload["output2"] = []
    elif kind == "duplicate":
        payload["output2"] = [row, row]
    elif kind == "wrong_symbol":
        payload["output1"]["rsym"] = "DAMSSPY"
    elif kind == "bad_shape":
        payload["output2"] = {}
    raw = json.dumps(payload).encode()

    def send(method, url, headers, body, timeout):
        if method == "POST":
            return 200, {}, json.dumps({"access_token": TOKEN, "expires_in": 86400}).encode()
        calls.append(url)
        return 200, {"tr_cont": "M"}, raw

    client = kis_client.KISClient("real", tmp_path / "cache", send=send, sleep=lambda _: None)
    monkeypatch.setattr(kis_diagnostic, "KISClient", lambda env: client)
    output = tmp_path / "diagnostics"
    for modp in ("0", "1"):
        assert (
            kis.main(
                [
                    "diagnose",
                    "--env",
                    "real",
                    "--endpoint",
                    "dailyprice",
                    "--symbol",
                    "XLC",
                    "--date",
                    "2023-06-05",
                    "--modp",
                    modp,
                    "--output",
                    str(output),
                ]
            )
            == 0
        )  # Request success remains separate from row validation.
        query = parse_qs(urlsplit(calls[-1]).query)
        assert query["BYMD"] == ["20230606"] and query["MODP"] == [modp]
    assert len(calls) == 2
    saved = list(output.glob("*/diagnostic.json"))
    assert len(saved) == 2
    for path in saved:
        record = json.loads(path.read_text())
        assert record["price_date_check"]["status"] == expected
        assert record["price_date_check"]["date"] == "2023-06-05"
        assert not record["can_collect"]
        assert (path.parent / "response.json").read_bytes() == raw
        assert record["response_sha256"] == kis_diagnostic.sha(raw)
        if kind == "invalid":
            assert record["price_date_check"]["rows"][0]["reasons"] == ["low_above_open_or_close"]
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize(
    "options",
    [
        ["--date", "2023-06-05"],
        ["--modp", "1"],
        ["--endpoint", "dailyprice", "--date", "2023-02-30"],
    ],
)
def test_invalid_price_diagnostic_arguments_never_authenticate(monkeypatch, options):
    def fail(env):
        raise AssertionError("Must validate options before reading keys or authenticating")

    monkeypatch.setattr(kis_diagnostic, "KISClient", fail)
    assert kis.main(["diagnose", "--env", "real", *options]) == 1
