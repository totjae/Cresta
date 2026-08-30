# Cresta v2 Refactoring Plan

## 1. 목적

현재 Cresta의 시장 데이터 수집, Broker, 주문 상태 관리,
LLM Provider Runtime 등 재사용 가능한 구성요소는 유지하면서
진입 판단과 자동 실행 구조를 Cresta v2 아키텍처로 단계적으로 전환한다.

본 문서는 상세 제품 명세가 아니다.

상세 계약의 기준 문서는 다음과 같다.

- AI 판단: `AI_DECISION_SPEC.md`
- Agent 실행 DAG: `MULTI_AGENT_ORCHESTRATION_SPEC.md`
- 판단→실행 경계: `DECISION_EXECUTION_SPEC.md`
- 전체 시스템 구조: `SYSTEM_DESIGN.md`
- 데이터 영속화: `DATABASE_SPEC.md`

---

## 2. 현재 구조

Phase 0 기준 현재 신규 진입 판단 흐름:

Market Data
→ Analysis Scheduler
→ deterministic-mock-v2
→ Decision
→ route_trading_decision()
→ Guard
→ Approval / Automatic Order
→ Worker
→ Kiwoom

LLM ENTRY Agent는 별도의 diagnostic/shadow DAG로 실행되며
실제 BUY 결정에는 사용되지 않는다.

상세 내용은 `CRESTA_V2_PHASE_0_ANALYSIS.md`를 참조한다.

---

## 3. 목표 구조

Market Data / External Evidence
            ↓
       Scout Agents
            ↓
      DecisionContext
            ↓
    ┌───────┼───────┐
    ▼       ▼       ▼
Conservative Balanced Aggressive
 Decision     Decision   Decision
    └───────┬───────┘
            ▼
 Deterministic Arbiter
            ↓
      ArbiterResult
            ↓
	Decision Finalizer
			↓
 purpose=TRADING ENTRY Decision
            ↓
 Execution Orchestrator
            ↓
           Guard
            ↓
 Approval / Order
            ↓
          Broker

세 Decision Agent는 동일한 불변 DecisionContext를 입력받는다.
각 Decision Agent의 차이는 DecisionContext가 아니라
별도로 고정된 자신의 PolicyProfile이다.

---

## 4. 아키텍처 불변조건

1. Scheduler는 평가를 시작할 뿐 투자 판단을 하지 않는다.
2. Scout Agent는 시장을 분석할 뿐 BUY/SELL을 결정하지 않는다.
3. Decision Agent만 BUY / WAIT / REJECT / UNKNOWN 후보 판단을 수행한다.
4. Decision Agent는 주문을 생성하지 않는다.
5. 세 Decision Agent는 동일한 불변 DecisionContext를 입력받는다. 각 Agent는 동일 DecisionContext에 자신의 고정된 PolicyProfile을 결합하여 판단하며, PolicyProfile은 DecisionContext 자체에 포함되지 않는다.
6. Conservative / Balanced / Aggressive의 차이는 입력 데이터가 아니라 판단 정책이다.
7. Decision Agent는 다른 Decision Agent의 결과를 입력으로 받을 수 없다.
8. Arbiter는 LLM을 사용하지 않는다.
9. Arbiter는 결정론적 규칙으로 세 Decision Agent 결과를 종합한다.
10. Arbiter는 주문을 생성하지 않는다.
11. ExecutionPolicy만 현재 운영 단계에서 실행 가능 여부를 판정한다.
12. Guard는 거래 위험 조건을 검사한다.
13. Broker Adapter만 증권사 주문 API를 호출한다.
14. UNKNOWN / ERROR / INSUFFICIENT_DATA에서 신규 주문이 발생해서는 안 된다.
15. LLM 실패를 deterministic BUY로 fallback하지 않는다.
16. 모든 판단은 기존 AgentRun/Decision lineage 또는 Phase 2에서 확정하는 동등한 단일 추적 lineage로 재현 가능해야 한다. 별도 DecisionRun 도입을 전제로 하지 않는다.

---

## 5. 재사용 원칙

Phase 0에서 재사용 가능하다고 확인한 구성요소를 우선 유지한다.

- MarketSnapshot 수집
- minute bar / indicator 계산
- canonical decision input/hash
- Broker lease/fencing
- 주문 sender
- reconciliation
- account projection
- LLM Provider Adapter
- structured output validation
- Agent stage lease/claim
- fail-closed 처리

기존 구현의 존재만을 이유로 새 구조와 충돌하는 책임을 유지하지 않는다.

---

## 6. 제거 또는 역할 변경 대상

### deterministic-mock-v2

운영 ENTRY 판단 주체에서 제거한다.

