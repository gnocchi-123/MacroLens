# MacroLens

2026-10-23: **KIS 조회 전용 수집기와 오프라인 검수를 구현했습니다. 실제 인증·가격 수집은 미실시입니다.** KIS는 전용 명령에서만 활성화하며, 기존 FRED·샘플·이력·재현에는 KIS 키가 필요하지 않습니다.

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
패키지를 유지합니다. M1~M3 계산은 Python 표준 라이브러리만 사용하고,
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

## M4 첫 단계: 가격 자료 검수와 검증 설계

[실제 자료 조사·검수 결과와 상세 명령](docs/m4_data_audit.md),
[검증 설계 및 미확정 제안](docs/m4_validation_design.md)을 확인하세요.
2026-10-18 사용자 결정으로 **한국투자증권(KIS) API를 우선 가격·권리 수집 경로로 도입**합니다.
[KIS 도입·검수 계획](docs/kis_integration_plan.md)과 [진행 기록](docs/progress.md)을 기준으로 이어갑니다.
이전 조사에서 공식 배당 대조원을 확보했습니다. **KIS 수집기는 구현했고 실제 인증·일별 가격 검수는 미실시**입니다.
Yahoo 자동 수집의 사전 허가 및 공식 CSV의 Gold 구독 조건 때문에 무료 수집기로
채택하지 않았습니다. 전략 백테스트·계수 최적화는 구현하지 않았습니다.

```bash
uv sync --locked --extra price-audit
uv run --locked --extra price-audit pytest -q
uv run --locked --extra price-audit ruff check .
uv run --locked --extra price-audit ruff format --check .

# 공개 출처 연결 확인: 접속 성공과 가격 적격성은 다릅니다.
uv run --locked --extra price-audit python -m macrolens.price_audit probe

# 공식 배당 XLSX 확보 및 XLC·XLK·SPY 사건 확인: 일별 가격 수집이 아닙니다.
uv run --locked --extra price-audit python -m macrolens.price_audit issuer

# 허용된 가격 파일을 나중에 검수하기 위한 빈 입력 명세
uv run --locked --extra price-audit python -m macrolens.price_audit template
```

이전 M4 작업의 검수 결과(2026-10-17): **120 tests passed**, Ruff 검사·포맷 통과. 최신 KIS 검수는 아래 절을 따릅니다.
`probe`와 `issuer`의 `held`·종료 코드 2는 가격 적격성 보류라는 정상적인 보고입니다.
원본·보고서는 Git 제외된 `data/price_audit/<새 ID>/`에 보존하며 덮어쓰지 않습니다.
`price-audit` 추가 의존성을 설치하지 않은 기본 테스트에서는 M4 검수 테스트가 생략됩니다.

아래 기존 `template/audit` 계약은 Yahoo 형태 입력용이며 KIS에 바로 사용할 수 없습니다. KIS는 아래 `macrolens.kis` 명령과 별도 원본 계약을 사용합니다.
기존 계약에 맞는 허용 경로·보존 권한·실제 가격 파일·공식 대조 기록을 채운 후에만 다음을 사용합니다.
빈 명세는 권한 미확인 오류로 중단됩니다. `SAVED_DIR`는 앞 명령이 출력한 저장 폴더로 바꿉니다.

```bash
uv run --locked --extra price-audit python -m macrolens.price_audit audit --manifest data/price_inbox/manifest.json
uv run --locked --extra price-audit python -m macrolens.price_audit recheck SAVED_DIR
uv run --locked --extra price-audit python -m macrolens.price_audit issuer --file SAVED_DIR/issuer-distributions.xlsx
```

`recheck`는 **가격 파일 검수 기록**, `issuer --file`은 **배당 XLSX**를 외부 API 없이 다시 읽습니다.
합성 자료의 `synthetic_only`나 재검수 `matched`는 실제 공급자 자료의 정확성 인증이 아닙니다.

## KIS 조회 전용 연결 (2026-10-23 구현)

별도 명령 `python -m macrolens.kis`에서만 인증합니다. 주문·잔고·자동매매는 없습니다.
기존 FRED·샘플·이력·비교·재현 명령은 그대로입니다. 새 의존성·잠금 파일 변경은 없습니다.
이번 개발 환경에 키가 없어 **실제 인증·가격·권리 조회는 아직 미실시**입니다.
147개 테스트와 Ruff 검사/포맷을 통과했지만 공급자 자료 정확성 인증이 아닙니다.

먼저 Codespaces에서 작업 파일을 가져옵니다. 변경 중인 파일이 있으면 보존한 뒤 진행하며
`--force`, `reset --hard`는 사용하지 않습니다.

