"""Synthetic accounting and offline archive tests; no real market-data certification."""

import json
import socket
from fractions import Fraction

import pytest

from macrolens import kis, kis_audit, kis_collect, kis_ledger
from macrolens.kis_client import KISClient
from macrolens.ledger import calculate
from macrolens.price_audit import sha
from macrolens.price_quality import AuditError, trading_sessions

pytest.importorskip("exchange_calendars")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Ledger must not use the network or authenticate")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(KISClient, "token", forbidden)
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)


def event(ex="2025-12-02", pay="2025-12-03", **changes):
    return {
        "ex_date": ex,
        "record_date": ex,
        "payable_date": pay,
        "dividend": "2",
        "short_term_gain": "0",
        "long_term_gain": "0",
        "share_basis": "per_ex_date_share",
        **changes,
    }


def ledger(prices=None, events=None, splits=(), start="2025-12-01", end="2025-12-04", ticker="SPY"):
    return calculate(
        ticker,
        start,
        end,
        prices or {"2025-12-01": "100", "2025-12-02": "98", "2025-12-03": "98", "2025-12-04": "98"},
        trading_sessions(start, "2027-01-31")[0],
        [event()] if events is None else events,
        splits,
    )


def val(row, field):
    return Fraction(row[field]["exact"])


def test_hand_calculation_ex_payment_next_session_reinvestment():
    r = ledger()
    assert r["status"] == "calculated_under_reviewed_inputs"
    initial, ex, pay, reinvest = r["rows"]
    assert val(initial, "shares") == 1 and val(initial, "external_contribution") == 100
    assert val(ex, "receivable") == 2 and val(ex, "cash") == 0
    assert val(pay, "cash") == 2 and val(pay, "receivable") == 0
    assert val(pay, "reinvested_shares") == 0  # Strictly AFTER the payment day.
    assert val(reinvest, "reinvested_shares") == Fraction(2, 98)
    assert all(val(row, "nav") == 100 for row in r["rows"])
    assert val(r, "total_return") == 0
    assert not r["validated_total_return"] and not r["strict_point_in_time"]


def test_two_for_one_split_preserves_value_without_distribution():
    r = ledger(
        {"2025-12-04": "100", "2025-12-05": "50"},
        events=[],
        splits=[{"date": "2025-12-05", "new_per_old": "2"}],
        start="2025-12-04",
        end="2025-12-05",
        ticker="XLK",
    )
    assert val(r["rows"][-1], "shares") == 2
    assert val(r["rows"][-1], "nav") == 100


def test_split_after_ex_does_not_double_receivable():
    prices = {d: "49" for d in trading_sessions("2025-12-03", "2025-12-09")[0]}
    prices.update({"2025-12-03": "100", "2025-12-04": "98"})
    r = ledger(
        prices,
        [event("2025-12-04", "2025-12-08")],
        [{"date": "2025-12-05", "new_per_old": "2"}],
        "2025-12-03",
        "2025-12-09",
        "XLK",
    )
    assert all(val(row, "nav") == 100 for row in r["rows"])
    split = next(row for row in r["rows"] if row["date"] == "2025-12-05")
    assert val(split, "shares") == 2 and val(split, "receivable") == 2
    assert val(r["rows"][-1], "reinvested_shares") == Fraction(2, 49)
    assert val(r["claims"][0], "amount") == 2


def test_weekend_payment_changes_cash_on_weekend_not_reinvestment():
    prices = {"2025-12-04": "100", "2025-12-05": "98", "2025-12-08": "98"}
    r = ledger(prices, [event("2025-12-05", "2025-12-06")], start="2025-12-04", end="2025-12-08")
    saturday = next(row for row in r["rows"] if row["date"] == "2025-12-06")
    assert not saturday["is_session"]
    assert saturday["valuation_close_date"] == "2025-12-05"
    assert val(saturday, "cash") == 2 and val(saturday, "reinvested_shares") == 0
    assert val(r["rows"][-1], "reinvested_shares") == Fraction(2, 98)


