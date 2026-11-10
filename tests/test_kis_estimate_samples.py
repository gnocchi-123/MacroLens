"""Synthetic XLC/SPY prices and event evidence; no live-provider certification."""

import copy
import json
import socket
from datetime import date
from fractions import Fraction

import pytest

from macrolens import kis, kis_audit, kis_collect, kis_estimate, kis_estimate_samples, kis_ledger
from macrolens.distribution_evidence import interpret, load_catalog
from macrolens.kis_client import KISClient
from macrolens.ledger import blocker, calculate
from macrolens.price_audit import sha
from macrolens.price_quality import AuditError, trading_sessions


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Sample estimates must not authenticate or download")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(KISClient, "token", forbidden)
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)


def events_for(ticker):
    return [
        dict(
            cusip=c,
            ex_date=ex,
            record_date=rec,
            payable_date=pay,
            dividend="2",
            short_term_gain=None,
            long_term_gain=None,
            share_basis="unknown",
            source_amounts={"dividend": "2", "short_term_gain": None, "long_term_gain": None},
        )
        for c, ex, rec, pay in sorted(kis_estimate_samples.expected_events(ticker))
    ]


def candidates(ticker):
    events, _ = interpret(ticker, events_for(ticker), load_catalog()[0])
    sessions, _ = trading_sessions(kis_estimate.START, kis_estimate.END)
    inputs = {
        "events": events,
        "splits": [],
        "prices_modp0": {d: "100" for d in sessions},
        "prices_modp1": {d: "100" for d in sessions},
    }
    strict = {
        "ticker": ticker,
        "start": kis_estimate.START,
        "end_inclusive": kis_estimate.END,
        "status": "held",
        "source_price_status": "held" if ticker == "XLC" else "conditional",
        "source_total_return_eligible": False,
        "price_contract_evidence": {"status": "matched_archived_requests"},
        "split_diagnostics": [],
        "blockers": [
            blocker(ticker, kis_estimate.START, k, "unknown_" + k, "SYNTHETIC assumption")
            for k in kis_ledger.CLAIMS
        ]
        + [
            blocker(ticker, e["ex_date"], "share_basis", "unknown_share_basis", "SYNTHETIC")
            for e in events
        ],
    }
    return strict, inputs


def review(tmp_path, ticker, inputs, endpoint=None, rows=()):
    pages = []
    if endpoint:
        blob = json.dumps(
            {"output" if endpoint == "period-rights" else "output1": list(rows)}
        ).encode()
        (tmp_path / "page.json").write_bytes(blob)
        pages = [
            {
                "file": "page.json",
                "sha256": sha(blob),
                "endpoint": endpoint,
                "params": {"PDNO" if endpoint == "period-rights" else "SYMB": ticker},
            }
        ]
    return kis_estimate_samples.evidence_review(
        tmp_path, {"pages": pages, "product_type": "529"}, ticker, inputs
    )


@pytest.mark.parametrize("ticker", ["XLC", "SPY"])
def test_no_xlk_split_or_multiplier_leaks_and_terminal_balances(ticker, tmp_path):
    strict, inputs = candidates(ticker)
    original = copy.deepcopy((strict, inputs))
    check = review(tmp_path, ticker, inputs)
    result, effective = kis_estimate_samples.scenarios(strict, inputs, check)
    assert (strict, inputs) == original
    assert result["status"] == "estimated_not_validated"
    assert result["source_price_status"] == strict["source_price_status"]
    assert set(result["scenarios"]) == {"reported_amounts"}
    item = result["scenarios"]["reported_amounts"]
    assert Fraction(item["estimated_total_return"]["exact"]) == Fraction(102, 100) ** 4 - 1
    assert len(item["rows"]) == 364
    assert all(Fraction(r["split_ratio"]["exact"]) == 1 for r in item["rows"])
    assert all(c["multiplier"] == 1 for c in item["dividend_basis_changes"])
    assert all(e["dividend"] == "2" for e in effective["reported_amounts"])
    assert result["scenario_difference_percentage_points"] is None
    assert not result["total_return_eligible"] and not result["strict_point_in_time"]
    # Empty secondary evidence is an explicit limitation, never evidence of no events.
    assert check["rights_rows_in_scope"] == {"period-rights": 0, "rights-by-ice": 0}
    assert check["limitations"]
    last = item["rows"][-1]
    assert Fraction(last["cash"]["exact"]) == 0
    if ticker == "XLC":
        assert Fraction(last["receivable"]["exact"]) == 0
    else:
        claim = item["claims"][-1]
        assert claim["payable_date"] == "2026-01-30" and claim["reinvest_date"] == "2026-02-02"
        assert claim["state"] == "receivable"
        assert Fraction(last["shares"]["exact"]) == Fraction(102, 100) ** 3
        assert Fraction(last["receivable"]["exact"]) == 2 * Fraction(102, 100) ** 3
        assert Fraction(last["nav"]["exact"]) == 100 * Fraction(last["shares"]["exact"]) + Fraction(
            last["receivable"]["exact"]
        )


