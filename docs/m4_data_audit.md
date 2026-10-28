# M4 가격 자료 확보·검수 기록

2026-10-23: **KIS 조회 전용 수집기와 오프라인 검수를 구현했습니다. 실제 인증·가격 수집은 미실시입니다.** KIS는 전용 명령에서만 활성화하며, 기존 FRED·샘플·이력·재현에는 KIS 키가 필요하지 않습니다.

출처 조사·실제 다운로드: 2026-10-13 UTC. 최종 코드·문서 검수: 2026-10-17.
**배당 대조원 확보, 일별 시장 가격 보류**.
전략 백테스트·투자 성과·계수 최적화 결과가 아니다.
[검증 설계](m4_validation_design.md)는 다음 단계의 검토 문서다.

## 0. 2026-10-18 KIS 도입 결정

사용자는 한국투자증권 Open API를 이용해 프로젝트를 진행하기로 결정했다.
**KIS 채택 결정 / 실제 인증·가격 수집·자료 적격성 검수 미완료**를 구별한다.
아래 1~6절의 실행 ID·120 tests·배당 확보는 10월 13~17일 기록이며 이번 문서 개정의 새 실행 결과가 아니다.

- 수집 우선순위: 기간별 일별시세와 수정주가 옵션, 기간별권리/권리종합, 종목 식별.
- 표본은 기존 XLC·XLK·SPY 유지. 세 종목 적격 후 12종 확대.
- 기존 Yahoo CSV/조정 계약은 KIS 계약으로 재사용하지 않는다. 공식 응답 원본을 보존하고 KIS 전용 어댑터를 구현한다.
- KIS 배당·분할 이력을 기존 운용사 대조원과 연결하고, 날짜·주당 기준·자본이득 분배·수정주가 범위를 확인한다.
- 조회 기간·연속조회·정규장/지연·이용 조건·실제 비용은 확인할 항목이다. 현재 보류를 통과로 변경하지 않는다.
- 자세한 입력·저장·수익률·완료 기준은 [KIS 도입 계획](kis_integration_plan.md)에 둔다.

## 1. 작업 환경과 증거 구분

| 구분 | 확인 내용 |
| --- | --- |
| 사용자 제공 이전 결과 | M0~M3 완료, M3 92 tests/Ruff, 샘플·전주 비교·재현 matched, 실제 FRED research_complete. 사용자 Codespaces 원본을 이번에 직접 검사한 것은 아님 |
| 이번 실제 경로 | `/workspace/MacroLens`. 사용자 `/workspaces/MacroLens`와 별도 환경 |
| 시작 상태 | `feat/m2-history-asof`, HEAD `08957c7`, 작업 트리 깨끗함. 기존 로컬 작업을 초기화하지 않음 |
| 이번 기준 | origin/main을 fetch해 M3 병합 `8f6bfa07ee74587e8ec4fc7e45cc9168895b14cc` 확인 후 로컬 `feat/m4-validation` 생성 |
| 직접 읽은 자료 | 프로젝트 계획·Context v2, model_card, m3_freeze, m3_implementation, research_backlog, 관련 수집·계산·저장·재현 코드. 적용할 AGENTS.md는 발견하지 못함 |
| 첨부 초안 | `MacroLens_M4_validation_design_v0.1.md`를 실제 다운로드·열람. 확정 요구사항으로 승격하지 않음 |
| 변경 전 직접 검증 | 기존 92 tests passed. 아래 최종 검증은 새 테스트를 포함 |
| Git 작업 범위 | 최초 검토 단계에서는 커밋·푸시하지 않음. 파일 전달 문제 이후 사용자가 작업 브랜치의 커밋·푸시를 명시적으로 승인함. PR 생성·main 병합은 제외 |

기존 src 파일과 model_card·m3_freeze 총 15개를 작업 전 SHA-256과 대조해 **변경 없음**을 확인했다.
11개 섹터·44개 계수, 변환·표준화·자료 검사 규칙을 유지한다.
연구 설정 원본 SHA-256은 다음과 같다.

`e51a89fc42c3adfa48268f09c120b0a140a40fccba04180745b7921a70e3f7ac`

## 2. 공급자 조사와 채택 판단 (10월 13~17일 기록; 최신 KIS 결정은 0절)

아래는 이번에 본 문서·접속 결과다. 무료 라이브러리의 사용권과 가격 자료의 사용권은 별개다.
어느 후보도 전략 성과를 계산해 선택하지 않았다.

