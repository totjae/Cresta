# Cresta MVP 제품 및 시스템 설계

## 1. 목표와 경계

Cresta MVP는 사용자가 등록한 국내주식 최대 3종목을 감시하고, 설정 가능한 AI 판단 주기와 실시간 규칙 검사를 결합해 **분석 및 키움 REST API 모의투자 주문**을 제공한다. 주문 가능 행동별로 자동·사용자 승인·비활성 모드를 설정한다. 종목 추천, 해외주식, 다중 계좌와 실거래 주문은 첫 버전 범위에서 제외한다.

상세 제품·거래·주문 기준은 다음 문서를 따른다.

- [제품 요구사항](PRODUCT_REQUIREMENTS.md)
- [거래 세션 및 감시 운영 명세](TRADING_SESSION_SPEC.md)
- [주문 가격 및 미체결 재처리 명세](ORDER_EXECUTION_SPEC.md)
- [주문 상태 머신 및 키움 매핑 명세](ORDER_STATE_MACHINE_SPEC.md)
- [계좌·주문 재동기화 명세](RECONCILIATION_SPEC.md)
- [키움 Broker Adapter 명세](KIWOOM_BROKER_SPEC.md)
- [Guard 리스크 및 비상정지 명세](GUARD_RISK_SPEC.md)
- [사용자 설정 및 적용 명세](CONFIGURATION_SPEC.md)
- [Web UI 명세](WEB_UI_SPEC.md)
- [인증 및 보안 명세](SECURITY_SPEC.md)
- [시장데이터 및 Watch 명세](MARKET_DATA_SPEC.md)
- [Scout·Core AI 판단 계약](AI_DECISION_SPEC.md)
- [다중 에이전트 오케스트레이션 명세](MULTI_AGENT_ORCHESTRATION_SPEC.md)
- [LLM Provider 및 Gateway 명세](LLM_PROVIDER_GATEWAY_SPEC.md)
- [판단 실행 및 승인 오케스트레이션 명세](DECISION_EXECUTION_SPEC.md)
- [데이터베이스 및 영속성 명세](DATABASE_SPEC.md)
- [배포·운영·장애복구 명세](OPERATIONS_RUNBOOK.md)
- [HTTP 및 WebSocket API 명세](API_SPEC.md)

### 성공 기준

- 시세 지연과 연결 단절을 감지하고 신규 매수를 차단한다.
- 모든 AI 판단은 스키마 검증과 Guard 검사를 거친다.
- 동일한 의도에 대해 중복 주문이 생성되지 않는다.
- 재시작 후 포지션, 미체결 주문, 비상정지 상태를 복구한다.
- 판단 입력·출력, 규칙 결과, 사용자 승인, 주문 결과를 감사 가능하게 기록한다.

## 2. 논리 아키텍처

```text
Browser ─HTTPS─> Host Nginx (trade.mihoservice.xyz)
                       └─HTTP/loopback─> 127.0.0.1:7788 ─> Cresta gateway
                                                               ├─> Console (Next.js)
                                                               └─> FastAPI
                         └> API (FastAPI) ─> PostgreSQL/TimescaleDB
                                  │          Redis (cache/queue/lock)
Market stream ─> Watch ─> Event Bus ─> Technical Scout ───┐
External sources ─> Intel ─> Verify ─┬─> News Scout ──────┤
                                     └─> Market Scout ────┤
Position/risk snapshot ───────────────> Position Scout ────┤
                                                             v
                                        Agent Orchestrator ─> Core
                                                             │
Watch ─> Guard trigger (real-time) ──────────────────────────┤
                                                             v
                                              Execution Orchestrator
                                                ├─> Guard evaluation
                                                ├─> Approval
                                                └─> CREATED order ─> Broker
```

| 모듈 | 책임 | 주문 권한 |
|---|---|---|
| Watch | 시세 정규화, 지표 계산, 지연·급변 감지 | 없음 |
| Intel·Verify | 허용 소스 수집, 출처·시각·중복·상충 검증과 불변 증거 bundle 생성 | 없음 |
| 복수 Scout | 기술·뉴스/공시·시장/업종·포지션 위험 평가 | 없음 |
| Agent Orchestrator | 버전 고정 DAG, 병렬 stage, timeout·멱등성·실패 격리 | 없음 |
| LLM Provider Layer | 공식 API·Gateway·Ollama Adapter, 구조화 출력·route·비용·health 정규화 | 없음 |
| Core | 제한된 행동 코드와 근거 생성 | 없음 |
| Guard | 한도·손절·데이터·연결 상태의 결정론적 평가 | 통과/차단·중지 범위 |
| Execution Orchestrator | 거래 목적 판단·Guard trigger를 실행 권한에 따라 승인 또는 주문으로 변환 | 승인·주문 생성, Broker 호출 없음 |
| Broker | 키움 REST API 계좌 동기화, 주문·정정·취소, 체결 확인 | 검증된 명령만 |
| Console | 설정, 승인, 관찰, 비상정지 | 사용자 의도 생성 |