def test_spy_future_payment_not_advanced_and_cash_shift_preserves_nav(tmp_path):
    strict, inputs = candidates("SPY")
    _, effective = kis_estimate_samples.scenarios(strict, inputs, review(tmp_path, "SPY", inputs))
    sessions, _ = trading_sessions(kis_estimate.START, "2026-02-02")
    # Extend pure accounting in a synthetic test, not the public 2025 estimate scope.
    result = calculate(
        "SPY",
        kis_estimate.START,
        "2026-02-02",
        {d: "100" for d in sessions},
        sessions,
        effective["reported_amounts"],
        [],
    )
    byday = {r["date"]: r for r in result["rows"]}
    assert Fraction(byday["2026-01-29"]["receivable"]["exact"]) > 0
    assert Fraction(byday["2026-01-30"]["receivable"]["exact"]) == 0
    assert byday["2026-01-30"]["cash"] == byday["2026-01-29"]["receivable"]
    assert Fraction(byday["2026-01-30"]["reinvested_cash"]["exact"]) == 0
    assert byday["2026-02-02"]["reinvested_cash"] == byday["2026-01-30"]["cash"]
    assert len({byday[d]["nav"]["exact"] for d in ("2026-01-29", "2026-01-30", "2026-02-02")}) == 1


def test_scope_and_unknown_fields_are_not_relaxed(tmp_path):
    strict, inputs = candidates("SPY")
    check = review(tmp_path, "SPY", inputs)
    strict["start"] = "2024-01-02"
    with pytest.raises(AuditError, match="2025"):
        kis_estimate_samples.scenarios(strict, inputs, check)
    strict["start"] = kis_estimate.START
    inputs["events"][0]["long_term_gain"] = None
    del inputs["prices_modp0"]["2025-03-24"]
    result, _ = kis_estimate_samples.scenarios(strict, inputs, check)
    item = result["scenarios"]["reported_amounts"]
    assert item["estimated_total_return"] is None
    assert {"unknown_distribution_amount", "missing_or_invalid_close"} <= {
        b["code"] for b in item["blockers"]
    }
    inputs["events"][0]["cusip"] = "OTHER"
    result, _ = kis_estimate_samples.scenarios(strict, inputs, check)
    assert not result["scenarios"]["reported_amounts"]["rows"]


def test_2025_zero_evidence_does_not_resolve_other_etf_or_year():
    source = events_for("SPY")[0]
    other, _ = interpret("XLC", [source], load_catalog()[0])
    assert other[0]["short_term_gain"] is None
    source["ex_date"] = "2026-03-21"
    other, _ = interpret("SPY", [source], load_catalog()[0])
    assert other[0]["long_term_gain"] is None
    # A 2026 payable date is still the exact 2025-12 SPY event, not a 2026 ex-date approval.
    effective, _ = interpret("SPY", events_for("SPY"), load_catalog()[0])
    assert effective[-1]["short_term_gain"] == "0"


@pytest.mark.parametrize("change", ["split", "identity", "extra_event", "amount", "date_format"])
def test_stored_primary_rights_conflicts_block_with_source_location(tmp_path, change):
    strict, inputs = candidates("XLC")
    row = {
        "pdno": "XLC",
        "prdt_type_cd": "529",
        "rght_type_cd": "03",
        "acpl_bass_dt": "20250324",
        "dfnt_yn": "Y",
        "crcy_cd": "USD",
        "alct_frcr_unpr": "2",
    }
    key, value = {
        "split": ("rght_type_cd", "14"),
        "identity": ("pdno", "XLK"),
        "extra_event": ("acpl_bass_dt", "20250501"),
        "amount": ("alct_frcr_unpr", "3"),
        "date_format": ("acpl_bass_dt", "bad"),
    }[change]
    row[key] = value
    check = review(tmp_path, "XLC", inputs, "period-rights", [row])
    result, _ = kis_estimate_samples.scenarios(strict, inputs, check)
    assert result["status"] == "held"
    assert check["blockers"][0]["source"]["row_1based"] == 1
    assert not result["scenarios"]["reported_amounts"]["rows"]