def test_holiday_payment_uses_next_exchange_session():
    r = ledger(
        {"2025-12-23": "100", "2025-12-24": "98", "2025-12-26": "98"},
        [event("2025-12-24", "2025-12-25")],
        start="2025-12-23",
        end="2025-12-26",
    )
    assert r["claims"][0]["reinvest_date"] == "2025-12-26"
    assert all(val(row, "nav") == 100 for row in r["rows"])


def test_missing_reinvestment_close_stops_without_postponement_or_fill():
    r = ledger(
        {"2025-12-01": "100", "2025-12-02": "98", "2025-12-03": "98", "2025-12-05": "99"},
        end="2025-12-05",
    )
    assert r["status"] == "held" and r["total_return"] is None
    assert r["rows"][-1]["date"] == "2025-12-03"
    assert r["claims"][0]["state"] == "cash"
    assert r["blockers"][0]["date"] == "2025-12-04"


def test_known_xlc_gap_stops_path_but_other_missing_is_not_whitelisted():
    r = ledger(
        {"2023-06-02": "100", "2023-06-06": "101"},
        events=[],
        start="2023-06-02",
        end="2023-06-07",
        ticker="XLC",
    )
    assert [b["code"] for b in r["blockers"]] == ["known_xlc_missing", "missing_or_invalid_close"]
    assert r["rows"][-1]["date"] == "2023-06-04"
    assert r["terminal_value"] is None


@pytest.mark.parametrize("field", ["share_basis", "short_term_gain", "long_term_gain", "dividend"])
def test_unknown_inputs_never_become_zero_or_full_period_returns(field):
    r = ledger(events=[event(**{field: None})])
    assert r["status"] == "held" and r["total_return"] is None
    assert len(r["rows"]) == 1
    assert any(b["input"] == field and b["date"] == "2025-12-02" for b in r["blockers"])


def test_terminal_receivable_and_unreinvested_cash_are_retained():
    r = ledger(events=[event(pay="2025-12-10")])
    assert val(r["rows"][-1], "receivable") == 2
    assert Fraction(r["terminal_value"]["exact"]) == 100
    assert val(r["rows"][-1], "nav") == 100
    assert r["claims"][0]["state"] == "receivable"
    r = ledger(end="2025-12-03")
    assert val(r["rows"][-1], "cash") == 2 and val(r["rows"][-1], "nav") == 100


def test_no_entitlement_on_start_ex_date_and_same_close_purchases():
    r = ledger(start="2025-12-02")
    assert not r["claims"] and val(r["rows"][-1], "shares") == 1
    r = ledger(
        {"2025-12-01": "100", "2025-12-02": "98", "2025-12-03": "98", "2025-12-04": "96"},
        events=[event(), event("2025-12-04", "2025-12-08")],
    )
    assert val(r["claims"][1], "entitled_shares") == 1
    assert val(r["rows"][-1], "receivable") == 2
    assert val(r["rows"][-1], "nav") == 100


def test_same_day_split_and_ex_uses_prior_holdings_in_new_share_basis():
    r = ledger(
        {"2025-12-04": "100", "2025-12-05": "49"},
        [event("2025-12-05", "2025-12-08", dividend="1")],
        [{"date": "2025-12-05", "new_per_old": "2"}],
        "2025-12-04",
        "2025-12-05",
    )
    assert val(r["claims"][0], "entitled_shares") == 2
    assert val(r["rows"][-1], "nav") == 100


def test_duplicate_events_and_non_session_start_are_held():
    assert ledger(events=[event(), event()])["status"] == "held"
    r = ledger(start="2025-11-30")
    assert not r["rows"] and r["blockers"][0]["code"] == "non_session_start"


def test_split_back_adjusted_detection_prevents_double_application():
    p = {"2025-12-04": "50", "2025-12-05": "50.5"}
    assert (
        kis_ledger.split_check(kis_ledger.SPLIT, p, p)["status"] == "appears_already_split_adjusted"
    )
    raw = {**p, "2025-12-04": "100"}
    assert kis_ledger.split_check(kis_ledger.SPLIT, raw, p)["status"] == (
        "consistent_with_as_traded_at_this_split"
    )


