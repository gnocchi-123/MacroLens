"""Standalone M4 file audit. No signals, backtest, optimization or credential handling.

Network probe checks public source access only. A price downloader is deliberately
not enabled: reviewed Yahoo terms require prior permission for automated collection.
The separate issuer command obtains a public reference file for personal/internal use.
"""

import argparse
import hashlib
import json
import platform
import sys
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4

from .price_issuer import DISTRIBUTIONS_URL, parse_distributions
from .price_quality import AuditError, audit_symbol

TOOL_VERSION = "m4-price-audit-v1"
SAMPLE = ("XLC", "XLK", "SPY")
UNIVERSE = ("XLE", "XLB", "XLI", "XLY", "XLP", "XLV", "XLF", "XLK", "XLC", "XLU", "XLRE", "SPY")
OPTIONS = {
    "interval": "1d",
    "auto_adjust": False,
    "back_adjust": False,
    "repair": False,
    "actions": True,
    "keepna": True,
    "rounding": False,
    "prepost": False,
}
SOURCES = {
    "yfinance_tool": "https://raw.githubusercontent.com/ranaroussi/yfinance/main/README.md",
    "yfinance_history_contract": "https://raw.githubusercontent.com/ranaroussi/yfinance/"
    "main/yfinance/scrapers/history.py",
    "yahoo_terms": "https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html",
    "yahoo_adjustment": "https://help.yahoo.com/kb/SLN28256.html",
    "yahoo_download_help": "https://help.yahoo.com/kb/SLN2311.html",
    "ssga_terms": "https://www.ssga.com/us/en/footer/terms-and-conditions",
    "ssga_xlc": "https://www.ssga.com/us/en/intermediary/etfs/"
    "state-street-communication-services-select-sector-spdr-etf-xlc",
    "ssga_xlk": "https://www.ssga.com/us/en/intermediary/etfs/"
    "state-street-technology-select-sector-spdr-etf-xlk",
    "ssga_spy": "https://www.ssga.com/us/en/intermediary/etfs/spdr-sp-500-etf-trust-spy",
    "ssga_distributions": "https://www.ssga.com/us/en/intermediary/resources/documents/"
    "etf-dividend-distributions",
    "issuer_split_release": "https://www.nasdaq.com/press-release/"
    "state-street-investment-management-announces-share-splits-"
    "five-select-sector-spdrr",
    "alfred_indpro_units": "https://alfred.stlouisfed.org/series?seid=INDPRO",
    "nyse_calendar": "https://www.nyse.com/markets/hours-calendars",
    "nasdaq_terms": "https://www.nasdaq.com/legal",
    "nasdaq_history": "https://www.nasdaq.com/market-activity/etf/xlk/historical",
    "stooq_terms": "https://stooq.com/terms.html",
    "twelvedata_pricing": "https://twelvedata.com/pricing",
}


def now():
    return datetime.now(UTC).isoformat()


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def dump(path, obj):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def versions():
    result = {
        "audit_tool": TOOL_VERSION,
        "python": platform.python_version(),
        "tool_source_sha256": {
            name: sha(Path(__file__).with_name(name).read_bytes())
            for name in ("price_audit.py", "price_quality.py", "price_issuer.py")
        },
    }
    for name in ("exchange-calendars", "pandas", "numpy"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = "not_installed"
    return result


def new_folder(root, kind):
    root = Path(root)
    # Inside this checkout only data/ and reports/ may hold raw private inputs.
    repo = Path(__file__).resolve().parents[2]
    target = root.resolve()
    if (repo / ".git").exists() and target.is_relative_to(repo):
        relative = target.relative_to(repo)
        if not relative.parts or relative.parts[0] not in ("data", "reports"):
            raise AuditError(
                "저장소 안의 가격 원자료는 Git 제외된 data/ 또는 reports/에 저장하세요."
            )
    folder = root / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12])
    folder.mkdir(parents=True, exist_ok=False)
    dump(folder / "run.json", {"kind": kind, "created_at": now(), "versions": versions()})
    return folder


