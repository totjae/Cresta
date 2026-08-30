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
- [AI 분석 및 의사결정 계약](AI_DECISION_SPEC.md)
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
Market stream ─> Watch ─────────────────> Technical Scout ───┐
External ─> Intel ─> Verify ─────────────> News Scout ────────┤
                                            Market Scout ─────┤
Position/risk snapshot ─────────────────> Position Scout(*) ──┤
                                                               v
                                                       DecisionContext
                                                               │
                         ┌─────────────────────┼─────────────────────┐
                         v                     v                     v
                  Conservative             Balanced             Aggressive
                         └─────────────────────┼─────────────────────┘
                                               v
                                    Deterministic Arbiter
                                               v
                                         ArbiterResult
                                               v
                                      Decision Finalizer
                                               v
                              purpose=TRADING ENTRY Decision
                                               v
                                  Sourced Handoff Worker ──────┐
Watch ─> Guard trigger (real-time) ────────────────────────────┤
                                                               v
                                                Execution Orchestrator
                                                               v
                                                        ExecutionStage
                                                               v
                                                       Guard evaluation
                                                               v
                                                        Approval / Order
                                                               v
                                                        Broker Worker
                                                               v
                                                            Kiwoom
```

| 모듈 | 책임 | 주문 권한 |
|---|---|---|
| Watch | 시세 정규화, 지표 계산, 지연·급변 감지 | 없음 |
| Intel·Verify | 허용 소스 수집, 출처·시각·중복·상충 검증과 불변 증거 bundle 생성 | 없음 |
| 복수 Scout | 기술·뉴스/공시·시장/업종·포지션 위험 평가 | 없음 |
| Agent Orchestrator | 버전 고정 DAG, 병렬 stage, timeout·멱등성·실패 격리 | 없음 |
| LLM Provider Layer | 공식 API·Gateway·Ollama Adapter, 구조화 출력·route·비용·health 정규화 | 없음 |
| Decision Context Builder | 단일 ENTRY run의 불변 입력 조합과 hash·provenance 고정 | 없음 |
| Decision Agent ×3 | 동일 입력에 대해 성향별 ENTRY 판단 후보 생성 | 없음 |
| Deterministic Arbiter | versioned consensus policy로 세 판단 종합 | 없음 |
| Guard | 한도·손절·데이터·연결 상태의 결정론적 평가 | 통과/차단·중지 범위 |
| Execution Orchestrator | 거래 목적 판단·Guard trigger를 실행 권한에 따라 승인 또는 주문으로 변환 | 승인·주문 생성, Broker 호출 없음 |
| Broker | 키움 REST API 계좌 동기화, 주문·정정·취소, 체결 확인 | 검증된 명령만 |
| Console | 설정, 승인, 관찰, 비상정지 | 사용자 의도 생성 |
| Decision Finalizer | 검증된 ArbiterResult를 불변 purpose=TRADING ENTRY Decision으로 확정하고 lineage/멱등성을 보장 | 없음
| Sourced Handoff Worker | committed sourced ENTRY Decision을 bounded deterministic sweep로 기존 Execution Orchestrator에 인계; 정책 재분석·Broker 호출 없음 | 없음 |

의사결정 우선순위는 `Guard > 사용자 수동 명령 > 실제 계좌/주문 상태 > Final TRADING Decision > Decision Agent Result > Scout Assessment`로 고정한다.

검증된 `purpose=TRADING` Decision과 Guard trigger의 후속 실행은 [판단 실행 및 승인 오케스트레이션 명세](DECISION_EXECUTION_SPEC.md)를 따른다. 실행 계층은 판단 생성자가 Core, Arbiter, deterministic POSITION 또는 position fusion인지 직접 의존하지 않는다. 진단 판단은 이 경로에 진입하지 않으며, 거래 목적 판단도 실행 권한과 Guard의 현재 상태를 다시 확인한 뒤에만 승인 또는 내부 `CREATED` 주문을 만든다.

Watch의 `watch-indicators-v2` 결과와 정규화 quote는 모델 호출 전에 `scout-input-v1` canonical JSON으로 고정한다. 이 입력은 SHA-256 해시와 시장·지표 snapshot 참조를 가진 불변 DB 행이며 사용자 소유권은 모델에 전달하지 않는 별도 metadata로 저장한다.
기존 deterministic-mock-v2와 agent-dag-v1~v6의 Scout/Core는
당시 불변 입력 계약을 유지한다.

신규 ENTRY v7은 기존 market/scout input과 EvidenceBundle을
DecisionContext에 참조하고 세 Decision Agent에 동일한
DecisionContext를 제공한다.

Phase 4는 이 최종 v7 DAG의 upstream slice만 `DIAGNOSTIC`으로 검증한다. 서버는 `scout-input-v2`와 네 기존 Scout route provenance를 한 admission에 고정하고 Intel → Verify → 네 Scout → Candidate Audit을 실행한다. Candidate Audit commit 후 별도 reconciliation transaction이 DecisionContext를 freeze하며, 성공한 run은 Context를 checkpoint 증거로 가진 채 `RUNNING`을 유지한다. 이 slice에는 `CORE`, C/B/A Decision Agent, `ENTRY_ARBITER`, Finalizer와 production scheduler 연결이 없고 새 최종 DAG version을 만들지 않는다.

다중 에이전트 확장은 자유 대화형 swarm이 아니라 versioned DAG다. 외부 문서는 Intel과 Verify를 거쳐 `EvidenceBundle`로 고정되고 Decision Agent에는 원시 웹 문서나 임의 도구를 제공하지 않는다. Agent Orchestrator는 필수 Scout와 Candidate Audit 종료 후 `DecisionContext`를 한 번 고정하고 세 Decision Agent를 독립 실행한 뒤 Arbiter를 한 번 실행한다. provider·model·prompt·schema·입력·출력과 실행 경로는 불변 run으로 기록한다. 신규 agent·provider·model은 SHADOW로 시작하고 필수 Decision Agent 장애는 신규매수 fail-closed로 처리한다.

### Cresta v2 ENTRY 전환 범위

Cresta v2 1차 의사결정 변경은 신규 `ENTRY` 판단에 적용한다. 기존 POSITION 판단의 `deterministic-position-v1 → optional TRADING_ADVISORY → position-agent-fusion-v1 → TRADING Decision` 흐름은 별도 POSITION migration 단계가 정의되기 전까지 유지한다. 기존 `agent-dag-v1`~`agent-dag-v6`와 Core 실행 이력도 소급 변경하지 않는다.

첫 Console 구현은 Next.js App Router와 TypeScript를 사용한다. 브라우저는 같은 origin의 `/api/v1`만 호출하고 세션 token은 `HttpOnly` cookie로, CSRF token은 React 메모리 상태로만 유지한다. 새로고침 시 `/api/v1/auth/session`으로 서버 세션을 다시 검증하고 새 CSRF token을 받는다. 미인증·만료 응답에서는 보호 화면 상태를 즉시 폐기하며 로그인 요청을 자동 재전송하지 않는다.

첫 화면 범위는 2단계 로그인, 로그아웃, 보호된 Console shell, MOCK 환경·API 상태와 아직 구현되지 않은 Broker·거래 기능의 명시적 비활성 상태다. 실제 계좌·시세·주문 데이터를 흉내 낸 값을 운영 화면에 표시하지 않는다.

## 3. 핵심 흐름

### 진입

1. 사용자가 전략과 투자 한도를 포함한 종목을 등록한다.
2. Watch가 실시간 데이터의 최신성과 완전성을 검증한다.
3. Scout 결과로 불변 `DecisionContext`를 고정하고 세 Decision Agent가 `BUY | WAIT | REJECT | UNKNOWN` 후보를 독립 생성한다.
4. Deterministic Arbiter가 consensus-policy-v1로 후보를 종합해 ArbiterResult를 생성한다.
5. TRADING activation gate가 열린 경우에만 서버 소유 Decision Finalizer가 검증된 ArbiterResult를 purpose=TRADING ENTRY Decision으로 확정한다.
6. SHADOW/DIAGNOSTIC에서는 ArbiterResult에서 종료하며 TRADING Decision을 만들지 않는다.
7. Finalizer transaction 밖의 별도 `sourced-handoff` worker가 명시적으로 활성화된 경우에만 committed TRADING Decision을 기존 reconciliation helper로 인계한다. Finalizer에는 synchronous execution callback이 없다. sourced Decision은 full persisted lineage를 검증하고 Decision당 `sourced-entry-execution-v1` lifecycle을 정확히 하나 만든다. WAIT·REJECT·UNKNOWN은 persistent NO_ACTION으로 끝난다.
8. current stage는 `V7_ENTRY_EXECUTION_STAGE` ConfigurationVersion, frozen lifecycle은 DecisionExecution에 저장한다. stage와 action mode는 frozen/current 중 더 제한적인 권한만 적용하며 설정 완화는 기존 execution을 자동 승격하지 않는다.
9. SHADOW는 Guard 기록만, APPROVAL_ONLY는 MANUAL_APPROVAL+Guard PASS의 Approval만, MOCK_AUTOMATIC은 effective AUTOMATIC+Guard PASS의 MOCK Order만 허용한다. Decision 만료와 PAUSE_ENTRY는 BUY의 외부 제출 직전까지 live authority다.
10. 검증된 source/Guard/Approval/stage/policy provenance를 가진 OrderIntent와 주문을 PostgreSQL에 `CREATED`로 저장한다. Active Broker worker는 `READY` 상태에서 `FOR UPDATE SKIP LOCKED`로 가장 오래된 주문 한 건을 선택한다.
11. worker는 Order→Intent→source chain, current stage/action authority, Decision validity, Guard/Approval, PAUSE_ENTRY, MOCK target과 lease·fencing token을 다시 검증한다. 실패한 unsent 주문은 `INVALIDATED`로 끝내고, 성공한 경우에만 `SUBMITTING`을 먼저 commit한 뒤 키움에 한 번 전송한다.

Phase 4의 중간 구현에서는 3번의 DecisionContext Freeze까지만 실행한다. Context Freeze는 stage가 아니라 Candidate Audit commit 뒤의 별도 server-owned reconciliation transaction이고, 이후 단계는 materialize하거나 실행하지 않는다. 이 중간 checkpoint를 3~6번을 완료한 최종 v7 run으로 해석하지 않는다.
12. 응답이 불명확하면 `UNKNOWN`으로 영속화하고 다음 주문을 중지한 뒤 즉시 전체 계좌 재동기화를 수행한다.

### 보유 및 청산

- Watch와 Guard는 AI 주기와 무관하게 손절, 급락, 데이터 단절, 비상정지를 검사한다.
- Scout는 설정 주기로 위험도를 갱신하며 임계치 초과 시 Core 재판단을 요청한다.
- Core는 `HOLD | TIGHTEN_STOP | PARTIAL_SELL | FULL_SELL | EMERGENCY_EXIT`만 반환한다.
- Guard의 청산 trigger는 일반 판단보다 높은 우선순위로 오케스트레이터에 전달하지만 ExecutionStage를 우회하지 않는다. `APPROVAL_ONLY`에서는 허용된 행동 mode에 따라 고우선 Approval 또는 위험 경보를 만들고, 승인 없는 자동 청산은 `MOCK_AUTOMATIC`에서만 허용한다. 연결 이상처럼 안전한 주문 실행 자체가 불가능한 경우 포지션을 종료로 오인하지 않고 `EXIT_PENDING` 위험과 경보를 유지한다.

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
| decisions | purpose/schema, input snapshot, action/reasons/validity, nullable legacy model fields, optional v7 source run/Arbiter lineage |
| decision_executions | sourced/legacy contract, decision/rule source, canonical key, frozen stage/action/policy provenance, state, version |
| guard_evaluations | PRE_ORDER/APPROVAL_REVALIDATION/BROKER_SEND, typed DecisionExecution/StopTrigger subject, result, input versions, valid_until |
| approvals | execution_id, decision_id, status, scope snapshot, expires_at, actor_id |
| order_intents | typed authority source, execution/trigger/Guard/Approval, stage/policy provenance, authority key |
| orders | intent authority chain, order_group_id, client/idempotency/broker IDs, side, quantities, status including unsent INVALIDATED |
| fills | order_id, quantity, price, fee, filled_at |
| risk_events | rule_code, severity, input_snapshot, resolution |
| audit_logs | actor, action, target, before, after, correlation_id, created_at |
| agent_runs | purpose, DAG/version maps, evaluation input hash, state, v7 PolicyProfile map, activation snapshot, valid_until |
| decision_contexts | unique run, immutable input/evidence/Scout reference manifest and hash, frozen_at, valid_until |
| agent_stage_runs | role, input/output hash, route/invocation, Decision Agent result 또는 internal ArbiterResult |
| configuration_versions | 실행·위험 설정과 분리된 v7 PolicyProfile 3종 및 Activation Gate manifest |
| account_funds_snapshots | kt00001 계좌 단위 자금, D+1/D+2, server received_at append-only evidence |
| order_capacity_snapshots | kt00010 exact account/symbol/side/price/request 조건별 capacity append-only evidence |

가격과 금액은 부동소수점이 아닌 `NUMERIC` 또는 정수 최소 화폐 단위로 저장한다. 실행·주문 이벤트는 기존 `correlation_id`로 연결하고 원본 입력 스냅샷은 불변으로 보관한다. v7 evaluation은 별도 상위 correlation ID나 DecisionRun을 만들지 않고 `AgentRun.id`를 lineage root로 사용하며, finalized Decision의 nullable source run·exact Arbiter stage/output hash가 `Decision → AgentRun → DecisionContext → stage/evidence` 역추적을 연결한다.

Broker 금융 권위는 주문·체결·포지션 projection과 분리한다. `kt00001`은 account-level generic 자금 증거이고 `kt00010`은 exact request-bound capacity이므로 한 current scalar로 합치지 않는다. 조회 network call 뒤 짧은 transaction으로 immutable snapshot을 append하고 selector가 exact identity의 최신 `received_at`을 반환한다.

v7 `DecisionContext`는 AgentRun과 1:1인 별도 불변 reference manifest이고 raw 시장·evidence·Scout 결과를 복제하지 않는다. 세 Decision Agent 결과와 Provider 없는 `ENTRY_ARBITER` 결과는 각각 기존 AgentStageRun의 versioned structured output으로 저장한다. Agent별 PolicyProfile은 DecisionContext에 섞지 않고 system-owned ConfigurationVersion 세 category로 관리해 run admission version map에 고정한다. Activation Gate도 별도 system-owned ConfigurationVersion manifest지만 Finalizer admission만 제어하며 ExecutionStage와 결합하지 않는다. 물리 필드, FK, canonical hash, freeze와 보존 계약은 [데이터베이스 및 영속성 명세](DATABASE_SPEC.md)의 DB-157~213을 따른다.

Phase 7 Decision Agent runtime은 committed DecisionContext 이후 별도 reconciliation으로 C/B/A stage를 원자적·멱등적으로 materialize한다. 세 stage는 Candidate Audit만 직접 dependency로 가지며 Context gate를 공통 전제조건으로 병렬 실행한다. worker는 짧은 claim transaction에서 fencing을 확정하고 lock 없는 Provider 호출 뒤 별도 completion transaction에서 Context·Policy·Route·Prompt·input hash와 expiry를 다시 검증한다. 성공과 모든 권위 terminal failure는 동일한 server-owned structured result/hash로 남긴다.

신규 Phase 7 v7 admission은 네 Scout와 C/B/A의 일곱 LLM route provenance를 고정하지만, Phase 4~6의 기존 네-route run은 당시 snapshot을 유지한다. logical 11-role registry와 phase별 enabled materialization set은 분리하고, Phase 7에서는 Arbiter를 생성·실행하지 않는다. Decision Agent는 frozen input evaluator로만 동작하며 외부 검색·Broker·거래 실행 tool과 권한을 갖지 않는다.

Phase 8 ENTRY_ARBITER는 C/B/A terminal structured result commit 뒤 별도 reconciliation로
materialize하고 기존 worker claim/lease/fencing과 전용 provider-less handler로 실행한다.
세 dependency는 operational success가 아니라 authoritative terminal structured result를
요구하므로 정상 non-success Result도 `MANDATORY_UNKNOWN` consensus 입력이다. 반면
구조 오류·cross-run/context·만료는 consensus로 축소하지 않는다. exact input/result,
pattern/reason, validity와 persistence는 AI-266~275, MAO-246~255, DB-197~204를 단일
기준으로 따른다. DIAGNOSTIC은 ArbiterResult에서 종료하고 Finalizer·Activation·Decision·
Approval·Order·Broker를 호출하지 않는다.

Phase 9 production path는 scheduler가 valid ACTIVE+OPEN `activation-gate-v1`을 선택한 경우에만
처음부터 별도 TRADING AgentRun으로 admission한다. Gate ID/hash는 frozen provenance지만
Gate는 live safety control이므로 Finalizer write boundary에서 현재 ACTIVE identity/hash,
OPEN state, target와 evidence를 다시 검증한다. supersession·closure·invalidity는 Decision을
다른 action으로 바꾸지 않고 0건으로 끝낸다.

Arbiter commit 뒤 별도 finalization reconciliation이 server-owned Finalizer를 호출한다.
Finalizer는 source lineage, canonical hash, expiry와 Gate만 검증하고 모든 Arbiter action을
`sourced-entry-decision-v1`에 그대로 보존한다. confidence·aggregate risk·대표 model/prompt와
legacy Scout/Core를 합성하지 않는다. Decision insert, success audit와 run terminal transition은
한 transaction이고, retryable DB failure는 RUNNING을 유지해 idle/crash recovery가 같은
helper로 재시도한다. Finalizer 성공 뒤에도 ExecutionStage·Approval·Order·Broker는 0건이며
후속 Execution Orchestrator만 별도 권한·Guard를 평가한다.

## 6. AI 계약

구조화 입력·출력과 실패 처리는 [AI 분석 및 의사결정 계약](AI_DECISION_SPEC.md)을 따른다.

- 기존 v1~v6 Core 출력의 schema/행동 오류는 기존 계약에 따라
  RISK_BLOCK 또는 WAIT/UNKNOWN으로 축소한다.
- v7 Decision Agent의 schema·action·reason·evidence 계약 오류는
  INVALID_OUTPUT으로 기록하며 BUY 후보로 사용할 수 없다.
- v7 Arbiter는 필수 결과 오류에서 BUY를 생성하지 않는다.
- `confidence`는 주문 크기를 직접 결정하지 않으며 Guard 한도를 완화하지 않는다.
- 모델에는 API 키, 계좌번호, 개인정보를 전달하지 않는다.
- 프롬프트 버전, 모델 버전, 입력 시점, 시세 최신성, 출력 및 검증 실패를 기록한다.
- `valid_until`이 지났거나 입력 가격이 허용 편차를 벗어나면 재판단한다.
- Agent Runtime v5 이상은 포지션 파생값과 적용 Risk Policy provenance를 admission 시 서버에서 계산해 불변 입력으로 고정하며 stage 실행 시 재계산하지 않는다.
- POSITION scheduler는 결정론적 기준 판단을 독립 실행한 뒤 같은 snapshot·position hash에 결합된 `TRADING_ADVISORY`를 선택적으로 등록한다. 외부 Core는 계속 `WAIT + shadow_assessment`만 출력하며 서버 소유 `position-agent-fusion-v1`이 위험 상향만 별도 TRADING 판단으로 변환하고 기존 Guard에 인계한다.
- 지수·업종·시장 breadth는 공식 또는 계약된 내부 Adapter의 `market-context-v1` snapshot만 사용한다. 유효한 source가 없으면 모델 추정을 허용하지 않고 결측으로 축소한다.
- 신규 ENTRY는 동일 `DecisionContext`를 사용하는 세 Decision Agent와 LLM을 호출하지 않는 Arbiter를 거친다. 필수 Agent 실패를 `deterministic-mock-v2` BUY로 대체하지 않는다.

## 7. 장애 및 보안 설계

- 기본값은 fail-closed: 데이터 지연, 스키마 오류, 잔고 불일치 시 신규 매수를 막는다.
- 비상정지는 DB에 영속화하고 Redis에 캐시하며, 미체결 취소와 신규 주문 금지를 별도 단계로 기록한다.
- Broker 자격증명은 Secret Manager 또는 암호화된 환경 비밀로 주입하고 로그에서 마스킹한다.
- Web UI와 API는 사용자 ID·비밀번호·TOTP를 모두 검증한 서버 세션만 허용한다.
- HTTPS, secure/httpOnly/sameSite 쿠키, CSRF 방어, 로그인 rate limit과 계정 잠금을 적용한다.
- 주문 승인과 위험 설정 완화 등 고위험 행동은 TOTP 재인증을 요구한다.
- 주문 API는 idempotency key와 DB unique constraint를 함께 사용한다.
- Approval approve/reject는 owner와 expected version을 확인하고 approve는 Approval/version에 결합된 one-time `APPROVE_ORDER` proof를 같은 transaction에서 소비한다.
- Broker pre-send에서 source가 없거나 corrupt한 CREATED 주문, stage/mode downgrade, expired Decision과 BUY PAUSE_ENTRY를 fail-closed하며 source를 추측하지 않는다.
- 프로세스 재시작 시 broker를 진실 공급원으로 계좌·미체결을 먼저 reconciliation한 뒤 거래를 재개한다.
- 재동기화 중에는 신규 주문을 차단하며, 외부 주문·포지션은 전략에 자동 편입하지 않고 종목 단위로 격리한다.

## 8. 배포 단위와 관측성

AI 정기 평가 admission은 키움 주문 worker와 분리된 `scheduler` 장기 실행 서비스가 담당한다. 신규 v7 ENTRY에서 scheduler는 versioned pipeline을 시작하고 결과를 기록·인계할 뿐 BUY 판단 규칙, Decision Agent 정책, consensus policy 또는 Finalizer 정책을 소유하지 않는다. scheduler 장애는 신규 판단만 중단하며 API·Console·Broker worker의 상태와 주문 복구 경로에는 영향을 주지 않는다. 기존 POSITION scheduler 계약은 변경하지 않는다.

다중 에이전트 DAG 실행은 별도 `agent` 장기 실행 서비스가 담당한다. API와 scheduler는 불변 route·generation parameter snapshot과 PENDING stage만 등록하고, agent worker가 DB claim·lease·fencing을 통해 실행한다. Agent worker 장애 시 Broker와 Guard는 계속 작동하며 invocation 결과가 불명확한 stage는 자동 재전송하지 않는다.

Finalized sourced ENTRY의 production 인계는 별도 `sourced-handoff` 장기 실행 서비스가 담당한다. 기본 OFF인 `CRESTA_V7_SOURCED_HANDOFF_ENABLED`가 true일 때만 기존 `reconcile_sourced_entry_executions()`를 bounded oldest-first sweep하며, transaction exact-one authority는 PostgreSQL unique/identity 계약에 둔다. 이 worker는 Finalizer transaction, Decision 내용, Stage/Gate/Policy seed와 Broker network를 소유하지 않는다. signal shutdown은 새 sweep을 막고 진행 중 DB 호출의 경계를 존중해 종료한다.

배포 startup에서는 별도 one-shot `migration` service 하나만 Alembic head를 적용한다. PostgreSQL health 이후 migration이 성공 종료해야 API·scheduler·agent·broker·sourced-handoff가 시작되며, API `/readyz`는 PostgreSQL 연결과 exact migration head를 read-only로 확인한다. migration 실패는 runtime을 차단하고 자동 downgrade하지 않는다. Redis는 cache/queue 배포 dependency지만 주문·실행 authority는 계속 PostgreSQL에만 둔다.

초기에는 `migration`, `frontend`, `api`, `worker`, `scheduler`, `agent`, `sourced-handoff`, `postgres`, `redis`, `nginx`의 Docker Compose 구성을 사용한다. Compose의 gateway는 `127.0.0.1:7788`에만 게시하고 호스트 Nginx가 `trade.mihoservice.xyz`의 TLS를 종료한다. gateway는 Docker embedded DNS로 API·Frontend 서비스명을 주기적으로 다시 해석해 컨테이너 재생성 후 이전 IP를 유지하지 않는다. 모든 장기 실행 컨테이너는 Docker daemon 재시작 후 복구되는 `unless-stopped` 정책을 사용하고, 호스트의 `cresta-boot.service`가 부팅 때 Compose 전체 구성을 한 번 조정한 뒤 user-facing health를 기다린다. `migration`은 restart하지 않는 one-shot이며 이 oneshot은 상시 프로세스 관리자가 아니다. 런타임 재시작은 Docker가 담당한다. Watch/Broker worker는 API 프로세스와 분리하고 외부 키움 장애가 Console health를 차단하지 않도록 자체 재연결한다. 메트릭은 시세 지연, 이벤트 큐 지연, 판단 시간, 주문 성공률, reconciliation 불일치, 활성 비상정지를 포함한다. 구조화 로그에는 비밀정보 없이 `correlation_id`, symbol, module, `event_type`을 담는다.

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