| 후보 | 이번 직접 확인 | 이번 판단 |
| --- | --- | --- |
| Yahoo / yfinance | [도구 README][yf]는 비공식·미승인 도구이며 Yahoo의 실제 자료 이용 약관 확인을 요구. [Yahoo 약관][yt]에는 사전 명시적 허가 없는 자동 수집 제한. [공식 다운로드 안내][yd]는 CSV 다운로드에 Gold 구독 요구 | 가격 형식 검수의 기술적 후보로만 유지. 무료 자동 수집 경로로 채택하지 않음. yfinance 설치·가격 endpoint 호출 없음 |
| State Street | [이용 조건][st]에 개인용 출력·합리적 내부 사본 허용과 재배포 제한. [공식 배당 XLSX][sx] 다운로드 성공. 상품 페이지에서 식별·설정일·상장일 확인 | 개인 내부 대조원으로 사용. 원문·XLSX 공개 Git 배포 안 함. 시장 종가 공급자로 채택한 것은 아님 |
| State Street XLK NAV History | 상품 페이지가 제공하는 [XLSX][nav]를 직접 열어 `Date, NAV, Shares Outstanding, Total Net Assets` 확인 | NAV는 펀드 순자산가치이며 시장 종가와 다르므로 대체 가격으로 쓰지 않음 |
| Nasdaq | [XLK 역사 페이지][nh] 접속 200이지만 본문은 “Historical Data is currently not available”. [정식 약관][nt]에 자료 capture·분석 소프트웨어용 추출 제한 | 이번 환경에서 허용된 무료 전체 역사 다운로드를 확정 못함. 별도 수집기·숨겨진 API 접근 구현 안 함 |
| Stooq | `https://stooq.com/terms.html`와 홈페이지 모두 HTTP 404 | 경로/서비스 응답 문제. 자료 결함·이용 금지를 단정할 근거는 없음. 조정 산식·권리 미확정으로 보류 |
| Twelve Data | [가격표][td] HTTP 403. 인증 없이 무료 역사·배당·분할 제공 범위를 확인 못함 | 공급자/경계 서버의 HTTP 응답이며 가격 결함으로 판정하지 않음. 가입·키 발급·무료 체험 없음 |

Yahoo 약관에서 확인한 핵심 표현은 “without our express, prior permission”이다.
개인 연구라는 목적이나 yfinance의 Apache 라이선스만으로 이 조건이 충족됐다고 판단하지 않았다.
약관 적용 지역·추가 계약은 실제 이용 시 재확인 대상이다. 현재 명세의 `reviewed` 값을 적는 행위가
별도의 이용 허가를 만들어 주지는 않는다. 유료 가입이나 접근 제한 우회는 수행하지 않았다.

### 실제 접속 기록

- 06:47 UTC 조사에서는 주요 사이트가 `Tunnel connection failed: 403 Forbidden`이었다.
  이는 환경 프록시 단계의 차단으로, 공급자 데이터 오류와 구별했다.
- 07:05 UTC 재실행에서는 Yahoo·State Street·ALFRED·NYSE와 분할 발표 문서가 HTTP 200이었다.
  초기 차단을 최종 미확보 사유로 계속 사용하지 않는다. **현재 가격 보류의 주된 이유는 이용 경로 미확정**이다.
- 재실행 가능한 `probe` 명령이 URL·수집 시각·응답 분류·본문 SHA-256을 저장한다.
  HTTP 200은 본문 의미·권리·가격 정확성 확인이 아니다. 자동 보고서의 `accessible_not_reviewed`와
  이 문서에서 사람이 내용을 읽어 확인한 사실을 구별한다.
- 최초 도구 기록: `data/price_audit/20261013T070523Z-32eca2ef4269/`.
  이후 도구에 Nasdaq 정식 약관·역사 페이지, yfinance 요청 계약 소스도 추가했다.
- onboarding 설정에는 필요한 공개 도메인 허용 초안을 저장했다. 초안 저장을 배포·적용 완료로
  간주하지 않으며, 접속 가능 여부는 위 실제 HTTP 실행 결과로 판단했다.

## 3. 공식 상품·사건 확인

[State Street XLC][xlc], [XLK][xlk], [SPY][spy] 페이지를 직접 열람했다.
세 상품 모두 USD·NYSE ARCA로 표시된다. 시장 날짜는 `America/New_York`를 사용한다.
거래소 달력의 시간대와 API 응답 메타데이터 시간대의 일치는 가격 확보 후 추가 확인해야 한다.

