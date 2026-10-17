"""All prices and reference contents here are SYNTHETIC; no provider downloads."""

import copy
import csv
import io
import json

import pytest

from macrolens import price_audit as audit
from macrolens.price_quality import (
    COLUMNS,
    AuditError,
    audit_symbol,
    event_returns,
    market_date,
    trading_sessions,
)

pytest.importorskip(
    "exchange_calendars", reason="M4 검수 의존성: uv sync --locked --extra price-audit"
)


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return stream.getvalue().encode()


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    meta = audit.template()
    meta.update(evidence_kind="synthetic", retrieved_at="2026-10-01T00:00:00+00:00")
    meta["library"]["version"] = "synthetic-test-export-v1"
    meta["request"].update(
        start="2024-12-01",
        evaluation_start="2025-12-01",
        end_inclusive="2025-12-10",
        provider_end="2025-12-11",
    )
    meta["adjustment"] = {
        "close": "unadjusted",
        "adj_close": "dividend_and_split_adjusted",
        "dividends": "same_share_basis_as_close",
        "method": "backward_dividend_factor",
        "definition_reference": "reference",
    }
    meta["calendar_reference"] = "reference"
    reference = b"SYNTHETIC source for unit tests only; not an issuer publication"
    (root / "reference.txt").write_bytes(reference)
    meta["references"] = {
        "reference": {
            "file": "reference.txt",
            "sha256": audit.sha(reference),
            "source_url": "https://www.ssga.com/synthetic-test-reference",
            "reviewed_at": "2026-10-01T00:00:00+00:00",
        }
    }
    meta["symbols"] = {"XLK": meta["symbols"]["XLK"]}
    info = meta["symbols"]["XLK"]
    info.update(
        currency="USD",
        instrument_type="ETF",
        exchange="PCX",
        inception_date="1998-12-16",
        listing_date="1998-12-22",
        identity_reference="reference",
        dividend_status="reported",
        split_status="reported",
        capital_gains_status="explicit_none_verified",
        capital_gains_reference="reference",
        official_events=[
            {"kind": "split", "date": "2025-12-05", "value": 2, "reference": "reference"},
            {
                "kind": "dividend",
                "date": "2025-12-08",
                "value": 1,
                "basis": "same_share_basis_as_close",
                "reference": "reference",
            },
        ],
    )
    days, _ = trading_sessions("2024-12-01", "2025-12-10")
    rows = []
    for day in days:
        close = 200 if day < "2025-12-05" else 100 if day < "2025-12-08" else 99
        rows.append(
            [
                day,
                close,
                close,
                close,
                close,
                99,
                1000,
                1 if day == "2025-12-08" else 0,
                2 if day == "2025-12-05" else 0,
            ]
        )
    (root / "XLK.csv").write_bytes(csv_bytes(rows))
    (root / "manifest.json").write_text(json.dumps(meta))
    return root, meta, rows


def quality(meta, rows):
    return audit_symbol(
        csv_bytes(rows),
        meta["symbols"]["XLK"],
        meta["request"],
        meta["adjustment"],
        set(meta["references"]),
    )


def test_holidays_special_closure_and_end_day_included():
    days, info = trading_sessions("2025-01-08", "2025-01-13")
    assert days == ["2025-01-08", "2025-01-10", "2025-01-13"]  # Carter mourning + weekend
    assert info["version"] == "4.13.2"
    days, _ = trading_sessions("2026-09-28", "2026-09-30")
    assert days[-1] == "2026-09-30"
    assert "2025-12-25" not in trading_sessions("2025-12-24", "2025-12-26")[0]


def test_timezone_is_exchange_local_date():
    assert market_date("2026-09-30T00:30:00+00:00", "America/New_York") == "2026-09-29"
    with pytest.raises(AuditError):
        market_date("2026-09-30T00:30:00", "America/New_York")


