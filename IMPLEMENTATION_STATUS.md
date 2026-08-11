# Cresta 구현 상태

## 1. 목적

명세된 요구사항의 계획, 구현, 검증 상태를 구분해 관리한다. `명세 완료`는 코드 구현이나 시험 완료를 의미하지 않는다.

### 2026-08-11 불완전 Scout의 결정론적 Core 축소

- 신규 `agent-dag-v6`에서 필수 Scout가 하나라도 불완전하면 Core Provider를 호출하지 않고 서버가 `WAIT/UNKNOWN`, confidence 0, HIGH risk와 정확한 incomplete roles를 기록한다. 기존 v5 run은 당시 의미와 idempotency key로 보존한다.
- Scout 원본 상태와 Provider 응답은 치환하지 않으며, 완전한 입력에서는 기존 Core Provider 호출을 유지한다.
- 실서버에서 확인된 `LLM_CORE_SHADOW_ASSESSMENT_MISMATCH` 재현 조건을 외부 Provider fixture로 고정하고 `PARTIAL` 종료·Core invocation 0건·주문 0건을 검증했다.
- Backend 집중시험·전체 회귀·Ruff와 Frontend TypeScript·12개 component 시험·production build가 통과했다. 실서버 재배포 후 동일 종목 수동 재검증은 대기 중이다.

### 2026-08-11 서버 소유 Agent 판단 입력 v1

- 신규 DIAGNOSTIC admission을 `agent-dag-v5`와 `agent-server-input-v1`로 전환했다.
- 포지션의 원가·평가금액·미실현손익·수익률·고점 하락률·고정 손절 가격/거리·보유시간·freshness와 적용 Risk Policy provenance를 admission 시 계산해 불변 snapshot으로 고정한다.
- 검증된 내부 `market-context-v1` snapshot만 지수·업종·시장 breadth 입력으로 고정하며, 부재·stale·INCOMPLETE 상태에서는 Market Scout가 Provider를 호출하거나 값을 추정하지 않고 `INSUFFICIENT_DATA`로 축소한다.
- migration `20260811_0027` 왕복, backend 전체 회귀·Ruff, frontend TypeScript·12개 component 시험을 로컬에서 통과했다. Ubuntu PostgreSQL 적용과 실제 시장 context source 연결은 미검증이다.

### 2026-08-11 Agent SHADOW 판단 계약 v2

- 신규 DIAGNOSTIC admission을 `agent-dag-v4`로 전환하고 ENTRY/POSITION context와 position snapshot hash를 불변으로 고정했다.
- `agent-assessment-v2`, `agent-core-v2`, `score-policy-v1`, ENTRY의 `NOT_APPLICABLE`과 Core의 별도 `shadow_assessment`를 구현했다.
- 기존 v1 schema 선언 route는 재생성 없이 사용할 수 있으며 run에는 선언 schema와 실제 v2 검증 schema를 함께 남긴다. 기존 v1~v3 run은 nullable 신규 field로 그대로 조회한다.
- migration `20260811_0026` 왕복, backend 전체 회귀·Ruff, frontend TypeScript·12개 component 시험·production build를 로컬에서 통과했다. Ubuntu PostgreSQL 적용과 Console 수동 확인은 미검증이다.

### 2026-08-11 LLM 구조화 출력 기본 여유 상향

- 신규 Model Profile의 API·ORM·DB server default와 Provider 미명시 fallback을 `8192`로 상향했다.
- Console의 신규 역할 변경 후보는 `max output=8192`를 명시적 override 기본값으로 사용한다.
- 기존 Model Profile, 활성 Route 및 Invocation 이력은 자동 변경하지 않는다.
- Backend 전체 시험·Ruff, Frontend component 시험·TypeScript 검사와 `20260811_0024` upgrade/downgrade/upgrade가 통과했다. Ubuntu PostgreSQL도 `20260811_0025` head까지 적용해 server default를 확인했다.

