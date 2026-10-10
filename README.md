# MacroLens

미국 거시경제 기반 섹터 분석. **M3: 11개 섹터 연구 점수**와 M2의 자료 시점·실행 이력·전주 비교를 제공합니다.
기본 명령은 기존 가상 모델이며, `--model research-v1`으로 동결한 연구 가설 계수를 선택합니다.
모든 출력은 **미검증 연구용**입니다.

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
- 기본 demo-v1은 가상 계수, research-v1은 미검증 정성 가설 계수입니다. 실제 자료 수집 성공이 투자 효용 검증을 뜻하지 않습니다.
- 과거 조회는 아래 `--as-of` 명령을 사용합니다. 저장 입력 재현(`replay`)과 다릅니다.

## 산출물과 상태

| 파일 | 내용 |
| --- | --- |
| `run.json` | 실행 시작 시각·종류·원본 run_id·요청 기준 시각 |
| `raw/*.json` | FRED 응답별 즉시 보존; 키를 제외한 요청 매개변수·수집 시각 |
| `snapshot.json` | 정규화 관측·출처·메타데이터·버전·실제 API 응답 |
| `inputs.json` | 시점 검사를 거쳐 계산기에 전달한 입력·제외 건수 |
| `config.json` | 지표 설정·가상 계수·모델 및 계산 엔진 버전 |
| `result.json` | 요인·점수·기여도·상태·설정/입력 해시·코드 버전 |
| `report.md` | 한국어 보고서·전주 또는 마지막 가용 기록과의 비교 |
| `status.json` | 완료/보류/실패와 종료 시각·안전한 오류 설명 |
| `manifest.json` | 완료된 실행 파일의 SHA-256 무결성 목록 |
| `COMPLETE` | 해당 실행의 파일 저장 완료 표시 |

`COMPLETE`가 없는 실행 디렉터리는 저장 중 실패한 불완전 기록입니다.
키 미설정 등 실패도 새 run_id에 남깁니다. 강제 종료·디스크 오류는 불완전 기록일 수 있습니다.
이 파일은 데이터 유효성 표시가 아닙니다. 자료 상태는 `result.json`의 `status`를 확인합니다.
`data/`, `reports/`는 Git에서 제외됩니다. Codespace 삭제 전 필요한 기록을 별도 보관하세요.

| 종료 코드 | 의미 | 확인할 것 |
| --- | --- | --- |
| 0 | 점수·보고서 생성 성공 | 가상 `demo_complete` / 연구 `research_complete`; 샘플/실제 구분 |
| 1 | 키 미설정·저장 실패 등 | 터미널 오류와 환경변수·출력 경로 |
| 2 | 점수·순위 보류 또는 두 실행 비교 불가 | 보고서 또는 비교 JSON의 사유 |

HTTP 400/403은 키·권한, 429는 사용량, 연결 오류는 네트워크를 확인합니다.
재실행은 새 기록을 만듭니다. 메타데이터가 바뀌면 설정을 검토하기 전 계산을 보류합니다.

## M3: 11개 섹터 연구 모델

[계수 근거서](docs/model_card.md), [동결 기록](docs/m3_freeze.md),
[구현·검수 기록](docs/m3_implementation.md)을 먼저 확인하세요.
`research-v1`은 경제적 정성 가설이며 실제 수익률로 추정하거나 검증한 모델이 아닙니다.
`research_complete`는 계산 완료를 의미하며 투자 성과 검증 완료가 아닙니다.

```bash
uv sync --locked --inexact
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .

# 합성 자료로 11개 섹터 점수 계산 (API 키 불필요)
uv run --locked python -m macrolens weekly --mode sample --model research-v1

# 실제 FRED 자료 (기존 FRED_API_KEY 환경변수 필요)
uv run --locked python -m macrolens weekly --mode fred --model research-v1
```

- 성공: `research_complete`, 11개 섹터와 네 요인의 기여도, 평균 순위.
- 보류: `held`, 종료 코드 2. 계수가 0인 지표라도 네 활성 입력 중 하나가 무효이면 전체 보류.
- `--model` 생략: 기존 `demo-v1`과 가상 A/B/C, `demo_complete` 유지.
- 설정 JSON은 패키지의 `src/macrolens/models/research_v1.json`에 있습니다.
  계수·근거 ID·요인 정의·시점 정책을 매 실행의 `config.json`에 함께 보존합니다.
  동결 해시와 다른 설정은 실행 전에 거부합니다. 변경 실험은 새 버전으로 구현해야 합니다.
- 정정/재수집은 원본 설정을 상속합니다. 다른 모델을 명시하면 거부합니다.
  모델 변경은 원본을 수정하지 않고 별도의 새 실행으로 기록하세요.
- `replay`는 저장 모델·입력만 사용합니다. M2 `m2-v1` 재현과 M1 읽기 호환을 유지합니다.
- 같은 폴더에 가상/연구 실행을 저장할 수 있습니다. 자동 전주 연결은 모델 계열도 구분합니다.
  서로 다른 모델/엔진/설정의 직접 비교는 수치 차이를 만들지 않고 비교 불가 사유를 표시합니다.