의사결정 우선순위는 `Guard > 사용자 수동 명령 > 실제 계좌/주문 상태 > Core > Scout`로 고정한다.

Core 판단과 Guard trigger의 후속 실행은 [판단 실행 및 승인 오케스트레이션 명세](DECISION_EXECUTION_SPEC.md)를 따른다. 진단 판단은 이 경로에 진입하지 않으며, 거래 목적 판단도 실행 권한과 Guard의 현재 상태를 다시 확인한 뒤에만 승인 또는 내부 `CREATED` 주문을 만든다.

Watch의 `watch-indicators-v2` 결과와 정규화 quote는 모델 호출 전에 `scout-input-v1` canonical JSON으로 고정한다. 이 입력은 SHA-256 해시와 시장·지표 snapshot 참조를 가진 불변 DB 행이며 사용자 소유권은 모델에 전달하지 않는 별도 metadata로 저장한다. 현재 결정론적 Mock v2와 향후 외부 Scout/Core provider는 같은 입력 경계를 사용한다.

다중 에이전트 확장은 자유 대화형 swarm이 아니라 versioned DAG다. 외부 문서는 Intel과 Verify를 거쳐 `EvidenceBundle`로 고정되고 Core에는 원시 웹 문서나 도구를 제공하지 않는다. Agent Orchestrator는 필수 Scout 종료 후 Core를 한 번만 호출하며 provider·model·prompt·schema·입력·출력과 실행 경로를 불변 run으로 기록한다. 신규 agent·provider·model은 SHADOW로 시작하고 Core 장애는 신규매수 fail-closed로 처리한다.

첫 Console 구현은 Next.js App Router와 TypeScript를 사용한다. 브라우저는 같은 origin의 `/api/v1`만 호출하고 세션 token은 `HttpOnly` cookie로, CSRF token은 React 메모리 상태로만 유지한다. 새로고침 시 `/api/v1/auth/session`으로 서버 세션을 다시 검증하고 새 CSRF token을 받는다. 미인증·만료 응답에서는 보호 화면 상태를 즉시 폐기하며 로그인 요청을 자동 재전송하지 않는다.

첫 화면 범위는 2단계 로그인, 로그아웃, 보호된 Console shell, MOCK 환경·API 상태와 아직 구현되지 않은 Broker·거래 기능의 명시적 비활성 상태다. 실제 계좌·시세·주문 데이터를 흉내 낸 값을 운영 화면에 표시하지 않는다.

## 3. 핵심 흐름

### 진입

1. 사용자가 전략과 투자 한도를 포함한 종목을 등록한다.
2. Watch가 실시간 데이터의 최신성과 완전성을 검증한다.
3. Scout와 Core가 `BUY | WAIT | REJECT | RISK_BLOCK` 중 하나를 출력한다.
4. Execution Orchestrator가 거래 목적 판단만 정규 행동으로 변환하고 활성 실행 권한과 기능 단계를 조회한다.
5. 비활성형은 기록만 남긴다. 승인형은 승인 생성 Guard를 통과한 뒤 유효시간과 가격 허용범위가 있는 요청을 만들고, 자동형은 주문 직전 Guard를 통과해야 한다.
6. 검증된 주문을 PostgreSQL에 `CREATED`로 저장한다. Active Broker worker는 `READY` 상태에서 `FOR UPDATE SKIP LOCKED`로 가장 오래된 주문 한 건을 선택한다.
7. worker는 현재 lease·fencing token·거래 gate를 재검증하고 `SUBMITTING`을 먼저 commit한 뒤 키움에 정확히 한 번 전송한다.
8. 응답이 불명확하면 `UNKNOWN`으로 영속화하고 다음 주문을 중지한 뒤 즉시 전체 계좌 재동기화를 수행한다.

### 보유 및 청산