### 2026-08-11 LLM 생성 파라미터 안전화

- 신규 sampling·seed 기본값을 Adapter 상속으로 변경하고 Gemini 3.x sampling 생략·thinkingLevel 변환, routed OpenAI reasoning 판정을 구현했다.
- 신규 Route 기본 120초, Flex 권장 300초, 연결 제한 10초와 Provider별 service tier 검증을 반영했다.
- 일일 호출 횟수를 실제 Invocation 경계에서 집행하며 비용 제한은 가격 산정 미구현 상태에서 양수 설정을 거부한다.
- 기존 활성 Route와 Model Profile 값은 자동 변경하지 않는다. Backend 전체 회귀·Ruff, Frontend component·TypeScript, SQLite migration 왕복과 Ubuntu PostgreSQL `20260811_0025` 적용을 확인했다.

## 2. 상태 정의

- `미명세`: 요구사항이 아직 문서화되지 않음
- `명세 완료`: 기준 문서와 인수 조건이 작성됨
- `구현 중`: 코드 또는 인프라 구현이 진행 중
- `구현 완료/미검증`: 구현됐지만 시험 근거가 없음
- `검증 완료`: `TEST_PLAN.md`의 대응 시험을 통과함
- `보류`: 외부 환경 또는 결정이 필요함

## 3. 현재 상태

