"""Synthetic estimate tests. No certification of actual XLK distributions/prices."""

import copy
import json
import socket
from fractions import Fraction

import pytest

from macrolens import kis, kis_audit, kis_collect, kis_estimate, kis_ledger
from macrolens.kis_client import KISClient
from macrolens.ledger import blocker
from macrolens.price_quality import AuditError, trading_sessions


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Estimates must not authenticate or download")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(KISClient, "token", forbidden)
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)


@pytest.fixture
def candidates():
    events = [
        dict(
            cusip=cusip,
            ex_date=ex,
            record_date=record,
            payable_date=pay,
            dividend="2",
            short_term_gain="0",
            long_term_gain="0",
            share_basis="unknown",
            source_amounts={"dividend": "2", "short_term_gain": None, "long_term_gain": None},
        )
        for cusip, ex, record, pay in sorted(kis_estimate.EXPECTED_EVENTS)
    ]
    strict = {
        "ticker": "XLK",
        "start": kis_estimate.START,
        "end_inclusive": kis_estimate.END,
        "status": "held",
        "source_price_status": "conditional",
        "source_total_return_eligible": False,
        "price_contract_evidence": {"status": "matched_archived_requests"},
        "split_diagnostics": [
            {"date": "2025-12-05", "status": "consistent_with_as_traded_at_this_split"}
        ],
        "blockers": [
            blocker("XLK", kis_estimate.START, k, "unknown_" + k, "SYNTHETIC unresolved basis")
            for k in kis_ledger.CLAIMS
        ]
        + [
            blocker("XLK", e["ex_date"], "share_basis", "unknown_share_basis", "SYNTHETIC unknown")
            for e in events
        ],
    }
    sessions, _ = trading_sessions(kis_estimate.START, kis_estimate.END)
    inputs = {
        "events": events,
        "prices_modp0": {d: "200" if d < "2025-12-05" else "100" for d in sessions},
        "splits": [dict(kis_ledger.SPLIT)],
    }
    return strict, inputs


def test_two_basis_scenarios_reuse_cash_rules_without_double_split(candidates):
    strict, inputs = candidates
    before = copy.deepcopy(candidates)
    result, effective = kis_estimate.scenarios(strict, inputs)
    assert candidates == before
    assert result["status"] == "estimated_not_validated"
    a, b = result["scenarios"].values()
    # Synthetic fixed prices: each payout reinvests before the next distribution.
    expected_a = Fraction(101, 100) ** 3 * Fraction(102, 100) - 1
    expected_b = Fraction(102, 100) ** 4 - 1
    assert Fraction(a["estimated_total_return"]["exact"]) == expected_a
    assert Fraction(b["estimated_total_return"]["exact"]) == expected_b
    assert (
        Fraction(result["scenario_difference_percentage_points"]["exact"])
        == (expected_b - expected_a) * 100
    )
    assert not result["validated_total_return"] and not result["total_return_eligible"]
    assert result["strict_ledger_status"] == "held"
    assert result["source_price_status"] == "conditional"
    assert len(result["assumed_unresolved_conditions"]) == 7
    for item in (a, b):
        assert "total_return" not in item  # Only the explicitly named estimate is exported.
        assert item["claims"][-1]["reinvest_date"] == "2025-12-26"
        split_day = next(r for r in item["rows"] if r["date"] == "2025-12-05")
        assert Fraction(split_day["shares"]["exact"]) == 2 * Fraction(
            split_day["shares_before"]["exact"]
        )
        assert len(item["claims"]) == 4
    assert [e["dividend"] for e in effective["pre_split_dividends_x2"]] == ["4", "4", "4", "2"]
    assert effective["pre_split_dividends_x2"][0]["source_amounts"]["short_term_gain"] is None


@pytest.mark.parametrize("change", ["ticker", "year"])
def test_assumptions_cannot_spill_into_other_etfs_or_years(candidates, change):
    strict, inputs = candidates
    strict["ticker" if change == "ticker" else "start"] = (
        "XLC" if change == "ticker" else "2024-01-02"
    )
    with pytest.raises(AuditError, match="XLK 2025"):
        kis_estimate.scenarios(strict, inputs)


@pytest.mark.parametrize("change", ["missing", "duplicate", "cusip", "date"])
def test_estimation_requires_the_exact_known_four_events(candidates, change):
    strict, inputs = candidates
    if change == "missing":
        inputs["events"].pop()
    elif change == "duplicate":
        inputs["events"].append(copy.deepcopy(inputs["events"][0]))
    else:
        inputs["events"][0]["cusip" if change == "cusip" else "payable_date"] = (
            "OTHER" if change == "cusip" else "2025-03-27"
        )
    result, _ = kis_estimate.scenarios(strict, inputs)
    assert result["status"] == "held" and result["scenario_difference_percentage_points"] is None
    assert all(not s["rows"] for s in result["scenarios"].values())


