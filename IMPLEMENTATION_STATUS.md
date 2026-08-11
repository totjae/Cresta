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
| Guard 리스크·비상정지 | `docs/GUARD_RISK_SPEC.md` | 구현 중 | BUY SHADOW 1차 평가와 `USER_DEFAULT / RISK_POLICY` 안전 기본값·검증·활성화 구현; 전체 노출·예수금·일일진입·spread 평가, 손절 trigger·비상정지는 미구현 |
| 사용자 설정·적용 | `docs/CONFIGURATION_SPEC.md` | 구현 중 | 실행 권한, Guard 사용자 기본 위험 설정과 Provider/Model/역할별 배정 UI/API 구현; 종목별 위험 override·영향 미리보기·예약 적용 미구현 |
| Web UI | `docs/WEB_UI_SPEC.md` | 구현 중 | 인증 Console, 감시 종목·Paper 조회·Broker 진단·실행 권한·Guard 위험 설정, Provider 모델·역할·프롬프트·FAILOVER 배정, stage 결과·구조화 응답 조회 구현; 승인 카드·Guard 평가 상세 결과 미구현 |
| 인증·세션·TOTP | `docs/SECURITY_SPEC.md` | 구현 중 | 로그인 TOTP·세션·CSRF·실패제한 구현; 현재 개발 단계의 로그인 이후 설정·Provider·역할 배정·MOCK 시험 재인증은 제거하고 향후 위험 분석 시 선택적 재도입 예정, 복구·운영 검증 미완료 |
| 시장데이터·Watch | `docs/MARKET_DATA_SPEC.md` | 구현 중 | 감시 종목·키움 `0B`·`0D`, 1분봉과 v2 VWAP·SMA5·상대 거래량·실현 변동성·고점 하락률·spread 영속화 로컬 검증 완료; 체결강도와 v2 실제 장중 수신 미검증 |
| Scout·Core AI 계약 | `docs/AI_DECISION_SPEC.md` | 구현 중 | 불변 `scout-input-v1`과 `deterministic-mock-v2`, KST 정기 TRADING scheduler·SHADOW 인계 구현; 실제 AI provider와 보유 포지션 판단 미구현 |
| 다중 에이전트 오케스트레이션 | `docs/MULTI_AGENT_ORCHESTRATION_SPEC.md` | 구현 중 | 비동기 DIAGNOSTIC admission, Intel·Verify·4개 Scout·Candidate Auditor·Core stage queue, claim·lease·fencing·만료 복구, 외부 Provider SHADOW 출력의 역할별 서버 재검증·stage 채택, 구조화 응답 이력, FAIL_STOP·단일 FAILOVER와 Core WAIT·주문 0건 구현; 실제 OpenDART·외부 Provider 회귀 검증 필요 |
| LLM Provider·Gateway | `docs/LLM_PROVIDER_GATEWAY_SPEC.md` | 구현 중 | 40개 Provider template, 35개 단일-key 등록, Native·OpenAI-compatible Adapter, 모델 동기화·역할 후보·Prompt·FAIL_STOP/단일 FAILOVER와 호출 이력 구현; 복합 인증 5종과 실제 API 호출 검증 미완료 |
| DB 스키마·영속성 | `docs/DATABASE_SPEC.md` | 구현 중 | 분봉·v2 지표·Scout 입력, LLM Foundation·Agent Runtime, 역할 배정·Agent lease·Provider tombstone·Prompt·실패 정책·제한된 구조화 응답 이력 `20260811_0023` 로컬 적용·순환 통과; 실서버 PostgreSQL 적용 미검증 |
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
- 외부 LLM Provider SHADOW 호출은 활성화됐지만 모델·Gateway별 실제 응답 편차를 계속 검증해야 한다.
- OpenDART Adapter는 구현됐지만 실제 키·고정 출구 IP 호출은 Ubuntu 서버에서 미검증이다. 거래소·뉴스의 추가 검증 source는 아직 미선정이다.

## 6. 다음 구현 작업

다음 작업은 Ubuntu에 `20260811_0023`을 적용해 구조화 응답 이력을 실제 Provider로 확인한 뒤 Guard 위험 설정과 승인·주문 생성 경계를 연결하는 단계다.

