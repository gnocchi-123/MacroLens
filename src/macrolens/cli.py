"""CLI collection, immutable history, offline replay and report regeneration."""

import argparse
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from .config import DEMO_MODEL, INDICATORS
from .features import calculate_feature
from .history import compare_runs, link_previous
from .ingest import DataError, fred_snapshot, sample_snapshot
from .reporting import markdown
from .scoring import score_sectors
from .snapshots import ENGINE_VERSION, instant, select_inputs, time_policy, week_slot
from .storage import (
    digest,
    finish,
    json_text,
    list_runs,
    load_run,
    new_run,
    read_json,
    run_path,
    write_json,
)


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


def configuration():
    return deepcopy(
        {"indicators": INDICATORS, "model": DEMO_MODEL, "engine_version": ENGINE_VERSION}
    )


def build_result(snapshot, run_id, config=None, inputs=None):
    config = configuration() if config is None else config
    inputs = select_inputs(snapshot, config["indicators"]) if inputs is None else inputs
    as_of = instant(inputs["as_of"])
    indicators, factors = {}, {}
    for sid, spec in config["indicators"].items():
        try:
            if sid in inputs["errors"]:
                raise DataError(inputs["errors"][sid])
            if sid not in inputs["series"]:
                raise DataError("지표 누락")
            item = calculate_feature(inputs["series"][sid], spec, as_of)
        except DataError as exc:
            item = {"status": "held", "reason": str(exc)}
        indicators[sid] = item
        factors[spec["factor"]] = item
    model = config["model"]
    scores = score_sectors(factors, model["sectors"])
    return {
        "schema_version": 2,
        "engine_version": config["engine_version"],
        "run_id": run_id,
        "as_of": inputs["as_of"],
        "generated_at": datetime.now(UTC).isoformat(),
        "previous_run_id": None,
        "mode": inputs["mode"],
        "model_version": model["version"],
        "model_purpose": model["purpose"],
        "validation_status": "미검증 연구용",
        "status": "demo_complete" if scores else "held",
        "strict_point_in_time": False,
        "time_policy": time_policy(inputs),
        "point_in_time_status": (
            "synthetic"
            if inputs["mode"] == "sample"
            else "date_only_conservative"
            if inputs.get("acquisition") == "historical_vintage"
            else "collected_inputs_only"
        ),
        "week_slot": week_slot(inputs["as_of"]),
        "config_hash": digest(config),
        "snapshot_hash": digest(snapshot),
        "input_hash": digest(inputs),
        **code_version(),
        "indicators": indicators,
        "factors": factors,
        "scores": scores,
        "limitations": [
            "계수는 실행 확인용 가상값이며 연구용 11개 섹터 계수는 미구현입니다.",
            "과거 vintage는 날짜 단위이며 장중 발표시각을 보장하지 않습니다. "
            "엄밀한 과거 검증용 유효성은 false입니다.",
            "현재 수집은 수집 시각부터, 과거 조회는 미국 중부 전일 vintage를 다음날 "
            "00:00부터 사용합니다. 최초 이용 가능 시각을 복원한 것이 아닙니다.",
            "샘플의 값·이용 시각·발표 주기는 합성입니다.",
            "관측 나이 제한(월별 75일·일별 7일)은 공식 발표 달력이 아닌 잠정 규칙입니다.",
            "기여도 차이는 산술 비교이며 새 발표·수정·기준창의 인과 분해가 아닙니다.",
            "가격·매매·백테스트·4주/13주 추세·자동 실행은 미구현입니다.",
        ],
    }