def test_raw_split_and_dividend_local_returns(bundle):
    _, meta, rows = bundle
    result = quality(meta, rows)
    assert result["status"] == "usable"  # technical result with synthetic reference input
    split, dividend = result["event_windows"]
    assert split["price_return"] == 0  # 200 -> 100 with 2 new shares, no fake 50% loss
    assert dividend["cash_reinvest_at_ex_date_close_approx"] == 0
    assert dividend["vendor_adjusted_return"] == 0
    assert not dividend["cash_added_to_adjusted_series"]


def test_split_adjusted_close_does_not_apply_split_twice(bundle):
    _, meta, rows = bundle
    meta["adjustment"]["close"] = "split_adjusted"
    for row in rows:
        if row[0] < "2025-12-05":
            row[1:5] = [100] * 4
    result = quality(meta, rows)
    assert result["status"] == "usable"
    assert result["event_windows"][0]["price_return"] == 0


def test_reinvestment_approx_and_backward_method_are_distinct():
    previous = {"Close": 100, "Adj Close": 99}
    current = {"Close": 101, "Adj Close": 101, "Dividends": 1, "Stock Splits": 0}
    result = event_returns(previous, current, "split_adjusted", "same_share_basis_as_close")
    assert result["cash_reinvest_at_ex_date_close_approx"] == pytest.approx(0.02)
    assert result["backward_dividend_factor_return"] == pytest.approx(101 / 99 - 1)
    assert result["vendor_adjusted_return"] == result["backward_dividend_factor_return"]


@pytest.mark.parametrize(
    "problem,expected",
    [
        ("empty", "빈 가격"),
        ("duplicate", "중복"),
        ("unsorted", "정렬"),
        ("missing", "거래일 가격 누락"),
        ("holiday", "휴장일"),
        ("nan", "결측"),
        ("negative", "0 이하"),
        ("inf", "비유한"),
        ("ohlc", "OHLC"),
    ],
)
def test_price_quality_failures_are_not_repaired(bundle, problem, expected):
    _, meta, rows = bundle
    if problem == "empty":
        rows.clear()
    elif problem == "duplicate":
        rows.insert(1, rows[0].copy())
    elif problem == "unsorted":
        rows.reverse()
    elif problem == "missing":
        rows.pop(2)
    elif problem == "holiday":
        rows.append(["2025-12-25", 99, 99, 99, 99, 99, 0, 0, 0])
    else:
        rows[0][2 if problem == "ohlc" else 4] = {
            "nan": "NaN",
            "negative": -1,
            "inf": "inf",
            "ohlc": 1,
        }[problem]
    original = copy.deepcopy(rows)
    result = quality(meta, rows)
    assert result["status"] == "held"
    assert any(expected in reason for reason in result["issues"])
    assert rows == original


def test_unknown_actions_not_confused_with_no_events(bundle):
    _, meta, rows = bundle
    info = meta["symbols"]["XLK"]
    info["split_status"] = "unknown"
    for row in rows:
        row[-1] = 0
    result = quality(meta, rows)
    assert result["split_status"]["positive_event_rows"] == 0
    assert result["status"] == "held"
    assert any("자료 유무 미확인" in reason for reason in result["issues"])


def test_definition_unknown_holds_even_if_numbers_are_finite(bundle):
    _, meta, rows = bundle
    meta["adjustment"]["close"] = "unknown"
    assert quality(meta, rows)["status"] == "held"


def test_official_dividend_basis_difference_not_called_price_defect(bundle):
    _, meta, rows = bundle
    official = meta["symbols"]["XLK"]["official_events"][1]
    official.update(value=2, basis="original_pre_split_share")
    result = quality(meta, rows)
    assert result["status"] == "conditional"
    assert not result["issues"]
    assert any("기준 차이" in x for x in result["warnings"])


def test_official_event_mismatch_and_wrong_adjustment_hold(bundle):
    _, meta, rows = bundle
    meta["symbols"]["XLK"]["official_events"][0]["value"] = 3
    result = quality(meta, rows)
    assert result["status"] == "held"
    assert any("공식 사건과 공급자 불일치" in x for x in result["issues"])
    meta["symbols"]["XLK"]["official_events"][0]["value"] = 2
    rows[-3][5] *= 2
    result = quality(meta, rows)
    assert any("조정률 차이" in x for x in result["issues"])


