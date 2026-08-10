# Cresta 구현 상태

## 1. 목적

명세된 요구사항의 계획, 구현, 검증 상태를 구분해 관리한다. `명세 완료`는 코드 구현이나 시험 완료를 의미하지 않는다.

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
| Guard 리스크·비상정지 | `docs/GUARD_RISK_SPEC.md` | 명세 완료 | BUY·부분/전량매도·고정손절 1차 평가 규칙과 기능 gate 명세 완료; 구현·모의시험 미착수 |
| 사용자 설정·적용 | `docs/CONFIGURATION_SPEC.md` | 구현 중 | 실행 권한 버전과 Provider/Model/역할별 배정 UI/API 구현; Guard 1차 위험 설정·entry order amount·실행 단계 계약 완료, 위험 설정 UI/API 미구현 |
| Web UI | `docs/WEB_UI_SPEC.md` | 구현 중 | 인증 Console, 감시 종목·Paper 조회·Broker 진단·실행 권한, Provider 내부 모델 관리·역할별 모델·프롬프트·단일 FAILOVER 배정, stage 채택 결과·schema·실제 provider/model 호출 이력과 scheduler·Scout provenance 구현; 승인 카드·Guard 상세 결과 미구현 |
| 인증·세션·TOTP | `docs/SECURITY_SPEC.md` | 구현 중 | 로그인 TOTP·세션·CSRF·실패제한 구현; 현재 개발 단계의 로그인 이후 설정·Provider·역할 배정·MOCK 시험 재인증은 제거하고 향후 위험 분석 시 선택적 재도입 예정, 복구·운영 검증 미완료 |
| 시장데이터·Watch | `docs/MARKET_DATA_SPEC.md` | 구현 중 | 감시 종목·키움 `0B`·`0D`, 1분봉과 v2 VWAP·SMA5·상대 거래량·실현 변동성·고점 하락률·spread 영속화 로컬 검증 완료; 체결강도와 v2 실제 장중 수신 미검증 |
| Scout·Core AI 계약 | `docs/AI_DECISION_SPEC.md` | 구현 중 | 불변 `scout-input-v1`과 `deterministic-mock-v2`, KST 정기 TRADING scheduler·SHADOW 인계 구현; 실제 AI provider와 보유 포지션 판단 미구현 |
| 다중 에이전트 오케스트레이션 | `docs/MULTI_AGENT_ORCHESTRATION_SPEC.md` | 구현 중 | 비동기 DIAGNOSTIC admission, Intel·Verify·4개 Scout·Core stage queue, claim·lease·fencing·만료 복구, 외부 Provider SHADOW 출력의 역할별 서버 재검증·stage 채택, FAIL_STOP·단일 FAILOVER와 Core WAIT·주문 0건 구현; 실제 유료 API 호출은 서버 검증 필요 |
| LLM Provider·Gateway | `docs/LLM_PROVIDER_GATEWAY_SPEC.md` | 구현 중 | 40개 Provider template, 35개 단일-key 등록, Native·OpenAI-compatible Adapter, 모델 동기화·역할 후보·Prompt·FAIL_STOP/단일 FAILOVER와 호출 이력 구현; 복합 인증 5종과 실제 API 호출 검증 미완료 |
| DB 스키마·영속성 | `docs/DATABASE_SPEC.md` | 구현 중 | 분봉·v2 지표·Scout 입력, LLM Foundation·Agent Runtime, 역할 배정·Agent lease·Provider tombstone·Prompt와 실패 정책 `20260808_0019` 로컬 적용·순환 통과; 실서버 PostgreSQL 적용 미검증 |
| 판단 실행·승인 | `docs/DECISION_EXECUTION_SPEC.md` | 구현 중 | DIAGNOSTIC/TRADING 경계, scheduler 인계, 멱등 SHADOW execution, 불변 Guard 평가와 안전 차단 구현; 승인·주문 생성은 미구현 |
| 운영·장애복구 | `docs/OPERATIONS_RUNBOOK.md` | 구현 중 | 전 서비스 `unless-stopped`, core healthcheck와 부팅 Compose 조정 unit 구현; 2026-08-05 Ubuntu 재부팅 후 9초 내 전체 health·worker READY 복구 통과, 백업·경보·복구훈련 미완료 |
| 구현 착수 준비도 | `docs/IMPLEMENTATION_READINESS_REVIEW.md` | 명세 완료 | 키움 출구 IP·MOCK 인증·시세 실서버 확인 반영, 계좌·주문 외부 통합 게이트 유지 |
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
- 첫 외부 LLM provider·과금 계정과 뉴스·공시 수집 source Adapter는 미선정이다. credential 등록은 가능하지만 외부 Agent 네트워크 호출과 route 활성화는 계속 차단한다.

## 6. 다음 구현 작업

다음 작업은 역할별 Prompt와 서버 계약 검증을 실제 외부 Provider SHADOW 호출로 서버 검증하는 단계다.

- PostgreSQL에 `20260808_0018` 적용 후 역할별 Prompt Profile 생성·검증·후보 연결 확인
- 외부 Provider 요청에서 system prompt와 구조화 user input 분리, 응답 schema·usage·request ID 실서버 확인
- timeout·provider 오류 시 FAIL_STOP과 단일 FAILOVER 이력, 프롬프트 원문 비노출 확인
- 이후 뉴스·공시 source Adapter와 Guard·승인·주문 생성 연결

## 2026-08-10 역할별 LLM 서비스 정책

- 역할 후보에서 전체 구조화 응답 timeout을 1–600초로 설정하며 신규 기본값은 30초다.
- `DEFAULT`, `PRIORITY`, `FLEX` 서비스 티어를 route version에 저장하고 외부 Adapter 요청에 전달한다.
- 진단 run 유효시간은 선택된 route timeout 합계와 안전 여유시간을 반영하여 10초 경계 응답이 run 자체의 1분 만료와 충돌하지 않도록 조정했다.
- 현재 전송은 non-streaming이며 최종 JSON을 받은 뒤 서버 계약 검증을 통과해야만 stage 성공으로 채택한다.