| 상품 | 공식 설정일 | 공식 페이지의 Listing Date | 해석 |
| --- | --- | --- | --- |
| XLC | 2018-06-18 | 2018-06-19 | 설정과 상장은 하루 차이. 표본 중 가장 짧은 역사. 이번에는 11개 전체 설정일을 다시 대조한 것은 아님 |
| XLK | 1998-12-16 | 1998-12-22 | 설정일을 첫 거래일로 대체하지 않음 |
| SPY | 1993-01-22 | 1993-01-22 | 해당 공식 페이지의 표시값. 실제 가격 계열의 최초 거래 관측일은 미확인 |

[State Street의 분할 발표(Nasdaq 재게시)][split]에서 **XLK 2:1**, 주주 기준일 2025-12-02,
분할 지급은 12-04 장 마감 후, **분할 가격 거래는 2025-12-05부터**임을 확인했다.
같은 발표의 XLY·XLE·XLU·XLB도 2:1 대상이다. 발표의 예상 분할 전후 가격을 실제 거래가격으로
쓰지 않았다. XLK의 공급자 실제 가격·분할 사건과의 수치 대조는 아직 하지 못했다.

### 실제 확보한 배당 대조원

`issuer` 명령으로 [State Street 공식 배당 XLSX][sx]를 HTTP 200으로 받았다.
개인 내부 검수용 원본은 Git 제외 폴더에 보존한다.

- 직접 실행 기록: `data/price_audit/20261013T071056Z-6ac266a6f608/`
- 파일: `issuer-distributions.xlsx`, 581,242 bytes.
- 수집 시각: `2026-10-13T07:10:56.622965+00:00`.
- 원본 SHA-256: `b162dd17027d1128f2209e312b0f634960daa78319dafe0ab821ab0b7e77ce23`.
- 표본별 원본 순서·배당락일·기준일·지급일·배당·자본이득 분배를 `audit.json`에 구분 보존.
  상세 주당 금액 표는 공개 문서에 복제하지 않는다.

| 상품 | XLSX 전체 배당락일 범위 | 요청 범위 안의 실제 배당락일 | 범위 내 사건 수 | 확인·남은 부분 |
| --- | --- | --- | ---: | --- |
| XLC | 2018-09-21~2026-09-21 | 2020-06-22~2026-09-21 | 26 | 일자 중복·누락 배당금/음수·비유한 배당금 없음. 자본이득 두 열 중 빈 셀 41개 |
| XLK | 1999-12-17~2026-09-21 | 2020-06-22~2026-09-21 | 26 | 위 형식 검사 통과. 자본이득 빈 셀 41개. 분할 전 배당의 현재 주당 기준 재작성 여부 미확인 |
| SPY | 1993-03-19~2026-09-18 | 2020-06-19~2026-09-18 | 26 | 위 형식 검사 통과. 자본이득 빈 셀 42개. 지급일은 배당락일보다 훨씬 뒤일 수 있음 |

채워진 자본이득 값 중 양수는 없었지만 **빈 셀을 0원으로 간주하지 않았다**.
26건이라는 수가 완전성을 독립적으로 증명하지는 않는다. 다른 가격 공급자의 사건과 대조하지
못했으므로 ‘배당 대조원 확보’이지 ‘배당 수치 교차검증 완료’가 아니다.
총수익률에는 배당뿐 아니라 해당하는 자본이득 분배도 필요하다. 지급일 전 현금 재투자는 근사다.

### 표본 적격성 표

요청 가격 범위는 세 상품 모두 **2020-06-01~2026-09-30(포함)**.
평가 후보 2021-06-07 시작에 앞서 최소 12개월 준비 자료를 요구한다.

| 상품 | 실제 일별 가격 범위 | 가격/배당/분할 자료 | 결측·중복·특이 사건 | 조정 방식·공식 대조 | 판정 |
| --- | --- | --- | --- | --- | --- |
| XLC | 미확보, 0행 | 가격 없음 / 공식 배당 26건 / 공급자 분할 미확보 | 가격 결측·중복 미검수. 설정일≠상장일 | Yahoo 문서의 정의만 확인; 실제 응답 미검수 | 보류: 허용 경로·가격·사건 대조 필요 |
| XLK | 미확보, 0행 | 가격 없음 / 공식 배당 26건 / 2:1 공식 발표 확인 | 2025-12-05 분할. 가격 전후·분할 전 배당 기준 미대조 | 분할을 손실로 보는 오류는 합성 테스트만 통과 | 보류: 실제 분할 전후 조정 대조 필요 |
| SPY | 미확보, 0행 | 가격 없음 / 공식 배당 26건 / 공급자 분할 미확보 | 가격 미검수. 배당락일·지급일 구분 | 시장 비교용 총수익 계열 미확정 | 보류: 가격·조정·배당 기준 대조 필요 |

