"""계수와 요인으로부터 점수와 합이 일치하는 기여도 계산."""

import math

from .ingest import DataError


def score_sectors(factors, coefficients):
    if not factors or any(item["status"] != "valid" for item in factors.values()):
        return []
    if any(not math.isfinite(item["value"]) or abs(item["value"]) > 1 for item in factors.values()):
        raise DataError("요인 범위 오류")
    scores = []
    for name, weights in coefficients.items():
        if set(weights) != set(factors):
            raise DataError("계수와 활성 요인 불일치")
        if not all(math.isfinite(x) for x in weights.values()):
            raise DataError("비유한 계수")
        denominator = sum(abs(x) for x in weights.values())
        if denominator == 0:
            raise DataError("계수 절댓값 합이 0")
        contributions = {
            key: 50 * weight * factors[key]["value"] / denominator
            for key, weight in weights.items()
        }
        scores.append(
            {
                "sector": name,
                "score": 50 + sum(contributions.values()),
                "contributions": contributions,
            }
        )
    scores.sort(key=lambda item: (-item["score"], item["sector"]))
    # 공동 순위: 1, 1, 3. 부동소수점 값은 반올림 전 점수로 비교한다.
    for index, item in enumerate(scores):
        item["rank"] = (
            scores[index - 1]["rank"]
            if index and item["score"] == scores[index - 1]["score"]
            else index + 1
        )
    return scores
