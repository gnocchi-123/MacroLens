"""Synthetic scoped evidence, not certification of real issuer or KIS data."""

import copy
import json
import socket
from fractions import Fraction

import pytest
from test_ledger import reviewed_file
from test_ledger import saved as saved
from test_price_audit import issuer_workbook

from macrolens import kis, kis_ledger
from macrolens.distribution_evidence import interpret, inventory, load_catalog, summary
from macrolens.kis_client import KISClient


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Distribution evidence must be offline")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(KISClient, "token", fail)
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)


def event(**extra):
    return {
        "cusip": "SYNTHETIC",
        "ex_date": "2025-09-22",
        "record_date": "2025-09-22",
        "payable_date": "2025-09-24",
        "dividend": "1",
        "short_term_gain": None,
        "long_term_gain": None,
        "share_basis": "unknown",
        "source_amounts": {"dividend": "1", "short_term_gain": None, "long_term_gain": None},
        **extra,
    }


def evidence(ticker="XLK", source=None):
    source = source or event()
    doc = {
        "kind": "final_event_tax_summary",
        "tax_year": 2025,
        "start": "2025-01-01",
        "end": "2025-12-31",
        "title": "SYNTHETIC event statement",
        "sheet": "Test",
        "url": "https://example.test/synthetic",
        "sha256": "0" * 64,
        "checked_at": "2026-11-08",
        "fields": {
            "short_term_gain": {"column": "O", "role": "tax_component_in_total"},
            "long_term_gain": {"column": "V", "role": "tax_component_in_total"},
        },
    }
    claim = {
        "id": "synthetic-zero",
        "ticker": ticker,
        **{k: source[k] for k in ("cusip", "ex_date", "record_date", "payable_date")},
        "scope": "exact_event",
        "document": "test",
        "estimated": False,
        "row": 14,
        "cells": {"short_term_gain": "O14", "long_term_gain": "V14"},
        "values": {"short_term_gain": "0", "long_term_gain": "0"},
    }
    return {
        "schema_version": 1,
        "id": "synthetic-only",
        "documents": {"test": doc},
        "claims": [claim],
    }


def test_explicit_event_zeros_are_a_separate_interpretation():
    raw = [event()]
    before = copy.deepcopy(raw)
    effective, report = interpret("XLK", raw, evidence())
    assert raw == before
    assert effective[0]["source_amounts"]["short_term_gain"] is None
    assert effective[0]["short_term_gain"] == effective[0]["long_term_gain"] == "0"
    assert effective[0]["share_basis"] == "unknown"
    assert report[0]["fields"]["short_term_gain"]["claims"] == ["synthetic-zero"]
    assert summary(report)["2025"]["resolved_blank_fields"] == 2


@pytest.mark.parametrize("year", ["2020", "2024", "2026"])
def test_2025_document_never_resolves_other_year_even_if_claim_changed(year):
    source = event(
        **{k: event()[k].replace("2025", year) for k in ("ex_date", "record_date", "payable_date")}
    )
    effective, report = interpret("XLK", [source], evidence(source=source))
    assert effective[0]["short_term_gain"] is None
    assert report[0]["fields"]["long_term_gain"]["rejected"][0]["reason"] == "different_tax_year"


@pytest.mark.parametrize(
    "key,value",
    [
        ("cusip", "OTHER"),
        ("ex_date", "2025-09-23"),
        ("record_date", "2025-09-23"),
        ("payable_date", "2025-09-25"),
    ],
)
def test_exact_event_identity_is_required(key, value):
    effective, _ = interpret("XLK", [event(**{key: value})], evidence())
    assert effective[0]["short_term_gain"] is None


def test_2025_xlk_facts_do_not_apply_to_other_etfs():
    effective, _ = interpret("XLC", [event()], evidence())
    assert effective[0]["long_term_gain"] is None


