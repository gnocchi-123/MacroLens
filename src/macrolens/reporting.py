"""저장할 계산 결과만 사용한 한국어 Markdown 보고서."""


def markdown(result):
    label = "합성 샘플 — 실제 경제 자료 아님" if result["mode"] == "sample" else "FRED 실제 자료"
    lines = [
        "# MacroLens M1 보고서",
        "",
        f"**{label} / 미검증 연구용**",
        "",
        "실행 확인용 가상 계수입니다. 실제 섹터 추천·확률·기대수익률이 아닙니다.",
        "",
        f"- 실행: `{result['run_id']}`",
        f"- 자료 기준: {result['as_of']}",
        f"- 모델: {result['model_version']}",
        f"- 상태: {result['status']}",
        "",
        "## 네 거시지표",
        "",
        "| 지표 | 관측일 | 원래 값 | 변환값 | z값 | 요인값 | 상태 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for sid, item in result["indicators"].items():
        if item["status"] != "valid":
            lines.append(f"| {sid} | — | — | — | — | — | 보류: {item['reason']} |")
        else:
            lines.append(
                f"| {sid} | {item['observation']['observation_date']} | "
                f"{item['raw_value']:.4f} | {item['transformed_value']:.4f} | "
                f"{item['z']:.4f} | {item['value']:.4f} | 유효 |"
            )
    lines += [
        "",
        "변환: INDPRO·CPI 전년 대비 %, UNRATE 3개월 차이의 음수(%포인트), "
        "DGS10 28일 차이(%포인트). 요인 양수는 과거 평균보다 높은 값입니다.",
        "",
        "## 가상 섹터 점수",
        "",
    ]
    if not result["scores"]:
        lines += ["입력 불완전: 모든 점수·순위를 보류합니다."]
    else:
        lines += [
            "| 가상 섹터 | 순위 | 점수 | 성장 기여 | 고용 기여 | 물가 기여 | 금리 기여 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for item in result["scores"]:
            c = item["contributions"]
            lines.append(
                f"| {item['sector']} | {item['rank']} | {item['score']:.2f} | "
                f"{c['growth']:+.2f} | {c['employment']:+.2f} | "
                f"{c['inflation']:+.2f} | {c['rates']:+.2f} |"
            )
        lines += ["", "기여도 단위는 점입니다. 반올림 전 기여도 합 + 50 = 점수입니다."]
    lines += ["", "## 출처와 시점", ""]
    for sid, item in result["indicators"].items():
        if item["status"] == "valid":
            obs = item["observation"]
            lines += [
                f"- {sid}: {item['source']}",
                f"  - 이용 가능: {obs['available_at']}; 수집: {obs['retrieved_at']}; "
                f"버전: {obs['vintage_date']}; 발표 시각: 미확인",
                f"  - 규칙: {item['availability_policy']}",
            ]
    lines += ["", "## 한계", "", *[f"- {x}" for x in result["limitations"]], ""]
    return "\n".join(lines)