def test_unresolved_amounts_and_missing_prices_are_not_assumed_away(candidates):
    strict, inputs = candidates
    inputs["events"][0]["short_term_gain"] = None
    del inputs["prices_modp0"]["2025-03-27"]
    result, _ = kis_estimate.scenarios(strict, inputs)
    for item in result["scenarios"].values():
        assert item["status"] == "held" and item["estimated_total_return"] is None
        assert {b["code"] for b in item["blockers"]} == {
            "unknown_distribution_amount",
            "missing_or_invalid_close",
        }
        assert item["rows"][-1]["date"] == "2025-03-23"
    assert result["scenario_difference_percentage_points"] is None


@pytest.mark.parametrize(
    "code", ["rejected_source_row", "identity_unverified", "distribution_evidence_conflict"]
)
def test_material_source_errors_cannot_be_relaxed(candidates, code):
    strict, inputs = candidates
    strict["blockers"].append(blocker("XLK", kis_estimate.START, "source", code, "SYNTHETIC error"))
    result, _ = kis_estimate.scenarios(strict, inputs)
    assert all(s["status"] == "held" and not s["rows"] for s in result["scenarios"].values())
    assert all(s["blockers"][0]["code"] == code for s in result["scenarios"].values())


@pytest.mark.parametrize("change", ["split", "request"])
def test_price_contradictions_still_block_estimates(candidates, change):
    strict, inputs = candidates
    if change == "split":
        strict["split_diagnostics"][0]["status"] = "appears_already_split_adjusted"
    else:
        strict["price_contract_evidence"]["status"] = "request_not_matched"
    result, _ = kis_estimate.scenarios(strict, inputs)
    assert result["status"] == "held"
    assert all(s["estimated_total_return"] is None for s in result["scenarios"].values())


def test_offline_cli_keeps_strict_result_sources_and_deterministic_estimates(tmp_path, capsys):
    from test_kis import FakeMarket, archive
    from test_price_audit import issuer_workbook

    saved = archive(tmp_path / "saved")
    kis_collect.collect(FakeMarket(), saved)
    issuer = tmp_path / "issuer.xlsx"
    issuer.write_bytes(
        issuer_workbook(
            [
                [
                    "SYNTHETIC",
                    "XLK",
                    c,
                    ex[5:7] + "/" + ex[8:] + "/2025",
                    rec[5:7] + "/" + rec[8:] + "/2025",
                    pay[5:7] + "/" + pay[8:] + "/2025",
                    "2",
                    "",
                    "",
                    "Q",
                ]
                for c, ex, rec, pay in sorted(kis_estimate.EXPECTED_EVENTS)
            ]
        )
    )
    kis.attach_issuer(saved, issuer)
    audit, _ = kis_audit.audit_archive(saved.folder, saved.meta)
    kis_collect.finish(saved, audit)
    before = {p.name: p.read_bytes() for p in saved.folder.iterdir()}
    output = tmp_path / "estimates"
    assert kis.main(["estimate-xlk-2025", str(saved.folder), "--output", str(output)]) == 0
    assert "estimated_not_validated / synthetic" in capsys.readouterr().out
    first_path = next(output.glob("*/result.json"))
    first = json.loads(first_path.read_text())
    second, folder = kis_estimate.run(saved.folder, output)
    assert first == second
    strict = json.loads((folder / "strict-result.json").read_text())
    assert all(s["status"] == "held" for s in strict["symbols"].values())
    assert not strict["symbols"]["XLK"]["rows"]
    assert any(b["code"] == "unknown_share_basis" for b in strict["symbols"]["XLK"]["blockers"])
    for name in kis_estimate.SCENARIOS:
        filename = name + "-ledger.csv"
        assert (first_path.parent / filename).read_bytes() == (folder / filename).read_bytes()
    assert before == {p.name: p.read_bytes() for p in saved.folder.iterdir()}
    assert not second["source_total_return_eligible"]
    assert second["distribution_evidence"]["catalog_id"]  # Same scoped official zero interpreters.
    inputs = json.loads((folder / "strict-inputs.json").read_text())["XLK"]
    assert inputs["events"][0]["share_basis"] == "unknown"
    assert inputs["events"][0]["source_amounts"]["short_term_gain"] is None
    assert "가정에 따른" in (folder / "report.md").read_text()
    # Explicit estimates still return held/exit 2 for a missing known distribution.
    incomplete = tmp_path / "incomplete.xlsx"
    incomplete.write_bytes(issuer_workbook([]))
    assert (
        kis.main(
            [
                "estimate-xlk-2025",
                str(saved.folder),
                "--issuer-file",
                str(incomplete),
                "--output",
                str(tmp_path / "incomplete-estimate"),
            ]
        )
        == 2
    )
    assert "XLK 2025 추정: held / synthetic" in capsys.readouterr().out