def test_long_term_only_source_does_not_resolve_short_term():
    c = evidence()
    del c["documents"]["test"]["fields"]["short_term_gain"]
    effective, report = interpret("XLK", [event()], c)
    assert effective[0]["long_term_gain"] == "0"
    assert effective[0]["short_term_gain"] is None
    assert report[0]["fields"]["short_term_gain"]["rejected"]


@pytest.mark.parametrize("change", ["estimated", "annual", "wrong_column", "preliminary"])
def test_estimates_annual_totals_and_wrong_columns_do_not_resolve_blanks(change):
    c = evidence()
    if change == "estimated":
        c["claims"][0]["estimated"] = True
    elif change == "annual":
        c["claims"][0]["scope"] = "annual_total"
    elif change == "wrong_column":
        c["claims"][0]["cells"]["short_term_gain"] = "V14"
    else:
        c["documents"]["test"]["kind"] = "estimated_capital_gains"
    effective, _ = interpret("XLK", [event()], c)
    assert effective[0]["short_term_gain"] is None


def test_conflicting_documents_leave_missing_input_unknown():
    c = evidence()
    other = copy.deepcopy(c["claims"][0])
    other.update(id="conflicting-doc", document="other")
    other["values"]["short_term_gain"] = "2"
    c["documents"]["other"] = copy.deepcopy(c["documents"]["test"])
    c["claims"].append(other)
    effective, report = interpret("XLK", [event()], c)
    assert effective[0]["short_term_gain"] is None
    assert report[0]["fields"]["short_term_gain"]["status"] == "conflict"


def test_source_amount_is_never_overwritten_to_make_evidence_agree():
    effective, report = interpret("XLK", [event(short_term_gain="0.01")], evidence())
    assert effective[0]["short_term_gain"] == "0.01"
    assert report[0]["fields"]["short_term_gain"]["status"] == "conflict"


def test_nonzero_tax_classification_is_not_added_to_total_cash():
    c = evidence()
    c["claims"][0]["values"]["short_term_gain"] = "0.25"
    effective, report = interpret("XLK", [event(dividend="1")], c)
    assert effective[0]["dividend"] == "1" and effective[0]["short_term_gain"] is None
    assert report[0]["fields"]["short_term_gain"]["status"] == "unsupported_nonzero_tax_component"
    assert not report[0]["fields"]["short_term_gain"]["additional_cash_from_tax_classification"]


def test_duplicate_source_events_are_not_silently_resolved():
    effective, _ = interpret("XLK", [event(), event()], evidence())
    assert all(e["short_term_gain"] is None for e in effective)


def test_repeated_consistent_evidence_is_one_field_interpretation():
    c = evidence()
    c["claims"].append({**copy.deepcopy(c["claims"][0]), "id": "second-independent-document"})
    effective, report = interpret("XLK", [event()], c)
    assert len(effective) == 1 and effective[0]["dividend"] == "1"
    assert summary(report)["2025"]["resolved_blank_fields"] == 2


def test_catalog_has_only_2025_exact_event_capital_zeros():
    # Registry invariants, not a claim that synthetic tests verify source documents.
    c, _ = load_catalog()
    assert len(c["claims"]) == 12
    assert {x["ticker"] for x in c["claims"]} == {"XLC", "XLK", "SPY"}
    assert all(x["ex_date"].startswith("2025-") for x in c["claims"])
    assert all(set(x["values"].values()) == {"0"} for x in c["claims"])


def test_inventory_cli_preserves_raw_and_is_not_a_price_ledger(tmp_path, capsys):
    raw = issuer_workbook(
        [
            [
                "SYNTHETIC",
                "XLK",
                "SYNTHETIC",
                "09/22/2025",
                "09/22/2025",
                "09/24/2025",
                "1",
                "",
                "",
                "Q",
            ]
        ]
    )
    file = tmp_path / "source.xlsx"
    file.write_bytes(raw)
    proof = tmp_path / "proof.json"
    proof.write_text(json.dumps(evidence()))
    args = [
        "distribution-evidence",
        "--issuer-file",
        str(file),
        "--evidence-file",
        str(proof),
        "--output",
        str(tmp_path / "out"),
    ]
    assert kis.main(args) == 0
    assert "XLK: 공식 사건 근거로 해석한 빈 셀 2개" in capsys.readouterr().out
    first = next((tmp_path / "out").glob("*/evidence.json"))
    replay, path = inventory(file, tmp_path / "out", "2020-06-01", "2026-09-30", proof)
    assert replay == json.loads(first.read_text())
    assert not replay["actual_price_ledger_run"] and not replay["total_return_eligible"]
    assert file.read_bytes() == (path / "issuer-distributions.xlsx").read_bytes() == raw


