"""Synthetic responses/archives only; public evidence catalog scope checks are separate."""

import copy
import json
import socket

import pytest
from test_kis import FakeMarket
from test_price_audit import issuer_workbook

from macrolens import kis, kis_audit, kis_collect, kis_inventory
from macrolens.distribution_evidence import interpret, load_catalog
from macrolens.kis_client import KISError
from macrolens.price_audit import UNIVERSE
from macrolens.price_quality import trading_sessions

NINE = tuple(t for t in UNIVERSE if t not in kis_collect.SAMPLE)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("network/auth forbidden")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(kis.KISClient, "token", fail)
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)


def saved(tmp_path, symbols=NINE, mode="collect", client=None, kind="synthetic"):
    a = kis_collect.Archive(
        tmp_path, "real", mode, "AMS", "529", "2025-12-01", "2025-12-10", symbols=symbols
    )
    a.meta["evidence_kind"] = kind  # 'real' only exercises preflight/common-date branches in mocks.
    client = client or FakeMarket(empty_rights=True)
    kis_collect.collect(client, a)
    result, _ = kis_audit.audit_archive(a.folder, a.meta)
    kis_collect.finish(a, result)
    return a, result, client


def test_nine_scope_no_old_three_requests_and_offline_recheck(tmp_path):
    a, result, client = saved(tmp_path / "source")
    assert set(result["symbols"]) == set(NINE)
    assert set(a.meta["symbols"]) == set(NINE)
    for page in a.meta["pages"]:
        params = page["params"]
        assert params.get("SYMB", params.get("PDNO")) in NINE
    assert result["collectable_symbols"] == []  # Synthetic never unlocks real collection.
    assert all(r["rights_completeness"] == "unknown" for r in result["symbols"].values())
    before = {p.name: p.read_bytes() for p in a.folder.iterdir()}
    again, new = kis.recheck(a.folder, tmp_path / "recheck")
    assert again["recheck"]["matched"] and new != a.folder
    assert before == {p.name: p.read_bytes() for p in a.folder.iterdir()}


@pytest.mark.parametrize("symbols", [[], ["XLE", "XLE"], ["QQQ"], "XLE"])
def test_invalid_symbol_scope_rejected_before_archive(tmp_path, symbols):
    with pytest.raises(KISError, match="invalid_or_duplicate_symbols"):
        kis_collect.Archive(
            tmp_path, "real", "probe", "AMS", "529", "2020-06-01", "2026-09-30", symbols=symbols
        )
    assert not list(tmp_path.iterdir())


def test_legacy_scope_and_default_probe_windows_unchanged():
    assert kis_collect.archive_symbols({}) == kis_collect.SAMPLE
    meta = {"start": "2020-06-01", "end_inclusive": "2026-09-30", "mode": "probe"}
    assert kis_collect.price_ranges(meta) == [
        ("recent", "2026-09-21", "2026-09-30"),
        ("old", "2020-06-01", "2020-06-10"),
    ]
    meta.update(start="2025-12-01", end_inclusive="2025-12-10")
    assert kis_collect.price_ranges(meta) == [("old", "2025-12-01", "2025-12-10")]
    assert kis_collect.rights_probe_range(meta) == ("2025-12-01", "2025-12-10")


class OneBadSymbol(FakeMarket):
    def get(self, endpoint, p, continuation=""):
        data, _, cont = super().get(endpoint, p, continuation)
        if endpoint == "dailyprice" and p["SYMB"] == "XLF":
            for row in data["output2"]:
                if row["xymd"] == "20251208":
                    row["low"] = "101"  # More than open and close; preserve raw error.
        return data, json.dumps(data).encode(), cont


def test_probe_subset_checks_before_auth_and_never_uses_failed_symbol(
    tmp_path, monkeypatch, capsys
):
    a, result, _ = saved(tmp_path / "probe", ("XLE", "XLF"), "probe", OneBadSymbol(), "real")
    assert not result["can_collect"] and result["collectable_symbols"] == ["XLE"]
    assert result["price_row_errors"][0]["ticker"] == "XLF"
    reached = []

    def stop(env):
        reached.append(env)
        raise RuntimeError("valid subset reached client construction")

    monkeypatch.setattr(kis, "KISClient", stop)
    base = ["collect", "--env", "real", "--probe", str(a.folder)]
    for extra in (
        [],
        ["--symbols", "XLF"],
        ["--symbols", "SPY"],
        ["--symbols", "XLE", "--start", "2020-06-01"],
        ["--symbols", "XLE", "--env", "demo"],
    ):
        assert kis.main(base + extra) == 1
        assert not reached
    with pytest.raises(RuntimeError, match="valid subset"):
        kis.main(base + ["--symbols", "XLE"])
    assert reached == ["real"]
    assert "matching_successful_real_probe_required" in capsys.readouterr().err


