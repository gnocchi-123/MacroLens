"""Explicit, read-only KIS CLI. Existing macrolens commands do not import this module."""

import argparse
import copy
import json
import sys
from pathlib import Path

from .kis_audit import audit_archive
from .kis_client import KISClient, KISError, private_dir, private_file
from .kis_collect import (
    DEFAULT_END,
    DEFAULT_START,
    SAMPLE,
    Archive,
    collect,
    finish,
    verify_archive,
)
from .price_audit import dump, sha
from .price_issuer import parse_distributions
from .price_quality import AuditError


def attach_issuer(archive, source):
    if source is None:
        return
    blob = Path(source).read_bytes()
    parse_distributions(blob, SAMPLE, archive.meta["start"], archive.meta["end_inclusive"])
    (archive.folder / "issuer-distributions.xlsx").write_bytes(blob)
    archive.meta["issuer_file"] = "issuer-distributions.xlsx"
    archive.meta["issuer_sha256"] = sha(blob)


def recheck(saved, output, issuer=None):
    saved = Path(saved)
    meta = verify_archive(saved)
    archive = Archive(
        output,
        meta["environment"],
        meta["mode"],
        meta["exchange"],
        meta["product_type"],
        meta["start"],
        meta["end_inclusive"],
    )
    current_version = archive.meta["source_sha256"]
    archive.meta = copy.deepcopy(meta)
    archive.meta["rechecked_from"] = saved.name
    archive.meta["recheck_source_sha256"] = current_version
    for page in meta["pages"]:
        (archive.folder / page["file"]).write_bytes((saved / page["file"]).read_bytes())
    if meta.get("issuer_file"):
        attach_issuer(archive, saved / meta["issuer_file"])
    if issuer:
        attach_issuer(archive, issuer)
    result, normalized = audit_archive(archive.folder, archive.meta)
    previous = json.loads((saved / "audit.json").read_text())
    result["recheck"] = {
        "matched": result["symbols"] == previous["symbols"],
        "new_issuer_reference": bool(issuer),
    }
    dump(archive.folder / "normalized.json", normalized)
    finish(archive, result)
    return result, archive.folder


def main(argv=None):
    parser = argparse.ArgumentParser(description="KIS 조회 전용; 주문·전략 성과 계산 없음")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("auth", "probe", "collect"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--env", choices=("real", "demo"), required=True)
        if command != "auth":
            cmd.add_argument("--exchange", choices=("AMS", "NYS", "NAS"), default="AMS")
            cmd.add_argument("--output", type=Path, default=Path("data/kis"))
            cmd.add_argument("--issuer-file", type=Path)
            cmd.add_argument(
                "--identity-source",
                choices=("overseas", "basic-quote"),
                help="상품 식별 경로. probe 기본 overseas; collect는 probe 경로 유지",
            )
        if command == "collect":
            cmd.add_argument("--probe", type=Path, required=True)
    check = sub.add_parser("recheck")
    check.add_argument("saved", type=Path)
    check.add_argument("--output", type=Path, default=Path("data/kis"))
    check.add_argument("--issuer-file", type=Path)
    diagnostic = sub.add_parser("diagnose", help="선택 API 한 번만 진단; 재시도·전체 probe 없음")
    diagnostic.add_argument("--env", choices=("real", "demo"), required=True)
    diagnostic.add_argument("--symbol", choices=SAMPLE, default="SPY")
    diagnostic.add_argument("--exchange", choices=("AMS", "NYS", "NAS"), default="AMS")
    diagnostic.add_argument(
        "--endpoint",
        choices=("search-info", "basic-info", "price-detail", "dailyprice"),
        default="price-detail",
    )
    diagnostic.add_argument("--output", type=Path, default=Path("data/kis_diagnostics"))
    diagnostic.add_argument("--date", help="dailyprice 진단 대상 거래일 YYYY-MM-DD")
    diagnostic.add_argument("--modp", choices=("0", "1"), help="dailyprice 수정주가 옵션; 기본 0")
    args = parser.parse_args(argv)
    try:
        if args.command == "diagnose":
            from .kis_diagnostic import diagnose

            return diagnose(args)
        if args.command == "recheck":
            result, folder = recheck(args.saved, args.output, args.issuer_file)
            print("recheck: " + ("matched" if result["recheck"]["matched"] else "different"))
        else:
            identity_source = getattr(args, "identity_source", None) or "overseas"
            if args.command == "collect":
                meta = verify_archive(args.probe)
                identity_source = meta.get("identity_source", "overseas")
                probe_result, _ = audit_archive(args.probe, meta)
                if (
                    not probe_result["can_collect"]
                    or meta["environment"] != args.env
                    or meta["exchange"] != args.exchange
                    or meta["mode"] != "probe"
                    or (args.identity_source and args.identity_source != identity_source)
                ):
                    raise KISError("matching_successful_real_probe_required")
            client = KISClient(args.env)
            if args.command == "auth":
                client.token()
                print("authentication_ok / " + args.env + " / 가격·권리 검수 미실시")
                return 0
            product = {"AMS": "529", "NYS": "513", "NAS": "512"}[args.exchange]
            # Serialize data collection per environment across processes to honor our rate limit.
            private_dir(client.cache)
            with private_file(client.cache / "collection.lock"):
                archive = Archive(
                    args.output,
                    args.env,
                    args.command,
                    args.exchange,
                    product,
                    DEFAULT_START,
                    DEFAULT_END,
                    identity_source=identity_source,
                )
                if args.command == "collect":
                    archive.meta["preflight_archive"] = args.probe.name
                attach_issuer(archive, args.issuer_file)
                try:
                    collect(client, archive)
                except (AuditError, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt):
                    archive.failure("ALL", "collection", "interrupted_or_invalid_response")
                result, normalized = audit_archive(archive.folder, archive.meta)
                if any(f["ticker"] == "ALL" for f in archive.meta["failures"]):
                    result["status"], result["can_collect"] = "held", False
                dump(archive.folder / "normalized.json", normalized)
                finish(archive, result)
                folder = archive.folder
        print(result["status"] + " / " + result["evidence_kind"])
        print("can_collect: " + str(result["can_collect"]).lower())
        print("검수 보고서: " + str(folder / "report.md"))
        # Probe readiness only authorizes evidence collection, not research eligibility.
        return 0 if result["can_collect"] else 2
    except (AuditError, OSError, ValueError, KeyError, TypeError):
        # Never display arbitrary exception text (including paths or server messages).
        error = sys.exception()
        print(
            "KIS 작업 중단: "
            + (
                str(error)
                if isinstance(error, KISError)
                else "입력·파일·무결성·의존성을 확인하세요"
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
