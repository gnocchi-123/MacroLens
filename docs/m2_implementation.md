# M2 구현 기록: 자료 시점·실행 이력·전주 비교

기준: main M1 병합 커밋 `d6054ab`, 작업 브랜치 `feat/m2-history-asof`.
초기 계획의 미구현 문구보다 실제 M1 코드·명령을 우선했습니다.
가상 A/B/C 섹터, `demo-v1`, 미검증 연구용 표시와 기존 계산식은 유지합니다.
표준 라이브러리만 사용하며 의존성·Python 버전 핀은 변경하지 않습니다.

## 1. 명령과 책임

- `weekly [--mode sample|fred] [--as-of ISO8601] [--output DIR]`
  새 수집/계산. `--as-of` 없으면 기존 M1 사용법과 같습니다.
- `weekly ... --corrects RUN_ID`: 같은 한국시간 주간 슬롯의 정정판.
  원본 as_of 기본 상속, parent_run_id 연결, 원본 보존.
- `weekly ... --rerun-of RUN_ID`: 성공·보류·실패/중단 기록을 참조한 재수집.
  기준 시각은 새 실행 기본값; 과거 재시도는 원래 --as-of를 다시 지정합니다.
- `history [--root DIR]`: 모든 실행 상태 요약.
- `show RUN_ID [--root DIR]`: 완료 실행의 저장 결과·시점·해시·원본 연결.
- `compare BEFORE_ID AFTER_ID [--root DIR] [--output NEW_FILE]`: AFTER − BEFORE JSON.
- `report RUN_ID [--root DIR] [--output NEW_FILE]`: 저장 결과에서 한국어 Markdown 재생성.
- `replay RUN_ID [--root DIR]`: 저장 입력·설정으로 새 재계산 기록 생성.

상세 복사 실행 블록은 [README](../README.md)의 M2 명령과 Codespaces 검수 예제에 있습니다.
`report`와 `compare`는 기본 표준 출력, 파일 출력은 배타적 생성(`x`)으로 덮어쓰기를 거부합니다.
`report`는 원래 report.md가 없어도 원천 JSON의 무결성이 맞으면 재생성할 수 있습니다.
외부 API 조회는 `weekly --mode fred`만 수행합니다.

종료 코드: 0=정상 계산/조회/비교, 1=명령·키·파일·실행 실패,
2=weekly/replay의 자료 보류 또는 compare의 비교 불가.
`report`는 보류 상태를 정상적으로 렌더링할 수 있으며 그 경우 0입니다.

## 2. 저장 구조와 상태

```text
reports/<UTC timestamp>-<random id>/
  run.json              # 시작 시각·종류·parent_run_id·요청 as_of
  config.json           # 지표 설정·가상 계수·모델 버전·engine_version
  raw/<SID>-metadata.json
  raw/<SID>-observations.json
  snapshot.json         # 응답·정규화 관측·수집 시각·오류
  inputs.json           # 시점 선택 후 계산기에 전달한 자료, 제외 건수
  result.json           # 지표·요인·점수·기여도·해시·시점 상태·동결한 비교
  report.md
  status.json           # 종료 상태·종료 시각·안전한 오류 설명
  manifest.json         # 생성 파일 SHA-256
  COMPLETE              # 모든 쓰기가 끝난 실행만 존재
```

- 먼저 디렉터리·run.json·config.json을 생성합니다. API 성공 응답은 형식 검증 전에
  raw 파일에 체크포인트를 남겨 뒤 지표나 파싱이 실패해도 이미 받은 원본을 보존합니다.
- 원본은 응답 JSON을 파싱·정렬하고 비밀값을 제거한 표현입니다. HTTP 바이트 원문 보존은
  아닙니다. 응답이 키를 반사한 경우 해당 값은 `[REDACTED]`로 바뀝니다.
- 요청에는 endpoint와 키를 제외한 params만 저장합니다. 키가 붙은 요청 URL·HTTP 오류 본문·
  임의 예외 문자열은 로그에 저장하지 않습니다. TLS 검증과 30초 대기는 유지합니다.
- `snapshot.json`에는 모든 수신 자료와 오류를, `inputs.json`에는 실제 계산 입력을 구분해
  보존합니다. 미래 시각·관측·자료 버전은 선택에서 제외하거나 모호하면 지표를 보류합니다.
  중복 관측 버전을 행 순서로 임의 선택하지 않습니다.