def test_precision_difference_never_changes_issuer_amount_and_old_rights_do_not_leak(tmp_path):
    strict, inputs = candidates("XLC")
    row = {
        "pdno": "XLC",
        "prdt_type_cd": "529",
        "rght_type_cd": "03",
        "acpl_bass_dt": "20250324",
        "dfnt_yn": "Y",
        "crcy_cd": "USD",
        "alct_frcr_unpr": "2.000009",
    }
    old = {**row, "acpl_bass_dt": "20231201", "rght_type_cd": "14"}
    check = review(tmp_path, "XLC", inputs, "period-rights", [row, old])
    assert not check["blockers"] and check["rights_rows_in_scope"]["period-rights"] == 1
    _, effective = kis_estimate_samples.scenarios(strict, inputs, check)
    assert effective["reported_amounts"][0]["dividend"] == "2"


@pytest.mark.parametrize("change", ["pay_date", "split", "undated_split"])
def test_ice_contradictions_are_not_silently_ignored(tmp_path, change):
    strict, inputs = candidates("SPY")
    row = {"div_lock_dt": "20251219", "record_dt": "20251219", "pay_dt": "20260130"}
    if change == "pay_date":
        row["pay_dt"] = "20251230"
    elif change == "split":
        row.update(ca_title="STOCK SPLIT", effective_dt="20251205")
    else:
        row = {"ca_title": "STOCK SPLIT", "anno_dt": "20251201"}
    check = review(tmp_path, "SPY", inputs, "rights-by-ice", [row])
    result, _ = kis_estimate_samples.scenarios(strict, inputs, check)
    assert result["status"] == "held"


def test_prior_year_ice_entitlement_is_not_added_because_payment_is_in_2025(tmp_path):
    _, inputs = candidates("SPY")
    row = {
        "div_lock_dt": "20241220",
        "record_dt": "20241220",
        "pay_dt": "20250131",
        "effective_dt": "20250131",
    }
    check = review(tmp_path, "SPY", inputs, "rights-by-ice", [row])
    assert not check["blockers"]
    assert check["rights_rows_in_scope"]["rights-by-ice"] == 0


def test_unexplained_adjustment_step_and_split_input_are_held(tmp_path):
    strict, inputs = candidates("XLC")
    inputs["prices_modp0"]["2025-12-05"] = "50"
    check = review(tmp_path, "XLC", inputs)
    assert check["blockers"][0]["code"] == "unexplained_adjustment_step"
    result, _ = kis_estimate_samples.scenarios(strict, inputs, check)
    assert result["scenarios"]["reported_amounts"]["estimated_total_return"] is None
    inputs["splits"] = [dict(kis_ledger.SPLIT)]
    result, _ = kis_estimate_samples.scenarios(strict, inputs, {"blockers": []})
    assert any(
        b["code"] == "unexpected_split_input"
        for b in result["scenarios"]["reported_amounts"]["blockers"]
    )


@pytest.fixture
def saved(tmp_path, request):
    from test_kis import FakeMarket, archive
    from test_price_audit import issuer_workbook

    class SampleMarket(FakeMarket):
        def get(self, endpoint, p, continuation=""):
            body, blob, cont = super().get(endpoint, p, continuation)
            ticker = p.get("SYMB", p.get("PDNO"))
            if ticker not in ("XLC", "SPY"):
                return body, blob, cont
            ev = events_for(ticker)
            if endpoint == "dailyprice":
                for row in body["output2"]:
                    for k in ("open", "high", "low", "clos"):
                        row[k] = "100"
                    if ticker == "XLC" and row["xymd"] == getattr(request, "param", "20230605"):
                        row["open"] = "90"  # The real issue's shape, synthetic values only.
            elif endpoint == "period-rights":
                body["output"] = [
                    {
                        "pdno": ticker,
                        "prdt_type_cd": "529",
                        "rght_type_cd": "03",
                        "acpl_bass_dt": e["record_date"].replace("-", ""),
                        "dfnt_yn": "Y",
                        "crcy_cd": "USD",
                        "alct_frcr_unpr": e["dividend"],
                    }
                    for e in ev
                    if p["INQR_STRT_DT"] <= e["record_date"].replace("-", "") <= p["INQR_END_DT"]
                ]
            elif endpoint == "rights-by-ice":
                body["output1"] = [
                    {
                        "div_lock_dt": e["ex_date"].replace("-", ""),
                        "record_dt": e["record_date"].replace("-", ""),
                        "pay_dt": e["payable_date"].replace("-", ""),
                    }
                    for e in ev
                    if p["ST_YMD"] <= e["ex_date"].replace("-", "") <= p["ED_YMD"]
                ]
            return body, json.dumps(body).encode(), cont

    source = archive(tmp_path / "source")
    kis_collect.collect(SampleMarket(), source)
    catalog = load_catalog()[0]
    issuer = tmp_path / "issuer.xlsx"
    issuer.write_bytes(
        issuer_workbook(
            [
                [
                    "SYNTHETIC",
                    c["ticker"],
                    c["cusip"],
                    *[
                        date.fromisoformat(c[k]).strftime("%m/%d/%Y")
                        for k in ("ex_date", "record_date", "payable_date")
                    ],
                    "2",
                    "",
                    "",
                    "Q",
                ]
                for c in catalog["claims"]
            ]
        )
    )
    kis.attach_issuer(source, issuer)
    audit, _ = kis_audit.audit_archive(source.folder, source.meta)
    kis_collect.finish(source, audit)
    return source.folder