def test_empty_rights_do_not_prove_no_events_or_block_price_preflight(tmp_path):
    a, result, _ = saved(tmp_path, ("XLE",), "probe", kind="real")
    assert result["can_collect"] and result["collectable_symbols"] == ["XLE"]
    item = result["symbols"]["XLE"]
    assert item["status"] == "held" and item["rights_completeness"] == "unknown"
    assert item["rights_rows"] == item["ice_rows"] == 0
    assert not result["total_return_eligible"]


def test_new_symbol_repeating_page_partial_failure_is_preserved(tmp_path):
    a = kis_collect.Archive(
        tmp_path, "real", "collect", "AMS", "529", "2020-06-01", "2026-09-30", symbols=("XLE",)
    )
    with pytest.raises(KISError, match="repeated_price_page"):
        kis_collect.price_pages(
            FakeMarket(repeat=True), a, "XLE", "0", "2020-06-01", "2026-09-30", "full"
        )
    assert len(a.meta["pages"]) == 2
    assert len(list(a.folder.glob("page-*.json"))) == 2
    assert not a.meta["streams"]


def test_new_event_evidence_is_scoped_to_exact_etf_cusip_dates_and_year():
    catalog, _ = kis_inventory.combined_catalog()
    original, _ = load_catalog()
    assert {c["ticker"] for c in original["claims"]} == set(kis_collect.SAMPLE)
    new = [c for c in catalog["claims"] if c["ticker"] in NINE]
    assert len(new) == 36
    for claim in new:
        source = {k: claim[k] for k in ("cusip", "ex_date", "record_date", "payable_date")}
        source.update(dividend="1", short_term_gain=None, long_term_gain=None)
        before = copy.deepcopy(source)
        effective, _ = interpret(claim["ticker"], [source], catalog)
        assert effective[0]["short_term_gain"] == effective[0]["long_term_gain"] == "0"
        assert source == before
        for changed in (
            {**source, "ex_date": source["ex_date"].replace("2025", "2024")},
            {**source, "cusip": "OTHER"},
            {**source, "payable_date": "2026-01-01"},
        ):
            held, _ = interpret(claim["ticker"], [changed], catalog)
            assert held[0]["short_term_gain"] is None
        wrong, _ = interpret("SPY", [source], catalog)
        assert wrong[0]["long_term_gain"] is None


def test_known_five_splits_do_not_leak_to_other_symbols_or_apply_twice():
    catalog = json.loads(kis_inventory.SPLITS.read_text())
    normalized = {
        "prices_by_modp": {
            "0": [{"date": "2025-12-04", "clos": 200}, {"date": "2025-12-05", "clos": 100}],
            "1": [{"date": "2025-12-04", "clos": 100}, {"date": "2025-12-05", "clos": 100}],
        },
        "period_rights": [],
        "ice_rights": [],
    }
    before = copy.deepcopy(normalized)
    for t in UNIVERSE:
        result = kis_inventory.split_review(t, normalized, "2025-01-01", "2025-12-31", catalog)
        assert result["history_completeness"] == "unknown"
        if t in ("XLK", "XLE", "XLY", "XLU", "XLB"):
            e = result["events"][0]
            assert e["status"] == "observed_consistent"
            assert e["diagnostic"]["official_split_neutral_ratio"] == 1
            assert e["diagnostic"]["provider_modp1_ratio"] == 1
            assert not e["diagnostic"]["dividend_added"]
        else:
            assert not result["events"]
    assert normalized == before
    assert not kis_inventory.split_review("XLE", normalized, "2024-01-01", "2024-12-31", catalog)[
        "events"
    ]


def test_inventory_without_prices_preserves_unknown_and_original_file(tmp_path):
    path = tmp_path / "issuer.xlsx"
    blob = issuer_workbook(
        [
            [
                "SYNTHETIC",
                "XLE",
                "81369Y506",
                "03/24/2025",
                "03/24/2025",
                "03/26/2025",
                "1",
                "",
                "",
                "Quarterly",
            ],
            [
                "SYNTHETIC",
                "XLE",
                "81369Y506",
                "03/25/2024",
                "03/25/2024",
                "03/27/2024",
                "1",
                "",
                "",
                "Quarterly",
            ],
        ]
    )
    path.write_bytes(blob)
    first, folder = kis_inventory.run([], tmp_path / "out", "2020-06-01", "2026-09-30", path)
    second, other = kis_inventory.run([], tmp_path / "out", "2020-06-01", "2026-09-30", path)
    assert first == second and folder != other
    assert path.read_bytes() == blob == (folder / "issuer-distributions.xlsx").read_bytes()
    assert all(r["status"] == "not_run" for r in first["symbols"].values())
    byyear = first["symbols"]["XLE"]["distributions"]["by_year"]
    assert byyear["2025"]["resolved_blank_fields"] == 2
    assert byyear["2024"]["unknown_short_term_gain"] == 1
    assert first["common_prices"]["status"] == "not_established"
    assert not first["returns_calculated"] and not first["total_return_eligible"]