| 영역 | 기준 문서 | 상태 | 비고 |
| --- | --- | --- | --- |
| 제품 범위와 실행 권한 | `docs/PRODUCT_REQUIREMENTS.md` | 구현 중 | 기본 행동 8종 mode 설정 구현; 거래/진단 판단 분리와 SHADOW→승인→MOCK 자동 단계 명세 완료, 실행 연결 미구현 |
| 거래 세션과 감시 일정 | `docs/TRADING_SESSION_SPEC.md` | 명세 완료 | NXT는 키움 모의투자 검증 불가 |
| 주문 가격과 미체결 처리 | `docs/ORDER_EXECUTION_SPEC.md` | 구현 중 | Paper와 키움 CREATED polling·ACK/REJECTED/UNKNOWN 구현; Guard 평가·수량·원자 주문 생성 계약 완료, 실제 전략 주문 미구현 |
| 주문 상태 머신과 키움 매핑 | `docs/ORDER_STATE_MACHINE_SPEC.md` | 구현 중 | Paper·키움 송신 전이 구현; execution·승인과 주문 상태 분리·원자 전이 명세 완료, 승인 구현 미착수 |
| 계좌·주문 재동기화 | `docs/RECONCILIATION_SPEC.md` | 구현 중 | snapshot 대조와 상시 worker READY·재시작 fencing은 실서버 통과; `00`·`04` 이벤트 즉시 gate 차단·debounce·BROKER_EVENT 대조 로컬 통과, 실제 체결·장애주입 미검증 |
| 시스템 아키텍처 | `docs/SYSTEM_DESIGN.md` | 구현 중 | Backend·Console·gateway·키움 worker·AI scheduler·별도 Agent worker·Watch와 SHADOW 실행 구현; 주문 생성 Guard 미구현 |
| HTTP/WebSocket API | `docs/API_SPEC.md` | 구현 중 | 인증·상태·주문/체결·포지션·quote 조회 구현; Guard·승인·실행 단계 REST/event 계약 완료, 거래 명령·stream 미구현 |
| UI 콘셉트 참고자료 | `stitch_cresta_ai_intraday_trading_system/` | 참고자료 | 실제 Console 구현물이 아님 |
| 키움 모의투자 Adapter | `docs/KIWOOM_BROKER_SPEC.md` | 구현 중 | 인증·snapshot·worker는 실서버 통과; 주문 Adapter·FIFO polling·UNKNOWN 대조·계좌 event gate·Web MOCK 1주 진단 API 자동시험 통과, 실제 모의주문 미검증 |
| Guard 리스크·비상정지 | `docs/GUARD_RISK_SPEC.md` | 구현 중 | BUY SHADOW 1차 평가와 `USER_DEFAULT / RISK_POLICY` 안전 기본값·검증·활성화 구현; 전체 노출·예수금·일일진입·spread 평가, 손절 trigger·비상정지는 미구현 |
| 사용자 설정·적용 | `docs/CONFIGURATION_SPEC.md` | 구현 중 | 실행 권한, Guard 사용자 기본 위험 설정과 Provider/Model/역할별 배정 UI/API 구현; 종목별 위험 override·영향 미리보기·예약 적용 미구현 |
| Web UI | `docs/WEB_UI_SPEC.md` | 구현 중 | 인증 Console, 감시 종목·Paper 조회·Broker 진단·실행 권한·Guard 위험 설정, Provider 모델·역할·프롬프트·FAILOVER 배정, stage 결과·구조화 응답 조회 구현; 승인 카드·Guard 평가 상세 결과 미구현 |
| 인증·세션·TOTP | `docs/SECURITY_SPEC.md` | 구현 중 | 로그인 TOTP·세션·CSRF·실패제한 구현; 현재 개발 단계의 로그인 이후 설정·Provider·역할 배정·MOCK 시험 재인증은 제거하고 향후 위험 분석 시 선택적 재도입 예정, 복구·운영 검증 미완료 |
| 시장데이터·Watch | `docs/MARKET_DATA_SPEC.md` | 구현 중 | 감시 종목·키움 `0B`·`0D`, 1분봉과 v2 VWAP·SMA5·상대 거래량·실현 변동성·고점 하락률·spread 영속화 로컬 검증 완료; 체결강도와 v2 실제 장중 수신 미검증 |
| Scout·Core AI 계약 | `docs/AI_DECISION_SPEC.md` | 구현 중 | 불변 `scout-input-v1`과 `deterministic-mock-v2`, 외부 Provider DIAGNOSTIC 판단, context별 v2 출력 계약과 `agent-server-input-v1` 포지션 파생값을 로컬 검증 완료; 실서버 v5 검증 대기 |
| 다중 에이전트 오케스트레이션 | `docs/MULTI_AGENT_ORCHESTRATION_SPEC.md` | 구현 중 | Agent Runtime v6의 Intel·Verify·4개 Scout·Candidate Auditor·Core, 서버 입력과 불완전 Scout의 결정론적 Core 축소 구현; v6 로컬 회귀 완료, 실서버 검증 대기 |
| LLM Provider·Gateway | `docs/LLM_PROVIDER_GATEWAY_SPEC.md` | 구현 중 | 40개 Provider template, 35개 단일-key 등록, Native·OpenAI-compatible Adapter, 모델 동기화·역할·Prompt·FAIL_STOP/단일 FAILOVER·service tier·웹 검색·호출 이력 구현; OpenAI·LLM Gateway 실제 SHADOW 호출 검증 완료, 복합 인증 5종·가격 기반 비용 집계 미구현 |
| DB 스키마·영속성 | `docs/DATABASE_SPEC.md` | 구현 중 | 분봉·v2 지표·Scout 입력, LLM Foundation·Agent Runtime v6, 역할 배정·Agent lease·Provider tombstone·Prompt·Evidence Auditor·Market Context와 제한된 구조화 응답 이력을 `20260811_0027`까지 로컬 왕복 완료; Ubuntu는 `0027` 적용 확인 |
| 판단 실행·승인 | `docs/DECISION_EXECUTION_SPEC.md` | 구현 중 | DIAGNOSTIC/TRADING 경계, scheduler 인계, 멱등 SHADOW execution, 불변 Guard 평가와 안전 차단 구현; 승인·주문 생성은 미구현 |
| 운영·장애복구 | `docs/OPERATIONS_RUNBOOK.md` | 구현 중 | 전 서비스 `unless-stopped`, core healthcheck와 선택형 DART·KRX overlay 감지 부팅 조정 unit 구현; 2026-08-05 기본·키움 재부팅 복구 통과, 신규 source overlay 재부팅 인수시험·백업·경보·복구훈련 미완료 |
| 구현 착수 준비도 | `docs/IMPLEMENTATION_READINESS_REVIEW.md` | 역사적 검토 | 2026-08-06 Foundation·Agent Runtime v1 착수 게이트 기록이며 현재 상태는 이 문서를 기준으로 한다. |
| Backend·Docker 골격 | `docs/SYSTEM_DESIGN.md`, `docs/OPERATIONS_RUNBOOK.md` | 검증 완료 | API source UID `10001` 소유권·PostgreSQL·Redis·API·Frontend·gateway 기동과 HTTPS/내부 health 실서버 확인 |