def test_offline_cli_replay_and_source_preservation_with_xlc_out_of_scope_error(
    saved, tmp_path, capsys
):
    before = {p.name: p.read_bytes() for p in saved.iterdir()}
    assert json.loads((saved / "audit.json").read_text())["symbols"]["XLC"]["status"] == "held"
    output = tmp_path / "estimates"
    assert kis.main(["estimate-xlc-spy-2025", str(saved), "--output", str(output)]) == 0
    assert "XLC: estimated_not_validated / 원장 364행 / 보류 사유 0건" in capsys.readouterr().out
    first = next(output.glob("*/result.json"))
    replay, folder = kis_estimate_samples.run(saved, output)
    assert replay == json.loads(first.read_text())
    assert replay["symbols"]["XLC"]["source_price_status"] == "held"
    for ticker in ("XLC", "SPY"):
        item = replay["symbols"][ticker]["scenarios"]["reported_amounts"]
        assert item["status"] == "estimated_not_validated"
        assert len(item["claims"]) == 4
        assert (first.parent / f"{ticker}-ledger.csv").read_bytes() == (
            folder / f"{ticker}-ledger.csv"
        ).read_bytes()
    assert before == {p.name: p.read_bytes() for p in saved.iterdir()}
    strict = json.loads((folder / "strict-result.json").read_text())
    assert strict["symbols"]["XLC"]["source_price_errors"][0]["date"] == "2023-06-05"
    assert strict["symbols"]["SPY"]["status"] == "held"
    assert (
        replay["symbols"]["SPY"]["scenarios"]["reported_amounts"]["claims"][-1]["state"]
        == "receivable"
    )
    # The existing command keeps its two scenarios and numerical accounting.
    legacy, _ = kis_estimate.run(saved, tmp_path / "legacy")
    assert set(legacy["scenarios"]) == set(kis_estimate.SCENARIOS)
    assert (
        Fraction(legacy["scenarios"]["reported_amounts"]["estimated_total_return"]["exact"])
        == Fraction(101, 100) ** 3 * Fraction(102, 100) - 1
    )
    assert (
        Fraction(legacy["scenarios"]["pre_split_dividends_x2"]["estimated_total_return"]["exact"])
        == Fraction(102, 100) ** 4 - 1
    )


def test_source_tampering_is_not_accepted_by_new_command(saved, tmp_path, capsys):
    manifest = saved / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    assert kis.main(["estimate-xlc-spy-2025", str(saved), "--output", str(tmp_path / "out")]) == 1
    assert "archive_integrity_failed" in capsys.readouterr().err


@pytest.mark.parametrize("saved", ["20250324"], indirect=True)
def test_in_scope_bad_ohlc_blocks_xlc_without_discarding_spy_result(saved, tmp_path, capsys):
    output = tmp_path / "estimates"
    assert kis.main(["estimate-xlc-spy-2025", str(saved), "--output", str(output)]) == 2
    assert "XLC: held" in capsys.readouterr().out
    result = json.loads(next(output.glob("*/result.json")).read_text())
    xlc = result["symbols"]["XLC"]["scenarios"]["reported_amounts"]
    assert xlc["estimated_total_return"] is None
    assert all(row["date"] < "2025-03-24" for row in xlc["rows"])
    assert any(
        b["code"] == "rejected_source_row" and b["date"] == "2025-03-24" for b in xlc["blockers"]
    )
    assert result["symbols"]["SPY"]["status"] == "estimated_not_validated"
