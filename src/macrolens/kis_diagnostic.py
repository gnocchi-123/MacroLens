"""One read-only request with opt-in sanitized error text; never an eligibility override."""

import json
from datetime import date, timedelta
from pathlib import Path

from .kis_audit import price_diagnostic_value, price_row_check
from .kis_client import ENDPOINT_PATHS, ENDPOINTS, KISClient, KISError, private_dir, private_file
from .kis_collect import rows
from .price_audit import dump, new_folder, now, sha


def diagnose(args):
    if (args.date or args.modp) and args.endpoint != "dailyprice":
        raise KISError("date_and_modp_require_dailyprice")
    target = None
    if args.endpoint == "dailyprice":
        try:
            target = date.fromisoformat(args.date or "2026-09-30")
            base_date = (target + timedelta(days=1)).strftime("%Y%m%d")
        except (ValueError, OverflowError):
            raise KISError("invalid_diagnostic_date") from None
    client = KISClient(args.env)
    params = {
        "PRDT_TYPE_CD": {"AMS": "529", "NYS": "513", "NAS": "512"}[args.exchange],
        "PDNO": args.symbol,
    }
    if args.endpoint in ("price-detail", "dailyprice"):
        params = {"AUTH": "", "EXCD": args.exchange, "SYMB": args.symbol}
        if args.endpoint == "dailyprice":
            params.update(GUBN="0", BYMD=base_date, MODP=args.modp or "0")
    private_dir(client.cache)
    with private_file(client.cache / "collection.lock"):
        folder = new_folder(args.output, "kis_diagnostic")
        result = {
            "kind": "diagnostic_only",
            "environment": args.env,
            "symbol": args.symbol,
            "endpoint": args.endpoint,
            "server": client.base,
            "method": "GET",
            "path": ENDPOINT_PATHS[args.endpoint],
            "tr_id": ENDPOINTS[args.endpoint][0],
            "params": params,
            "requested_at": now(),
            "max_get_attempts": 1,
            "timeout_seconds": 15,
            "can_collect": False,
            "strict_point_in_time": False,
            "source_sha256": {
                name: sha(Path(__file__).with_name(name).read_bytes())
                for name in ("kis_client.py", "kis_diagnostic.py", "kis_audit.py", "kis_collect.py")
            },
        }
        try:
            data, blob, cont = client.get(args.endpoint, params, max_attempts=1, diagnostics=True)
            # Successful market body is already checked for credential echoes by the client.
            (folder / "response.json").write_bytes(blob)
            result.update(
                status="request_succeeded", http_status=200, tr_cont=cont, response_sha256=sha(blob)
            )
            if target:
                result["price_date_check"] = price_date_check(
                    data, target, args.exchange, args.symbol
                )
        except KISError as error:
            result.update(
                status="request_failed",
                error=str(error),
                provider_message=getattr(error, "provider_message", None),
            )
        result["finished_at"] = now()
        dump(folder / "diagnostic.json", result)
        dump(
            folder / "checksums.json",
            {p.name: sha(p.read_bytes()) for p in folder.iterdir() if p.is_file()},
        )
        (folder / "COMPLETE").write_text("saved\n")
    print("KIS 단일 요청 진단: " + result["status"])
    if "error" in result:
        print(result["error"])
        print(
            "KIS 설명(민감값 검사·일부 생략): "
            + (result["provider_message"] or "안전하게 표시할 설명 없음")
        )
    if "price_date_check" in result:
        check = result["price_date_check"]
        print(f"대상 날짜: {check['date']} / MODP={params['MODP']} / {check['status']}")
        for row in check["rows"]:
            print("대상 행: " + json.dumps(row, ensure_ascii=False))
        print("단일 응답의 대상 행 검사이며 기존 가격 원본·판정을 수정하지 않습니다.")
    print("진단 기록: " + str(folder / "diagnostic.json"))
    print("가격·권리 적격성 승인이 아닙니다. can_collect: false")
    return 0 if result["status"] == "request_succeeded" else 2


def price_date_check(data, target, exchange, symbol):
    """Inspect one date without paging, repairing data, or approving a price series."""
    check = {"date": target.isoformat(), "status": "target_missing", "rows": []}
    try:
        page = rows(data, "output2")
    except KISError:
        check["status"] = "invalid_response_shape"
        return check
    for index, row in enumerate(page, start=1):
        if row.get("xymd") != target.strftime("%Y%m%d"):
            continue
        _, errors = price_row_check(row)
        check["rows"].append(
            {
                "source_row_1based": index,
                "reasons": errors,
                "observed": {
                    f: price_diagnostic_value(row, f)
                    for f in ("xymd", "open", "high", "low", "clos", "tvol")
                },
            }
        )
    if check["rows"]:
        check["status"] = (
            "invalid_price_row" if check["rows"][0]["reasons"] else "row_checks_passed"
        )
    if len(check["rows"]) > 1:
        check["status"] = "duplicate_target_rows"
    header = data.get("output1")
    if not isinstance(header, dict) or header.get("rsym") != "D" + exchange + symbol:
        check["status"] = "symbol_unverified"
    return check
