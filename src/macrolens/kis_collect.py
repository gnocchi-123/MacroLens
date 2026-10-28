"""Immutable KIS market archive and bounded pagination; no synthetic fallback."""

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

from .kis_client import ENDPOINT_PATHS, ENDPOINTS, KISError
from .price_audit import dump, new_folder, now, sha

SAMPLE = ("XLC", "XLK", "SPY")
CONTRACT = "kis-quotes-v1"
SOURCE_COMMIT = "3a9be59a3ed8745720dc3aa9d584bdf3b7830362"
DEFAULT_START = "2020-06-01"
DEFAULT_END = "2026-09-30"


def day(text):
    if not isinstance(text, str) or len(text) != 8 or not text.isdigit():
        raise KISError("invalid_market_date")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:])).isoformat()
    except ValueError:
        raise KISError("invalid_market_date") from None


def rows(data, key):
    value = data.get(key)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise KISError("invalid_rows_shape")
    return value


def verify_archive(folder):
    folder = Path(folder)
    if not (folder / "COMPLETE").is_file():
        raise KISError("archive_incomplete")
    checks = json.loads((folder / "checksums.json").read_text())
    if not {"manifest.json", "audit.json", "report.md", "run.json"} <= checks.keys():
        raise KISError("archive_checksums_missing")
    for name, expected in checks.items():
        path = folder / name
        if Path(name).name != name or path.is_symlink() or sha(path.read_bytes()) != expected:
            raise KISError("archive_integrity_failed")
    meta = json.loads((folder / "manifest.json").read_text())
    if meta.get("contract") != CONTRACT:
        raise KISError("unsupported_archive_contract")
    for record in meta["pages"]:
        if record["file"] not in checks or record["sha256"] != checks[record["file"]]:
            raise KISError("page_checksum_missing")
    if meta.get("issuer_file") and (
        meta["issuer_file"] != "issuer-distributions.xlsx"
        or checks.get(meta["issuer_file"]) != meta.get("issuer_sha256")
    ):
        raise KISError("issuer_checksum_missing")
    return meta


class Archive:
    def __init__(
        self, output, environment, mode, exchange, product, start, end, identity_source="overseas"
    ):
        if identity_source not in ("overseas", "basic-quote"):
            raise KISError("unknown_identity_source")
        self.folder = new_folder(output, "kis_" + mode)
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
            ).strip()
            dirty = bool(
                subprocess.check_output(["git", "status", "--porcelain"], stderr=subprocess.DEVNULL)
            )
        except (OSError, subprocess.CalledProcessError):
            commit = None
            dirty = None
        self.meta = {
            "contract": CONTRACT,
            "provider": "kis",
            "evidence_kind": "real",
            "environment": environment,
            "mode": mode,
            "exchange": exchange,
            "product_type": product,
            "identity_source": identity_source,
            "start": start,
            "end_inclusive": end,
            "evaluation_start": "2021-06-07",
            "created_at": now(),
            "git_commit": commit,
            "git_dirty": dirty,
            "strict_point_in_time": False,
            "official_example_commit": SOURCE_COMMIT,
            "source_sha256": {
                name: sha(Path(__file__).with_name(name).read_bytes())
                for name in (
                    "kis_client.py",
                    "kis_collect.py",
                    "kis_audit.py",
                    "kis.py",
                    "price_quality.py",
                    "price_issuer.py",
                )
            },
            "pages": [],
            "streams": [],
            "failures": [],
            "automatic_corrections": [],
            "usage": {
                "personal_research": True,
                "redistribution_allowed": False,
                "account_terms_and_retention": "user_review_required",
            },
        }

    def request(self, client, endpoint, params, label, continuation=""):
        requested = now()
        data, blob, cont = client.get(endpoint, params, continuation)
        name = f"page-{len(self.meta['pages']):05d}.json"
        with (self.folder / name).open("xb") as stream:
            stream.write(blob)
        self.meta["pages"].append(
            {
                "file": name,
                "sha256": sha(blob),
                "endpoint": endpoint,
                "path": ENDPOINT_PATHS[endpoint],
                "tr_id": ENDPOINTS[endpoint][0],
                "params": dict(params),
                "label": label,
                "requested_at": requested,
                "received_at": now(),
                "tr_cont_request": continuation,
                "tr_cont_response": cont,
                "server": client.base,
                "http_status": 200,
            }
        )
        return data, cont

    def failure(self, ticker, stream, code):
        self.meta["failures"].append({"ticker": ticker, "stream": stream, "code": code})