- `demo_complete`는 가상 모델 계산 완료, `held`는 자료 문제로 모든 점수 보류입니다.
  둘 다 저장이 완료되면 COMPLETE가 있습니다. `failed`는 별도 status.json만 남깁니다.
  COMPLETE 없이 성공 상태만 남거나 강제 종료된 기록은 조회 시 `incomplete`입니다.
- `original`, `rerun`, `correction`, `replay`와 parent_run_id로 새 실행 종류를 구별합니다.
  문법 오류·미래 시각·없는 parent ID 등 실행 전 거부에는 run_id를 만들지 않습니다.
- 실패한 HTTP 응답/해석 불가능한 본문은 키 보호를 위해 저장하지 않고 안전한 오류만 기록합니다.
  raw 파일 쓰기 자체가 실패한 경우 해당 응답의 보존을 보장할 수 없습니다.
- manifest는 우발적 손상 검출용입니다. 전자서명이나 악의적 변조 방지 장치는 아닙니다.
  보존 파일을 사용자 또는 다른 프로그램이 직접 수정/삭제하는 것을 OS 수준으로 막지는 않습니다.
- 실행당 개별 파일을 사용하고 공유 인덱스를 덮어쓰지 않습니다. 장기 백업·복구·예약 중복 방지·
  실행 중 프로세스 잠금·fsync 트랜잭션은 범위 밖입니다. `reports/`는 Git 제외이므로 별도 백업이 필요합니다.

## 3. 시점 의미와 FRED 조회

| 필드 | 의미 |
| --- | --- |
| observation_date | 통계가 설명하는 월/날짜. 발표일이 아님 |
| published_at | 검증된 실제 발표 시각; 현재 null |
| available_at | 아래 프로젝트 규칙상 입력 사용 가능 시각; 최초 발표시각 아님 |
| retrieved_at | 해당 응답을 실제 수신한 시각; 과거 조회에서도 현재 시각 |
| vintage_date | 조회한 날짜 단위 자료 버전 기준일 |
| realtime_start/end | API가 반환한 실시간 구간; 실제 최초 발표시각으로 복사하지 않음 |
| as_of | 계산의 판단 기준 시각; 시간대 필수 |
| started_at/generated_at/finished_at | 프로그램 실행·계산·저장 시각 |

### 현재 수집

시작 시각의 미국 중부 전일 vintage를 요청하고 수신한 값은 retrieved_at부터 사용합니다.
전체 수집 뒤 시각을 as_of로 둡니다. `time_policy=collected-at-v1`,
`point_in_time_status=collected_inputs_only`입니다. 당시 실행을 남기는 것이며 과거 버전 재현을
주장하지 않습니다. 각 지표의 실제 수신 시각은 서로 다를 수 있습니다.

### 과거 버전 조회

`weekly --mode fred --as-of '2026-10-05T09:00:00+09:00'`은 당시 파일을 복원하는 명령이
아니라 API에 과거 날짜 버전을 **지금** 요청하는 실행입니다.

1. 판단 시각을 America/Chicago로 변환하고 그 지역 전일을 vintage로 선택합니다.
2. `series`와 `series/observations`에 동일한 `realtime_start=realtime_end=vintage`를 보냅니다.
3. 관측 요청은 `output_type=1`, `units=lin`, `sort_order=asc`, 최근 약 10년,
   `observation_end=vintage`, `limit=100000`을 명시합니다.
4. 응답의 메타데이터/관측 실시간 구간이 vintage를 포함하는지 검사합니다. 행 단위 구간이
   없거나 이후 수정치, 미래 관측, count 불일치가 있으면 보류합니다. 최신 값으로 대체하지 않습니다.
5. 그 vintage 전체를 미국 중부 **다음날 00:00부터** 쓸 수 있다는 보수적 날짜 규칙을 적용합니다.
   이 규칙은 확인된 최초 이용 가능 시각이 아닙니다. 일광절약시간은 zoneinfo로 처리합니다.

예시 판단 시각은 미국 중부 10월 4일 저녁이므로 vintage는 10월 3일,
available_at은 10월 4일 00:00 CDT입니다. 최신 수정값에 observation_date 필터만 적용해
과거 자료라고 주장하지 않습니다. FRED/ALFRED의 같은 실시간 기간 인터페이스를 사용합니다.