테스트 fixture/provider로 유지할 수 있으나
실제 LLM 오류에 대한 BUY fallback으로 사용할 수 없다.

### ENTRY Core Agent

Cresta v2 ENTRY production 판단 경로에서는
기존 단일 Core 투자판단 역할을 제거한다.

ENTRY는 다음 구조로 대체한다.

- Conservative Decision Agent
- Balanced Decision Agent
- Aggressive Decision Agent
- Deterministic Arbiter
- server-owned Decision Finalizer

기존 agent-dag-v1~v6의 Core 실행 이력과
POSITION의 TRADING_ADVISORY / position-agent-fusion-v1에 사용되는
legacy Core 계약은 별도 POSITION migration 전까지 유지한다.

### Scheduler

Scheduler 내부의 BUY 판단 책임을 제거한다.

최종적으로 Scheduler는 평가 pipeline을 시작하는 역할만 담당한다.

---

## 7. 구현 단계

### Phase 0 — Existing Architecture Analysis
완료.

### Phase 1 — Decision Architecture Specification
완료. 공식 AI/Agent/System 명세를 Cresta v2 구조로 갱신했으며 코드는 변경하지 않았다.

### Phase 2 — Domain / Persistence Design

완료. Phase 2A에서 기존 AgentRun, AgentStageRun, Decision,
DecisionInputSnapshot, EvidenceBundle, AgentAssessment와 실행 lineage를
역설계했고 Phase 2B/2C에서 v7 ENTRY persistence mapping과 규범 계약을
확정했다. 코드와 migration은 아직 만들지 않았다.

확정 결과:

- 신규 DecisionRun·EvidenceSet 없이 기존 AgentRun을 v7 evaluation root로 확장
- AgentRun당 하나의 별도 immutable `decision_contexts` reference manifest
- DecisionAgentResult와 ArbiterResult는 기존 AgentStageRun output에 저장
- PolicyProfile 3종과 Activation Gate는 system-owned ConfigurationVersion 재사용
- 기존 Decision에 nullable source AgentRun·exact Arbiter stage/output hash lineage 추가
- 기존 `evaluation_request_id` unique를 finalization idempotency에 재사용
- Activation Gate와 ExecutionStage를 독립 유지

세부 물리 계약은 `docs/DATABASE_SPEC.md` DB-157~182가 단일 기준이다.

### Phase 3 — DecisionContext / Lineage Persistence

Phase 2에서 확정된 persistence 기반만 구현한다.

- AgentRun의 v7 purpose·PolicyProfile map·Activation Gate provenance nullable 확장
- `decision_contexts` migration, ORM과 immutable freeze persistence
- 기존·신규 role allowlist와 v7 DAG별 role constraint 기반
- system-owned ConfigurationVersion PolicyProfile mapping
- Decision의 nullable source AgentRun·ENTRY_ARBITER stage/output hash lineage
- PostgreSQL upgrade→downgrade→upgrade, SQLite schema compatibility와 legacy 보존 시험

이 Phase에서는 Decision Agent Provider runtime 전체, Agent별 투자 정책 실행,
deterministic Arbiter truth table 구현, Decision Finalizer 실행 서비스와 TRADING
activation을 구현하지 않는다. 해당 기능은 기존 Phase 7~10, 13~14의 범위를
유지한다.

### Phase 4 — Scout Runtime
최종 `agent-dag-v7`의 Intel → Verify → 네 Scout → Candidate Audit → DecisionContext Freeze upstream slice를 `DIAGNOSTIC`으로 연결한다. 기존 Intel/Evidence/Scout/Candidate Audit 비즈니스 로직과 Scout route를 가능한 한 재사용하고, `scout-input-v2`, v7 Verifier/Audit envelope, role input hash, atomic admission과 `RUNNING` checkpoint/reconciliation을 구현한다. C/B/A Decision Agent·Arbiter·Finalizer·production scheduler는 실행하지 않는다.

### Phase 5 — Technical Scout
Technical Scout를 새로 작성하는 단계가 아니다. Phase 4에서 연결한 v7 upstream을 기준으로 기존 Technical Scout의 role-specific input, AgentAssessmentV2, route provenance, input/output hash와 DecisionContext compatibility를 E2E acceptance/revalidation한다.

### Phase 6 — Scout Expansion
News·Disclosure, Market·Sector, Position Risk Scout를 새로 작성하는 단계가 아니다. 기존 로직을 재사용하면서 v7 role-specific input, Provider 호출 조건, 허용 evidence subset, ENTRY Position Risk의 explicit `NOT_APPLICABLE`과 Candidate Audit/DecisionContext compatibility를 역할별 재검증한다. 계약 공백이 확인된 경우에만 공통 runtime을 보완한다.

### Phase 7 — Decision Agent Runtime
Phase 7은 다음 고정 순서로 나눈다.

