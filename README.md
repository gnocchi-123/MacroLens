# MacroLens

미국 거시경제 기반 섹터 분석. **M1: 네 지표의 작은 동작 버전**입니다.
실행 확인용 가상 계수를 사용하며, 모든 출력은 **미검증 연구용**입니다.

## 빠른 시작 (Codespaces)

저장소 루트에서 실행합니다. 기존 Python 3.14.2와 `.venv`를 사용합니다.

```bash
source .venv/bin/activate
python --version
python -m pip install uv
uv sync --locked --inexact
uv run --locked python -m macrolens weekly --mode sample
```

`uv`가 이미 있으면 설치 명령은 생략합니다. `--inexact`는 기존 가상환경의 다른
패키지를 유지합니다. 실행 패키지는 Python 표준 라이브러리만 사용하고,
개발 도구 pytest·Ruff는 `uv.lock`으로 고정합니다.

정상 실행은 `demo_complete / 미검증 연구용 / sample`과 파일 경로를 출력합니다.
`reports/<run_id>/report.md`를 VS Code에서 열고 **Ctrl+Shift+V**로 미리 봅니다.
샘플은 2026-10-05 09:00 KST 기준의 **합성 자료**이며 실제 경제 수치가 아닙니다.
실행할 때마다 새 디렉터리를 만들며 기존 결과를 덮어쓰지 않습니다.

## 실제 FRED 자료 실행

[FRED API 키 안내](https://fred.stlouisfed.org/docs/api/api_key.html)에 따라 발급한
본인 키를 Codespaces 터미널에서 환경변수로 설정합니다. 키를 Git이나 채팅에
붙여 넣지 않습니다. 아래 `read -s`는 화면과 셸 명령 이력에 입력값을 남기지 않습니다.

```bash
read -rsp 'FRED API key: ' FRED_API_KEY
export FRED_API_KEY
uv run --locked python -m macrolens weekly --mode fred
unset FRED_API_KEY
```

또는 Codespaces의 저장소용 Secret `FRED_API_KEY`를 설정합니다.
`.env` 자동 로딩은 지원하지 않습니다. 실제 모드가 실패해도 샘플로 대체하지 않습니다.

- 수집: INDPRO, UNRATE, CPIAUCSL, DGS10의 메타데이터와 최근 10년 관측값.
- 시점: 미국 중부시간 전일의 FRED vintage. 각 값은 실제 수집 시각부터 사용합니다.
- 실제 발표 시각은 `null`입니다. `available_at`을 발표 시각으로 해석하지 않습니다.
- 실제 입력을 사용해도 섹터 계수는 가상값입니다. 실제 섹터 추천은 아닙니다.
- 과거 `--as-of` 실행은 아직 제공하지 않습니다. M2에서 과거 버전·시점 선택을 구현합니다.

## 산출물과 상태

| 파일 | 내용 |
| --- | --- |
| `snapshot.json` | 입력·출처·메타데이터·관측/이용/수집 시각·버전·실제 API 응답 |
| `result.json` | 요인·점수·기여도·상태·설정/입력 해시·코드 버전 |
| `report.md` | 한국어 보고서 |
| `COMPLETE` | 해당 실행의 파일 저장 완료 표시 |

`COMPLETE`가 없는 실행 디렉터리는 저장 중 실패한 불완전 기록입니다.
이 파일은 데이터 유효성 표시가 아닙니다. 자료 상태는 `result.json`의 `status`를 확인합니다.
`data/`, `reports/`는 Git에서 제외됩니다. Codespace 삭제 전 필요한 기록을 별도 보관하세요.

| 종료 코드 | 의미 | 확인할 것 |
| --- | --- | --- |
| 0 | 가상 점수·보고서 생성 성공 | `demo_complete`; 샘플/실제 모드 구분 |
| 1 | 키 미설정·저장 실패 등 | 터미널 오류와 환경변수·출력 경로 |
| 2 | 입력 불완전으로 점수·순위 보류 | `report.md`의 지표별 사유 |

HTTP 400/403은 키·권한, 429는 사용량, 연결 오류는 네트워크를 확인합니다.
재실행은 새 기록을 만듭니다. 메타데이터가 바뀌면 설정을 검토하기 전 계산을 보류합니다.

## 검증

```bash
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

테스트는 네트워크·API 키 없이 실행합니다. FRED 테스트는 가짜 응답을 사용하므로
실제 키를 통한 수집 성공을 의미하지 않습니다.

## 파일별 역할

| 경로 | 역할 |
| --- | --- |
| `src/macrolens/config.py` | 네 지표의 단위·빈도·변환·창·지연 한도와 가상 계수 |
| `src/macrolens/ingest.py` | 합성 샘플 생성, FRED 메타데이터·원본 수집 |
| `src/macrolens/features.py` | 시각 필터·지표 변환·표준화·자료 품질 검사 |
| `src/macrolens/scoring.py` | 가상 점수·기여도·공동 순위 |
| `src/macrolens/reporting.py` | 계산 결과로부터 한국어 Markdown 생성 |
| `src/macrolens/cli.py` | 명령 처리·실행 식별자·JSON 저장 |
| `tests/` | 손계산·누락·미래 시각·수집 오류·전체 실행 검증 |
| `docs/m1_implementation.md` | M1의 범위·설계 결정·제약·다음 단계 |

계획 원본은 `docs/macro_sector_project_plan_v2.md`, 맥락은
`docs/MacroLens_Project_Context_v2.txt`입니다. M1 구현 기록은 원본 계획과 구분해 남깁니다.
