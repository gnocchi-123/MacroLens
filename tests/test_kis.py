"""Synthetic KIS responses only. No provider authentication or downloaded prices."""

import json
import socket

import pytest

from macrolens import kis, kis_audit, kis_collect
from macrolens.kis_client import (
    KISClient,
    KISError,
    NoRedirect,
    private_dir,
    private_file,
    public_payload,
)
from macrolens.price_quality import trading_sessions

pytest.importorskip("exchange_calendars")
KEY, SECRET, TOKEN = (
    "TEST_ONLY_KEY_NOT_REAL",
    "TEST_ONLY_SECRET_NOT_REAL",
    "TEST_ONLY_TOKEN_NOT_REAL",
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Network forbidden in synthetic KIS tests")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)


def credentials(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", KEY)
    monkeypatch.setenv("KIS_APP_SECRET", SECRET)


def params():
    return {"AUTH": "", "EXCD": "AMS", "SYMB": "XLK", "GUBN": "0", "BYMD": "20260930", "MODP": "0"}


def test_missing_keys_only_block_explicit_kis(tmp_path, capsys):
    assert kis.main(["auth", "--env", "real"]) == 1
    assert "missing_KIS_APP_KEY_or_KIS_APP_SECRET" in capsys.readouterr().err
    # Import and all offline helpers need no KIS variables.
    assert kis_collect.CONTRACT == "kis-quotes-v1"


def test_token_reuse_environment_separation_and_permissions(tmp_path, monkeypatch):
    credentials(monkeypatch)
    calls = []

    def send(method, url, headers, body, timeout):
        calls.append(url)
        assert method == "POST" and timeout == 15
        return 200, {}, json.dumps({"access_token": TOKEN, "expires_in": 86400}).encode()

    for env in ("real", "real", "demo"):
        client = KISClient(env, tmp_path, send=send)
        assert client.token() == TOKEN
        assert (client.cache / "token.json").stat().st_mode & 0o777 == 0o600
        assert client.cache.stat().st_mode & 0o777 == 0o700
        cache = (client.cache / "token.json").read_text()
        assert KEY not in cache and SECRET not in cache
    assert len(calls) == 2
    assert ":9443/" in calls[0] and ":29443/" in calls[1]


def test_failed_auth_is_not_retried_or_echoed(tmp_path, monkeypatch):
    credentials(monkeypatch)
    calls = []

    def send(*args):
        calls.append(1)
        return 500, {"authorization": TOKEN}, SECRET.encode()

    client = KISClient("real", tmp_path, send=send)
    with pytest.raises(KISError, match="^authentication_failed$"):
        client.token()
    with pytest.raises(KISError, match="cooldown"):
        client.token()
    assert len(calls) == 1
    assert SECRET not in (client.cache / "token.json").read_text()


def test_cache_lock_symlink_and_redirect_refused(tmp_path):
    root = private_dir(tmp_path / "auth")
    with private_file(root / "lock"):
        with pytest.raises(KISError, match="another_kis"):
            with private_file(root / "lock"):
                pass
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(KISError, match="symlink"):
        private_dir(link)
    with pytest.raises(KISError, match="redirect"):
        NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.test")


@pytest.mark.parametrize("status, expected_calls", [(429, 3), (503, 3), (401, 1), (403, 1)])
def test_get_retries_bounded_and_auth_failures_stop(tmp_path, monkeypatch, status, expected_calls):
    credentials(monkeypatch)
    calls, sleeps = [], []

    def send(method, *args):
        if method == "POST":
            return 200, {}, json.dumps({"access_token": TOKEN, "expires_in": 86400}).encode()
        calls.append(1)
        return status, {}, SECRET.encode()

    client = KISClient("real", tmp_path, send=send, sleep=sleeps.append)
    with pytest.raises(KISError):
        client.get("dailyprice", params())
    assert len(calls) == expected_calls
    assert sum(sleeps) <= 9


@pytest.mark.parametrize(
    "payload",
    [
        {"rt_cd": "0", "msg1": SECRET},
        {"rt_cd": "0", "authorization": "other-secret"},
        {"rt_cd": "0", "output2": [{"access_token": TOKEN}]},
    ],
)
def test_secret_echo_rejected_before_market_storage(payload):
    with pytest.raises(KISError, match="credential"):
        public_payload(json.dumps(payload).encode(), (KEY, SECRET, TOKEN))


def test_read_only_contract_rejects_orders_and_auth_parameter(tmp_path, monkeypatch):
    credentials(monkeypatch)
    client = KISClient("real", tmp_path)
    with pytest.raises(KISError, match="quotation_contract"):
        client.get("order", {})
    p = params()
    p["AUTH"] = "not-empty"
    with pytest.raises(KISError, match="AUTH_must_be_empty"):
        client.get("dailyprice", p)


class FakeMarket:
    environment = "real"
    base = "https://openapi.koreainvestment.com:9443"

    def __init__(self, repeat=False, conflict=False, empty_rights=False, missing_end=False):
        self.repeat, self.conflict = repeat, conflict
        self.empty_rights, self.missing_end = empty_rights, missing_end
        self.calls = []
        self.days = trading_sessions("2020-01-01", "2026-09-30")[0]

    def get(self, endpoint, p, continuation=""):
        self.calls.append((endpoint, dict(p), continuation))
        data = {"rt_cd": "0"}
        if endpoint == "search-info":
            data["output"] = {
                "tr_crcy_cd": "USD",
                "ovrs_excg_cd": "AMEX",
                "prdt_eng_name": "SYNTHETIC " + p["PDNO"],
            }
        elif endpoint == "dailyprice":
            end = kis_collect.day(p["BYMD"])
            if self.repeat or self.conflict:
                end = "2026-09-30"
            days = [d for d in self.days if d <= end][-100:][::-1]
            if self.missing_end:
                days = [d for d in days if d != "2026-09-30"]
            data["output1"] = {"rsym": "DAMS" + p["SYMB"]}
            data["output2"] = []
            for d in days:
                price = 200 if d < "2025-12-05" and p["MODP"] == "0" else 100
                data["output2"].append(
                    {
                        "xymd": d.replace("-", ""),
                        "open": str(price),
                        "high": str(price),
                        "low": str(price),
                        "clos": str(price),
                        "tvol": "1000",
                    }
                )
            if self.conflict and len(self.calls) > 1:
                data["output2"][0]["clos"] = "99"
        elif endpoint == "period-rights":
            data.update(ctx_area_nk50="", ctx_area_fk50="")
            data["output"] = (
                []
                if self.empty_rights
                else [
                    {
                        "pdno": p["PDNO"],
                        "prdt_type_cd": "529",
                        "rght_type_cd": "14",
                        "acpl_bass_dt": "20251202",
                        "stck_alct_rt": "100",
                        "dfnt_yn": "Y",
                    }
                ]
            )
        else:
            data["output1"] = (
                []
                if self.empty_rights
                else [
                    {
                        "anno_dt": "20251101",
                        "ca_title": "SYNTHETIC split",
                        "effective_dt": "20251205",
                    }
                ]
            )
        cont = "M" if self.repeat or self.conflict else ""
        return data, json.dumps(data).encode(), cont


def archive(tmp_path, mode="collect"):
    a = kis_collect.Archive(
        tmp_path, "real", mode, "AMS", "529", kis_collect.DEFAULT_START, kis_collect.DEFAULT_END
    )
    a.meta["evidence_kind"] = "synthetic"
    return a


@pytest.mark.parametrize("conflict", [False, True])
def test_repeating_or_conflicting_pages_preserved_and_stop(tmp_path, conflict):
    a, client = archive(tmp_path), FakeMarket(repeat=not conflict, conflict=conflict)
    with pytest.raises(KISError, match="repeated_price_page|overlapping_or_conflicting"):
        kis_collect.price_pages(client, a, "XLK", "0", "2020-06-01", "2026-09-30", "full")
    assert len(a.meta["pages"]) == 2
    assert len(list(a.folder.glob("page-*.json"))) == 2


def test_page_limit_is_partial_not_success(tmp_path):
    a = archive(tmp_path)
    with pytest.raises(KISError, match="price_page_limit_partial"):
        kis_collect.price_pages(
            FakeMarket(), a, "XLK", "0", "2020-06-01", "2026-09-30", "full", max_pages=1
        )
    assert not a.meta["streams"]


def test_rights_cursor_stall_preserves_page(tmp_path):
    a = archive(tmp_path)
    with pytest.raises(KISError, match="rights_cursor_stalled"):
        kis_collect.rights_pages(FakeMarket(repeat=True), a, "XLK", "2020-06-01", "2026-09-30")
    assert len(a.meta["pages"]) == 1


def test_full_synthetic_archive_offline_recheck_and_original_preservation(tmp_path):
    a = archive(tmp_path / "original")
    kis_collect.collect(FakeMarket(empty_rights=True), a)
    result, normalized = kis_audit.audit_archive(a.folder, a.meta)
    assert result["status"] == "synthetic_only"
    assert result["total_return_eligible"] is False
    assert result["can_collect"] is False
    for ticker in kis_collect.SAMPLE:
        item = result["symbols"][ticker]
        assert item["prices"]["0"] == {"rows": 1592, "first": "2020-06-01", "last": "2026-09-30"}
        assert item["rights_completeness"] == "unknown"
        assert any("빈 기간별 권리" in s for s in item["issues"])
        assert "Adj Close" not in json.dumps(normalized[ticker])
    split = result["symbols"]["XLK"]["xlk_split"]["price_diagnostic"]
    assert split["official_split_neutral_ratio"] == split["provider_modp1_ratio"] == 1
    assert not split["dividend_added"]
    kis_collect.finish(a, result)
    original = {p.name: p.read_bytes() for p in a.folder.iterdir()}
    replay, target = kis.recheck(a.folder, tmp_path / "replay")
    assert replay["recheck"]["matched"]
    assert replay["status"] == "synthetic_only"
    assert target != a.folder
    assert original == {p.name: p.read_bytes() for p in a.folder.iterdir()}
    # Tampering is rejected before parsing/replay, even with no credentials or network.
    (a.folder / a.meta["pages"][0]["file"]).write_text("{}")
    with pytest.raises(KISError, match="integrity"):
        kis.recheck(a.folder, tmp_path / "bad")


def test_probe_missing_end_cannot_unlock_collection(tmp_path):
    a = archive(tmp_path, "probe")
    kis_collect.collect(FakeMarket(missing_end=True), a)
    result, _ = kis_audit.audit_archive(a.folder, a.meta)
    assert not result["can_collect"]
    missing = result["symbols"]["XLK"]["coverage"][0]["missing_sessions"]
    assert "2026-09-30" in missing


def test_double_split_application_refused():
    with pytest.raises(KISError, match="must_not_be_applied"):
        kis_audit.split_diagnostic(100, 100, 100, 100, modp="1")


def test_rights_continuation_uses_returned_keys(tmp_path):
    a = archive(tmp_path)

    class RightsClient:
        base = FakeMarket.base

        def __init__(self):
            self.calls = []

        def get(self, endpoint, p, continuation=""):
            self.calls.append((dict(p), continuation))
            if len(self.calls) == 1:
                data = {
                    "rt_cd": "0",
                    "output": [{"pdno": "XLK", "id": "one"}],
                    "ctx_area_nk50": "NEXT",
                    "ctx_area_fk50": "FILTER",
                }
                cont = "M"
            else:
                data = {"rt_cd": "0", "output": [{"pdno": "XLK", "id": "two"}]}
                cont = "D"
            return data, json.dumps(data).encode(), cont

    client = RightsClient()
    kis_collect.rights_pages(client, a, "XLK", "2025-01-01", "2025-12-31")
    assert client.calls[1][0]["CTX_AREA_NK50"] == "NEXT"
    assert client.calls[1][0]["CTX_AREA_FK50"] == "FILTER"
    assert client.calls[1][1] == "N"
    assert a.meta["streams"][0]["pagination"] == "provider_end"


def test_probe_readiness_is_distinct_from_price_approval(tmp_path):
    a = archive(tmp_path, "probe")
    kis_collect.collect(FakeMarket(empty_rights=True), a)
    # Exercise the production evidence-label branch using MOCKED data only.
    a.meta["evidence_kind"] = "real"
    result, _ = kis_audit.audit_archive(a.folder, a.meta)
    assert result["can_collect"]
    assert result["status"] == "held"
    assert not result["total_return_eligible"]
    a.meta["failures"].append({"ticker": "ALL", "stream": "collection", "code": "interrupted"})
    assert not kis_audit.audit_archive(a.folder, a.meta)[0]["can_collect"]


def test_malformed_success_body_stays_held(tmp_path):
    a = archive(tmp_path, "probe")

    class Malformed(FakeMarket):
        def get(self, endpoint, p, continuation=""):
            body, _, cont = super().get(endpoint, p, continuation)
            if endpoint == "dailyprice":
                body["output2"] = {"not": "a list"}
            return body, json.dumps(body).encode(), cont

    kis_collect.collect(Malformed(), a)
    result, _ = kis_audit.audit_archive(a.folder, a.meta)
    assert not result["can_collect"]
    assert all(i["prices"]["0"]["rows"] == 0 for i in result["symbols"].values())
    kis_collect.finish(a, result)
    assert (a.folder / "COMPLETE").exists()


def test_provider_rejection_message_cannot_reach_archive(tmp_path, monkeypatch, capsys):
    credentials(monkeypatch)

    def send(method, *args):
        if method == "POST":
            return 200, {}, json.dumps({"access_token": TOKEN, "expires_in": 86400}).encode()
        return 200, {}, json.dumps({"rt_cd": "1", "msg1": SECRET}).encode()

    client = KISClient("real", tmp_path / "cache", send=send, sleep=lambda _: None)
    a = archive(tmp_path / "archive", "probe")
    kis_collect.collect(client, a)
    assert not a.meta["pages"]
    result, _ = kis_audit.audit_archive(a.folder, a.meta)
    kis_collect.finish(a, result)
    for p in a.folder.iterdir():
        text = p.read_text()
        assert all(secret not in text for secret in (KEY, SECRET, TOKEN))
    assert SECRET not in capsys.readouterr().err


def test_expired_token_refreshes_once_and_invalidates_on_401(tmp_path, monkeypatch):
    credentials(monkeypatch)
    clock, calls = [1000.0], []

    def send(method, *args):
        calls.append(method)
        if method == "POST":
            return 200, {}, json.dumps({"access_token": TOKEN, "expires_in": 120}).encode()
        return 401, {}, b"{}"

    client = KISClient("real", tmp_path, send=send, clock=lambda: clock[0], sleep=lambda _: None)
    client.token()
    clock[0] += 70
    client.token()
    assert calls == ["POST", "POST"]
    with pytest.raises(KISError, match="authentication"):
        client.get("dailyprice", params())
    state = json.loads((client.cache / "token.json").read_text())
    assert "token" not in state
    with pytest.raises(KISError, match="cooldown"):
        client.token()


def test_issuer_record_date_is_not_ex_date(tmp_path, monkeypatch):
    a = archive(tmp_path, "probe")
    kis_collect.collect(FakeMarket(), a)
    a.meta["issuer_file"] = "issuer-distributions.xlsx"
    (a.folder / a.meta["issuer_file"]).write_bytes(b"SYNTHETIC issuer workbook")

    def issuer(*args):
        return {
            t: {
                "events_in_source_order": [
                    {
                        "ex_date": "2025-12-01",
                        "record_date": "2025-12-02",
                        "payable_date": "2025-12-10",
                        "dividend": 1,
                    }
                ]
            }
            for t in kis_collect.SAMPLE
        }

    monkeypatch.setattr(kis_audit, "parse_distributions", issuer)
    result, _ = kis_audit.audit_archive(a.folder, a.meta)
    item = result["symbols"]["XLK"]["issuer_comparisons"][0]
    assert item["issuer_ex_date"] != item["issuer_record_date"]
    assert item["ice_date_matches"] == 0
    assert item["amount_and_share_basis"] == "unverified"


def test_rights_semantics_do_not_invent_ex_date_or_split_ratio():
    normalized = kis_audit.normalize_rights(
        [{"acpl_bass_dt": "20251202", "rght_type_cd": "14", "stck_alct_rt": "100"}],
        [{"anno_dt": "20251101", "effective_dt": "20251205"}],
    )
    event = normalized["period"][0]
    assert event["record_date"] == "2025-12-02"
    assert event["ex_date"] is None and event["split_new_per_old"] is None
    assert event["cash_dividend_per_share"] is None
    assert normalized["ice"][0]["effective_date"] == "2025-12-05"


def test_duplicate_json_keys_cannot_hide_secret_fields():
    with pytest.raises(KISError, match="duplicate_json_key"):
        public_payload(b'{"output": {"appkey": "secret"}, "output": {}}')


@pytest.mark.parametrize("status", [429, 500, 503])
def test_http_diagnostic_keeps_status_and_code_not_message(tmp_path, monkeypatch, status):
    credentials(monkeypatch)
    calls = []

    def send(method, *args):
        if method == "POST":
            return 200, {}, json.dumps({"access_token": TOKEN, "expires_in": 86400}).encode()
        calls.append(1)
        return status, {}, b'{"msg_cd":"EGW00201","msg1":"PRIVATE_PROVIDER_TEXT"}'

    client = KISClient("real", tmp_path, send=send, sleep=lambda _: None)
    with pytest.raises(KISError) as error:
        client.get("search-info", {"PRDT_TYPE_CD": "529", "PDNO": "SPY"})
    assert f"HTTP {status}; KIS EGW00201" in str(error.value)
    assert "PRIVATE_PROVIDER_TEXT" not in str(error.value)
    assert len(calls) == 3


@pytest.mark.parametrize(
    "body",
    [
        {"msg_cd": SECRET, "msg1": "anything"},
        {"msg_cd": "EGW00201", "msg1": TOKEN},
        {"msg_cd": "EGW00201\nPRIVATE", "msg1": "anything"},
    ],
)
def test_error_code_extraction_rejects_secret_echo_or_invalid_code(body):
    from macrolens.kis_client import provider_code

    assert provider_code(json.dumps(body).encode(), (KEY, SECRET, TOKEN)) is None


def test_urllib_http_error_only_returns_sanitized_code(monkeypatch):
    import io
    from urllib.error import HTTPError

    from macrolens import kis_client

    body = io.BytesIO(b'{"msg_cd":"EGW00201","msg1":"PRIVATE_PROVIDER_TEXT"}')

    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError(
                "https://example.test", 500, "private reason", {"authorization": TOKEN}, body
            )

    monkeypatch.setattr(kis_client, "build_opener", lambda *args: Opener())
    status, headers, raw = kis_client.transport(
        "GET",
        "https://example.test",
        {"appkey": KEY, "appsecret": SECRET, "authorization": "Bearer " + TOKEN, "custtype": "P"},
        None,
        15,
    )
    assert status == 500 and headers == {}
    assert json.loads(raw) == {"msg_cd": "EGW00201"}
    assert body.closed
    assert b"PRIVATE_PROVIDER_TEXT" not in raw


class FakeBasicQuote(FakeMarket):
    def __init__(self, endpoint_patch=None, fail_basic=False):
        super().__init__(empty_rights=True)
        self.endpoint_patch = endpoint_patch or {}
        self.fail_basic = fail_basic

    def get(self, endpoint, p, continuation=""):
        if endpoint not in ("basic-info", "price-detail"):
            return super().get(endpoint, p, continuation)
        self.calls.append((endpoint, dict(p), continuation))
        if endpoint == "basic-info":
            if self.fail_basic:
                raise KISError("http_retry_exhausted (HTTP 500; KIS EGW02004)")
            output = {"pdno": p["PDNO"], "prdt_type_cd": "529", "std_pdno": "SYNTHETIC_ISIN"}
        else:
            output = {
                "rsym": "DAMS" + p["SYMB"],
                "curr": "USD",
                "etyp_nm": "ETF",
                "last": "999999999",
                "open": "999999999",
            }
        output.update(self.endpoint_patch.get(endpoint, {}))
        data = {"rt_cd": "0", "output": output}
        return data, json.dumps(data).encode(), ""


def basic_probe(tmp_path, client):
    a = kis_collect.Archive(
        tmp_path,
        "real",
        "probe",
        "AMS",
        "529",
        kis_collect.DEFAULT_START,
        kis_collect.DEFAULT_END,
        identity_source="basic-quote",
    )
    # All calls are mocked. 'real' exercises the production-label gate only.
    kis_collect.collect(client, a)
    result, normalized = kis_audit.audit_archive(a.folder, a.meta)
    return a, result, normalized


def test_basic_quote_requires_both_sources_and_preserves_offline_replay(tmp_path):
    client = FakeBasicQuote()
    a, result, normalized = basic_probe(tmp_path / "source", client)
    assert result["can_collect"]
    assert not result["total_return_eligible"] and result["status"] == "held"
    assert all(endpoint != "search-info" for endpoint, _, _ in client.calls)
    for ticker in kis_collect.SAMPLE:
        symbol = result["symbols"][ticker]
        assert symbol["prices"]["0"]["rows"] == 16
        assert symbol["prices"]["1"]["rows"] == 16
        assert symbol["identity"]["price_detail"]["last"] == "999999999"
        assert all(
            r["clos"] != 999999999 for p in normalized[ticker]["prices_by_modp"].values() for r in p
        )
    kis_collect.finish(a, result)
    replay, _ = kis.recheck(a.folder, tmp_path / "replay")
    assert replay["recheck"]["matched"]
    report = (a.folder / "report.md").read_text()
    assert "최초~최종 날짜 사이 전체를 수집한 것이 아닙니다" in report
    assert report.index("| SPY |") < report.index("XLC:")


@pytest.mark.parametrize(
    "patch",
    [
        {"basic-info": {"pdno": "WRONG"}},
        {"basic-info": {"prdt_type_cd": "512"}},
        {"price-detail": {"curr": "JPY"}},
        {"price-detail": {"rsym": "DNYSXLK"}},
        {"price-detail": {"curr": ""}},
    ],
)
def test_basic_quote_identity_mismatch_still_blocks_collection(tmp_path, patch):
    _, result, _ = basic_probe(tmp_path, FakeBasicQuote(endpoint_patch=patch))
    assert not result["can_collect"]
    assert result["symbols"]["XLK"]["prices"]["0"]["rows"] == 16


def test_basic_identity_failure_not_ignored_when_quote_and_prices_work(tmp_path):
    _, result, _ = basic_probe(tmp_path, FakeBasicQuote(fail_basic=True))
    assert not result["can_collect"]
    assert any("identity_basic-info:" in s for s in result["symbols"]["SPY"]["issues"])


def test_basic_quote_transport_uses_official_paths_and_tr_ids(tmp_path, monkeypatch):
    credentials(monkeypatch)
    calls = []

    def send(method, url, headers, body, timeout):
        if method == "POST":
            return 200, {}, json.dumps({"access_token": TOKEN, "expires_in": 86400}).encode()
        calls.append((url, headers["tr_id"]))
        assert KEY not in url and SECRET not in url and TOKEN not in url
        return 200, {}, b'{"rt_cd":"0", "output":{}}'

    client = KISClient("real", tmp_path, send=send, sleep=lambda _: None)
    client.get("basic-info", {"PRDT_TYPE_CD": "529", "PDNO": "SPY"})
    client.get("price-detail", {"AUTH": "", "EXCD": "AMS", "SYMB": "SPY"})
    assert calls[0][0].split("?")[0].endswith("/uapi/domestic-stock/v1/quotations/search-info")
    assert calls[0][1] == "CTPF1604R"
    assert calls[1][0].split("?")[0].endswith("/uapi/overseas-price/v1/quotations/price-detail")
    assert calls[1][1] == "HHDFS76200200"


def test_collect_preserves_probe_identity_source_and_rejects_change(tmp_path, monkeypatch, capsys):
    a, result, _ = basic_probe(tmp_path / "source", FakeBasicQuote())
    kis_collect.finish(a, result)
    assert (
        kis.main(
            ["collect", "--env", "real", "--probe", str(a.folder), "--identity-source", "overseas"]
        )
        == 1
    )
    assert "matching_successful_real_probe_required" in capsys.readouterr().err

    class CLIClient(FakeBasicQuote):
        def __init__(self, env):
            super().__init__()
            self.cache = tmp_path / "private"

    monkeypatch.setattr(kis, "KISClient", CLIClient)
    target = tmp_path / "collected"
    assert (
        kis.main(["collect", "--env", "real", "--probe", str(a.folder), "--output", str(target)])
        == 2
    )
    manifest = json.loads(next(target.glob("*/manifest.json")).read_text())
    assert manifest["identity_source"] == "basic-quote"
    assert all(p["endpoint"] != "search-info" for p in manifest["pages"])