- **Phase 7A — Gap Analysis:** 기존 v7 persistence/upstream runtime과 Decision Agent 요구 계약의 차이를 조사한다. 구현은 하지 않는다.
- **Phase 7B — Contract Finalization:** `decision-agent-input-v1`, strict PolicyProfile, stage input/hash, model/result schema, route·prompt provenance, materialization/reconciliation, failure·transaction 계약과 계획 시험을 문서로 확정한다. 구현·migration·시험 코드는 작성하지 않는다.
- **Phase 7C — Runtime Foundation:** 세 role의 control-plane validation, 일곱 route admission, phase enablement registry, C/B/A atomic materialization, input resolver와 strict schema를 구현한다. Provider 호출과 결과 실행은 아직 활성화하지 않는다.
- **Phase 7D — Decision Agent Execution:** 명시적 worker dispatch, role별 Prompt/Route/Policy 실행, lock 없는 Provider 호출, completion revalidation, structured success/failure result를 구현한다. 세 Agent는 DIAGNOSTIC에서 병렬 실행하고 Arbiter·Finalizer·거래 resource는 생성하지 않는다.
- **Phase 7E — Acceptance / Revalidation:** C/B/A 독립성·병렬성, canonical hash, provenance, expiry/fencing, 모든 failure matrix, tool/거래 권한 0건과 legacy v1~v6 및 Phase 4~6 historical run 회귀를 E2E 검증한다.

### Phase 8 — ENTRY_ARBITER Runtime

과거 Phase 8 Conservative, Phase 9 Balanced/Aggressive, Phase 10 Arbiter sequencing은
Phase 7C~7E의 공통 3-role runtime 완료와 현행 Phase 8로 대체된 역사적 placeholder다.
현행 Phase 8은 다음 고정 순서로 진행한다.

- **Phase 8A — Gap Analysis:** C/B/A persistence/runtime과 Phase 1/2 Arbiter 계약의 차이를 조사하고 구현하지 않는다.
- **Phase 8B — Contract Finalization:** `entry-arbiter-input-v1`, `entry-consensus-v1`, pattern/reason, validity, structural failure, reconciliation, dependency와 provider-less claim/completion 계약을 AI-266~275, MAO-246~255, DB-197~204로 확정한다. 구현·migration·시험 코드는 작성하지 않는다.
- **Phase 8C — Runtime Implementation:** strict contracts, canonical hash, pure consensus evaluator, arbiter reconciliation과 provider-less worker execution을 구현한다. Finalizer·Activation·TRADING은 연결하지 않는다.
- **Phase 8D — Production Acceptance:** C/B/A→Arbiter E2E, truth table, order independence, corruption·expiry·fencing, provider/거래 권한 0건과 legacy 회귀를 검증한다.

### Phase 9 — Activation Gate / Decision Finalizer

- **Phase 9A — Gap Analysis:** 현행 Decision physical/API 계약, Activation control plane, Finalizer trigger·lineage·lifecycle과 Execution boundary의 구현 gap을 조사하고 파일을 변경하지 않는다.
- **Phase 9B — Contract Finalization:** `activation-gate-v1`, `entry-finalization-identity-v1`, `sourced-entry-decision-v1`, source/Gate write-boundary validation, audit, run lifecycle, API union, migration intent와 acceptance를 AI-276~286, MAO-256~262, DB-205~213, CFG-104~111, EXE-216~220으로 확정한다. 명세만 변경하고 production·ORM·migration·시험 코드는 작성하지 않는다.
- **Phase 9C — Activation/Admission Foundation:** additive Decision persistence migration, UNKNOWN과 sourced nullable/API schema foundation, Gate validator/control plane/selector/evidence validation, purpose-separated v7 TRADING admission과 admission-time Gate freeze를 구현한다. Decision Finalizer insert는 아직 하지 않는다.
- **Phase 9D — Finalizer Runtime:** server-owned Finalizer, authoritative source validator, live Gate revalidation, exact-once transaction, run lifecycle, AuditLog, reconciliation/recovery와 sourced-v7 Decision read path를 구현한다. Execution Orchestrator는 연결하지 않는다.
- **Phase 9E — Production Acceptance:** 네 action 보존, open/closed/supersession, DIAGNOSTIC 0/TRADING exact-one Decision, UNKNOWN persistence/API, idempotency/race/expiry/audit/lifecycle/crash recovery, no-LLM/no-Execution과 legacy/PostgreSQL 회귀를 검증한다.

### Phase 10 — Execution Authority