def seal(folder, result):
    dump(folder / "audit.json", result)
    with (folder / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(render(result))
    dump(
        folder / "checksums.json",
        {p.name: sha(p.read_bytes()) for p in folder.iterdir() if p.is_file()},
    )
    (folder / "COMPLETE").write_text("saved\n", encoding="utf-8")


def render(result):
    lines = [
        "# MacroLens M4 가격 자료 검수",
        "",
        "전략 성과·계수 검증 결과가 아닙니다.",
        "",
        f"- 상태: {result['status']}",
        f"- 증거 종류: {result['evidence_kind']}",
        f"- 도구: {TOOL_VERSION}",
        "",
    ]
    for reason in result.get("issues", []):
        lines.append("- " + reason)
    if result.get("source_access"):
        lines += ["", "| 출처 | 연결 결과 | 분류 |", "| --- | --- | --- |"]
        for name, item in result["source_access"].items():
            lines.append(f"| {name} | {item.get('http_status', '—')} | {item['status']} |")
    lines += [
        "",
        "| 상품 | 상태 | 행 수 | 실제 첫 관측 | 마지막 관측 | 사유 |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for ticker, item in result.get("symbols", {}).items():
        reasons = item.get("issues", []) + item.get("warnings", [])
        lines.append(
            f"| {ticker} | {item['status']} | {item.get('rows', 0)} | "
            f"{item.get('first_date') or '미확보'} | {item.get('last_date') or '미확보'} | "
            + "; ".join(reasons)
            + " |"
        )
    for ticker, item in result.get("symbols", {}).items():
        if "distribution_rows" in item:
            lines.append(
                f"\n{ticker} 공식 배당 파일: 범위 내 {item['distribution_rows']}건, "
                f"배당락일 범위 {item['requested_distribution_range']}. "
                "일별 가격 행 수와 구별합니다.\n"
            )
    lines += [
        "",
        "세부 날짜·사건 전후 계산·출처 및 해시는 audit.json과 입력 보존 파일을 확인하세요.",
        "COMPLETE는 파일 저장 완료 표시이며 자료 적격성 통과를 뜻하지 않습니다.",
        "",
    ]
    return "\n".join(lines)


def source_probe(output):
    folder = new_folder(output, "source_access_probe")
    checks = {}
    for name, url in SOURCES.items():
        item = {
            "url": url,
            "retrieved_at": now(),
            "method": "GET",
            "timeout_seconds": 15,
            "auth": "none",
            "retries": 0,
        }
        try:
            with urlopen(url, timeout=15) as response:
                blob = response.read(2 * 1024 * 1024 + 1)
                item.update(
                    http_status=response.status,
                    bytes=len(blob),
                    sha256=sha(blob),
                    status="accessible_not_reviewed"
                    if len(blob) <= 2 * 1024 * 1024
                    else "response_too_large",
                )
            # Only the Apache-licensed tool README is retained automatically.
            if name == "yfinance_tool" and item["status"] == "accessible_not_reviewed":
                (folder / "yfinance-tool-readme.txt").write_bytes(blob)
        except HTTPError as exc:
            item.update(http_status=exc.code, status="provider_or_edge_http_error")
        except URLError as exc:
            item["status"] = (
                "proxy_connect_403"
                if "Tunnel connection failed: 403" in str(exc.reason)
                else "network_error"
            )
        except OSError:
            item["status"] = "network_error"
        checks[name] = item
        print(name + ": " + item["status"], file=sys.stderr, flush=True)
    result = {
        "status": "held",
        "evidence_kind": "actual_access_probe_not_price_data",
        "source_access": checks,
        "actual_price_rows": 0,
        "issues": ["공급자 약관·저장 권한·가격 다운로드 경로 검토 필요; 가격 적격성 미검수"],
        "symbols": {
            t: {"status": "held", "rows": 0, "issues": ["실제 가격·배당·분할 미확보"]}
            for t in SAMPLE
        },
    }
    seal(folder, result)
    return result, folder


def template():
    return {
        "schema_version": 1,
        "evidence_kind": "real",
        "provider": "yahoo_yfinance_export",
        "source_url": "https://finance.yahoo.com/",
        "retrieved_at": None,
        "library": {"name": "yfinance", "version": None},
        "usage": {
            "review_status": "unverified",
            "terms_url": SOURCES["yahoo_terms"],
            "reviewed_at": None,
            "access_basis": None,
            "local_storage_allowed": False,
            "redistribution_allowed": False,
        },
        "request": {
            "start": "2020-06-01",
            "end_inclusive": "2026-09-30",
            "evaluation_start": "2021-06-07",
            "provider_end": "2026-10-01",
            "end_exclusive": True,
            "options": OPTIONS.copy(),
        },
        "adjustment": {
            "close": "unknown",
            "adj_close": "unknown",
            "dividends": "unknown",
            "method": "unknown",
            "definition_reference": None,
        },
        "calendar_reference": None,
        "references": {},
        "symbols": {
            t: {
                "ticker": t,
                "file": t + ".csv",
                "currency": None,
                "instrument_type": None,
                "exchange": None,
                "timezone": "America/New_York",
                "inception_date": None,
                "listing_date": None,
                "identity_reference": None,
                "dividend_status": "unknown",
                "split_status": "unknown",
                "capital_gains_status": "unknown",
                "capital_gains_reference": None,
                "official_events": [],
            }
            for t in SAMPLE
        },
    }


def issuer_distributions(output, local_file=None):
    """One public issuer download for personal/internal review, or an offline copy."""
    folder = new_folder(output, "issuer_distribution_reference")
    request = {
        "source_url": DISTRIBUTIONS_URL,
        "collected_at": now(),
        "terms_url": SOURCES["ssga_terms"],
        "terms_review_date": "2026-10-13",
        "use": "personal/internal reference copy only; no public redistribution",
        "network_used": local_file is None,
        "request_options": (
            {"method": "GET", "timeout_seconds": 30, "auth": "none", "retries": 0}
            if local_file is None
            else {"method": "local_file_read"}
        ),
        "original_collection_time": "see original request.json" if local_file else None,
    }
    if local_file is None:
        try:
            with urlopen(DISTRIBUTIONS_URL, timeout=30) as response:
                blob = response.read(10_000_001)
                request["http_status"] = response.status
        except (HTTPError, URLError, OSError):
            dump(folder / "failure.json", {"status": "download_failed", **request})
            raise AuditError("공식 배당 파일 다운로드 실패; 불완전 기록에 요청 정보 보존") from None
    else:
        blob = Path(local_file).read_bytes()
    if len(blob) > 10_000_000:
        raise AuditError("공식 배당 파일 크기 한도 초과")
    request.update(bytes=len(blob), sha256=sha(blob))
    (folder / "issuer-distributions.xlsx").write_bytes(blob)
    dump(folder / "request.json", request)
    symbols = parse_distributions(blob, SAMPLE, "2020-06-01", "2026-09-30")
    result = {
        "status": "held",
        "evidence_kind": (
            "supplied_issuer_file_not_prices"
            if local_file
            else "actual_issuer_distributions_not_prices"
        ),
        "actual_price_rows": 0,
        "source": request,
        "symbols": symbols,
        "issues": ["공식 배당 대조원 확보; 일별 가격 적격성은 미확인"],
    }
    seal(folder, result)
    return result, folder


def validate_time(value):
    value = datetime.fromisoformat(value)
    if value.tzinfo is None or value > datetime.now(UTC):
        raise AuditError("수집·검토 시각은 시간대가 있는 현재 이전 시각이어야 합니다.")


def secret_check(value):
    """Reject credential fields and signed/request URLs before any archival write."""
    if isinstance(value, dict):
        for key, child in value.items():
            if any(
                word in key.lower()
                for word in ("api_key", "apikey", "token", "password", "cookie", "authorization")
            ):
                raise AuditError("입력 명세에 비밀값 필드를 넣지 마세요.")
            secret_check(child)
    elif isinstance(value, list):
        for child in value:
            secret_check(child)
    elif isinstance(value, str) and "://" in value:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise AuditError("출처 URL은 인증값·쿼리가 없는 공개 HTTPS 문서 주소만 허용합니다.")


def safe_input(base, name):
    path = (base / name).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise AuditError("입력 파일 누락/입력 디렉터리 밖의 경로")
    return path


def inspect_manifest(meta):
    secret_check(meta)
    if meta["schema_version"] != 1 or meta["provider"] != "yahoo_yfinance_export":
        raise AuditError("지원하지 않는 가격 입력 명세")
    if meta["evidence_kind"] not in ("real", "synthetic"):
        raise AuditError("실제/합성 자료 구분이 필요합니다.")
    if not meta["symbols"] or any(
        t not in UNIVERSE or info["ticker"] != t for t, info in meta["symbols"].items()
    ):
        raise AuditError("상품 식별자 불일치 또는 빈 목록")
    # Storage rights are independent of technical accessibility and tool licensing.
    usage = meta["usage"]
    if meta["evidence_kind"] == "real":
        if (
            usage.get("review_status") != "reviewed"
            or not usage.get("access_basis")
            or usage.get("local_storage_allowed") is not True
            or not usage.get("terms_url")
        ):
            raise AuditError("이용 경로와 로컬 보존 권한 미확인: 원자료를 복사하지 않습니다.")
        validate_time(usage["reviewed_at"])
    validate_time(meta["retrieved_at"])
    if meta["library"]["name"] != "yfinance" or not meta["library"]["version"]:
        raise AuditError("실제 사용한 yfinance 버전 기록이 필요합니다.")
    req = meta["request"]
    start, end = date.fromisoformat(req["start"]), date.fromisoformat(req["end_inclusive"])
    evaluation = date.fromisoformat(req["evaluation_start"])
    if not start <= evaluation <= end:
        raise AuditError("준비·평가·종료일 순서 오류")
    try:
        warmup = evaluation.replace(year=evaluation.year - 1)
    except ValueError:
        warmup = evaluation.replace(year=evaluation.year - 1, day=28)
    if start > warmup:
        raise AuditError("평가 시작 이전 최소 12개월의 준비 가격 요청이 필요합니다.")
    if (
        req["end_exclusive"] is not True
        or req["provider_end"] != (end + timedelta(days=1)).isoformat()
    ):
        raise AuditError("Yahoo 종료일은 배타적입니다. 최종 포함일 다음날을 요청하세요.")
    if req["options"] != OPTIONS:
        raise AuditError("가격 요청 옵션 누락/불일치: 자동 조정·수리 금지 및 사건 요청 필요")


def audit_bundle(manifest, output, source_files=None, previous=None):
    manifest = Path(manifest)
    original = manifest.read_bytes()
    meta = json.loads(original)
    inspect_manifest(meta)

    def contents(name):
        if source_files is not None:
            return source_files[name]
        return safe_input(manifest.parent, name).read_bytes()

    folder = new_folder(output, "offline_price_audit")
    (folder / "input-manifest.json").write_bytes(original)
    copied = {}
    reference_ids = set()
    reference_issues = []
    for key, ref in meta["references"].items():
        blob = contents(ref["file"])
        if sha(blob) != ref["sha256"]:
            raise AuditError("대조 근거 파일 해시 불일치")
        validate_time(ref["reviewed_at"])
        if not ref.get("source_url"):
            raise AuditError("공식 대조 근거 URL 누락")
        filename = f"reference-{len(copied)}.bin"
        (folder / filename).write_bytes(blob)
        copied[ref["file"]] = filename
        reference_ids.add(key)
    if meta["adjustment"].get("definition_reference") not in reference_ids:
        reference_issues.append("가격·배당 조정 정의의 공식 대조 근거 미확인")
    if meta.get("calendar_reference") not in reference_ids:
        reference_issues.append("거래소 공식 달력과 라이브러리 일정 대조 미완료")
    symbols = {}
    for ticker, info in meta["symbols"].items():
        blob = contents(info["file"])
        filename = ticker + ".csv"
        (folder / filename).write_bytes(blob)
        copied[info["file"]] = filename
        item = audit_symbol(blob, info, meta["request"], meta["adjustment"], reference_ids)
        item["raw_sha256"] = sha(blob)
        item["warnings"].extend(reference_issues)
        if item["status"] == "usable" and reference_issues:
            item["status"] = "conditional"
        symbols[ticker] = item
    states = {item["status"] for item in symbols.values()}
    technical_status = (
        "held" if "held" in states else "conditional" if "conditional" in states else "usable"
    )
    result = {
        "status": "synthetic_only" if meta["evidence_kind"] == "synthetic" else technical_status,
        "technical_status": technical_status,
        "evidence_kind": meta["evidence_kind"],
        "provider": meta["provider"],
        "source_url": meta["source_url"],
        "retrieved_at": meta["retrieved_at"],
        "request": meta["request"],
        "provider_library": meta["library"],
        "audit_versions": versions(),
        "input_manifest_sha256": sha(original),
        "copied_inputs": copied,
        "symbols": symbols,
        "automatic_corrections": [],
        "expansion_eligible": meta["evidence_kind"] == "real"
        and set(SAMPLE) <= symbols.keys()
        and all(symbols[t]["status"] == "usable" for t in SAMPLE),
    }
    if states == {"usable"}:
        result["common_available_range"] = [
            max(x["first_date"] for x in symbols.values()),
            min(x["last_date"] for x in symbols.values()),
        ]
    else:
        result["common_available_range"] = None
    if previous is not None:
        result["recheck"] = {
            "source": previous["source"],
            "matched": result["symbols"] == previous["symbols"],
        }
    seal(folder, result)
    return result, folder


def recheck(saved, output):
    saved = Path(saved)
    if not (saved / "COMPLETE").is_file():
        raise AuditError("불완전한 검수 기록")
    checksums = json.loads((saved / "checksums.json").read_text())
    required = {"input-manifest.json", "audit.json", "report.md", "run.json"}
    if not required <= checksums.keys():
        raise AuditError("파일 검수 기록의 필수 해시 누락")
    for name, expected in checksums.items():
        if sha(safe_input(saved, name).read_bytes()) != expected:
            raise AuditError("저장 원본 무결성 불일치")
    previous = json.loads((saved / "audit.json").read_text())
    source_files = {}
    for original, stored in previous["copied_inputs"].items():
        if stored not in checksums:
            raise AuditError("원자료 체크섬 누락")
        source_files[original] = safe_input(saved, stored).read_bytes()
    return audit_bundle(
        saved / "input-manifest.json",
        output,
        source_files,
        {"source": saved.name, "symbols": previous["symbols"]},
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="M4 가격 자료 검수 (전략 성과 계산 없음)")
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe", help="공식 출처 연결 점검; 가격 수집 성공을 뜻하지 않음")
    probe.add_argument("--output", type=Path, default=Path("data/price_audit"))
    issuer = sub.add_parser("issuer", help="공식 배당 대조원 확보·검수 (가격 수집 아님)")
    issuer.add_argument("--file", type=Path, help="저장 XLSX로 오프라인 재검수")
    issuer.add_argument("--output", type=Path, default=Path("data/price_audit"))
    init = sub.add_parser("template", help="허용된 가격 파일 검수용 입력 명세 생성")
    init.add_argument("--output", type=Path, default=Path("data/price_inbox/manifest.json"))
    audit = sub.add_parser("audit", help="보존 권한을 확인한 가격 CSV와 공식 대조 기록 검수")
    audit.add_argument("--manifest", type=Path, required=True)
    audit.add_argument("--output", type=Path, default=Path("data/price_audit"))
    again = sub.add_parser("recheck", help="저장 원자료로 외부 네트워크 없이 재검수")
    again.add_argument("saved", type=Path)
    again.add_argument("--output", type=Path, default=Path("data/price_audit"))
    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            dump(args.output, template())
            print("검수 입력 명세: " + str(args.output) + " (미확인 값은 비워 둠)")
            return 0
        if args.command == "probe":
            result, folder = source_probe(args.output)
        elif args.command == "issuer":
            result, folder = issuer_distributions(args.output, args.file)
        elif args.command == "audit":
            result, folder = audit_bundle(args.manifest, args.output)
        else:
            result, folder = recheck(args.saved, args.output)
        if "recheck" in result:
            print("recheck: " + ("matched" if result["recheck"]["matched"] else "different"))
        print(result["status"] + " / " + result["evidence_kind"])
        print("검수 보고서: " + str(folder / "report.md"))
        passed = result["status"] == "usable" and result.get("recheck", {}).get("matched", True)
        return 0 if passed else 2
    except (AuditError, OSError, ValueError, KeyError, TypeError) as exc:
        print(
            "검수 실패: "
            + (
                str(exc)
                if isinstance(exc, AuditError)
                else "입력 명세·파일 형식을 확인하세요 (본문 비공개)."
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
