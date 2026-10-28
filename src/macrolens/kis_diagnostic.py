"""One read-only request with opt-in sanitized error text; never an eligibility override."""

from pathlib import Path

from .kis_client import ENDPOINT_PATHS, ENDPOINTS, KISClient, KISError, private_dir, private_file
from .price_audit import dump, new_folder, now, sha


def diagnose(args):
    client = KISClient(args.env)
    params = {
        "PRDT_TYPE_CD": {"AMS": "529", "NYS": "513", "NAS": "512"}[args.exchange],
        "PDNO": args.symbol,
    }
    if args.endpoint in ("price-detail", "dailyprice"):
        params = {"AUTH": "", "EXCD": args.exchange, "SYMB": args.symbol}
        if args.endpoint == "dailyprice":
            params.update(GUBN="0", BYMD="20261001", MODP="0")
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
                for name in ("kis_client.py", "kis_diagnostic.py")
            },
        }
        try:
            _, blob, cont = client.get(args.endpoint, params, max_attempts=1, diagnostics=True)
            # Successful market body is already checked for credential echoes by the client.
            (folder / "response.json").write_bytes(blob)
            result.update(
                status="request_succeeded", http_status=200, tr_cont=cont, response_sha256=sha(blob)
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
    print("진단 기록: " + str(folder / "diagnostic.json"))
    print("가격·권리 적격성 승인이 아닙니다. can_collect: false")
    return 0 if result["status"] == "request_succeeded" else 2
