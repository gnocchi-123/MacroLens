"""저장할 계산 결과만 사용한 한국어 Markdown 보고서."""


def markdown(result):
    label = "합성 샘플 — 실제 경제 자료 아님" if result["mode"] == "sample" else "FRED 실제 자료"
    lines = [
        "# MacroLens " + ("M2" if result.get("schema_version") == 2 else "M1") + " 보고서",
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
    for sid, item in sorted(result["indicators"].items()):
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
    for sid, item in sorted(result["indicators"].items()):
        if item["status"] == "valid":
            obs = item["observation"]
            lines += [
                f"- {sid}: {item['source']}",
                f"  - 이용 가능: {obs['available_at']}; 수집: {obs['retrieved_at']}; "
                f"버전: {obs['vintage_date']}; 발표 시각: 미확인",
                f"  - 규칙: {item['availability_policy']}",
            ]
    if result.get("schema_version") == 2:
        lines += [
            "",
            "## 실행 이력과 시점 적합성",
            "",
            f"- 실행 종류: {result.get('kind', '미기록')}; "
            f"원본 연결: {result.get('parent_run_id') or '없음'}",
            f"- 한국시간 주 시작일: {result['week_slot']}",
            f"- 시점 상태: {result['point_in_time_status']}; 엄밀한 과거 검증용 유효성: false",
            f"- 시점 규칙: {result['time_policy']}",
        ]
        if result.get("replay_verification"):
            lines.append(f"- 저장 입력 재현: {result['replay_verification']} (외부 API 미사용)")
        if result.get("kind") == "replay":
            lines.append("- 전주 비교는 원본 실행에 저장된 당시 연결과 run_id를 유지합니다.")
        history = result.get("history", {})
        previous = history.get("previous_week")
        lines += ["", "## 전주 비교", ""]
        if previous:
            lines += comparison_lines(previous)
        else:
            lines += ["전주 비교 없음"]
            if history.get("last_available"):
                lines += ["", "### 마지막 가용 기록과 별도 비교", ""]
                lines += comparison_lines(history["last_available"])
        lines += ["", *["- " + warning for warning in history.get("warnings", [])]]
    else:
        lines += [
            "",
            "## 실행 이력",
            "",
            "M1 기록: 선택 입력·전체 설정·주간 연결 미기록. 추정하지 않음.",
        ]
    lines += ["", "## 한계", "", *[f"- {x}" for x in result["limitations"]], ""]
    return "\n".join(lines)


def comparison_lines(comparison):
    lines = [f"- 비교 실행: `{comparison['before_run_id']}` → `{comparison['after_run_id']}`"]
    if "before_as_of" in comparison:
        lines += [
            f"- 실제 판단 시각: {comparison['before_as_of']} → {comparison['after_as_of']}",
            f"- 실제 간격: {comparison['interval_days']:.6f}일",
        ]
    if comparison["status"] != "comparable":
        return lines + ["- 비교 불가: " + "; ".join(comparison["reasons"])]
    lines += [
        "",
        "| 지표 | 관측일 이전 → 이후 | 원래 값 차이 | 변환값 차이 | z 차이 | 요인 차이 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for sid, item in sorted(comparison["indicators"].items()):
        lines.append(
            f"| {sid} | {item['before_observation_date']} → "
            f"{item['after_observation_date']} | {item['raw_value_delta']:+.4f} | "
            f"{item['transformed_value_delta']:+.4f} | {item['z_delta']:+.4f} | "
            f"{item['value_delta']:+.4f} |"
        )
    lines += [
        "",
        "| 가상 섹터 | 이전 | 이후 | 차이(점) | 순위 개선 | 요인별 기여도 차이(점) |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for sector, item in sorted(comparison["sectors"].items()):
        contribution = ", ".join(
            f"{k} {v:+.4f}" for k, v in sorted(item["contribution_deltas"].items())
        )
        lines.append(
            f"| {sector} | {item['before_score']:.4f} | {item['after_score']:.4f} | "
            f"{item['score_delta']:+.4f} | {item['rank_improvement']:+d} | {contribution} |"
        )
    return lines + [
        "",
        "반올림 전 기여도 변화 합 = 점수 변화 (허용 오차 1e-9점). "
        "순위 개선 = 이전 순위 − 이후 순위. 변화 원인의 인과 분해는 포함하지 않습니다.",
    ]