세 상품이 적격하다는 전제는 충족되지 않아 12개로 확대하지 않았다.
XLE·XLB·XLI·XLY·XLP·XLV·XLF·XLU·XLRE도 가격 검수 미실시이며 사용 가능으로 표시하지 않는다.
현재 실제 확보 가능한 **가격 공통 기간·가격 준비 기간은 미확정**이다.
1592개는 아래 달력이 예상하는 거래일 수이며 확보 가격 행 수가 아니다.

## 4. 날짜·조정 의미와 검수 규칙

- `exchange-calendars==4.13.2`의 XNYS 정규장 날짜 사용. 요청 범위 예상 거래일 **1592개**,
  첫날 2020-06-01, 마지막 2026-09-30. 휴일·주말로 시작/끝나는 요청도 처리한다.
- [NYSE 공식 달력][nyse]은 모든 NYSE 시장의 공통 휴일을 안내한다. 2026년 공시 휴일 10개가
  라이브러리에서 모두 제외됨을 직접 확인했다. 2025-01-09 특별 휴장·크리스마스는 테스트로 확인.
  2020~2025 모든 거래소 공시를 재구성해 대조한 것은 아니며 특별 휴장 이력 확인이 남는다.
- 조기 종료일은 거래일이다. 날짜 문자열은 현지 거래일, 시각 문자열은 시간대가 있을 때만
  뉴욕 날짜로 변환한다. 장중 데이터·정확한 체결 시각 검증은 이번 도구 범위 밖이다.
- [yfinance history 계약 소스][yh]에서 `start` 포함·`end` 제외, `auto_adjust=True`,
  `keepna=False` 기본값을 확인했다. 검수 명세는 마지막 포함일의 다음날인 **2026-10-01**을
  provider_end로 요구한다. 옵션은 `interval=1d, auto_adjust=false, back_adjust=false,
  repair=false, actions=true, keepna=true, rounding=false, prepost=false`를 명시한다.
  이는 **이번 가격 다운로드에 쓴 옵션이 아니라 향후 허용된 입력에 요구하는 계약**이다.
- [Yahoo 조정 정의][ya]는 Adj Close를 배당·분할 조정가격으로 설명한다. `auto_adjust=false`만으로
  Close가 당시 비조정 거래가격이라고 단정하지 않는다. `unadjusted`와 `split_adjusted`를 구분하고
  실제 내보낸 자료의 의미와 라이브러리 버전을 별도로 기록해야 한다.
- 현재 조정가격의 절대 수준은 당시 거래가격이 아니다. 공급자·조정 기준일이 다르면 수준이
  달라도 같은 기간 비율은 같을 수 있다. 설명되지 않은 차이는 조사 대상으로 보류한다.

분할비를 q(신주/구주), 전일 가격 P0, 당일 가격 P1, 당일 배당 D라 할 때,
비조정 가격은 `q × P1 / P0 - 1`로 분할 전후 가격 변화를 비교한다.
이미 분할 조정된 Close에는 q를 다시 적용하지 않는다. 배당 D는 P1과 같은 주당 기준이어야 한다.

- 배당락일 종가에 현금을 재투자하는 근사: `q × (P1 + D) / P0 - 1`.
- Yahoo 설명의 과거 배당 조정계수 `(1 - D / (P0/q))`에 따른 조정가격 변화:
  `P1 / (P0/q - D) - 1`. 위 종가 재투자 근사와 가격 변동이 있을 때 정확히 같지 않을 수 있다.
- Adj Close 비율에 배당 D를 다시 더하지 않는다. 실제 지급일 재투자·세금·현금 지연을
  정확히 복제한 원장이라고 표현하지 않는다. 이후 실험 전 수익률 정의를 하나로 고정한다.
- 도구는 사건 전후 산식과 공급자 조정가격 비율을 0.0001(1bp) 절대 차이로 비교한다.
  이는 가격 자료 조사 경보이며 투자 전략 합격 기준이 아니다. 무사건일의 설명되지 않는
  조정계수 변화도 조사한다. 전략 누적 성과·섹터 순위는 계산하지 않는다.

## 5. 최소 도구·저장 구조

기존 M3 CLI·계산기에서 호출하지 않는 별도 모듈이다.

| 파일 | 역할 |
| --- | --- |
| `src/macrolens/price_audit.py` | 명령·출처 접속·보존·재검수 |
| `src/macrolens/price_quality.py` | 거래일·형식·조정·공식 사건 입력 대조·사건 전후 산식 |
| `src/macrolens/price_issuer.py` | 공식 배당 XLSX의 검토한 열 구조만 읽기. 표준 zip/XML 사용 |
| `tests/test_price_audit.py` | 합성 가격·합성 공식 근거·모의 네트워크로 중요한 실패 검증 |