def test_endpoint_options_and_warmup_are_explicit(bundle):
    _, meta, _ = bundle
    audit.inspect_manifest(meta)
    meta["request"]["provider_end"] = meta["request"]["end_inclusive"]
    with pytest.raises(AuditError, match="배타적"):
        audit.inspect_manifest(meta)
    meta["request"]["provider_end"] = "2025-12-11"
    meta["request"]["options"]["repair"] = True
    with pytest.raises(AuditError, match="옵션"):
        audit.inspect_manifest(meta)
    meta["request"]["options"]["repair"] = False
    meta["request"]["start"] = "2025-01-01"
    with pytest.raises(AuditError, match="12개월"):
        audit.inspect_manifest(meta)


def test_archive_and_recheck_offline_without_overwriting(bundle, tmp_path, monkeypatch):
    root, _, _ = bundle
    output = tmp_path / "audits"
    result, saved = audit.audit_bundle(root / "manifest.json", output)
    assert result["status"] == "synthetic_only"
    assert result["technical_status"] == "usable"
    assert not result["expansion_eligible"]
    assert (saved / "XLK.csv").read_bytes() == (root / "XLK.csv").read_bytes()
    original = {p.name: p.read_bytes() for p in saved.iterdir()}

    def no_network(*args, **kwargs):
        pytest.fail("offline recheck must not contact a provider")

    monkeypatch.setattr(audit, "urlopen", no_network)
    again, saved_again = audit.recheck(saved, output)
    assert saved_again != saved
    assert again["recheck"]["matched"]
    assert {p.name: p.read_bytes() for p in saved.iterdir()} == original
    (saved / "XLK.csv").write_bytes(b"corrupt")
    with pytest.raises(AuditError, match="무결성"):
        audit.recheck(saved, output)


def test_usage_and_secret_fail_before_copying_anything(bundle, tmp_path):
    root, meta, _ = bundle
    meta["evidence_kind"] = "real"
    (root / "manifest.json").write_text(json.dumps(meta))
    with pytest.raises(AuditError, match="권한 미확인"):
        audit.audit_bundle(root / "manifest.json", tmp_path / "audits")
    assert not (tmp_path / "audits").exists()
    meta["api_key"] = "SYNTHETIC_SECRET_FOR_TEST"
    with pytest.raises(AuditError, match="비밀값"):
        audit.inspect_manifest(meta)
    del meta["api_key"]
    meta["source_url"] = "https://example.test/data?api_key=SYNTHETIC_SECRET_FOR_TEST"
    with pytest.raises(AuditError, match="URL"):
        audit.inspect_manifest(meta)


def test_probe_classifies_proxy_denial_without_fake_prices(tmp_path, monkeypatch):
    from urllib.error import URLError

    def denied(*args, **kwargs):
        raise URLError("Tunnel connection failed: 403 Forbidden")

    monkeypatch.setattr(audit, "urlopen", denied)
    result, folder = audit.source_probe(tmp_path)
    assert result["actual_price_rows"] == 0
    assert all(item["status"] == "proxy_connect_403" for item in result["source_access"].values())
    assert result["status"] == "held"
    assert (folder / "COMPLETE").exists()


def test_cli_template_does_not_overwrite(tmp_path):
    target = tmp_path / "manifest.json"
    assert audit.main(["template", "--output", str(target)]) == 0
    original = target.read_bytes()
    assert audit.main(["template", "--output", str(target)]) == 1
    assert target.read_bytes() == original


def test_path_escape_and_reference_tampering_rejected(bundle, tmp_path):
    root, meta, _ = bundle
    with pytest.raises(AuditError):
        audit.safe_input(root, "../not_allowed.txt")
    meta["references"]["reference"]["sha256"] = "0" * 64
    (root / "manifest.json").write_text(json.dumps(meta))
    with pytest.raises(AuditError, match="대조 근거"):
        audit.audit_bundle(root / "manifest.json", tmp_path / "audits")


