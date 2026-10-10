"""Comparable run deltas and one representative per Korean calendar week."""

import math
from datetime import date, timedelta

from .ingest import DataError
from .snapshots import instant, week_slot
from .storage import list_runs, load_run, read_json


def compare_loaded(before, after):
    old, new = before["result"], after["result"]
    comparison = {
        "before_run_id": old["run_id"],
        "after_run_id": new["run_id"],
        "before_as_of": old["as_of"],
        "after_as_of": new["as_of"],
        "interval_days": (instant(new["as_of"]) - instant(old["as_of"])).total_seconds() / 86400,
        "status": "not_comparable",
        "reasons": [],
        "indicators": {},
        "factors": {},
        "sectors": {},
    }
    reasons = comparison["reasons"]
    if before["config"] is None or after["config"] is None:
        reasons.append("M1에는 전체 설정·선택 입력이 없어 비교 가능성을 확정할 수 없음")
    for field, label in (
        ("model_version", "모델 버전"),
        ("config_hash", "모델/입력 설정"),
        ("engine_version", "계산 엔진"),
        ("mode", "샘플/실제 모드"),
        ("time_policy", "시점 사용 규칙"),
    ):
        if old.get(field) is None or old.get(field) != new.get(field):
            reasons.append(label + " 불일치 또는 미상")
    if any(
        r.get("status") not in ("demo_complete", "research_complete") or not r.get("scores")
        for r in (old, new)
    ):
        reasons.append("보류/실패한 실행 또는 유효 점수 없음")
    if set(old["indicators"]) != set(new["indicators"]):
        reasons.append("활성 지표 구성 불일치")
    if reasons:
        return comparison
    try:
        for sid, left in old["indicators"].items():
            right = new["indicators"][sid]
            if left["status"] != "valid" or right["status"] != "valid":
                raise DataError("유효하지 않은 지표")
            if any(left.get(k) != right.get(k) for k in ("source", "transform", "window")):
                raise DataError("지표 출처·변환·표준화 창 불일치")
            comparison["indicators"][sid] = {
                "before_observation_date": left["observation"]["observation_date"],
                "after_observation_date": right["observation"]["observation_date"],
                "before_vintage": left["observation"].get("vintage_date"),
                "after_vintage": right["observation"].get("vintage_date"),
                **{
                    key + "_delta": right[key] - left[key]
                    for key in ("raw_value", "transformed_value", "z", "value")
                },
            }
        for sid, spec in before["config"]["indicators"].items():
            left, right = old["indicators"][sid], new["indicators"][sid]
            comparison["factors"][spec["factor"]] = {
                "before_value": left["value"],
                "after_value": right["value"],
                "delta": right["value"] - left["value"],
            }
        left_scores = {x["sector"]: x for x in old["scores"]}
        right_scores = {x["sector"]: x for x in new["scores"]}
        if left_scores.keys() != right_scores.keys():
            raise DataError("섹터 구성 불일치")
        for sector, left in left_scores.items():
            right = right_scores[sector]
            if left["contributions"].keys() != right["contributions"].keys():
                raise DataError("기여 요인 구성 불일치")
            for item in (left, right):
                if not math.isclose(
                    50 + sum(item["contributions"].values()), item["score"], rel_tol=0, abs_tol=1e-9
                ):
                    raise DataError("기존 점수와 기여도 합 불일치")
            delta = right["score"] - left["score"]
            contributions = {
                k: right["contributions"][k] - v for k, v in left["contributions"].items()
            }
            if not math.isclose(sum(contributions.values()), delta, rel_tol=0, abs_tol=1e-9):
                raise DataError("기여도 변화 합과 점수 변화 불일치")
            comparison["sectors"][sector] = {
                "before_score": left["score"],
                "after_score": right["score"],
                "score_delta": delta,
                "contribution_deltas": contributions,
                "contribution_delta_sum": sum(contributions.values()),
                "before_rank": left["rank"],
                "after_rank": right["rank"],
                "rank_improvement": left["rank"] - right["rank"],
            }
        for item in comparison["indicators"].values():
            if any(not math.isfinite(v) for k, v in item.items() if k.endswith("_delta")):
                raise DataError("비유한 지표 차이")
    except (KeyError, TypeError, ValueError) as exc:
        reasons.append(str(exc) if isinstance(exc, DataError) else "저장 결과 형식 오류")
        comparison["indicators"], comparison["factors"], comparison["sectors"] = {}, {}, {}
        return comparison
    comparison["status"] = "comparable"
    return comparison


def compare_runs(root, before_id, after_id):
    try:
        return compare_loaded(load_run(root, before_id), load_run(root, after_id))
    except DataError as exc:
        return {
            "status": "not_comparable",
            "reasons": [str(exc)],
            "before_run_id": before_id,
            "after_run_id": after_id,
            "indicators": {},
            "factors": {},
            "sectors": {},
        }


def link_previous(root, current, started_at):
    result = current["result"]
    slot = week_slot(result["as_of"])
    previous_slot = (date.fromisoformat(slot) - timedelta(days=7)).isoformat()
    representatives = {}
    warnings = []
    for row in list_runs(root):
        if row["run_id"] == result["run_id"]:
            continue
        if row["status"] == "unreadable":
            warnings.append(row["run_id"] + ": 손상/읽기 불가 기록 제외")
            continue
        if row.get("schema_version") != 2 or row["status"] not in (
            "demo_complete",
            "research_complete",
            "held",
        ):
            continue
        candidate = load_run(root, row["run_id"])
        other, record = candidate["result"], candidate["record"]
        finished = read_json(candidate["folder"] / "status.json")["finished_at"]
        if instant(finished) >= instant(started_at):
            continue
        if record.get("kind") == "replay" or instant(record["started_at"]) >= instant(started_at):
            continue
        if any(other.get(k) != result.get(k) for k in ("mode", "time_policy")):
            continue
        if other.get("model_kind", "demo") != result.get("model_kind", "demo"):
            continue
        other_slot = week_slot(other["as_of"])
        if other_slot >= slot:
            continue
        order = (instant(record["started_at"]), other["run_id"])
        if other_slot not in representatives or order > representatives[other_slot][0]:
            representatives[other_slot] = (order, candidate)
    prior = representatives.get(previous_slot)
    links = {
        "week_slot": slot,
        "previous_slot": previous_slot,
        "selection_rule": (
            "같은 모드·시점규칙, 시작 전 완료된 실행 중 주별 마지막 생성 실행; 재현 제외"
        ),
        "previous_week": None,
        "last_available": None,
        "warnings": warnings,
    }
    if result.get("model_kind") == "research":
        links["selection_rule"] += "; 같은 연구 모델 계열 (버전 변경은 비교 불가 표시)"
    if prior:
        links["previous_week"] = compare_loaded(prior[1], current)
    elif representatives:
        nearest = representatives[max(representatives)][1]
        links["last_available"] = compare_loaded(nearest, current)
    return links