- Watch와 Guard는 AI 주기와 무관하게 손절, 급락, 데이터 단절, 비상정지를 검사한다.
- Scout는 설정 주기로 위험도를 갱신하며 임계치 초과 시 Core 재판단을 요청한다.
- Core는 `HOLD | TIGHTEN_STOP | PARTIAL_SELL | FULL_SELL | EMERGENCY_EXIT`만 반환한다.
- Guard의 청산 trigger는 일반 승인 대기보다 우선해 오케스트레이터에 전달한다. 연결 이상처럼 안전한 주문 실행 자체가 불가능한 경우 포지션을 종료로 오인하지 않고 `EXIT_PENDING` 위험과 경보를 유지한다.

## 4. 상태 머신

종목·포지션의 상위 상태는 다음과 같다.

```text
SELECTED -> PRECHECK -> ENTRY_WATCH -> ENTRY_READY -> BUY_PENDING
  -> POSITION_OPEN -> EXIT_WATCH -> SELL_PENDING -> CLOSED
```

개별 주문의 접수·부분체결·취소·정정·불명확 상태는 [주문 상태 머신 명세](ORDER_STATE_MACHINE_SPEC.md)에서 별도로 관리한다.

예외 상태는 `RISK_HALTED`, `MANUAL_HALTED`, `DATA_ERROR`, `BROKER_ERROR`, `ORDER_ERROR`다. 상태 전이는 DB 트랜잭션과 종목별 Redis 잠금으로 직렬화하며, 이벤트에는 예상 이전 상태를 넣어 낙관적 동시성 검사를 수행한다.

## 5. 데이터 모델

아래는 논리 요약이며 실제 테이블·제약·트랜잭션은 [데이터베이스 및 영속성 명세](DATABASE_SPEC.md)를 따른다.

| 테이블 | 핵심 필드 |
|---|---|
| users | id, email, password_hash, mfa_enabled |
| instruments | symbol, name, market, tradable |
| strategies | symbol, entry_mode, limits, stops, intervals, execution_policy, overnight_policy, version |
| positions | symbol, Broker total/available/average, Cresta managed quantity/average, origin, state, version |
| decisions | purpose, model/version, input_snapshot_id, action, confidence, reasons, valid_until |
| decision_executions | decision/rule source, action, execution mode/policy version, state, version |
| guard_evaluations | phase, subject, result, rule results, input versions, valid_until |
| approvals | execution_id, decision_id, status, scope snapshot, expires_at, actor_id |
| orders | order_group_id, parent_order_id, client_order_id, idempotency_key, broker_order_id, side, quantities, status |
| fills | order_id, quantity, price, fee, filled_at |
| risk_events | rule_code, severity, input_snapshot, resolution |
| audit_logs | actor, action, target, before, after, correlation_id, created_at |

가격과 금액은 부동소수점이 아닌 `NUMERIC` 또는 정수 최소 화폐 단위로 저장한다. 이벤트·판단·주문은 `correlation_id`로 연결하고 원본 입력 스냅샷은 불변으로 보관한다.

## 6. AI 계약

구조화 입력·출력과 실패 처리는 [Scout·Core AI 판단 계약](AI_DECISION_SPEC.md)을 따른다.

- JSON Schema에 맞지 않거나 허용되지 않은 행동 코드는 `RISK_BLOCK` 처리한다.
- `confidence`는 주문 크기를 직접 결정하지 않으며 Guard 한도를 완화하지 않는다.
- 모델에는 API 키, 계좌번호, 개인정보를 전달하지 않는다.
- 프롬프트 버전, 모델 버전, 입력 시점, 시세 최신성, 출력 및 검증 실패를 기록한다.
- `valid_until`이 지났거나 입력 가격이 허용 편차를 벗어나면 재판단한다.
- Agent Runtime v5 이상은 포지션 파생값과 적용 Risk Policy provenance를 admission 시 서버에서 계산해 불변 입력으로 고정하며 stage 실행 시 재계산하지 않는다.
- 지수·업종·시장 breadth는 공식 또는 계약된 내부 Adapter의 `market-context-v1` snapshot만 사용한다. 유효한 source가 없으면 모델 추정을 허용하지 않고 결측으로 축소한다.

## 7. 장애 및 보안 설계