## 4. 구현 완료 조건

기능별 완료는 다음 조건을 모두 만족해야 한다.

1. 관련 요구사항 ID가 기준 문서에 존재한다.
2. 구현이 요구사항과 일치한다.
3. 대응 테스트 ID가 `TEST_PLAN.md`에 존재한다.
4. 자동 또는 수동 시험 결과가 기록된다.
5. 미검증 사항과 외부 제약이 숨김없이 표시된다.
6. 관련 문서와 `AGENTS.md` 색인이 갱신된다.

## 5. 미결정·보류 항목

- 키움 모의투자 계정·API 사용신청·고정 출구 IP와 REST 인증·시세·10자리 계좌 일치는 실제 서버 확인 완료
- NXT/SOR 실거래 검증 환경
- 외부 LLM Provider SHADOW 호출은 활성화됐지만 모델·Gateway별 실제 응답 편차를 계속 검증해야 한다.
- OpenDART 실제 키·고정 출구 IP 호출과 삼성전자 최근 3일 공시 6건 수집을 Ubuntu 서버에서 확인했다. KRX 전 거래일 공식 일별매매 Adapter는 구현했지만 KRX 인증키·서비스 승인과 실제 서버 호출은 아직 미검증이며 계약 뉴스 source는 미선정이다.
- DART·KRX secret을 감지하는 선택 overlay 부팅 조정은 구현했지만 실제 Ubuntu 재부팅 자동복구 인수시험은 미검증이다.

## 6. 다음 구현 작업

구현 순서는 아래 단계로 고정한다. 각 단계는 해당 요구사항·자동시험·운영 확인을 모두 충족한 뒤 다음 단계로 넘어가며, 외부 LLM 결과를 곧바로 주문으로 연결하지 않는다.

### 6.1 완료 — Agent SHADOW 판단 계약 v2

범위:

- admission 시 `ENTRY/POSITION` analysis context와 position snapshot을 불변으로 고정
- ENTRY의 포지션 위험 역할에 `NOT_APPLICABLE` 의미 추가
- `agent-assessment-v2`, `agent-core-v2`, `score-policy-v1`, `agent-dag-v4` 도입
- Core의 실행 `WAIT`와 별도 `shadow_assessment` 분리
- API·Console에서 context, 계약 version, N/A와 null 점수를 명확히 표시

완료 gate:

- `T-AGENT-SHADOW-001`~`010` 통과
- migration `20260811_0026_agent_shadow_contract_v2` 왕복 검증
- 기존 v1~v3 run 조회·멱등성 회귀 통과
- DIAGNOSTIC run의 Decision·Approval·OrderIntent·TradingOrder 0건 확인

구현 순서:

1. `backend/app/models.py`와 신규 migration에 run context·snapshot hash·N/A 상태·shadow assessment 저장 구조를 추가한다.
2. `backend/app/agents/contracts.py`, `reason_codes.py`, `runtime.py`에 v2 schema와 v4 admission·멱등 계약을 추가한다.
3. Agent worker의 dependency·필수 역할·Core 입력 조립을 context-aware 방식으로 변경한다.
4. `backend/app/schemas.py`와 Agent run API에 version·context·assessment 응답을 추가한다.
5. Console에 WAIT/평가 분리, `해당 없음`, null 점수와 version 표시를 추가한다.
6. 신규 집중시험 후 backend 전체 회귀·Ruff, frontend component·TypeScript·production build와 migration 왕복을 실행한다.

