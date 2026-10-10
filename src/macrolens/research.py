"""Frozen research configuration, independent of mutable live defaults."""

import json
from importlib.resources import files

from .ingest import DataError
from .storage import digest

RESEARCH_ENGINE = "m3-v1"
RESEARCH_VERSION = "macro-sector-hypothesis-v1"
FROZEN_CONFIG_HASH = "e51a89fc42c3adfa48268f09c120b0a140a40fccba04180745b7921a70e3f7ac"
FACTORS = {"growth", "employment", "inflation", "rates"}


def validate_research(config):
    """Reject edits under a frozen version before collection or calculation."""
    try:
        model = config["model"]
        sectors, metadata = model["sectors"], model["sector_metadata"]
        if (
            config["engine_version"] != RESEARCH_ENGINE
            or model["version"] != RESEARCH_VERSION
            or model["kind"] != "research"
            or model["ranking_policy"] != "average"
            or model["status"] != "frozen"
            or len(sectors) != 11
            or set(metadata) != set(sectors)
            or {x["factor"] for x in config["indicators"].values()} != FACTORS
        ):
            raise ValueError
        ids, codes = [], []
        for name, weights in sectors.items():
            if set(weights) != FACTORS or any(
                type(v) is not int or v not in (-2, -1, 0, 1, 2) for v in weights.values()
            ):
                raise ValueError
            if sum(abs(v) for v in weights.values()) == 0:
                raise ValueError
            details = metadata[name]
            if set(details["rationale_ids"]) != FACTORS:
                raise ValueError
            ids.extend(details["rationale_ids"].values())
            codes.append(details["gics"])
        if len(set(ids)) != 44 or len(set(codes)) != 11:
            raise ValueError
        if digest(config) != FROZEN_CONFIG_HASH:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise DataError("연구 모델 동결 설정 불일치: 계수·근거·계산 규칙을 확인하세요.") from None


def research_configuration():
    try:
        config = json.loads(
            files("macrolens").joinpath("models/research_v1.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        raise DataError("연구 모델 설정 파일을 읽을 수 없습니다.") from None
    validate_research(config)
    return config