def test_ledger_applies_only_interpretation_and_does_not_double_split_or_dividend(saved, tmp_path):
    source = tmp_path / "source.xlsx"
    source.write_bytes(
        issuer_workbook(
            [
                [
                    "SYNTHETIC",
                    t,
                    "SYNTHETIC",
                    "12/08/2025",
                    "12/09/2025",
                    "12/10/2025",
                    "1",
                    "",
                    "",
                    "Q",
                ]
                for t in ("XLC", "XLK", "SPY")
            ]
        )
    )
    doc_event = event(ex_date="2025-12-08", record_date="2025-12-09", payable_date="2025-12-10")
    c = evidence(source=doc_event)
    c["claims"] = []
    for ticker in ("XLC", "XLK", "SPY"):
        claim = evidence(ticker, doc_event)["claims"][0]
        claim["id"] = ticker + "-synthetic"
        c["claims"].append(claim)
    proof = tmp_path / "proof.json"
    proof.write_text(json.dumps(c))
    kwargs = dict(
        saved=saved,
        output=tmp_path / "ledger",
        start="2025-12-01",
        end="2025-12-16",
        issuer_file=source,
        distribution_evidence=proof,
    )
    original = {p.name: p.read_bytes() for p in saved.iterdir()}
    _, folder = kis_ledger.run(**kwargs)
    reviewed = reviewed_file(folder, tmp_path)
    result, folder = kis_ledger.run(**kwargs, review_file=reviewed)
    xlk = result["symbols"]["XLK"]
    assert xlk["status"] == "calculated_under_reviewed_inputs"
    assert len(xlk["claims"]) == 1 and Fraction(xlk["claims"][0]["amount"]["exact"]) == 2
    assert Fraction(xlk["rows"][-1]["shares"]["exact"]) == Fraction("2.02")
    inputs = json.loads((folder / "inputs.json").read_text())["XLK"]
    assert inputs["source_events"][0]["short_term_gain"] is None
    assert inputs["events"][0]["short_term_gain"] == "0"
    assert not xlk["source_total_return_eligible"]
    assert original == {p.name: p.read_bytes() for p in saved.iterdir()}


def test_evidence_conflict_keeps_ledger_held_despite_review(saved, tmp_path):
    source = tmp_path / "source.xlsx"
    source.write_bytes(
        issuer_workbook(
            [
                [
                    "SYNTHETIC",
                    t,
                    "SYNTHETIC",
                    "12/08/2025",
                    "12/09/2025",
                    "12/10/2025",
                    "1",
                    "1",
                    "0",
                    "Q",
                ]
                for t in ("XLC", "XLK", "SPY")
            ]
        )
    )
    c = evidence(
        source=event(ex_date="2025-12-08", record_date="2025-12-09", payable_date="2025-12-10")
    )
    proof = tmp_path / "proof.json"
    proof.write_text(json.dumps(c))
    kwargs = dict(
        saved=saved,
        output=tmp_path / "ledger",
        start="2025-12-01",
        end="2025-12-16",
        issuer_file=source,
        distribution_evidence=proof,
    )
    _, folder = kis_ledger.run(**kwargs)
    reviewed = reviewed_file(folder, tmp_path)
    result, _ = kis_ledger.run(**kwargs, review_file=reviewed)
    xlk = result["symbols"]["XLK"]
    assert xlk["status"] == "held" and xlk["total_return"] is None
    assert any(b["code"] == "distribution_evidence_conflict" for b in xlk["blockers"])