def price_pages(client, archive, ticker, modp, start, end, label, max_pages=100):
    # BYMD is a base date; the example does not promise inclusive/exclusive semantics.
    # Ask one calendar day later, retain raw rows, and filter the target interval in audit.
    cursor = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
    continuation, seen_dates, seen_pages = "", set(), set()
    for _ in range(max_pages):
        params = {
            "AUTH": "",
            "EXCD": archive.meta["exchange"],
            "SYMB": ticker,
            "GUBN": "0",
            "BYMD": cursor.replace("-", ""),
            "MODP": modp,
        }
        data, cont = archive.request(client, "dailyprice", params, label, continuation)
        page = rows(data, "output2")
        signature = sha(json.dumps(page, sort_keys=True).encode())
        if not page:
            raise KISError("empty_price_page_partial")
        if signature in seen_pages:
            raise KISError("repeated_price_page")
        seen_pages.add(signature)
        dates = [day(r.get("xymd")) for r in page]
        if dates != sorted(dates, reverse=True) or len(set(dates)) != len(dates):
            raise KISError("unsorted_or_duplicate_price_page")
        if max(dates) > cursor:
            raise KISError("price_after_requested_cursor")
        if set(dates) & seen_dates:
            raise KISError("overlapping_or_conflicting_prices")
        seen_dates.update(dates)
        if min(dates) <= start:
            archive.meta["streams"].append(
                {
                    "ticker": ticker,
                    "label": label,
                    "modp": modp,
                    "start": start,
                    "end": end,
                    "pagination": "reached_start",
                }
            )
            return
        if cont in ("M", "F"):
            # Follow the official continuation contract first; same BYMD, tr_cont=N.
            continuation = "N"
        else:
            # Date cursor fallback is verified by returned dates and calendar coverage.
            next_cursor = (date.fromisoformat(min(dates)) - timedelta(days=1)).isoformat()
            if next_cursor >= cursor:
                raise KISError("date_cursor_stalled")
            cursor, continuation = next_cursor, ""
    raise KISError("price_page_limit_partial")


def rights_pages(client, archive, ticker, start, end, max_pages=100):
    params = {
        "RGHT_TYPE_CD": "%%",
        "INQR_DVSN_CD": "02",
        "INQR_STRT_DT": start.replace("-", ""),
        "INQR_END_DT": end.replace("-", ""),
        "PDNO": ticker,
        "PRDT_TYPE_CD": archive.meta["product_type"],
        "CTX_AREA_NK50": "",
        "CTX_AREA_FK50": "",
    }
    seen_pages, seen_keys, continuation = set(), set(), ""
    for _ in range(max_pages):
        data, cont = archive.request(client, "period-rights", params, "rights", continuation)
        page = rows(data, "output")
        signature = sha(json.dumps(page, sort_keys=True).encode())
        if signature in seen_pages:
            raise KISError("repeated_rights_page")
        seen_pages.add(signature)
        if cont not in ("M", "F"):
            archive.meta["streams"].append(
                {
                    "ticker": ticker,
                    "label": "rights",
                    "start": start,
                    "end": end,
                    "pagination": "provider_end",
                }
            )
            return
        key = (data.get("ctx_area_nk50", "").strip(), data.get("ctx_area_fk50", "").strip())
        if not page or not any(key) or key in seen_keys:
            raise KISError("rights_cursor_stalled")
        seen_keys.add(key)
        params["CTX_AREA_NK50"], params["CTX_AREA_FK50"] = key
        continuation = "N"
    raise KISError("rights_page_limit_partial")