추가 직접 의존성은 선택 설치 `price-audit`의 거래소 달력 하나다. 단순 평일 목록은 특별 휴장을
결측으로 오판하므로 채택했다. `uv.lock`에 달력과 8개 전이 의존성
(numpy, pandas, korean-lunar-calendar, pyluach, python-dateutil, six, toolz, tzdata),
즉 새 패키지 총 **9개**를 고정했다. 기본 M3 런타임 dependencies는 여전히 비어 있다.
yfinance·Excel 처리 전용 패키지는 설치하지 않았다. 테스트 시에는 extra를 명시한다.

```text
data/price_audit/<UTC 시각-UUID>/
  run.json                    실행 종류·도구 소스 해시·Python/달력 버전
  request.json                issuer의 공개 URL·수집 시각·원본 해시 (해당 실행만)
  issuer-distributions.xlsx   공식 배당 원본 (issuer만)
  input-manifest.json         입력 명세의 원래 바이트 (audit만)
  XLC.csv, XLK.csv, SPY.csv    제공된 CSV 원본 (audit만, 실제 미확보)
  reference-*.bin             검토자가 제공한 공식 대조 근거 사본 (audit만)
  audit.json, report.md       검사 결과 및 한국어 보고서
  checksums.json              저장 파일의 SHA-256
  COMPLETE                    저장 완료; 적격성 통과 표시가 아님
```

- UUID별 새 폴더와 배타적 파일 생성. 기존 원본·보고서를 덮어쓰지 않는다.
- 자동 정렬·중복 제거·빈 값 보간·분할 수리 없음. `automatic_corrections=[]`.
- XLSX 빈 자본이득은 `null`, 빈 가격/빈 파일과 사건 없음은 구별한다.
- 실패가 쓰기 도중 발생하면 새 불완전 폴더를 남긴다. issuer 네트워크 실패는 `failure.json` 기록.
- `audit`와 `recheck`는 파일만 사용한다. 재검수는 해시를 먼저 확인하고 새 폴더에 결과를 저장한다.
  `matched`는 동일 입력의 검사 결과 일치이며 원자료 진실성 인증이 아니다.
- `data/`, `reports/`는 기존 `.gitignore` 대상. 새 원본 경로도 제외됨을 확인했다.
  도구는 체크아웃 안에서는 이 두 폴더 밖으로 원자료를 저장하지 않는다. 강제 `git add -f`를 쓰지 않는다.
- 공개 URL만 기록하며 메타데이터의 credential 필드·쿼리/인증 포함 URL을 거부한다.
  입력 파일에 사용자가 임의로 넣은 모든 비밀을 자동 탐지한다는 보장은 아니다. 키·쿠키·로그를
  공식 대조 첨부 파일로 넣지 않는다. 도구는 키를 요청하거나 인증 세션을 만들지 않는다.

### 향후 허용된 가격 파일 입력 계약

`template`는 성공 예제가 아니라 **미확인 값이 비어 있는 명세**를 만든다.
현재 Yahoo 이용 조건 문제를 해결하지 않은 상태에서 값을 임의로 승인 처리하지 않는다.

- CSV 헤더는 정확히 `Date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits`.
  여러 티커가 섞인 다중 헤더나 추가 자본이득 열은 조용히 버리지 않고 거부한다.
  자본이득 분배가 있는 상품은 별도 명세/합산 정의를 검토하기 전 보류한다.
- `evidence_kind`: `real` 또는 `synthetic`. 합성은 기술 검사가 통과해도 항상 `synthetic_only`.
- `library`: 실제 내보내기에 사용한 yfinance 이름·버전. 이번에는 미설치이므로 버전을 만들어 적지 않음.
- `usage`: 약관 URL·검토 시각·허용된 접근 근거·로컬 보존 허용을 실제 근거로 기록.
  실제 자료는 이 검토가 없으면 원본 복사 전에 중단한다. 재배포 허용은 기본 false.
- `retrieved_at`: 가격을 실제 수집한 시간대 포함 시각. 원래 가격 요청과 후속 파일 복사를 혼동하지 않음.
- `request`: 시작·평가 시작·포함 종료·배타적 종료와 위 옵션. 준비 기간 12개월 이상 검사.
- `adjustment`: Close 구분, Adj Close의 `dividend_and_split_adjusted`, 배당의
  `same_share_basis_as_close`, 산식 `backward_dividend_factor` 또는 `cash_reinvest_ex_date_close`.