@pytest.fixture
def saved(tmp_path):
    from test_kis import FakeMarket, archive
    from test_price_audit import issuer_workbook

    a = archive(tmp_path / "source")
    kis_collect.collect(FakeMarket(), a)
    source = tmp_path / "issuer.xlsx"
    source.write_bytes(
        issuer_workbook(
            [
                [
                    "SYNTHETIC",
                    t,
                    "fake",
                    "12/08/2025",
                    "12/09/2025",
                    "12/10/2025",
                    "1.12345678901234567890",
                    "0",
                    "0",
                    "Q",
                ]
                for t in kis_collect.SAMPLE
            ]
        )
    )
    kis.attach_issuer(a, source)
    result, _ = kis_audit.audit_archive(a.folder, a.meta)
    kis_collect.finish(a, result)
    return a.folder


def reviewed_file(folder, tmp_path):
    template = json.loads((folder / "review-template.json").read_text())
    content = b"SYNTHETIC ONLY: explicitly constructed as-traded prices and event share bases."
    (tmp_path / "synthetic-evidence.txt").write_bytes(content)
    ref = {"file": "synthetic-evidence.txt", "sha256": sha(content), "reason": "Synthetic fixture"}
    for entry in template["symbols"].values():
        for key, (value, _) in kis_ledger.CLAIMS.items():
            entry[key].update(value=value, references=[ref])
        for claim in entry["event_share_basis"].values():
            claim.update(value="per_ex_date_share", references=[ref])
    path = tmp_path / "synthetic-review.json"
    path.write_text(json.dumps(template))
    return path


def test_offline_adapter_preserves_sources_and_deterministic_replay(saved, tmp_path):
    before = {p.name: p.read_bytes() for p in saved.iterdir()}
    kwargs = dict(saved=saved, output=tmp_path / "ledger", start="2025-12-01", end="2025-12-16")
    held, folder = kis_ledger.run(**kwargs)
    assert held["status"] == "held" and not held["symbols"]["SPY"]["rows"]
    review = reviewed_file(folder, tmp_path)
    first, path1 = kis_ledger.run(**kwargs, review_file=review)
    second, path2 = kis_ledger.run(**kwargs, review_file=review)
    assert first == second and path1 != path2
    replay, _ = kis_ledger.run(**kwargs, review_file=path1 / "review.json")
    assert replay == first
    assert first["status"] == "calculated_under_reviewed_inputs"
    assert all(not s["source_total_return_eligible"] for s in first["symbols"].values())
    assert not first["validated_total_return"]
    for t in kis_collect.SAMPLE:
        assert (path1 / f"{t}-ledger.csv").read_bytes() == (path2 / f"{t}-ledger.csv").read_bytes()
    inputs = json.loads((path1 / "inputs.json").read_text())
    assert inputs["XLK"]["events"][0]["dividend"] == "1.12345678901234567890"
    assert before == {p.name: p.read_bytes() for p in saved.iterdir()}


def test_review_does_not_override_detected_back_adjusted_prices(saved, tmp_path, monkeypatch):
    kwargs = dict(saved=saved, output=tmp_path / "ledger", start="2025-12-01", end="2025-12-16")
    _, folder = kis_ledger.run(**kwargs)
    review = reviewed_file(folder, tmp_path)
    original = kis_ledger.exact_prices

    def back_adjusted(source, normalized, modp):
        # Synthetic contamination: make MODP=0 identical to MODP=1 around the official split.
        return original(source, normalized, "1")

    monkeypatch.setattr(kis_ledger, "exact_prices", back_adjusted)
    result, _ = kis_ledger.run(**kwargs, review_file=review)
    xlk = result["symbols"]["XLK"]
    assert xlk["status"] == "held" and not xlk["rows"]
    assert any(b["code"] == "appears_already_split_adjusted" for b in xlk["blockers"])