- 평균 순위 예: 1·2위 동점은 각각 1.5위. 동점 표시는 GICS 코드 순이며 우열이 아닙니다.
- `strict_point_in_time=false`, 샘플/실제 자료 구분, 미검증 표시를 유지합니다.

아래 두 실행 후 출력된 실제 run_id로 기존 `compare`, `replay`, `report`를 사용합니다.

```bash
uv run --locked python -m macrolens weekly --mode sample --model research-v1 --as-of '2026-09-28T09:00:00+09:00'
uv run --locked python -m macrolens weekly --mode sample --model research-v1 --as-of '2026-10-05T09:00:00+09:00'
```

정상 비교는 `comparable`, 재현은 `result.json`의 `replay_verification: matched`입니다.
점수는 0~100이며 반올림 전 `50 + 기여도 합 = 점수`입니다.
에너지의 높은 샘플 점수 등을 실제 추천으로 해석하지 마세요.
가격·백테스트·포트폴리오 거래는 M4의 작업입니다.

## M2 명령

모든 명령은 저장소 루트에서 실행합니다. `weekly --output`은 실행 저장 **디렉터리**,
조회 명령의 `--root`는 그 디렉터리입니다(둘 다 기본 `reports`). `report`·`compare`의
`--output`은 새 **파일**이며 이미 존재하면 종료 코드 1로 거부합니다.
아래 `RUN_ID`, `BEFORE_ID`, `AFTER_ID`는 실제 출력에서 복사한 ID로 바꿉니다.

```bash
uv run --locked python -m macrolens history
uv run --locked python -m macrolens show RUN_ID
uv run --locked python -m macrolens compare BEFORE_ID AFTER_ID
uv run --locked python -m macrolens report RUN_ID --output reports/regenerated.md
uv run --locked python -m macrolens replay RUN_ID
```

- `history`: 완료·보류·실패·불완전·손상 기록 목록(JSON).
- `show`: 저장 실행의 설정 해시·시점·지표·가상 점수·연결 상태(JSON).
- `compare`: **AFTER − BEFORE**의 지표 원래 값·변환값·z·요인·점수·기여도 차이(JSON).
  비교 가능하면 `comparable`, 불가능하면 `not_comparable`와 `reasons`; 종료 코드는 0/2입니다.
- `report`: 저장된 결과와 당시 저장된 비교로 한국어 보고서를 재생성합니다. API를 호출하거나
  현재 이력으로 전주 연결을 다시 선택하지 않습니다. `--output` 생략 시 표준 출력입니다.
- `replay`: 저장된 **선택 입력 + 전체 설정**으로 새 run_id에 재계산합니다. API를 호출하지
  않으며 원본 지표·점수·상태가 같아야 `replay_verification: matched`입니다.
  원본의 전주 비교 문맥도 유지합니다. 새 재현 실행은 주별 대표 선택에서 제외합니다.

현재 수집, 과거 조회, 재현은 다음처럼 구분합니다.

```bash
# 현재 실제 수집: FRED_API_KEY 필요
uv run --locked python -m macrolens weekly --mode fred

# 과거 자료 버전을 지금 조회: FRED_API_KEY 필요; 시간대 필수
uv run --locked python -m macrolens weekly --mode fred --as-of '2026-10-05T09:00:00+09:00'

# 다른 주의 합성 샘플 (실제 경제 자료가 아님)
uv run --locked python -m macrolens weekly --mode sample --as-of '2026-09-28T09:00:00+09:00'

# 같은 주 정정판: 새 수집·계산, 원본은 보존. FRED이면 --mode fred 명시
uv run --locked python -m macrolens weekly --mode sample --corrects RUN_ID

# 원본(실패 기록 포함)을 참조한 재수집: 현재 기본 기준 또는 --as-of 사용
uv run --locked python -m macrolens weekly --mode sample --rerun-of RUN_ID
```

`--corrects`는 기준 시각 생략 시 원본 `as_of`를 사용합니다. 다른 주로 이동할 수 없습니다.
FRED 현재 수집을 정정하면 과거 vintage 조회가 되므로 원본과 시점 규칙이 달라 직접 비교가
보류될 수 있습니다. 이는 당시 수집과 사후 조회를 같은 자료로 가장하지 않기 위한 제한입니다.
`--rerun-of`는 원본 시각을 자동 상속하지 않습니다. 같은 과거 조회를 재시도하려면
원래 `--as-of`를 함께 지정하세요. 사유 없는 기본 `weekly` 실행은 `original`로 기록합니다.

**시점 한계:** 과거 조회는 판단 시각을 미국 중부시간으로 변환한 뒤 **전일**을 vintage로
선택해 메타데이터·관측값의 `realtime_start=realtime_end`에 지정합니다. 예를 들어
2026-10-05 09:00 KST는 미국 중부 10월 4일 저녁이므로 vintage는 **10월 3일**입니다.
그 vintage 전체를 미국 중부 다음날 00:00부터 사용한다는 보수적 규칙을 적용합니다.
실제 발표시각은 `null`이며, 모든 결과의 `strict_point_in_time`은 **false**입니다.
이는 날짜 단위 자료 버전 조회이며 엄밀한 장중 과거 검증을 보장하지 않습니다.