def run(mode, output, as_of=None, parent_run_id=None, kind="original", replay=None):
    as_of = instant(as_of) if as_of is not None else None
    if as_of is not None and as_of > datetime.now(UTC):
        raise DataError("미래 판단 시각은 사용할 수 없습니다.")
    parent = None
    if parent_run_id:
        parent_folder = run_path(output, parent_run_id)
        if kind == "rerun" and not (parent_folder / "COMPLETE").exists():
            record = read_json(parent_folder / "run.json")
            parent = {"result": {"mode": record["mode"]}}
        else:
            parent = load_run(output, parent_run_id, verify_report=kind != "replay")
    if parent and kind in ("correction", "rerun"):
        if parent["result"]["mode"] != mode:
            raise DataError("원본과 같은 모드를 지정하세요.")
        if kind == "correction":
            as_of = as_of or instant(parent["result"]["as_of"])
            if week_slot(as_of) != week_slot(parent["result"]["as_of"]):
                raise DataError("정정판은 원본과 같은 한국시간 주간 슬롯이어야 합니다.")
    config = deepcopy(replay["config"]) if replay else configuration()
    if replay and (config is None or config.get("engine_version") != ENGINE_VERSION):
        raise DataError("저장 설정/선택 입력이 없거나 재현 엔진을 지원하지 않습니다.")
    run_id, folder = new_run(
        output,
        {
            "schema_version": 2,
            "kind": kind,
            "parent_run_id": parent_run_id,
            "mode": mode,
            "requested_as_of": as_of.isoformat() if as_of else None,
            "operation": "saved_input_replay"
            if replay
            else "historical_vintage"
            if as_of and mode == "fred"
            else "collection",
        },
    )
    try:
        write_json(folder / "config.json", config)

        def raw_sink(sid, name, envelope):
            (folder / "raw").mkdir(exist_ok=True)
            write_json(folder / "raw" / f"{sid}-{name}.json", envelope)

        if replay:
            snapshot, inputs = deepcopy(replay["snapshot"]), deepcopy(replay["inputs"])
        else:
            if mode == "sample":
                snapshot = sample_snapshot(as_of) if as_of else sample_snapshot()
            else:
                snapshot = fred_snapshot(os.getenv("FRED_API_KEY"), as_of, raw_sink)
            write_json(folder / "snapshot.json", snapshot)
            inputs = select_inputs(snapshot, config["indicators"])
        if replay:
            write_json(folder / "snapshot.json", snapshot)
        write_json(folder / "inputs.json", inputs)
        result = build_result(snapshot, run_id, config, inputs)
        result.update(kind=kind, parent_run_id=parent_run_id)
        if replay:
            original = replay["result"]
            matched = all(result[k] == original[k] for k in ("scores", "indicators", "status"))
            result["replay_verification"] = "matched" if matched else "different"
            if not matched:
                raise DataError("저장 입력 재계산이 원래 결과와 다릅니다. 엔진 변경을 확인하세요.")
        if replay:
            result["history"] = deepcopy(replay["result"]["history"])
        else:
            loaded = {"result": result, "config": config, "inputs": inputs}
            result["history"] = link_previous(
                output, loaded, read_json(folder / "run.json")["started_at"]
            )
        previous = result["history"]["previous_week"]
        result["previous_run_id"] = previous["before_run_id"] if previous else None
        write_json(folder / "result.json", result)
        with (folder / "report.md").open("x", encoding="utf-8") as stream:
            stream.write(markdown(result))
        finish(folder, result["status"])
        return result, folder
    except Exception as exc:
        # Never persist arbitrary exception text (it may include a credential URL).
        message = (
            str(exc) if isinstance(exc, DataError) else "실행/저장 오류: " + type(exc).__name__
        )
        try:
            if not (folder / "status.json").exists():
                finish(folder, "failed", message)
        except OSError:
            pass  # A disk failure may leave only run.json: explicitly incomplete.
        raise DataError(f"{message} (run_id: {run_id})") from None


def emit(text, output=None):
    if output:
        with Path(output).open("x", encoding="utf-8") as stream:
            stream.write(text)
    else:
        print(text, end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description="MacroLens M2 — 미검증 연구용")
    sub = parser.add_subparsers(dest="command", required=True)
    weekly = sub.add_parser("weekly", help="새 수집·계산 실행")
    weekly.add_argument("--mode", choices=("sample", "fred"), default="sample")
    weekly.add_argument("--output", type=Path, default=Path("reports"))
    weekly.add_argument("--as-of", help="시간대가 있는 판단 시각; FRED 과거 vintage 조회")
    relation = weekly.add_mutually_exclusive_group()
    relation.add_argument("--corrects", metavar="RUN_ID", help="같은 주 원본의 정정판")
    relation.add_argument("--rerun-of", metavar="RUN_ID", help="원본을 참조하는 새 수집")
    for command in ("history", "show", "compare", "report", "replay"):
        child = sub.add_parser(command)
        child.add_argument("--root", type=Path, default=Path("reports"))
        if command == "compare":
            child.add_argument("before_run_id")
            child.add_argument("after_run_id")
        elif command != "history":
            child.add_argument("run_id")
        if command in ("compare", "report"):
            child.add_argument(
                "--output", type=Path, help="새 파일로 저장; 기존 파일 덮어쓰기 금지"
            )
    args = parser.parse_args(argv)
    try:
        if args.command == "history":
            emit(json_text(list_runs(args.root)))
            return 0
        if args.command == "compare":
            comparison = compare_runs(args.root, args.before_run_id, args.after_run_id)
            emit(json_text(comparison), args.output)
            return 0 if comparison["status"] == "comparable" else 2
        if args.command in ("show", "report", "replay"):
            saved = load_run(args.root, args.run_id, verify_report=args.command == "show")
            if args.command == "show":
                emit(
                    json_text(
                        {
                            "run": saved["record"],
                            "result": saved["result"],
                            "compatibility": "M2" if saved["config"] else "M1 read-only",
                        }
                    )
                )
                return 0
            if args.command == "report":
                emit(markdown(saved["result"]), args.output)
                return 0
            result, folder = run(
                saved["result"]["mode"],
                args.root,
                parent_run_id=args.run_id,
                kind="replay",
                replay=saved,
            )
        else:
            parent = args.corrects or args.rerun_of
            kind = "correction" if args.corrects else "rerun" if args.rerun_of else "original"
            result, folder = run(args.mode, args.output, args.as_of, parent, kind)
    except (DataError, OSError) as exc:
        message = str(exc) if isinstance(exc, DataError) else "파일 읽기/저장 실패 (덮어쓰기 불가)"
        print(f"실행 실패: {message}", file=sys.stderr)
        return 1
    print(f"{result['status']} / 미검증 연구용 / {result['mode']}")
    print(f"run_id: {result['run_id']}")
    print(f"결과: {folder / 'result.json'}")
    print(f"보고서: {folder / 'report.md'}")
    if result["status"] == "held":
        print("자료 문제로 점수·순위를 보류했습니다. report.md에서 사유를 확인하세요.")
        return 2
    return 0