def collect(client, archive):
    meta = archive.meta
    for ticker in SAMPLE:
        requests = [("search-info", {"PRDT_TYPE_CD": meta["product_type"], "PDNO": ticker})]
        if meta.get("identity_source", "overseas") == "basic-quote":
            requests = [
                ("basic-info", {"PRDT_TYPE_CD": meta["product_type"], "PDNO": ticker}),
                ("price-detail", {"AUTH": "", "EXCD": meta["exchange"], "SYMB": ticker}),
            ]
        for endpoint, params in requests:
            label = "identity" if endpoint == "search-info" else "identity_" + endpoint
            try:
                data, cont = archive.request(client, endpoint, params, label)
                if not data.get("output") or cont in ("M", "F"):
                    raise KISError("identity_missing_or_partial")
            except KISError as exc:
                archive.failure(ticker, label, str(exc))
        ranges = [("full", meta["start"], meta["end_inclusive"])]
        if meta["mode"] == "probe":
            ranges = [("recent", "2026-09-21", "2026-09-30"), ("old", "2020-06-01", "2020-06-10")]
        for label, start, end in ranges:
            for modp in ("0", "1"):
                try:
                    price_pages(
                        client,
                        archive,
                        ticker,
                        modp,
                        start,
                        end,
                        label,
                        max_pages=2 if meta["mode"] == "probe" else 100,
                    )
                except KISError as exc:
                    archive.failure(ticker, label + "_modp" + modp, str(exc))
        # Probe only a small rights window; collect full periods in calendar-year chunks.
        windows = (
            [("2025-12-01", "2025-12-31")]
            if meta["mode"] == "probe"
            else [
                (max(meta["start"], f"{year}-01-01"), min(meta["end_inclusive"], f"{year}-12-31"))
                for year in range(
                    date.fromisoformat(meta["start"]).year,
                    date.fromisoformat(meta["end_inclusive"]).year + 1,
                )
            ]
        )
        for start, end in windows:
            for endpoint in ("period-rights", "rights-by-ice"):
                try:
                    if endpoint == "period-rights":
                        rights_pages(client, archive, ticker, start, end)
                    else:
                        data, cont = archive.request(
                            client,
                            endpoint,
                            {
                                "NCOD": "US",
                                "SYMB": ticker,
                                "ST_YMD": start.replace("-", ""),
                                "ED_YMD": end.replace("-", ""),
                            },
                            "ice",
                        )
                        rows(data, "output1")
                        if cont in ("M", "F"):
                            raise KISError("ice_unverified_continuation")
                except KISError as exc:
                    archive.failure(ticker, endpoint, str(exc))
    return archive


def finish(archive, result):
    dump(archive.folder / "manifest.json", archive.meta)
    dump(archive.folder / "audit.json", result)
    lines = [
        "# KIS 가격·권리 검수",
        "",
        "백테스트·총수익 검증 결과가 아닙니다.",
        "",
        f"상태: {result['status']} / {result['evidence_kind']}",
        f"소량 점검 후 전체 수집 가능: {result.get('can_collect', False)}",
        "상품 식별 경로: " + archive.meta.get("identity_source", "overseas"),
        (
            "소량 가격 구간: 2020-06-01~06-10 및 2026-09-21~09-30. "
            "최초~최종 날짜 사이 전체를 수집한 것이 아닙니다. "
            "권리는 2025년 12월 현지 기준일/ICE 발표일 기준 조회입니다."
            if archive.meta["mode"] == "probe"
            else "전체 목표 구간의 완전성은 coverage를 확인하세요."
        ),
        "",
        "| 상품 | MODP=0 행 수·범위 | MODP=1 행 수 | 권리/ICE 행 수 | 판정 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for ticker, info in result["symbols"].items():
        p = info["prices"]["0"]
        lines.append(
            f"| {ticker} | {p['rows']} / {p['first']}~{p['last']} | "
            f"{info['prices']['1']['rows']} | {info['rights_rows']}/{info['ice_rows']} | "
            f"{info['status']} |"
        )
    for ticker, info in result["symbols"].items():
        lines.extend(["", ticker + ": " + "; ".join(info["issues"] + info["limits"])])
    lines += [
        "",
        "strict_point_in_time=false. 원본·페이지·날짜·공식 대조는 audit.json 참조.",
        "COMPLETE는 보존 완료이며 적격성 승인이 아닙니다.",
    ]
    (archive.folder / "report.md").write_text("\n".join(lines) + "\n")
    dump(
        archive.folder / "checksums.json",
        {p.name: sha(p.read_bytes()) for p in archive.folder.iterdir() if p.is_file()},
    )
    (archive.folder / "COMPLETE").write_text("saved\n")