`time_policy=chicago-prior-day-v1`, `point_in_time_status=date_only_conservative`입니다.
API의 날짜 단위 이력만으로 정확한 장중 발표시각·당시 공급 지연·전체 수정 이력의
완전성을 입증할 수 없습니다. Chicago 날짜 경계도 프로젝트의 보수적 사용 규칙이지,
모든 원 제공기관의 장중 발표·배포 시각 보증이 아닙니다.
**모든 결과의 strict_point_in_time=false를 유지합니다. 엄밀한 과거 검증용 유효 표시를 하지 않습니다.**
아주 오래된 조회는 표준화 이력·계절조정·기준연도 단위가 현재 설정과 맞지 않아 보류될 수 있습니다.

### 공식 문서와 확인 상태

구현 검토 대상 공식 문서:

- [Real-Time Periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)
- [Series](https://fred.stlouisfed.org/docs/api/fred/series.html)
- [Series Observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)

이번 클라우드 작업에서는 공식 문서 HTTPS 연결이 프록시 HTTP 403으로 거부되었습니다.
따라서 **이번 작업에서 공식 원문을 다시 읽고 확인했다고 주장하지 않습니다.**
기존 M1의 동일 vintage 요청을 확장하고 요청/응답 검사를 모의 응답 테스트로 검증했습니다.
문서 재확인 및 실제 과거 요청 검수는 남은 외부 확인 항목입니다.
환경 설정 초안에 `fred.stlouisfed.org`, `api.stlouisfed.org`를 추가했으며,
사용자 저장/적용 전에는 런타임 네트워크 허용을 보장하지 않습니다.

## 4. 저장 입력 재현과 M1 호환

`replay`는 API·현재 설정을 사용하지 않고 저장된 inputs.json/config.json을 읽습니다.
동일 `engine_version=m2-v1`만 지원하며 재계산된 indicators/scores/status가 원본과 일치하는지
확인합니다. 일치하지 않으면 성공 결과를 쓰지 않고 실패 실행을 남깁니다.
run_id·생성 시각·현재 코드 식별자는 새 값이므로 결과 파일 전체의 바이트 일치는 요구하지 않습니다.
원본의 history와 previous_run_id를 유지하여 나중 정정판으로 당시 비교 문맥을 바꾸지 않습니다.
그 동결된 비교의 before/after_run_id는 원래 실행을 가리킵니다. 재현 원본은 parent_run_id로 확인합니다.
재현 실행의 snapshot에 원본 raw 응답이 포함되며 새 raw API 파일을 만들지는 않습니다.

코드 자체를 실행마다 복사하지는 않습니다. 계산 의미가 달라지는 후속 수정은 엔진 버전을
올려야 합니다. unsupported engine은 재현 거부이며 현재 코드를 구버전 엔진이라고 가장하지 않습니다.
코드 버전은 commit SHA와 dirty 여부를 기록하므로 미커밋 소스의 완전한 복원은 별도 Git 보존이 필요합니다.

M1 schema_version=1의 result.json은 조회·보고서 재생성에 사용합니다.
snapshot.json이 있으면 원래 snapshot_hash를 확인합니다. 전체 설정·선택 입력이 없으므로
이를 현재 설정에서 추정하지 않습니다. M1 재계산·M2 수치 비교·자동 주간 연결은 거부/제외합니다.
M1 보고서는 현재 템플릿으로 렌더링되므로 원래 보고서와 문장까지 동일하다고 보장하지 않습니다.

## 5. 비교와 주별 대표 선택

비교 가능 조건: 두 완료 실행 모두 유효 점수, M2 저장 설정, 같은 config_hash(지표 정의와 계수),
모델·엔진·모드·시점 정책, 동일 지표 출처/변환/창, 동일 섹터·기여 요인 구성.
단위·빈도·계절조정은 계산 전 config와 비교되며 어긋나면 held입니다.
가상 표본과 실제 자료, 현재 수집과 사후 vintage 조회, 모델 변경, 보류 실행은 수치 비교를 보류합니다.
차단 사유는 reasons로 출력하며 그 경우 수치 차이 표를 채우지 않습니다.

- 지표: 관측일/버전 전후, 원래 값·변환값·z·요인값의 AFTER − BEFORE.
- 요인: growth/employment/inflation/rates의 전후 값과 차이.
- 섹터: 점수 전후와 차이, 기여도 차이, 이전 순위 − 이후 순위.
- 각 원래 결과의 `50 + 기여도 합 = 점수`와 `기여도 변화 합 = 점수 변화`를
  반올림 전 절대 오차 1e-9점으로 검사합니다. 수익률·확률·인과 분해가 아닙니다.

주간 슬롯은 Asia/Seoul 월요일 00:00~다음 월요일 00:00의 반열린 구간이고 키는 월요일 날짜입니다.
판단 as_of로 배정하며 파일 생성 시각이나 실행 개수를 주차로 세지 않습니다.
현재 실행 시작 전에 저장 완료된 같은 mode/time_policy 후보 중,
각 주의 started_at이 가장 늦은 실행(동률 run_id 사전순 마지막)이 대표입니다.
실패·불완전·손상·replay·M1은 제외하지만 held는 남겨 최신 자료 문제를 숨기지 않습니다.
모델이 바뀐 대표를 예전 모델 기록으로 몰래 대체하지 않고 비교 불가를 표시합니다.

정확히 7일 전 슬롯이 없으면 previous_run_id=null, **전주 비교 없음**입니다.
그 이전 가장 가까운 슬롯의 대표가 있으면 last_available로 실제 as_of 둘과 간격(초/86400)을
따로 표시합니다. 같은 주의 실행은 전주나 마지막 이전 주 후보로 사용하지 않습니다.
이미 저장한 result의 연결과 비교는 나중 실행·정정판 때문에 바뀌지 않습니다.

정정은 같은 주를 유지합니다. 현재 FRED 수집의 과거 정정은 날짜 버전 조회로 성격이 바뀌므로
원본 current 정책의 대표를 자동 교체하지 않고 historical 정책 그룹에서만 후보가 됩니다.
이 제한과 비교 불가 사유를 감추지 않습니다.

## 6. 완료 기준과 검증 증거

테스트는 실제 키·네트워크를 사용하지 않습니다. M1 기존 36개 중 키 미설정 테스트 하나는
M2 요구에 따라 '산출물 없음'에서 '실패 실행 보존'으로 기대 동작을 변경했습니다.
M1 계산식·샘플 점수·네트워크 오류 키 비노출 검증은 유지했습니다.

- 미래 관측·미래 이용 시각·미래 수정치·없는 실시간 구간의 차단.
- 메타데이터와 관측값의 동일 vintage 요청, Chicago DST 경계.
- 원본 응답의 부분 실패 보존 및 응답에 반사된 키 삭제.
- 저장 입력/설정 재현과 보고서 재생성의 네트워크 미사용.
- 새 실행·정정·재현 후 기존 파일 바이트 보존, 덮어쓰기 거부, 손상 검사.
- 실패·중단·최종 파일 쓰기 실패의 상태 구분.
- 전주 누락(14일 별도 비교), 같은 주 재실행/정정/재현, KST 월요일 경계·연말.
- 비교 불가 이유, 가상 점수와 기여도 변화 합, M1 읽기 범위.

2026-10-07 클라우드 검증 결과: **pytest 73개 통과**, `ruff check .`,
`ruff format --check .`, `git diff --check` 통과.
README의 Codespaces 검수 블록을 그대로 실행해 두 주 샘플 수집 → 목록/상세 조회 →
7일 간격 비교 → 오프라인 재현 → 보고서 재생성을 확인했습니다.
비교는 comparable, 재현은 matched였고 재생성 Markdown은 원래 M2 보고서와 바이트가 같았습니다.
M2 수정 전에 실제로 생성해 둔 M1 샘플 파일도 show/report로 읽고 원본 해시 보존을 확인했습니다.

이번 클라우드에는 FRED_API_KEY가 없어 실제 API 수집을 수행하지 않았습니다.
M1 Codespaces 실수집 성공은 `docs/m1_implementation.md`의 사용자 확인 기록이며
M2 과거 조회의 성공 증거로 옮겨 쓰지 않습니다.
공식 문서 원문 접근과 Codespaces 실수집 확인을 마친 뒤 실제 자료 기반 검수 완료로 판단해야 합니다.

범위 밖: 연구용 11개 계수 확정, 가격·백테스트, 4주/13주 추세, 상세 변화 원인 분해,
자동 실행, 유료 서비스, AI API. M1 가상 계수·미검증 표시는 그대로 유지합니다.