- `references`: ID별 `file, sha256, source_url, reviewed_at`. 입력 명세와 같은 폴더 아래 파일만 허용.
  `definition_reference`, `calendar_reference`, 상품별 `identity_reference`가 이 ID를 가리킨다.
- 상품별 티커·USD·ETF·거래소·시간대·설정일·상장일을 실제 공급자/공식 문서와 대조한다.
  `dividend_status`, `split_status`는 `reported`(사건 있음), `explicit_none`(범위 내 무사건 확인),
  `unknown` 중 하나. 빈 응답을 `explicit_none`으로 바꾸지 않는다.
- `official_events`에는 `kind`(dividend/split), `date`(배당락일/분할 거래일), `value`,
  `reference`를 기록하고 배당에는 `basis`도 필요하다. 모든 분할과 최소 한 배당 표본을 대조한다.
  `capital_gains_status=explicit_none_verified`와 `capital_gains_reference`가 없으면 조건부로 남는다.
- 해시는 공식 문서가 실제로 그 주장을 하는지 검증하지 않는다. **대조 근거의 의미·권한은 사람의
  검토 기록**이며 도구는 그 기록과 수치·날짜·보존 무결성을 검사한다.
- CSV는 제공된 내보내기 결과의 원본이다. yfinance 내부 변환 전 HTTP 응답까지 보존했다고
  주장하지 않는다. 다른 공급자는 조정 의미·요청 계약을 확인한 별도 어댑터가 필요하다.

## 6. 직접 실행한 검사와 재실행 방법

이번 환경: Python 3.14.2. 최종 **120 tests passed**(기존 92 + 새 28),
Ruff check·format check 통과. 모두 네트워크 없는 단위/통합 테스트다.
실제 출처 조회·공식 XLSX 다운로드는 위의 별도 실행이며 테스트 통과로 대체하지 않았다.

2026-10-17 최종 확인에서 Ruff는 `All checks passed!`, 포맷은 `33 files already formatted`였다.
합성 가격 파일로 실제 CLI `audit`·`recheck`도 실행해 `synthetic_only`, `recheck: matched`를
확인했다. 저장 경로는 `data/price_synthetic_check/07430a9d99f8/audits/`다.
10월 13일의 공식 XLSX를 다시 다운로드하지 않고 `issuer --file`로 읽은 결과도 세 종목 각
26건으로 같았다(`data/price_audit/20261017T061017Z-f09836b70301/`).
이 재검수는 공식 원본의 현재 수정 여부나 가격 대조를 새로 검증한 결과가 아니다.
최종 도구는 이후 실행에 세 검수 모듈의 소스 해시와 issuer 요청 옵션도 남긴다.
그 필드를 추가하기 전에 만들어진 기록에 과거 정보를 소급해 채우지 않았다.

새 테스트는 빈 응답·중복·역순·휴장과 실제 결측·잘못된 가격·일자 시간대·배타적 종료·12개월 준비,
분할 이중 처리·배당 이중 가산 방지·조정 산식 차이·공식 입력 불일치·설명 없는 조정 변화,
원본 바이트/해시 보존·오프라인 재검수·덮어쓰기 거부·권한 미확인/비밀 필드 차단,
XLSX 중복·빈 자본이득·배당락일/지급일 구별을 검사한다.
`Index 2012=100`으로 바꾼 **합성 INDPRO**가 기존 M3 검사에서 거부되는 것도 확인했다.
실제 FRED API를 이번에 재호출하거나 엄밀한 발표 시각을 검증한 것은 아니다.

사용자 Codespaces에는 이 작업 파일을 아직 전송하지 않았다. 변경 검토 후 파일이 반영된 상태에서:

```bash
cd /workspaces/MacroLens
git branch --show-current
git status --short
uv sync --locked --extra price-audit
uv run --locked --extra price-audit pytest -q
uv run --locked --extra price-audit ruff check .
uv run --locked --extra price-audit ruff format --check .
sha256sum src/macrolens/models/research_v1.json
```

정상: 브랜치 `feat/m4-validation`, 테스트 120 passed, `All checks passed!`,
포맷 변경 불필요, 해시는 위 동결 해시. extra 없이 실행하면 M4 테스트는 생략된다.
기존 테스트를 통과해도 실제 가격 확보나 예측력이 검증된 것은 아니다.