@pytest.mark.parametrize("problem", ["row", "header", "pagination"])
def test_existing_source_failures_cannot_be_approved_by_review(
    saved, tmp_path, monkeypatch, problem
):
    kwargs = dict(saved=saved, output=tmp_path / "ledger", start="2025-12-01", end="2025-12-16")
    _, folder = kis_ledger.run(**kwargs)
    review = reviewed_file(folder, tmp_path)
    original = kis_ledger.audit_archive

    def failed_source(*args):
        result, normalized = original(*args)
        if problem == "row":
            # A second bad row must prevent cherry-picking the otherwise accepted same-day close.
            result["price_row_errors"].append(
                {
                    "ticker": "SPY",
                    "modp": "0",
                    "date": "2025-12-09",
                    "source_file": "synthetic-bad-page.json",
                    "reasons": ["low_above_open_or_close"],
                }
            )
        else:
            result["symbols"]["SPY"]["issues"].append(
                "가격 응답 종목 식별 미확인"
                if problem == "header"
                else "가격 페이지 진행 검증 실패: synthetic_conflict"
            )
        return result, normalized

    monkeypatch.setattr(kis_ledger, "audit_archive", failed_source)
    result, _ = kis_ledger.run(**kwargs, review_file=review)
    assert result["symbols"]["SPY"]["status"] == "held"
    assert result["symbols"]["SPY"]["total_return"] is None
    assert result["symbols"]["XLK"]["status"] == "calculated_under_reviewed_inputs"


def test_review_cannot_fill_blank_gains_and_is_bound_to_inputs(saved, tmp_path):
    from test_price_audit import issuer_workbook

    issuer = tmp_path / "blank.xlsx"
    issuer.write_bytes(
        issuer_workbook(
            [
                [
                    "SYNTHETIC",
                    t,
                    "fake",
                    "12/08/2025",
                    "12/09/2025",
                    "12/10/2025",
                    "1",
                    "",
                    "0",
                    "Q",
                ]
                for t in kis_collect.SAMPLE
            ]
        )
    )
    kwargs = dict(saved=saved, output=tmp_path / "ledger", start="2025-12-01", end="2025-12-16")
    _, folder = kis_ledger.run(**kwargs, issuer_file=issuer)
    review = reviewed_file(folder, tmp_path)
    result, _ = kis_ledger.run(**kwargs, issuer_file=issuer, review_file=review)
    assert all(
        s["status"] == "held" and any(b["input"] == "short_term_gain" for b in s["blockers"])
        for s in result["symbols"].values()
    )
    with pytest.raises(AuditError, match="해시 불일치"):
        kis_ledger.run(**kwargs, review_file=review)  # Different issuer input.
    (tmp_path / "synthetic-evidence.txt").write_text("modified")
    with pytest.raises(AuditError, match="해시 불일치"):
        kis_ledger.run(**kwargs, issuer_file=issuer, review_file=review)


def test_cli_offline_status_and_archive_integrity(saved, tmp_path, capsys):
    assert (
        kis.main(
            [
                "ledger",
                str(saved),
                "--start",
                "2025-12-01",
                "--end",
                "2025-12-16",
                "--output",
                str(tmp_path / "ledger"),
            ]
        )
        == 2
    )
    assert "offline ledger: held / synthetic" in capsys.readouterr().out
    manifest = saved / "manifest.json"
    manifest.write_text(manifest.read_text() + " ")
    assert kis.main(["ledger", str(saved), "--output", str(tmp_path / "ledger")]) == 1
    assert "archive_integrity_failed" in capsys.readouterr().err


def test_period_specific_no_entitlement_does_not_require_past_distribution_basis():
    r = ledger(events=[event(share_basis="unknown", short_term_gain=None)], start="2025-12-03")
    assert r["status"] == "calculated_under_reviewed_inputs" and not r["claims"]


def test_rational_reinvestment_does_not_round_before_next_event():
    r = ledger(
        {
            "2025-12-01": "101",
            "2025-12-02": "99",
            "2025-12-03": "99",
            "2025-12-04": "99",
            "2025-12-05": "97",
        },
        [event(), event("2025-12-05", "2025-12-08")],
        end="2025-12-05",
    )
    assert val(r["claims"][1], "entitled_shares") == Fraction(101, 99)
    assert val(r["rows"][-1], "receivable") == Fraction(202, 99)
    assert val(r["rows"][-1], "nav") == 101
