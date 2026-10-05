"""한 명령으로 수집 → 계산 → JSON/Markdown 저장."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .config import DEMO_MODEL, INDICATORS
from .features import calculate_feature
from .ingest import DataError, fred_snapshot, sample_snapshot
from .reporting import markdown
from .scoring import score_sectors


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def code_version():
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return {"commit_sha": None, "working_tree_dirty": None}
    try:

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL, text=True
            ).strip()

        return {
            "commit_sha": git("rev-parse", "HEAD"),
            "working_tree_dirty": bool(git("status", "--porcelain")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"commit_sha": None, "working_tree_dirty": None}


def build_result(snapshot, run_id):
    as_of = datetime.fromisoformat(snapshot["as_of"])
    indicators, factors = {}, {}
    for sid, spec in INDICATORS.items():
        try:
            if sid in snapshot["errors"]:
                raise DataError(snapshot["errors"][sid])
            if sid not in snapshot["series"]:
                raise DataError("지표 누락")
            item = calculate_feature(snapshot["series"][sid], spec, as_of)
        except DataError as exc:
            item = {"status": "held", "reason": str(exc)}
        indicators[sid] = item
        factors[spec["factor"]] = item
    scores = score_sectors(factors, DEMO_MODEL["sectors"])
    return {
        "schema_version": 1,
        "run_id": run_id,
        "as_of": snapshot["as_of"],
        "generated_at": datetime.now(UTC).isoformat(),
        "previous_run_id": None,
        "mode": snapshot["mode"],
        "model_version": DEMO_MODEL["version"],
        "model_purpose": DEMO_MODEL["purpose"],
        "validation_status": "미검증 연구용",
        "status": "demo_complete" if scores else "held",
        "strict_point_in_time": False,
        "config_hash": digest({"indicators": INDICATORS, "model": DEMO_MODEL}),
        "snapshot_hash": digest(snapshot),
        **code_version(),
        "indicators": indicators,
        "scores": scores,
        "limitations": [
            "계수는 실행 확인용 가상값이며 연구용 11개 섹터 계수는 미구현입니다.",
            "발표 시각·과거 버전별 선택이 미완성이라 엄밀한 과거 검증에 사용할 수 없습니다.",
            "실제 모드는 전일 미국 중부시간 vintage를 수집 시각부터 사용합니다.",
            "샘플 모드의 값·이용 시각·발표 주기는 모두 합성입니다.",
            "지연 한도는 관측일부터 월별 75일·일별 7일인 잠정 규칙입니다. "
            "발표 달력에 따른 미발표/지연 구분은 M2 이후 보완합니다.",
            "전주 비교·가격·매매·백테스트·자동 실행은 미구현입니다.",
        ],
    }


def run(mode, output):
    snapshot = sample_snapshot() if mode == "sample" else fred_snapshot(os.getenv("FRED_API_KEY"))
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    result = build_result(snapshot, run_id)
    folder = Path(output) / run_id
    folder.mkdir(parents=True, exist_ok=False)
    # 개별 실행 디렉터리를 사용해 재실행이 기존 기록을 덮어쓰지 않는다.
    for name, value in (("snapshot.json", snapshot), ("result.json", result)):
        (folder / name).write_text(json_text(value), encoding="utf-8")
    (folder / "report.md").write_text(markdown(result), encoding="utf-8")
    # 저장 중 실패한 디렉터리와 정상적으로 끝난 실행을 구분한다.
    (folder / "COMPLETE").write_text(result["status"] + "\n", encoding="utf-8")
    return result, folder


def main(argv=None):
    parser = argparse.ArgumentParser(description="MacroLens M1 — 미검증 연구용")
    sub = parser.add_subparsers(dest="command", required=True)
    weekly = sub.add_parser("weekly", help="네 지표 계산과 JSON·Markdown 생성")
    weekly.add_argument("--mode", choices=("sample", "fred"), default="sample")
    weekly.add_argument("--output", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)
    try:
        result, folder = run(args.mode, args.output)
    except (DataError, OSError) as exc:
        print(f"실행 실패: {exc}", file=sys.stderr)
        return 1
    print(f"{result['status']} / 미검증 연구용 / {args.mode}")
    print(f"결과: {folder / 'result.json'}")
    print(f"보고서: {folder / 'report.md'}")
    if result["status"] == "held":
        print("자료 문제로 점수·순위를 보류했습니다. report.md에서 사유를 확인하세요.")
        return 2
    return 0