```bash
# 공개 문서의 연결 상태. 가격 요청은 하지 않음.
uv run --locked --extra price-audit python -m macrolens.price_audit probe

# 공식 배당 대조원 다운로드와 표본 3종 확인
uv run --locked --extra price-audit python -m macrolens.price_audit issuer

# 출력의 data/price_audit/<ID> 폴더를 아래 SAVED_DIR 대신 사용
uv run --locked --extra price-audit python -m macrolens.price_audit issuer --file SAVED_DIR/issuer-distributions.xlsx

# 허용된 가격 자료 확보 후 사용할 입력 틀. 기존 파일이 있으면 새 경로를 지정
uv run --locked --extra price-audit python -m macrolens.price_audit template --output data/price_inbox/manifest.json
uv run --locked --extra price-audit python -m macrolens.price_audit audit --manifest data/price_inbox/manifest.json

# audit가 출력한 폴더를 PRICE_AUDIT_DIR 대신 사용. probe/issuer 폴더와 다름
uv run --locked --extra price-audit python -m macrolens.price_audit recheck PRICE_AUDIT_DIR
```

| 출력/종료 코드 | 뜻·오류 확인 |
| --- | --- |
| `held / actual_issuer_distributions_not_prices`, 2 | 정상적으로 공식 배당만 검수했고 가격 적격성은 보류. report.md·audit.json 확인 |
| `held / actual_access_probe_not_price_data`, 2 | 연결 점검 완료. source_access의 proxy/HTTP/network 분류 확인 |
| `usable / real`, 0 | 명세와 기술/공식 입력 대조 조건 충족. 데이터 진실성·예측력 인증은 아님 |
| `conditional`, 2 | 공식 대조·정의 등 미완료. 상품별 warnings 확인. 12개 확대 조건 아님 |
| `held`, 2 | 빈 값·결측·사건 차이 등 issues를 조사. 자동 수정하지 않음 |
| `synthetic_only`, 2 | 합성 자료. technical_status와 무관하게 실제 자료 통과로 사용 금지 |
| `recheck: matched` | 기존 원본으로 검사 결과 재현. 원본 상태가 보류면 계속 보류 |
| `recheck: different`, 2 | 도구/달력 버전·원래 결과와 차이 조사. 원본은 유지 |
| `검수 실패`, 1 | 빈 명세의 권한 미확인, 파일 없음, 해시 불일치, 기존 template 덮어쓰기 등. 안전한 오류 문구와 불완전 폴더 확인 |

`report.md`는 VS Code에서 Ctrl+Shift+V로 읽는다. 원본은 Codespace 삭제 전에 허용되는 개인 보관소에
백업한다. 이 문서의 실행 ID는 **이번 별도 환경**의 것이며 사용자 Codespaces에 있다고 가정하지 않는다.

## 7. 다음 단계의 전제

KIS 조회 전용 어댑터는 구현했다(8절). 다음은 세 표본의 실제 가격·권리 수집이다. 이용 조건·보존 범위·조정 의미·공식 사건 대조를 확인해야 한다.
통과하면 XLE, XLB, XLI, XLY, XLP, XLV, XLF, XLK, XLC, XLU, XLRE, SPY로 확대하고,
실제 공통 기간·준비 기간을 동결한다. 지금은 백테스트 구현 착수 조건이 충족되지 않았다.
사용자는 설계의 미확정 항목과 자료 제약을 검토할 수 있으며, 성과를 본 뒤 자료·기간을 고르지 않는다.

[yf]: https://github.com/ranaroussi/yfinance
[yh]: https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py
[yt]: https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html
[yd]: https://help.yahoo.com/kb/SLN2311.html
[ya]: https://help.yahoo.com/kb/SLN28256.html
[st]: https://www.ssga.com/us/en/footer/terms-and-conditions
[sx]: https://www.ssga.com/library-content/products/fund-data/etfs/us/spdr-etf-historical-distributions.xlsx
[nav]: https://www.ssga.com/library-content/products/fund-data/etfs/us/navhist-us-en-xlk.xlsx
[xlc]: https://www.ssga.com/us/en/intermediary/etfs/state-street-communication-services-select-sector-spdr-etf-xlc
[xlk]: https://www.ssga.com/us/en/intermediary/etfs/state-street-technology-select-sector-spdr-etf-xlk
[spy]: https://www.ssga.com/us/en/intermediary/etfs/spdr-sp-500-etf-trust-spy
[split]: https://www.nasdaq.com/press-release/state-street-investment-management-announces-share-splits-five-select-sector-spdrr
[nyse]: https://www.nyse.com/markets/hours-calendars
[nh]: https://www.nasdaq.com/market-activity/etf/xlk/historical
[nt]: https://www.nasdaq.com/legal
[td]: https://twelvedata.com/pricing

## 8. 2026-10-23 KIS 구현 검수 — 실제 시장자료와 구별