- **Phase 10A — Gap Analysis:** current router, stage/config, Guard, Approval, OrderIntent/Order, broker pre-send, fixed-stop과 recovery gap을 실제 코드 기준으로 조사하고 파일을 변경하지 않는다.
- **Phase 10B — Contract Finalization:** sourced Decision exact-one execution identity, versioned stage control-plane, frozen/current authority minimum, expiry/emergency, phase-aware Guard, Approval owner/CAS/reauth, typed Order provenance, unsent invalidation과 broker pre-send 계약을 EXE-221~260, GRD-098~106, DB-214~230, CFG-112~120으로 확정한다. SPEC-ONLY다.
- **Phase 10C.1 — Persistence / Control-plane Foundation:** additive migration, sourced execution discriminator/exact-one, strict stage selector, provenance/FK/CHECK와 unsent INVALIDATED foundation을 구현한다. sourced handoff는 하지 않는다.
- **Phase 10C.2 — Sourced Orchestrator Foundation:** full source validation, canonical lifecycle/recovery, WAIT/REJECT/UNKNOWN NO_ACTION, BUY expiry, stage/policy freeze, initial Guard와 SHADOW를 구현한다. Approval/automatic Order authority는 닫아 둔다.
- **Phase 10D — Guard / Approval Authority:** complete BUY Guard, APPROVAL_ONLY automatic fail-closed, atomic Approval creation/authorization, owner/version/reauth와 live authority invalidation을 구현한다.
- **Phase 10E — MOCK Automatic / Fixed Stop:** validated MOCK_AUTOMATIC, automatic BUY와 fixed-stop의 stage/action matrix를 구현하며 LIVE는 추가하지 않는다.
- **Phase 10F — Broker Pre-send / Recovery:** typed source dispatch, stage/mode/expiry/emergency/Guard/Approval pre-send recheck, unsent invalidation과 reconciliation/crash recovery를 구현한다.
- **Phase 10G — Production Acceptance / Scheduler Handoff:** full Decision→Kiwoom MOCK E2E, downgrade/duplicate/crash/PostgreSQL matrices를 닫은 뒤 sourced execution sweep과 scheduler migration을 활성화한다.

### Phase 11 — Execution Policy
Phase 10으로 세분화된 역사적 placeholder다. 현행 stage/action policy authority는 Phase 10B~10G를 따른다.

### Phase 12 — Guard / Readiness / Global Gate
Phase 10D~10F로 세분화된 역사적 placeholder다. Guard와 broker pre-send authority는 Phase 10 계약을 따른다.

### Phase 13 — ENTRY Decision Finalization / Execution Boundary
이 절의 Finalizer 부분은 현행 Phase 9C~9E로 세분화된 역사적 placeholder다. Execution
Orchestrator 인계는 Phase 9 범위 밖이며 후속 실행 단계에서만 연결한다.

검증된 ArbiterResult를 서버 소유 Decision Finalizer가
불변 purpose=TRADING ENTRY Decision으로 확정하는 경계를 구현한다.

Finalizer가 생성한 TRADING Decision만 기존 Execution Orchestrator에 전달한다.
SHADOW/DIAGNOSTIC ArbiterResult의 승격은 금지한다.

### Phase 14 — Scheduler Migration
기존 deterministic ENTRY 생성 경로를 제거하고 신규 pipeline을 연결한다.

### Phase 15 — E2E Acceptance
Watchlist
→ Market/Evidence
→ Scouts
→ DecisionContext
→ Decision Agents ×3
→ Arbiter
→ ArbiterResult
→ Decision Finalizer
→ purpose=TRADING ENTRY Decision
→ Execution Orchestrator
→ ExecutionStage
→ Guard
→ Approval / Order
→ Broker Worker
→ Kiwoom Mock
→ Fill/Reconciliation

---

## 8. 공통 작업 규칙

1. 해당 Phase 범위를 넘는 구현을 하지 않는다.
2. 관련 없는 파일을 리팩터링하지 않는다.
3. 명세를 먼저 갱신하고 구현한다.
4. API/DB 계약 변경 시 관련 명세도 함께 갱신한다.
5. 신규 domain type에 `any`를 사용하지 않는다.
6. 오류를 catch 후 무시하지 않는다.
7. LLM 오류 시 BUY fallback을 만들지 않는다.
8. 안전하지 않은 상태에서는 fail closed 한다.
9. 명세 기반 테스트 없이 구현 완료로 처리하지 않는다.
10. 다음 Phase는 명시적인 작업 지시 전 구현하지 않는다.

---

## 9. Phase 완료 보고 형식

각 Phase 완료 시 다음을 보고한다.

- 변경 파일
- 변경 내용
- 신규/변경 Domain Contract
- DB migration
- 실행한 테스트
- 테스트 결과
- 발견한 기존 문제
- 미검증 항목
- 남은 TODO

계획, 구현, 검증, 외부 환경 미검증 상태를 구별한다.