```bash
cd /workspaces/MacroLens
git status --short
git fetch origin
git switch feat/m4-kis-data
git merge --ff-only origin/feat/m4-kis-data
uv sync --locked --extra price-audit
uv run --locked --extra price-audit pytest -q
uv run --locked --extra price-audit ruff check .
uv run --locked --extra price-audit ruff format --check .
```

키는 이미 Codespaces Secrets에 설정했다면 다시 입력하지 않습니다.
[GitHub Codespaces Secrets](https://github.com/settings/codespaces)에서
`KIS_APP_KEY`·`KIS_APP_SECRET`의 저장소 접근 대상이 MacroLens인지 확인하고,
변경했다면 Codespace를 중지 후 다시 시작해 환경에 반영합니다.
`.env` 자동 로딩은 없습니다. 키를 터미널 출력·채팅·Git에 붙이지 마세요.
존재 여부만 확인하려면 다음을 사용합니다.

```bash
python -c 'import os; print({k: bool(os.getenv(k)) for k in ("KIS_APP_KEY", "KIS_APP_SECRET")})'
```

본인의 키가 **실전용이면 `--env real`**, 모의용이면 **`--env demo`**를 사용합니다.
아래는 실전용 키를 선택한 경우의 명령이며 프로그램이 키 종류를 추정하지 않습니다.
실전 서버의 시세 조회도 주문을 실행하지 않습니다. 계정의 개인 조회·보존·비용 조건을 먼저 확인하세요.

```bash
# 인증만 확인. 정상: authentication_ok / real / 가격·권리 검수 미실시
uv run --locked --extra price-audit python -m macrolens.kis auth --env real

# XLC·XLK·SPY: 최근/2020년 소량 가격 두 옵션 + 상품 식별 + 2025년 12월 권리
uv run --locked --extra price-audit python -m macrolens.kis probe --env real
```

출력된 `data/kis/<ID>/report.md`를 VS Code에서 열고 Ctrl+Shift+V로 확인합니다.
`audit.json`의 `can_collect=true`는 최근·2020년 가격/식별 검사를 통과하여 **전체 수집을 진행할 수 있음**을 뜻합니다.
권리 미확인 때문에 같은 결과에 `held`가 함께 표시될 수 있습니다. 자료 적격성 승인이 아닙니다.
AMS/529는 기본 거래소 요청 후보이며 응답과 불일치하면 보류합니다.
실제 상품정보를 확인한 경우에만 `--exchange NYS` 또는 `NAS`로 별도 probe를 만들 수 있습니다.

```bash
# <probe가 출력한 ID>를 실제 ID로 바꾸세요. report.md를 제외한 폴더 경로입니다.
KIS_PROBE_DIR="data/kis/<probe가 출력한 ID>"
uv run --locked --extra price-audit python -m macrolens.kis collect --env real --probe "$KIS_PROBE_DIR"
```

전체 수집 목표는 **2020-06-01~2026-09-30 포함**, 평가 후보 시작은 **2021-06-07**입니다.
실제 기간·행 수·결측·실패는 새 보고서에 나옵니다. `MODP=0/1`을 별개로 보존하며
존재하지 않는 Adj Close를 만들거나 배당을 더하지 않습니다. 권리 금액/주당 기준과 조정 범위가
미확인이라 최고 판정은 `conditional`, 총수익 평가 적격성은 false입니다.

운용사 대조 파일이 이미 있으면 재다운로드 없이 사용하세요. 없을 때만 기존 `issuer` 명령으로 받습니다.

```bash
# 공식 배당만 확보하므로 held와 종료 코드 2가 나와도 다운로드 보고서를 확인합니다.
uv run --locked --extra price-audit python -m macrolens.price_audit issuer

# 각각 collect/issuer가 출력한 실제 폴더 ID로 변경하세요.
KIS_SAVED_DIR="data/kis/<collect가 출력한 ID>"
ISSUER_FILE="data/price_audit/<issuer가 출력한 ID>/issuer-distributions.xlsx"
uv run --locked --extra price-audit python -m macrolens.kis recheck "$KIS_SAVED_DIR" --issuer-file "$ISSUER_FILE"

# 새 근거를 추가하지 않고 원본만 재검수: 키·네트워크 사용 없음
uv run --locked --extra price-audit python -m macrolens.kis recheck "$KIS_SAVED_DIR"
```

`recheck: matched`는 저장 원본으로 검사 결과가 같다는 뜻이며 실제 자료 승인이 아닙니다.
공식 XLSX를 새로 추가한 재검수는 `different`일 수 있습니다. 원래 실행은 그대로 남습니다.

| 출력/오류 | 의미와 다음 확인 |
| --- | --- |
| 인증 성공, 코드 0 | 토큰 발급/캐시 재사용. 상품 조회나 무료 전체 역사 검증은 아님 |
| `can_collect: true`, 코드 0 | 소량 가격 검사 통과. 전체 수집 가능; 권리·총수익 적격성과 별개 |
| `held`/`conditional`, 코드 2 | 검사 완료 후 보류/조건부. report.md와 audit.json의 issues/limits 확인 |
| `missing_KIS_APP_KEY_or_KIS_APP_SECRET`, 코드 1 | 환경에 키 미반영. Secrets 저장소 대상 및 Codespace 재시작 확인 |
| `authentication_failed`/`authentication_or_permission_failed` | 키의 실전/모의 선택·서비스 신청·계정 권한 확인. 오류 본문이나 키를 공유하지 않음 |
| `token_cooldown_wait_61_seconds` | 최근 인증 시도 후 61초 대기. 실패한 POST도 반복 발급하지 않음 |
| `network_or_timeout`/`http_retry_exhausted` | 네트워크·서버·호출 제한 문제. 데이터 자체 결함과 구별 |
| `matching_successful_real_probe_required` | 같은 서버·거래소 probe의 가격 범위/식별 검사를 먼저 해결 |
| `archive_integrity_failed` | 저장 파일 변경/손상. 덮어쓰지 말고 원본 백업과 비교 |

토큰은 Git 제외 `data/.kis_auth/<real 또는 demo>/`에 보호 저장합니다. 이 폴더와 시장 원자료를
공유하지 않습니다. KIS 명령을 실행하지 않으면 비활성입니다. 이 코드의 토큰 캐시는 Linux/Codespaces용입니다.
다음 단계는 세 상품의 실제 보고서 검토입니다. 12개 확대·백테스트·계수 최적화는 실행하지 않습니다.

### SPY 상품정보 조회 오류 재점검 (2026-10-28)

`identity: http_retry_exhausted`는 상품정보 요청이 HTTP 429 또는 5xx에서 재시도 후
종료됐다는 뜻입니다. 따라서 `USD 통화 미확인`은 USD가 아니라는 판정이 아닙니다.
이전 기록에는 HTTP 세부 코드가 남지 않아 재검수만으로 원인을 복원할 수 없습니다.
업데이트 후 `probe --env real`을 한 번 실행해 새 보고서를 확인하세요. `auth`를 먼저
반복 실행할 필요는 없습니다. 기존 토큰이 유효하면 자동 재사용합니다.

새 오류 예시(실제 사용자 오류 코드가 아님):
`identity: http_retry_exhausted (HTTP 503; KIS unavailable)`.
HTTP 429는 호출 제한, 5xx는 서버가 오류 응답을 보낸 경우입니다. 5xx라도 요청·권한 문제일 수
있어 KIS 코드와 함께 확인합니다. `unavailable`은 안전하게 기록할 코드가 없었다는 뜻입니다.
키·토큰·오류 응답 원문 대신 새 `report.md` 전체를 공유하면 됩니다.
추가 모의 테스트 포함 154개 및 Ruff 검사·포맷 통과. 실제 SPY 장애 해결은 아직 미확인입니다.

### 세 상품 모두 EGW02004인 경우: 공식 대체 식별 조회

다음 선택지는 기존 해외 상품정보 대신 **공통 상품기본조회 + 해외 현재가상세**를 함께 호출합니다.
상품번호·상품유형·통화·종목코드를 모두 대조하며, 식별 실패를 무시하지 않습니다.
`EGW02004`의 공식 의미·근본 원인은 아직 확인되지 않았습니다.

```bash
git switch feat/m4-kis-data
git pull --ff-only origin feat/m4-kis-data
uv run --locked --extra price-audit python -m macrolens.kis probe --env real --identity-source basic-quote
```

`can_collect: true`가 나오면 이 **새 probe 폴더**를 앞 절의 collect `--probe`에 지정합니다.
collect는 그 식별 경로를 자동으로 유지하므로 옵션을 다시 지정할 필요가 없습니다.
권리·조정 대조가 남아 `held`가 함께 나올 수 있습니다. `false`이면 새 report.md 전체를 확인합니다.
기존 실패 폴더와 원본은 보존합니다. `recheck`만으로 새 인증 조회를 대신하지는 못합니다.

보고서의 16행은 2020년 6월과 2026년 9월의 **두 소량 구간 합계**입니다. 날짜 최소/최대 사이
전체 자료가 있다는 뜻이 아닙니다. 현재가상세의 현재 가격은 과거 가격 자료에 섞지 않습니다.
현재 구현 환경의 실제 KIS 호출은 미실시이며 이 대체 경로의 실서비스 성공도 아직 미확인입니다.