**전주 선택:** 한국시간 월요일 00:00~다음 월요일 00:00의 슬롯입니다.
같은 모드·시점 규칙에서 이번 실행 시작 전에 완료된 기록 중 주별 가장 나중에 시작한
실행(동률은 run_id 사전순 마지막)을 선택합니다. `held`도 대표가 될 수 있고 그때는 비교 불가를
표시합니다. 실패·불완전·손상·재현·M1 기록은 자동 연결 대상에서 제외합니다.
동일 주 재실행·정정은 추가 주가 아닙니다. 정확히 전주가 없으면 **전주 비교 없음**,
더 오래된 가용 기록은 실제 판단 날짜와 간격을 붙여 별도 비교합니다.
이미 저장된 연결은 나중에 정정판이 생겨도 바꾸지 않습니다.

**M1 호환:** 원본 파일을 그대로 두고 조회·보고서 재생성을 지원합니다.
M1에는 전체 설정과 선택 입력이 저장되지 않았으므로 정밀 재계산과 M2 비교·주별 대표
선택은 지원하지 않습니다. 없는 정보를 현재 설정으로 추정해 채우지 않습니다.

## Codespaces 검수 예제 (네트워크·키 불필요)

아래 블록은 독립된 디렉터리를 만들어 반복 실행해도 기존 기록을 보존합니다.
정상적으로는 두 샘플 모두 `demo_complete`, 비교 `comparable`, 재현 `matched`가 나옵니다.

```bash
uv sync --locked --inexact
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .

mkdir -p reports
M2_ROOT=$(mktemp -d reports/m2-check.XXXXXX)
uv run --locked python -m macrolens weekly --mode sample --as-of '2026-09-28T09:00:00+09:00' --output "$M2_ROOT"
uv run --locked python -m macrolens weekly --mode sample --as-of '2026-10-05T09:00:00+09:00' --output "$M2_ROOT"
M2_BEFORE=$(uv run --locked python -c 'import json,pathlib,sys; print(next(p.parent.name for p in pathlib.Path(sys.argv[1]).glob("*/result.json") if json.loads(p.read_text())["as_of"].startswith("2026-09-28")))' "$M2_ROOT")
M2_AFTER=$(uv run --locked python -c 'import json,pathlib,sys; print(next(p.parent.name for p in pathlib.Path(sys.argv[1]).glob("*/result.json") if json.loads(p.read_text())["as_of"].startswith("2026-10-05")))' "$M2_ROOT")
uv run --locked python -m macrolens history --root "$M2_ROOT"
uv run --locked python -m macrolens show "$M2_AFTER" --root "$M2_ROOT"
uv run --locked python -m macrolens compare "$M2_BEFORE" "$M2_AFTER" --root "$M2_ROOT" --output "$M2_ROOT/comparison.json"
uv run --locked python -m macrolens replay "$M2_AFTER" --root "$M2_ROOT"
uv run --locked python -m macrolens report "$M2_AFTER" --root "$M2_ROOT" --output "$M2_ROOT/regenerated.md"
```

`comparison.json`에서 `interval_days: 7`, 각 섹터의
`contribution_delta_sum ≈ score_delta`를 확인합니다.
`regenerated.md`에는 전주 run_id·실제 비교 시각·가상 점수 변화가 나옵니다.
실제 API 검수는 키가 설정된 Codespaces에서 위 FRED 현재/과거 명령을 별도로 실행합니다.
과거 조회가 `held`라면 `report.md`의 vintage 응답·이력 부족·메타데이터 오류를 확인하세요.
모의 응답 테스트 통과가 실수집 성공을 의미하지 않습니다.

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
| `src/macrolens/scoring.py` | 점수·기여도·가상 공동 순위/연구 평균 순위 |
| `src/macrolens/research.py` | 연구 모델 동결 설정 검증·로딩 |
| `src/macrolens/reporting.py` | 계산 결과로부터 한국어 Markdown 생성 |
| `src/macrolens/cli.py` | 수집·조회·비교·재현·보고서 CLI |
| `src/macrolens/storage.py` | 실행 보존·상태·무결성 검사·M1 읽기 |
| `src/macrolens/snapshots.py` | 판단 시각·선택 입력·한국시간 주간 슬롯 |
| `src/macrolens/history.py` | 비교 가능성·기여도 차이·주별 대표 연결 |
| `docs/m2_implementation.md` | M2 설계·완료 기준·검증 한계 |
| `tests/` | 손계산·누락·미래 시각·수집 오류·전체 실행 검증 |
| `docs/m1_implementation.md` | M1의 범위·설계 결정·제약·다음 단계 |

계획 원본은 `docs/macro_sector_project_plan_v2.md`, 맥락은
`docs/MacroLens_Project_Context_v2.txt`입니다. M1 구현 기록은 원본 계획과 구분해 남깁니다.