### 6.2 완료 — 서버 소유 판단 입력 확장

범위:

- Position Risk 입력에 미실현 손익, 고점 대비 하락, stop 거리, 보유시간과 잔여 수량을 서버 계산값으로 추가
- Market Sector 입력에 KRX 지수·업종·시장 breadth의 검증 snapshot을 추가
- 각 값에 관측시각, freshness와 source reference를 부여

완료 gate:

- 동일 원시 snapshot의 계산 재현성과 stale/missing 경계 시험 통과
- 모델이 서버 계산값을 덮어쓰거나 추정값을 evidence로 승격할 수 없음
- ENTRY와 POSITION fixture 모두에서 v2 계약 통과

검증 결과:

- `T-AGENT-INPUT-001`~`005`, `T-MARKET-CONTEXT-001`~`003` 자동시험 통과
- migration `20260811_0027_server_owned_agent_inputs` upgrade/downgrade/upgrade 통과
- backend 전체 회귀·Ruff와 frontend TypeScript·12개 component 시험 통과
- 모든 DIAGNOSTIC 경로의 Decision·Approval·OrderIntent·TradingOrder 0건 유지

### 6.3 진행 중 — 검증된 외부 증거 coverage

범위:

- OpenDART 외에 뉴스와 시장·업종의 공식 또는 계약된 source Adapter 선정
- Provider citation은 계속 `UNRATED`로 보존하고 독립 검증을 통과한 항목만 EvidenceBundle에 편입
- 출처별 freshness, 중복 제거, 장애와 quota 정책 확정

현재 결과:

- 공식 KRX OPEN API의 KOSPI·KOSDAQ 일별매매정보를 최근 전 거래일 증거로 수집하는 선택 Adapter를 구현했다.
- 정확한 종목코드 매칭, 7일 freshness 상한, 일자·시장 cache, 정상 무자료와 장애 분리, DART와의 복합 EvidenceBundle 편입 경계를 적용했다.
- DART·KRX secret 존재 여부에 따라 선택 overlay를 포함하는 boot reconcile 스크립트와 systemd unit을 구현했다.
- 계약 뉴스 source 선정, KRX 실제 키 호출, DART·KRX 재부팅 인수시험은 남아 있다.

완료 gate:

- 뉴스·공시·시장 source의 성공·빈 결과·stale·장애 시험 통과
- Core가 허용된 evidence ID 외 URL·자유문장을 근거로 사용할 수 없음
- DART overlay를 포함한 재부팅 자동복구 인수시험 통과

### 6.4 4순위 — replay·평가·모델 채택

범위:

- 동일 입력에 대한 모델·prompt·tier별 schema 통과율, latency, 비용과 5분·10분·30분 수익률, MFE·MAE 수집
- score-policy와 shadow assessment의 방향성·일관성 검증
- 운영 역할별 primary·fallback·timeout·tier 추천 근거 생성

완료 gate:

- 최소 표본수와 평가 기간을 별도 eval 계획에서 확정
- 결과 재현 가능한 replay report와 모델 교체 근거 존재
- 이 gate 전에는 조건부 고성능 Core, 복수 모델 투표와 자동 모델 교체를 구현하지 않음

### 6.5 5순위 — TRADING Guard·승인·MOCK 주문 연결

범위:

- Guard 전체 노출·예수금·일일진입·spread와 손절 trigger 완성
- 기능별 `AUTOMATIC/MANUAL_APPROVAL/DISABLED` 실행 권한 적용
- 승인 카드, 만료·거절, 재평가와 원자 OrderIntent·TradingOrder 생성
- 외부 AI 결과가 실패·불완전·만료일 때 신규매수 fail-closed