def test_saved_inventory_reuses_originals_and_rejects_ambiguity(tmp_path):
    a, _, _ = saved(tmp_path / "source", ("XLE",))
    before = {p.name: p.read_bytes() for p in a.folder.iterdir()}
    result, _ = kis_inventory.run([a.folder], tmp_path / "inv", "2025-12-01", "2025-12-10")
    assert result["symbols"]["XLE"]["status"] == "synthetic_only"
    assert result["symbols"]["SPY"]["status"] == "not_run"
    assert result["common_prices"]["status"] == "not_established"
    assert before == {p.name: p.read_bytes() for p in a.folder.iterdir()}
    ambiguous, _ = kis_inventory.run(
        [a.folder, a.folder], tmp_path / "inv", "2025-12-01", "2025-12-10"
    )
    assert ambiguous["symbols"]["XLE"]["status"] == "held"
    assert "multiple_archives" in ambiguous["symbols"]["XLE"]["blocking_inputs"][0]
    (a.folder / a.meta["pages"][0]["file"]).write_text("{}")
    with pytest.raises(KISError, match="integrity"):
        kis_inventory.run([a.folder], tmp_path / "inv", "2025-12-01", "2025-12-10")


def test_common_dates_leave_gaps_and_do_not_certify_returns(tmp_path):
    a, _, _ = saved(
        tmp_path / "source", UNIVERSE, client=OneBadSymbol(empty_rights=True), kind="real"
    )
    result, _ = kis_inventory.run([a.folder], tmp_path / "inv", "2025-12-01", "2025-12-10")
    common = result["common_prices"]
    assert common["status"] == "observed_dates_only_not_eligibility"
    assert common["missing_sessions"] == ["2025-12-08"]
    assert [s["end"] for s in common["continuous_segments"]] == ["2025-12-05", "2025-12-10"]
    assert result["symbols"]["XLF"]["price_status"] == "held"
    assert not result["total_return_eligible"]
    assert common["sessions"] == len(trading_sessions("2025-12-01", "2025-12-10")[0]) - 1


def test_cli_inventory_needs_no_auth_and_new_archives_cannot_start_old_ledger(tmp_path, capsys):
    assert kis.main(["inventory", "--output", str(tmp_path / "inv")]) == 0
    assert "not_run" in capsys.readouterr().out
    a, _, _ = saved(tmp_path / "source", ("XLE",))
    assert kis.main(["ledger", str(a.folder), "--output", str(tmp_path / "ledger")]) == 1
    assert not (tmp_path / "ledger").exists()


@pytest.mark.parametrize("start,end", [("20250601", "2025-12-10"), ("2025-12-10", "2025-06-01")])
def test_bad_collection_dates_fail_before_auth(tmp_path, monkeypatch, start, end):
    def fail(*args):
        pytest.fail("invalid dates must fail before client creation")

    monkeypatch.setattr(kis, "KISClient", fail)
    assert (
        kis.main(["probe", "--env", "real", "--symbols", "XLE", "--start", start, "--end", end])
        == 1
    )


def test_cli_subset_collection_records_only_selected_symbols(tmp_path, monkeypatch):
    probe, _, _ = saved(tmp_path / "probe", ("XLE", "XLF"), "probe", OneBadSymbol(), "real")
    market = FakeMarket(empty_rights=True)
    market.cache = tmp_path / "cache"
    monkeypatch.setattr(kis, "KISClient", lambda env: market)
    assert (
        kis.main(
            [
                "collect",
                "--env",
                "real",
                "--probe",
                str(probe.folder),
                "--symbols",
                "XLE",
                "--output",
                str(tmp_path / "out"),
            ]
        )
        == 2
    )
    paths = list((tmp_path / "out").glob("*/manifest.json"))
    assert len(paths) == 1
    meta = kis_collect.verify_archive(paths[0].parent)
    assert meta["symbols"] == ["XLE"]
    assert meta["start"] == "2025-12-01" and meta["end_inclusive"] == "2025-12-10"
    assert all(p["params"].get("SYMB", p["params"].get("PDNO")) == "XLE" for p in meta["pages"])


def test_non_session_prices_never_expand_common_calendar(tmp_path):
    class WeekendPrice(FakeMarket):
        def get(self, endpoint, params, continuation=""):
            data, _, cont = super().get(endpoint, params, continuation)
            if endpoint == "dailyprice":
                data["output2"].append(
                    {
                        "xymd": "20251206",
                        "open": "100",
                        "high": "100",
                        "low": "100",
                        "clos": "100",
                        "tvol": "1",
                    }
                )
                data["output2"].sort(key=lambda r: r["xymd"], reverse=True)
            return data, json.dumps(data).encode(), cont

    a, _, _ = saved(
        tmp_path / "source", UNIVERSE, client=WeekendPrice(empty_rights=True), kind="real"
    )
    result, _ = kis_inventory.run([a.folder], tmp_path / "inv", "2025-12-01", "2025-12-10")
    assert all(s["price_status"] == "held" for s in result["symbols"].values())
    assert result["common_prices"]["sessions"] == len(
        trading_sessions("2025-12-01", "2025-12-10")[0]
    )
