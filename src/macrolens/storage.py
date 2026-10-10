"""Append-only run directories and integrity checks; no shared mutable index."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .ingest import DataError


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def write_json(path, value):
    # Exclusive creation also protects against accidental reuse and concurrent writers.
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json_text(value))


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DataError("저장 JSON을 읽을 수 없습니다: " + Path(path).name) from None


def new_run(root, record):
    now = datetime.now(UTC)
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    folder = Path(root) / run_id
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "run.json", {**record, "run_id": run_id, "started_at": now.isoformat()})
    return run_id, folder


def finish(folder, status, error=None):
    write_json(
        folder / "status.json",
        {
            "status": status,
            "finished_at": datetime.now(UTC).isoformat(),
            "error": error,
        },
    )
    if status != "failed":
        files = sorted(p for p in folder.rglob("*") if p.is_file())
        write_json(
            folder / "manifest.json",
            {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        )
        with (folder / "COMPLETE").open("x", encoding="utf-8") as stream:
            stream.write(status + "\n")


def run_path(root, run_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise DataError("잘못된 run_id")
    root = Path(root).resolve()
    folder = root / run_id
    if folder.is_symlink() or folder.resolve().parent != root or not folder.is_dir():
        raise DataError("실행 기록을 찾을 수 없습니다.")
    return folder


def load_run(root, run_id, verify_report=True):
    try:
        return _load_run(root, run_id, verify_report)
    except DataError:
        raise
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        raise DataError("저장 실행의 구조·필드가 손상되었습니다.") from None


def _load_run(root, run_id, verify_report):
    folder = run_path(root, run_id)
    if not (folder / "COMPLETE").is_file():
        raise DataError("실패 또는 불완전 실행: COMPLETE 없음")
    result = read_json(folder / "result.json")
    if result.get("run_id") != run_id:
        raise DataError("실행 ID 불일치")
    schema = result.get("schema_version")
    if schema == 2:
        manifest = read_json(folder / "manifest.json")
        required = {
            "run.json",
            "status.json",
            "config.json",
            "snapshot.json",
            "inputs.json",
            "result.json",
            "report.md",
        }
        if not isinstance(manifest, dict) or not required <= manifest.keys():
            raise DataError("실행 무결성 목록 누락")
        for name, expected in manifest.items():
            if name == "report.md" and not verify_report:
                continue  # Derived text may be missing; source data must still verify.
            path = folder / name
            if path.resolve().is_relative_to(folder.resolve()) and path.is_file():
                if hashlib.sha256(path.read_bytes()).hexdigest() == expected:
                    continue
            raise DataError("저장 파일 무결성 오류: " + name)
        record = read_json(folder / "run.json")
        config = read_json(folder / "config.json")
        inputs = read_json(folder / "inputs.json")
        snapshot = read_json(folder / "snapshot.json")
        for key, value in (
            ("config_hash", config),
            ("input_hash", inputs),
            ("snapshot_hash", snapshot),
        ):
            if result.get(key) != digest(value):
                raise DataError("저장 내용 해시 불일치: " + key)
        if (folder / "COMPLETE").read_text().strip() != result["status"]:
            raise DataError("완료 상태 불일치")
    elif schema == 1:
        # M1 never saved its full config or the selected inputs. Do not invent them.
        record, config, inputs = {}, None, None
        snapshot = (
            read_json(folder / "snapshot.json") if (folder / "snapshot.json").exists() else None
        )
        if snapshot is not None and result.get("snapshot_hash") != digest(snapshot):
            raise DataError("M1 snapshot 해시 불일치")
    else:
        raise DataError("지원하지 않는 실행 스키마")
    return {
        "result": result,
        "record": record,
        "config": config,
        "inputs": inputs,
        "snapshot": snapshot,
        "folder": folder,
    }


def list_runs(root):
    records = []
    root = Path(root)
    if not root.exists():
        return records
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.is_symlink():
            continue
        if not any(
            (folder / name).exists()
            for name in ("run.json", "result.json", "status.json", "COMPLETE")
        ) and not re.fullmatch(r"\d{8}T\d{6}Z-[a-f0-9]{12}", folder.name):
            continue
        item = {"run_id": folder.name, "status": "incomplete"}
        try:
            if (folder / "run.json").is_file():
                record = read_json(folder / "run.json")
                item.update(
                    {
                        k: record.get(k)
                        for k in ("kind", "parent_run_id", "started_at", "requested_as_of")
                    }
                )
            if (folder / "COMPLETE").is_file():
                loaded = load_run(root, folder.name)
                result = loaded["result"]
                item.update(
                    {
                        k: result.get(k)
                        for k in (
                            "status",
                            "as_of",
                            "mode",
                            "schema_version",
                            "week_slot",
                            "model_version",
                            "model_kind",
                        )
                    }
                )
            elif (folder / "status.json").is_file():
                status = read_json(folder / "status.json")
                item.update(status)
                if status["status"] != "failed":
                    item["status"] = "incomplete"
        except (DataError, OSError, KeyError, TypeError, AttributeError) as exc:
            item.update(
                status="unreadable",
                error=str(exc) if isinstance(exc, DataError) else "실행 기록 구조/읽기 오류",
            )
        records.append(item)
    return records