원격 KIS 계획 `6331d97` 기준 `feat/m4-kis-data`에서 구현했다. 이전 환경의 미커밋 작업을
찾았다고 주장하지 않으며 현재 체크아웃의 기존 변경은 없었다. 소스·동결 문서 원본을 작업 전후
SHA-256으로 대조했다. 기존 Yahoo 입력과 M1~M3 코드는 변경하지 않았다.

| 상품 | 목표 기간 | 이번 실제 가격 파일/행 수 | 이번 실제 KIS 권리/식별 | 판정 |
| --- | --- | --- | --- | --- |
| XLC | 2020-06-01~2026-09-30 | 없음 / 0 | 미조회 | 보류: 실행 환경에 KIS 키 없음 |
| XLK | 동일 | 없음 / 0 | 미조회 | 보류: 실제 분할·배당 대조 미실시 |
| SPY | 동일 | 없음 / 0 | 미조회 | 보류: 실제 조정 범위·배당 대조 미실시 |

과거 공식 배당 각 26건 확보는 10월 13일 기록이다. 이번 새 환경에서 다시 수집한 것으로
세지 않는다. 실제 공통 가격 기간과 준비 기간도 여전히 미확정이다. 12개 확대는 하지 않았다.

직접 실행: 기존 120개 + KIS 모의/오프라인 27개 = **147 passed**, Ruff 검사·포맷 통과.
모의 가격 1592행은 달력/페이지/원본 재검수 테스트의 합성 자료이며 위 실측 0행을 바꾸지 않는다.
검사 범위: 인증 실패·캐시 재사용/만료/환경 분리/잠금·비밀 반사/중복 JSON 키 거부·주문 경로 거부,
429/5xx/401·반복/충돌 페이지·권리 연속키·부분 수집·9월 30일 누락·조정 이중 반영 거부,
빈 권리 unknown·권리 날짜 의미·오프라인 matched/원본 보존/해시 변조 거부.
키 없는 실제 `auth` 명령은 설정 누락으로 종료 1, 인증 HTTP 요청은 하지 않았다.
실제 FRED 호출도 하지 않았다. 기존 샘플 연구 모델 계산은 KIS 키 없이 `research_complete`를 확인했다.
실행 ID: `20261023T141812Z-83996da6d2f0` (합성 입력).

구현 파일: kis_client.py(인증·조회), kis_collect.py(원본·페이지), kis_audit.py(공급자 입력·검수),
kis.py(명령), tests/test_kis.py. 신규 의존성과 uv.lock 변경 없음.
최신 요청·필드 계약·확인 못한 조건은 [KIS 계획 11절](kis_integration_plan.md#11-2026-10-23-구현-계약과-확인-범위),
실행 순서·오류는 [README](../README.md#kis-조회-전용-연결-2026-10-23-구현)에 기록했다.
기존 6절 명령은 이전 Yahoo 도구 기록이며 KIS에 그 입력 계약을 적용하지 않는다.

다음 사용자 검토: 본인 계정의 무료 조회·개인 보존 조건, 실제 종목/거래소와 역사 범위,
MODP 조정 의미, 배당 금액/주당 기준·자본이득, XLK 분할 대조, 이후 평가의 총수익 정의.
`strict_point_in_time=false`와 R-001 후속 연구를 유지한다. 실제 보고서 검토 전 백테스트 착수 불가.

### 사용자 전달 KIS 실제 소량 결과 (2026-10-28 후속)

| 상품 | MODP=0 / MODP=1 가격 | 기간별권리 / ICE | 상품정보 조회 | 판정 |
| --- | --- | --- | --- | --- |
| XLC | 16 / 16행 | 1 / 0행 | HTTP 500 / EGW02004 | held |
| XLK | 16 / 16행 | 2 / 0행 | 동일 | held |
| SPY | 16 / 16행 | 1 / 0행 | 동일 | held |

사용자가 공유한 보고서의 결과이며 이 환경에서 원본을 직접 검사한 것은 아니다.
가격 범위는 **2020-06-01~06-10과 2026-09-21~09-30 두 구간**, 최소/최대 날짜 사이 전체 아님.
권리는 2025년 12월 기준일/발표일 요청이며 빈 ICE를 무배당으로 해석하지 않는다.
[공식 대체 식별 경로](kis_integration_plan.md#12-상품-식별-대체-경로--2026-10-28-후속)를
구현했으나 실제 성공은 미확인이다. 사용자는 새 probe를 실행해 식별·가격 결과를 확인한 뒤
전체 기간을 수집한다. 실제 공통 가격 기간·준비 기간과 총수익 적격성은 아직 미확정이다.