- OpenDART를 선택적으로 활성화하고 실제 공시 PRIMARY evidence와 0건·오류 상태 확인
- 성공·schema 실패 외부 호출의 구조화 응답 조회, 64 KiB·민감 key 차단과 목록 비노출 확인
- Guard 평가 상세 조회·표시와 전체 노출·예수금·일일진입·spread 규칙 구현
- MANUAL_APPROVAL 승인 카드 및 승인 만료·거절 이후 원자 주문 생성 연결

## 2026-08-10 역할별 LLM 서비스 정책

- 역할 후보에서 전체 구조화 응답 timeout을 1–600초로 설정하며 신규 기본값은 30초다.
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
- 로컬 전체 backend 시험은 통과했으며 실제 LLM Gateway 계정의 GPT-5와 Gateway 경유 Gemini SHADOW 호출은 Ubuntu 서버 배포 후 검증한다.

## 2026-08-11 OpenAI Responses reasoning normalization

- 서버 SHADOW 결과에서 OpenAI 공식 `gpt-5-mini`가 DEFAULT tier에서도 즉시 거부되는 경로를 분리했다.
- Responses Adapter가 GPT-5/o계열의 `temperature/top_p`를 reasoning 기본값에서도 제거하도록 APIchat 모델 정책을 공통 적용했다.
- 집중 시험 20개와 backend 전체 183개 시험 및 Ruff를 통과했으며 Ubuntu 서버의 Technical Scout 재검증은 남아 있다.

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
- 검증 evidence ID는 Scout allowlist에 전달하지만 뉴스·거래소 coverage가 없으므로 Bundle과 run은 `PARTIAL`, 주문은 0건으로 유지한다.
- 선택형 `deploy/compose.dart.yaml`과 secret 권한 준비 절차를 추가했다. 활성화 상태에서 secret이 유효하지 않으면 run admission을 409로 차단한다. 로컬 backend 전체 205개 테스트와 Ruff lint가 통과했으며 실제 OpenDART 키 호출은 Ubuntu 검증 항목이다.

## 2026-08-11 구조화 LLM 응답 이력

- Adapter가 추출한 구조화 JSON을 역할 계약 검증 전에 canonical form·SHA-256 hash·capture 시각으로 invocation에 저장한다. 성공뿐 아니라 reason code·evidence ref 등 서버 계약 실패 응답도 프롬프트 개선을 위해 보존한다.
- Provider raw body, prompt, tool payload와 credential은 저장하지 않는다. 64 KiB 초과 또는 민감 key 이름을 포함한 output은 보관하지 않고 전용 오류로 fail-closed 처리한다.
- run 목록에는 output을 포함하지 않고 소유권을 확인하는 개별 API와 Console의 지연 로딩 버튼으로만 조회한다.
- `20260811_0023` upgrade→downgrade→upgrade, backend 전체 207개 시험·Ruff, frontend TypeScript·11개 component 시험·production build가 통과했다. Ubuntu PostgreSQL 적용과 실제 외부 응답 조회는 남아 있다.

## 2026-08-11 Guard 사용자 기본 위험 설정

- 기존 `configuration_versions`에 독립 category `RISK_POLICY`를 사용해 안전 기본값 조회, DRAFT→VALIDATED→ACTIVE, 이전 ACTIVE→SUPERSEDED와 stale base 충돌 검사를 구현했다.
- 진입금액·1회/종목/전체 금액·최대 보유종목·일일진입·고정손절·시세 지연·spread·가격편차 범위와 금액 순서를 서버에서 검증한다. 활성 버전이 없으면 `entry_order_amount=null`로 BUY 차단을 유지한다.
- Guard SHADOW 평가와 execution에 사용한 risk version ID를 기록하고, Console에서 source·active version·미설정 차단을 표시하며 변경안 검증·확정을 수행한다.
- backend 전체 209개 테스트·Ruff, frontend TypeScript·12개 component 테스트·production build가 통과했다. 전체 계좌 노출 계산·영향 미리보기·종목별 override는 후속 구현이다.