완료 gate:

- 고정 손절·비상정지·장마감 청산이 AI와 독립적으로 동작
- 승인·자동 경로의 멱등성, 중복 주문, UNKNOWN 대조와 재시작 복구 시험 통과
- 키움 모의투자 소액 주문·부분체결·취소·거절의 장중 실서버 검증

### 6.6 6순위 — 운영 안정화와 제한 자동매매 준비

- DART 포함 systemd boot profile, backup·restore drill, 경보와 운영 dashboard 완성
- 실제 장중 Watch 지표, 체결 event와 reconciliation 장애 주입 시험
- 거래일별 손익·모델 판단·Guard 차단·주문 결과 review report
- 실거래 credential과 권한은 별도 승인 전까지 추가하지 않음

### 보류 기준

- 조건부 Core 모델 승격, factor별 evidence 강제 매핑과 가격 기반 비용 차단은 4순위 평가 결과가 생긴 뒤 결정한다.
- NXT/SOR 실거래, 복합 인증 Provider 5종과 Ollama 운영 모델은 현재 핵심 경로를 막지 않으므로 별도 backlog로 유지한다.
- 단계 순서를 바꾸려면 관련 명세와 인수 gate를 먼저 수정하고 변경 이유를 이 문서에 기록한다.

## 2026-08-10 역할별 LLM 서비스 정책

- 역할 후보에서 전체 구조화 응답 timeout을 1–600초로 설정하며 신규 기본값은 120초다.
- `DEFAULT`, `PRIORITY`, `FLEX` 서비스 티어를 route version에 저장하고 외부 Adapter 요청에 전달한다.
- 진단 run 유효시간은 선택된 route timeout 합계와 안전 여유시간을 반영하여 10초 경계 응답이 run 자체의 1분 만료와 충돌하지 않도록 조정했다.
- 현재 전송은 non-streaming이며 최종 JSON을 받은 뒤 서버 계약 검증을 통과해야만 stage 성공으로 채택한다.

## 2026-08-11 Provider web search and runtime clock

- APIchat Adapter 동작을 읽기 전용으로 분석해 OpenAI Responses, Anthropic Messages, Gemini generateContent 및 LLM Gateway 호환 웹 검색 변환을 추가했다.
- 역할 route에 versioned 웹 검색 권한을 추가하고 뉴스·공시/시장·업종 Scout만 SHADOW에서 허용하도록 fail-closed 검증을 적용했다.
- 모든 LLM 호출에 UTC와 Asia/Seoul 현재 시각 및 최신성 지침을 주입하고 invocation 감사 이력에 실행 시각과 검색 사용 여부를 저장한다.
- Provider 검색 결과의 citation/source metadata를 공통 후보로 수집하는 경계는 구현했지만 검증된 EvidenceBundle로 승격하는 Verifier는 아직 구현되지 않았으므로 검색 결과는 주문·승인 경계 밖에 있다.

## 2026-08-11 APIchat-compatible request normalization

- APIchat의 실제 Provider parameter policy와 OpenAI-compatible request builder를 읽기 전용으로 대조했다.
- GPT-5/o계열 토큰 한도 필드, reasoning sampling 억제, reasoning effort 전달, strict schema 요청과 모델별 capability 동기화를 구현했다.
- 로컬 전체 backend 시험을 통과했고 Ubuntu 서버에서 OpenAI GPT-5 계열과 LLM Gateway 경유 Gemini SHADOW 호출·schema 재검증을 확인했다.

## 2026-08-11 OpenAI Responses reasoning normalization

- 서버 SHADOW 결과에서 OpenAI 공식 `gpt-5-mini`가 DEFAULT tier에서도 즉시 거부되는 경로를 분리했다.
- Responses Adapter가 GPT-5/o계열의 `temperature/top_p`를 reasoning 기본값에서도 제거하도록 APIchat 모델 정책을 공통 적용했다.
- 집중 시험과 backend 전체 회귀·Ruff를 통과했고 Ubuntu 서버의 Technical Scout에서 OpenAI 공식 응답의 schema 통과를 확인했다.