def test_m3_old_indpro_units_hold_without_changing_frozen_model():
    from datetime import datetime

    from macrolens.features import calculate_feature
    from macrolens.ingest import DataError, sample_snapshot
    from macrolens.research import FROZEN_CONFIG_HASH, research_configuration
    from macrolens.storage import digest

    config = research_configuration()
    data = sample_snapshot()
    data["series"]["INDPRO"]["metadata"]["units"] = "Index 2012=100"
    with pytest.raises(DataError, match="메타데이터 불일치: units"):
        calculate_feature(
            data["series"]["INDPRO"],
            config["indicators"]["INDPRO"],
            datetime.fromisoformat(data["as_of"]),
        )
    assert digest(config) == FROZEN_CONFIG_HASH


def test_unreported_adjustment_change_is_held(bundle):
    _, meta, rows = bundle
    rows[10][5] *= 0.9
    result = quality(meta, rows)
    assert result["status"] == "held"
    assert any("미기록 사건" in reason for reason in result["issues"])


def issuer_workbook(rows):
    """Synthetic tiny XLSX. Never downloaded or attributed to an issuer."""
    import io
    from xml.sax.saxutils import escape
    from zipfile import ZipFile

    from macrolens.price_issuer import HEADERS, NS

    output = io.BytesIO()
    with ZipFile(output, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="' + NS["x"] + '"/>')
        cells = []
        for index, row in enumerate([list(HEADERS), *rows], 1):
            cells.append('<row r="' + str(index) + '">')
            for col, value in enumerate(row):
                cells.append(
                    f'<c r="{chr(65 + col)}{index}" t="inlineStr"><is><t>'
                    + escape(str(value))
                    + "</t></is></c>"
                )
            cells.append("</row>")
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="'
            + NS["x"]
            + '"><sheetData>'
            + "".join(cells)
            + "</sheetData></worksheet>",
        )
    return output.getvalue()


def test_issuer_blank_gains_are_not_zero_and_dates_remain_distinct(tmp_path, monkeypatch):
    row = [
        "SYNTHETIC",
        "XLK",
        "fake",
        "12/08/2025",
        "12/08/2025",
        "12/10/2025",
        "1",
        "",
        "0",
        "Quarterly",
    ]
    path = tmp_path / "synthetic.xlsx"
    path.write_bytes(issuer_workbook([row]))

    def no_network(*args, **kwargs):
        pytest.fail("supplied workbook parsing must be offline")

    monkeypatch.setattr(audit, "urlopen", no_network)
    result, saved = audit.issuer_distributions(tmp_path / "out", path)
    assert result["status"] == "held"
    assert result["evidence_kind"] == "supplied_issuer_file_not_prices"
    item = result["symbols"]["XLK"]
    assert item["distribution_rows"] == 1
    assert item["blank_capital_gains"] == 1
    event = item["events_in_source_order"][0]
    assert event["ex_date"] != event["payable_date"]
    assert event["short_term_gain"] is None and event["long_term_gain"] == 0
    assert result["symbols"]["SPY"]["distribution_rows"] == 0
    assert (saved / "issuer-distributions.xlsx").read_bytes() == path.read_bytes()


def test_issuer_duplicates_empty_bad_format_and_missing_dividend():
    from macrolens.price_issuer import parse_distributions

    row = [
        "SYNTHETIC",
        "XLK",
        "fake",
        "12/08/2025",
        "12/08/2025",
        "12/10/2025",
        "1",
        "",
        "",
        "Quarterly",
    ]
    result = parse_distributions(issuer_workbook([row, row]), ["XLK"], "2020-01-01", "2026-01-01")
    assert result["XLK"]["distribution_rows"] == 2
    assert any("중복" in x for x in result["XLK"]["issues"])
    row[6] = ""
    result = parse_distributions(issuer_workbook([row]), ["XLK"], "2020-01-01", "2026-01-01")
    assert result["XLK"]["distribution_rows"] == 0
    assert any("비정상" in x for x in result["XLK"]["issues"])
    with pytest.raises(AuditError, match="XLSX 형식"):
        parse_distributions(b"not an XLSX", ["XLK"], "2020-01-01", "2026-01-01")