- 기본값은 fail-closed: 데이터 지연, 스키마 오류, 잔고 불일치 시 신규 매수를 막는다.
- 비상정지는 DB에 영속화하고 Redis에 캐시하며, 미체결 취소와 신규 주문 금지를 별도 단계로 기록한다.
- Broker 자격증명은 Secret Manager 또는 암호화된 환경 비밀로 주입하고 로그에서 마스킹한다.
- Web UI와 API는 사용자 ID·비밀번호·TOTP를 모두 검증한 서버 세션만 허용한다.
- HTTPS, secure/httpOnly/sameSite 쿠키, CSRF 방어, 로그인 rate limit과 계정 잠금을 적용한다.
- 주문 승인과 위험 설정 완화 등 고위험 행동은 TOTP 재인증을 요구한다.
- 주문 API는 idempotency key와 DB unique constraint를 함께 사용한다.
- 프로세스 재시작 시 broker를 진실 공급원으로 계좌·미체결을 먼저 reconciliation한 뒤 거래를 재개한다.
- 재동기화 중에는 신규 주문을 차단하며, 외부 주문·포지션은 전략에 자동 편입하지 않고 종목 단위로 격리한다.

## 8. 배포 단위와 관측성

AI 정기 판단은 키움 주문 worker와 분리된 `scheduler` 장기 실행 서비스가 담당한다. scheduler 장애는 신규 판단만 중단하며 API·Console·Broker worker의 상태와 주문 복구 경로에는 영향을 주지 않는다.

다중 에이전트 DAG 실행은 별도 `agent` 장기 실행 서비스가 담당한다. API와 scheduler는 불변 route·generation parameter snapshot과 PENDING stage만 등록하고, agent worker가 DB claim·lease·fencing을 통해 실행한다. Agent worker 장애 시 Broker와 Guard는 계속 작동하며 invocation 결과가 불명확한 stage는 자동 재전송하지 않는다.

초기에는 `console`, `api`, `worker`, `postgres`, `redis`, `nginx`의 Docker Compose 구성을 사용한다. Compose의 gateway는 `127.0.0.1:7788`에만 게시하고 호스트 Nginx가 `trade.mihoservice.xyz`의 TLS를 종료한다. gateway는 Docker embedded DNS로 API·Frontend 서비스명을 주기적으로 다시 해석해 컨테이너 재생성 후 이전 IP를 유지하지 않는다. 모든 장기 실행 컨테이너는 Docker daemon 재시작 후 복구되는 `unless-stopped` 정책을 사용하고, 호스트의 `cresta-boot.service`가 부팅 때 Compose 전체 구성을 한 번 조정한 뒤 user-facing health를 기다린다. 이 oneshot은 상시 프로세스 관리자가 아니며 런타임 재시작은 Docker가 담당한다. Watch/Broker worker는 API 프로세스와 분리하고 외부 키움 장애가 Console health를 차단하지 않도록 자체 재연결한다. 메트릭은 시세 지연, 이벤트 큐 지연, 판단 시간, 주문 성공률, reconciliation 불일치, 활성 비상정지를 포함한다. 구조화 로그에는 비밀정보 없이 `correlation_id`, symbol, module, `event_type`을 담는다.

초기 구현 저장소 구조는 다음으로 고정한다.

```text
backend/                 FastAPI 애플리케이션·CLI·테스트
  app/
    api/                 HTTP endpoint
    auth/                비밀번호·TOTP·세션
    models/              SQLAlchemy 영속 모델
    services/            도메인 서비스
  migrations/            Alembic migration
deploy/                  Docker Compose·Nginx·환경 예시
frontend/                Next.js Console·인증 UI
docs/                    기준 명세
```

Backend 기준은 Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL과 Redis다. 서버가 Intel N100·메모리 16GiB이므로 로컬 LLM은 배포하지 않고 worker 동시성을 제한한다. 상세 자원·디스크 예산은 [배포·운영·장애복구 명세](OPERATIONS_RUNBOOK.md)를 따른다.

## 9. 단계별 구현

1. **분석 전용:** 전략 CRUD, 가짜/지연 시세, 지표, 판단 감사 로그.
2. **자체 모의매매:** 수수료·슬리피지·부분체결을 포함한 paper broker.
3. **키움 모의투자:** 키움 REST/WebSocket adapter contract, 주문·정정·취소, 재시도/조회, reconciliation. 첫 제품 버전의 완료 범위다.
4. **승인형 실거래:** 소액 한도, 강제손절 외 승인, 운영 알림.
5. **제한 자동매매:** 성능·장애·감사 기준 충족 후 기능 플래그로 개방.

각 단계는 이전 단계의 회귀 테스트, 장애 주입, 손익 계산 대조와 수동 복구 훈련을 통과한 뒤 진행한다.