## 2026-08-11 역할 비종속 Provider 출처 후보 수집

- OpenAI Responses, Anthropic Messages, Gemini generateContent와 OpenAI-compatible 응답의 알려진 citation 위치를 공통 `EvidenceSourceCandidate`로 정규화한다.
- 공개 HTTPS URL만 run별 중복 없이 `UNRATED EvidenceItem`으로 저장하며 원문 응답, private·loopback URL과 credential은 저장하지 않는다.
- Scout의 일반 `allowed_input_refs`와 검증 근거 전용 `allowed_evidence_refs`를 분리했다. 검증된 근거가 없으면 모델은 빈 `evidence_refs`를 반환해야 하며 URL이나 임의 ID를 반환하면 fail-closed 처리한다.
- schema, evidence reference와 Core incomplete-role 계약 오류를 구분된 안전 코드로 기록한다. 집중 시험 25개, backend 전체 188개 시험 및 Ruff를 통과했다.

## 2026-08-11 Evidence Candidate Auditor

- `agent-dag-v2` 고정 DAG를 8개 stage로 확장하고 네 Scout 이후 Core 전에 내부 `EVIDENCE_CANDIDATE_AUDITOR`를 배치했다. 신규 버전은 기존 v1 멱등 run과 분리되며 진행 중 v1은 감사 없음으로 안전하게 마무리한다.
- Auditor는 외부 호출 없이 현재 run의 `UNRATED EvidenceItem`을 중복 제거된 내부 ID와 Provider별 개수로 집계한다. URL·Provider 원문 응답은 Core에 전달하지 않는다.
- 기존 EvidenceBundle은 수정하지 않으며 Core에는 감사 stage ref, 후보 개수, reason code와 검증 근거 개수만 전달한다. Bundle이 `VERIFIED`가 아니면 run은 계속 `PARTIAL`이다.
- `20260811_0022` migration의 upgrade→downgrade→upgrade, backend 전체 188개 시험과 Ruff를 통과했다. 실제 출처를 `PRIMARY/SECONDARY`로 승격하는 정책은 DART·거래소·뉴스 수집 방식 확정 후 구현한다.

## 2026-08-11 역할별 reason code 계약

- `reason-code-policy-v1`에서 4개 Scout와 Core가 반환할 수 있는 reason code를 역할별 allowlist로 고정했다.
- 각 LLM 요청의 구조화 입력과 JSON Schema enum에 동일한 정책 버전과 허용 목록을 전달하며, 서버가 응답을 다시 검증한다.
- 등록되지 않은 code는 Provider의 schema 성공 여부와 무관하게 `INVALID_OUTPUT/FAILED` 및 `LLM_REASON_CODE_NOT_ALLOWED`로 fail-closed 처리한다.
- Mock 판단도 같은 용어를 사용하도록 정리했으며, 원문 응답 저장은 이번 범위에서 제외하고 별도 로깅 단계로 남겼다.
- 로컬 backend 전체 196개 테스트와 Ruff lint가 통과했다.

## 2026-08-11 OpenDART PRIMARY evidence Adapter

- `agent-dag-v3`에서 선택형 OpenDART 공시검색 Adapter를 `INTEL_COLLECTOR`에 연결했다. 공식 endpoint, KST 최근 3일, 최대 page와 정확한 종목코드 필터를 적용한다.
- 검증된 공시는 공식 viewer URL과 안전한 메타데이터만 `DART_DISCLOSURE/PRIMARY`로 저장하며 키와 원문 응답은 보존하지 않는다.
- `000`과 `013`을 정상 처리하고 인증·IP·한도·HTTP·timeout·page cap 오류는 `DART_*` code로 fail-closed 처리한다.
- 검증 evidence ID는 Scout allowlist에 전달하지만 계약 뉴스 coverage가 없으므로 Bundle과 run은 `PARTIAL`, 주문은 0건으로 유지한다.
- 선택형 `deploy/compose.dart.yaml`과 secret 권한 준비 절차를 추가했다. 활성화 상태에서 secret이 유효하지 않으면 run admission을 409로 차단한다. 로컬 회귀시험과 Ubuntu 실제 OpenDART 호출·공시 6건 수집을 확인했다. 이후 선택 overlay 감지 boot reconcile을 구현했으며 실제 재부팅 인수시험은 남아 있다.

## 2026-08-11 구조화 LLM 응답 이력

- Adapter가 추출한 구조화 JSON을 역할 계약 검증 전에 canonical form·SHA-256 hash·capture 시각으로 invocation에 저장한다. 성공뿐 아니라 reason code·evidence ref 등 서버 계약 실패 응답도 프롬프트 개선을 위해 보존한다.
- Provider raw body, prompt, tool payload와 credential은 저장하지 않는다. 64 KiB 초과 또는 민감 key 이름을 포함한 output은 보관하지 않고 전용 오류로 fail-closed 처리한다.
- run 목록에는 output을 포함하지 않고 소유권을 확인하는 개별 API와 Console의 지연 로딩 버튼으로만 조회한다.
- `20260811_0023` upgrade→downgrade→upgrade, backend 전체 회귀·Ruff, frontend TypeScript·component 시험·production build가 통과했다. Ubuntu PostgreSQL 적용과 실제 외부 구조화 응답 Console 조회도 확인했다.

## 2026-08-11 Guard 사용자 기본 위험 설정

- 기존 `configuration_versions`에 독립 category `RISK_POLICY`를 사용해 안전 기본값 조회, DRAFT→VALIDATED→ACTIVE, 이전 ACTIVE→SUPERSEDED와 stale base 충돌 검사를 구현했다.
- 진입금액·1회/종목/전체 금액·최대 보유종목·일일진입·고정손절·시세 지연·spread·가격편차 범위와 금액 순서를 서버에서 검증한다. 활성 버전이 없으면 `entry_order_amount=null`로 BUY 차단을 유지한다.
- Guard SHADOW 평가와 execution에 사용한 risk version ID를 기록하고, Console에서 source·active version·미설정 차단을 표시하며 변경안 검증·확정을 수행한다.
- backend 전체 209개 테스트·Ruff, frontend TypeScript·12개 component 테스트·production build가 통과했다. 전체 계좌 노출 계산·영향 미리보기·종목별 override는 후속 구현이다.

## 2026-08-11 KRX PRIMARY 전 거래일 시장 증거 Adapter

- 공식 KRX OPEN API의 유가증권·코스닥 일별매매정보를 선택형 `INTEL_COLLECTOR` source로 추가했다. KST 실행일 이전 최근 7일, 정확한 6자리 종목코드와 공식 응답 필드만 채택한다.
- KRX 행은 `KRX_DAILY_MARKET/PRIMARY`로 저장하며 인증키·원문 응답은 보존하지 않는다. 일자·시장·credential fingerprint별 메모리 cache로 key당 일 10,000회 한도를 보호한다.
- 정상 무자료와 HTTP·timeout·형식 장애를 구분하고, 활성 secret이 잘못되면 run admission을 409로 차단한다. DART와 KRX 증거는 같은 불변 Bundle에 들어가지만 계약 뉴스 coverage 전까지 `PARTIAL`, 주문 0건을 유지한다.
- DART·KRX secret 존재 여부로 선택 overlay를 조합하는 `boot-reconcile.sh`와 systemd unit을 추가했다. Ruff와 backend 전체 회귀시험을 통과했으며 KRX 실제 키 호출과 Ubuntu 재부팅 인수시험은 남아 있다.
