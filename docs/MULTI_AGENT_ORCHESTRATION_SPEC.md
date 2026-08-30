# Cresta 다중 에이전트 오케스트레이션 명세

## 1. 목적

Cresta의 정보 수집, 증거 검증, 기술·뉴스·시장·포지션 평가와 최종 행동 판단을 역할별 에이전트로 분리하고, 각 단계가 불변 입력과 구조화 출력으로만 연결되도록 정의한다. 이 문서는 자유 대화형 에이전트 군집이 아니라 재현·감사·실패 격리가 가능한 방향성 비순환 그래프(DAG)를 구현 기준으로 삼는다.

## 2. 적용 범위

- 거래 판단에 사용할 외부 정보의 수집·정규화·검증
- `Technical`, `News/Disclosure`, `Market/Sector`, `Position Risk` Scout
- 기존 v1~v6에서 Scout 결과를 종합하는 Core
- Cresta v2 ENTRY v7의 DecisionContext, 세 Decision Agent와 deterministic Arbiter
- 실행 순서, 병렬 실행, 멱등성, timeout과 실패 처리
- 에이전트 실행·증거·출력의 영속화와 감사
- SHADOW 평가와 이후 승인·자동 실행 단계의 경계

다음은 이 문서의 범위 밖이다.

- 키움 주문·체결 전송: [주문 실행 명세](ORDER_EXECUTION_SPEC.md)
- 최종 리스크 허용·차단: [Guard 리스크 명세](GUARD_RISK_SPEC.md)
- 모델 API별 인증·라우팅: [LLM Provider 및 Gateway 명세](LLM_PROVIDER_GATEWAY_SPEC.md)

## 3. 용어와 역할

| 역할 코드 | 이름 | 입력 | 출력 | 주문 권한 |
| --- | --- | --- | --- | --- |
| `INTEL_COLLECTOR` | Cresta Intel | 소스 조회 작업 | `EvidenceItem[]` | 없음 |
| `EVIDENCE_VERIFIER` | Cresta Verify | 수집 증거 | `EvidenceBundle` | 없음 |
| `EVIDENCE_CANDIDATE_AUDITOR` | Cresta Evidence Audit | Provider 출처 후보 | 후보 감사 보고서 | 없음 |
| `TECHNICAL_SCOUT` | 기술 Scout | `scout-input-v1` | `AgentAssessment` | 없음 |
| `NEWS_DISCLOSURE_SCOUT` | 뉴스·공시 Scout | 검증된 증거 묶음 | `AgentAssessment` | 없음 |
| `MARKET_SECTOR_SCOUT` | 시장·업종 Scout | 시장 snapshot·검증 증거 | `AgentAssessment` | 없음 |
| `POSITION_RISK_SCOUT` | 포지션 위험 Scout | 포지션·시세·위험 요약 | `AgentAssessment` | 없음 |
| `CORE` | Cresta Core | 불변 입력·Scout 결과 | 기존 `core_output` | 없음 |
| `CONSERVATIVE_DECISION` | 보수형 Decision Agent | `DecisionContext` + Conservative `PolicyProfile` | `decision-agent-result-v1` | 없음 |
| `BALANCED_DECISION` | 균형형 Decision Agent | `DecisionContext` + Balanced `PolicyProfile` | `decision-agent-result-v1` | 없음 |
| `AGGRESSIVE_DECISION` | 공격형 Decision Agent | `DecisionContext` + Aggressive `PolicyProfile` | `decision-agent-result-v1` | 없음 |
| `ENTRY_ARBITER` | 결정론적 ENTRY Arbiter | 세 Decision Agent 결과 | `entry-consensus-v1` | 없음 |

`ENTRY_ARBITER`, `Guard`, `Execution Orchestrator`, `Broker`는 LLM 에이전트가 아니라 결정론적 서비스 또는 내부 stage다. 어떤 에이전트에도 주문 API, Broker 자격증명, 파일시스템 또는 임의 네트워크 도구를 제공하지 않는다.

## 4. 오케스트레이션 그래프

아래 그래프와 MAO-001~005는 기존 Agent Runtime v1~v6 계약이다. 신규 ENTRY v7 그래프와 요구사항은 15절을 적용한다.

```text
공식 공시·뉴스·허용 웹 소스 ─> Intel ─> Verify ─┬─> News/Disclosure Scout ─┐
                                                  └─> Market/Sector Scout ───┤
Watch·분봉·지표 ───────────────────────> Technical Scout ────────────────────┤
포지션·계좌 위험 요약 ────────────────> Position Risk Scout ────────────────┤
                                                                                v
                                                                              Core
                                                                                v
                                                                       Execution Orchestrator
                                                                                v
                                                                              Guard
```

| ID | 요구사항 |
| --- | --- |
| MAO-001 | 오케스트레이터는 역할과 의존성이 버전 고정된 DAG만 실행하고 모델이 다음 에이전트·도구·실행 순서를 임의로 선택하게 하지 않는다. |
| MAO-002 | 같은 판단 run 안에서 독립 Scout는 병렬 실행할 수 있지만 Core는 모든 필수 Scout가 종료 상태에 도달한 뒤 한 번만 실행한다. |
| MAO-003 | Core는 원시 웹 문서나 비검증 증거를 직접 입력받지 않고 Verify가 생성한 `VERIFIED` 또는 명시적 `CONFLICTED` 증거 묶음만 입력받는다. |
| MAO-004 | 에이전트 출력은 다른 에이전트의 시스템 지시나 도구 권한을 변경할 수 없으며 데이터 필드로만 전달한다. |
| MAO-005 | 에이전트 run과 판단 실행은 분리한다. `purpose=DIAGNOSTIC` run은 어떤 경우에도 승인·주문 경로에 승격하지 않는다. |

## 5. 증거 계약

### 5.1 EvidenceItem

```yaml
evidence_item:
  schema_version: evidence-item-v1
  evidence_id: uuidv7
  symbol: "005930"
  market: KRX
  source_type: DART_DISCLOSURE | COMPANY_IR | EXCHANGE_NOTICE | NEWS | WEB
  source_tier: PRIMARY | SECONDARY | UNRATED
  source_name: string
  source_url: string
  title: string
  published_at: timestamptz | null
  event_at: timestamptz | null
  received_at: timestamptz
  language: ko | en | other
  facts: [string]
  content_hash: sha256
  extraction_method: RULE | MODEL
  raw_content_ref: string | null
```

| ID | 요구사항 |
| --- | --- |
| MAO-010 | 실시간 가격·호가·체결의 진실 공급원은 Watch/Broker이며 웹 검색 결과를 가격 입력으로 사용하지 않는다. |
| MAO-011 | 신규 진입 관련 외부 정보는 DART·거래소·기업 IR 같은 1차 출처를 우선하고 출처 URL, 게시시각, 사건시각, 수신시각과 hash를 보존한다. |
| MAO-012 | 원문과 추출 사실을 분리한다. 모델이 생성한 요약은 사실 원문으로 승격하지 않고 `extraction_method=MODEL`을 유지한다. |
| MAO-013 | 같은 URL 또는 content hash의 중복은 하나의 대표 증거로 묶되 최초·최종 수신시각은 잃지 않는다. |
| MAO-014 | robots, 사용 약관, 저작권 또는 인증 경계를 위반하는 수집기는 등록할 수 없다. 허용 소스와 수집 방식은 버전 관리한다. |

### 5.2 EvidenceBundle

```yaml
evidence_bundle:
  schema_version: evidence-bundle-v1
  bundle_id: uuidv7
  symbol: "005930"
  as_of: timestamptz
  policy_version: string
  state: VERIFIED | PARTIAL | CONFLICTED | REJECTED
  evidence_ids: [uuidv7]
  contradiction_groups: []
  stale_evidence_ids: []
  verification_reason_codes: []
  bundle_hash: sha256
```

| ID | 요구사항 |
| --- | --- |
| MAO-020 | Verify는 출처 등급, 시간 관련성, 종목 연결, 중복, 상충 정보를 결정론적 규칙으로 먼저 검사하고 필요한 경우에만 LLM을 보조적으로 사용한다. |
| MAO-021 | 상충 정보는 임의로 하나를 선택하지 않고 `CONFLICTED` 상태와 근거 그룹으로 Core에 전달한다. |
| MAO-022 | 외부 정보가 없다는 사실은 긍정 신호가 아니며 `PARTIAL` 또는 명시적 빈 묶음으로 전달한다. |
| MAO-023 | 증거 묶음은 canonical JSON과 SHA-256 hash를 가지며 생성 후 수정하지 않는다. 정정은 새 묶음을 생성한다. |

## 6. Scout 공통 출력 계약

```yaml
agent_assessment:
  schema_version: agent-assessment-v1
  stage_run_id: uuidv7
  role: TECHNICAL_SCOUT | NEWS_DISCLOSURE_SCOUT | MARKET_SECTOR_SCOUT | POSITION_RISK_SCOUT
  symbol: "005930"
  input_refs: []
  status: SUCCEEDED | INSUFFICIENT_DATA | CONFLICTED | TIMED_OUT | FAILED | INVALID_OUTPUT
  stance: SUPPORTIVE | NEUTRAL | CAUTION | RISK | UNKNOWN
  entry_score: 0..100 | null
  exit_risk_score: 0..100 | null
  confidence: 0.0..1.0
  uncertainty: 0.0..1.0
  reason_codes: []
  evidence_refs: []
  observed_at: timestamptz
  valid_until: timestamptz
```

| ID | 요구사항 |
| --- | --- |
| MAO-030 | 모든 Scout는 동일 공통 envelope를 사용하고 역할별 확장 필드는 별도 버전 schema로 검증한다. |
| MAO-031 | `confidence`는 데이터 충분도와 출력 확신을 나타낼 뿐 주문 금액·Guard 한도를 확대하지 않는다. |
| MAO-032 | `evidence_refs`는 해당 run 입력에 포함된 증거만 참조할 수 있고 존재하지 않는 출처나 URL을 생성하면 출력을 거부한다. |
| MAO-033 | 자연어 설명은 실행에 사용하지 않는다. Core와 UI는 허용된 reason code, 점수, stance와 검증된 증거 참조만 사용한다. |
| MAO-034 | 필수 입력이 누락되면 점수를 추정하지 않고 `INSUFFICIENT_DATA`와 null 점수를 반환한다. |
| MAO-035 | 외부 Scout와 Core의 `reason_codes`는 서버 소유 `reason-code-policy-v1`의 역할별 allowlist만 허용한다. 허용 목록은 runtime 입력과 JSON Schema enum에 함께 포함하며 모델이나 prompt가 확장할 수 없다. |
| MAO-036 | 서버는 Provider의 schema 성공 표시와 별개로 reason code allowlist를 다시 검사한다. 미등록 code가 하나라도 있으면 invocation을 `INVALID_OUTPUT`, validation을 `FAILED`, 오류를 `LLM_REASON_CODE_NOT_ALLOWED`로 기록하고 stage는 fail-closed 처리한다. |

### 6.1 `reason-code-policy-v1`

모든 Scout가 공통으로 사용할 수 있는 코드는 `DATA_SUFFICIENT`, `INPUT_DATA_MISSING`, `INPUT_DATA_STALE`, `INPUT_DATA_CONFLICTED`, `NO_VERIFIED_EVIDENCE`, `VERIFIED_EVIDENCE_AVAILABLE`이다. 여기에 역할별로 다음 코드만 추가 허용한다.

| 역할 | 추가 허용 reason code |
| --- | --- |
| `TECHNICAL_SCOUT` | `PRICE_ABOVE_VWAP`, `PRICE_AT_VWAP`, `PRICE_BELOW_VWAP`, `SMA5_RISING`, `SMA5_FLAT`, `SMA5_FALLING`, `RELATIVE_VOLUME_HIGH`, `RELATIVE_VOLUME_NORMAL`, `RELATIVE_VOLUME_LOW`, `VOLATILITY_ELEVATED`, `VOLATILITY_NORMAL`, `DRAWDOWN_FROM_RECENT_HIGH`, `SPREAD_ACCEPTABLE`, `SPREAD_WIDE`, `MARKET_DATA_QUALITY_DEGRADED`, `INDICATOR_DATA_MISSING`, `TECHNICAL_SIGNALS_MIXED`, `MOMENTUM_SUPPORTIVE`, `MOMENTUM_WEAKENING` |
| `NEWS_DISCLOSURE_SCOUT` | `MATERIAL_POSITIVE_DISCLOSURE`, `MATERIAL_NEGATIVE_DISCLOSURE`, `MATERIAL_NEUTRAL_DISCLOSURE`, `RECENT_POSITIVE_NEWS`, `RECENT_NEGATIVE_NEWS`, `NEWS_IMPACT_NEUTRAL`, `DISCLOSURE_AND_NEWS_CONFLICT`, `EVIDENCE_STALE`, `EVIDENCE_NOT_SYMBOL_RELEVANT`, `NEWS_DATA_INSUFFICIENT` |
| `MARKET_SECTOR_SCOUT` | `MARKET_TREND_SUPPORTIVE`, `MARKET_TREND_NEUTRAL`, `MARKET_TREND_WEAK`, `SECTOR_MOMENTUM_SUPPORTIVE`, `SECTOR_MOMENTUM_NEUTRAL`, `SECTOR_MOMENTUM_WEAK`, `MARKET_BREADTH_POSITIVE`, `MARKET_BREADTH_NEUTRAL`, `MARKET_BREADTH_NEGATIVE`, `MARKET_RISK_OFF`, `MARKET_VOLATILITY_ELEVATED`, `MARKET_SECTOR_SIGNALS_MIXED`, `MARKET_DATA_INSUFFICIENT`, `MARKET_DATA_QUALITY_DEGRADED` |
| `POSITION_RISK_SCOUT` | `OPEN_POSITION_NOT_FOUND`, `POSITION_DATA_STALE`, `POSITION_DATA_CONFLICTED`, `POSITION_PROFITABLE`, `POSITION_LOSING`, `DRAWDOWN_LOW`, `DRAWDOWN_MODERATE`, `DRAWDOWN_HIGH`, `FIXED_STOP_NEAR`, `FIXED_STOP_TRIGGERED`, `TRAILING_STOP_NEAR`, `TRAILING_STOP_TRIGGERED`, `BREAK_EVEN_STOP_ACTIVE`, `TIME_STOP_NEAR`, `TIME_STOP_TRIGGERED`, `LIQUIDITY_EXIT_RISK`, `POSITION_RISK_NORMAL`, `POSITION_RISK_ELEVATED`, `POSITION_RISK_CRITICAL` |
| `CORE` | `AGENT_RUNTIME_SHADOW_ONLY`, `DIAGNOSTIC_WAIT_ONLY`, `REQUIRED_SCOUT_INCOMPLETE`, `SCOUT_SIGNALS_SUPPORTIVE`, `SCOUT_SIGNALS_NEUTRAL`, `SCOUT_SIGNALS_CAUTION`, `SCOUT_SIGNALS_CONFLICTED`, `NO_VERIFIED_EVIDENCE`, `MATERIAL_EVENT_RISK`, `MARKET_RISK_ELEVATED`, `POSITION_RISK_ELEVATED`, `POSITION_RISK_CRITICAL`, `DATA_QUALITY_INSUFFICIENT`, `HIGH_UNCERTAINTY`, `ENTRY_CONDITIONS_INCOMPLETE`, `RISK_REWARD_UNFAVORABLE` |

정책 버전은 run의 route·prompt 버전과 별도로 runtime 입력에 기록한다. allowlist 변경은 새 정책 버전, 회귀시험과 SHADOW 비교를 요구하며 기존 run의 출력 의미를 소급 변경하지 않는다.

## 7. Core 종합 계약

| ID | 요구사항 |
| --- | --- |
| MAO-040 | Core 입력은 `scout-input-v1`, 활성 구성·프롬프트·route 버전, 필수 Scout 결과와 선택적 Scout 결과의 명시적 목록으로 고정한다. |
| MAO-041 | 단순 다수결이나 confidence 평균만으로 행동을 결정하지 않는다. Core 출력은 기존 [AI 판단 계약](AI_DECISION_SPEC.md)의 제한 행동 schema를 통과해야 한다. |
| MAO-042 | 신규매수는 모든 활성 필수 Scout가 `SUCCEEDED`이고 입력이 유효한 경우에만 `BUY` 후보가 될 수 있다. 실패·timeout·충돌은 `WAIT` 또는 `RISK_BLOCK`으로 제한한다. |
| MAO-043 | 보유 중 Scout 또는 Core 장애가 발생해도 실시간 손절·비상정지·장마감 규칙은 Guard가 독립 실행한다. |
| MAO-044 | Core는 Scout가 참조하지 않은 새 사실, 가격, 뉴스 또는 증거를 출력 근거로 추가할 수 없다. |
| MAO-045 | 동일 run에서 Core 모델이나 route를 fallback으로 변경하려면 활성 fallback 정책이 명시적으로 허용해야 하며 실제 경로를 기록한다. 기본 정책은 fail-closed다. |
| MAO-046 | Core 입력에는 `reason_code_policy_version`과 Core 허용 code 목록을 포함하고 `reason_codes`는 해당 목록의 부분집합이어야 한다. `incomplete_roles` 일치 검사는 reason code 검사와 독립적으로 유지한다. |

## 8. Run 상태와 멱등성

### 8.1 상태

```text
AgentRun: CREATED -> RUNNING -> SUCCEEDED
                         ├──> PARTIAL
                         ├──> FAILED
                         └──> CANCELLED

StageRun: PENDING -> RUNNING -> SUCCEEDED
                       ├──> INSUFFICIENT_DATA
                       ├──> CONFLICTED
                       ├──> TIMED_OUT
                       ├──> FAILED
                       └──> INVALID_OUTPUT
```

| ID | 요구사항 |
| --- | --- |
| MAO-050 | `agent_run_id`는 판단 목적, symbol, 입력 snapshot, DAG version과 분석 slot의 멱등 key로 유일해야 한다. |
| MAO-051 | stage claim은 DB 조건부 전이 또는 lease로 한 worker만 획득하고 lease 만료 후에도 완료된 stage를 재호출하지 않는다. |
| MAO-052 | 외부 호출 전 `RUNNING`과 invocation 식별자를 영속화하고 응답 유실 시 같은 stage를 무조건 재전송하지 않는다. Provider의 멱등 보장이 없으면 결과를 `FAILED/AMBIGUOUS`로 격리한다. |
| MAO-053 | stage timeout 기본값은 Intel 20초, Verify 15초, Scout 10초, Core 15초이며 role route가 더 짧게 설정할 수 있다. 거래 입력의 `valid_until` 이후 결과는 폐기한다. |
| MAO-054 | 한 종목 run 실패가 다른 종목 run을 중단시키지 않으며 전역 자원 고갈은 신규 run admission을 차단하고 기존 Guard를 유지한다. |

## 9. 실행 자원과 우선순위

| ID | 요구사항 |
| --- | --- |
| MAO-060 | N100/16GB 첫 배포의 기본 동시 외부 LLM 호출은 전체 2개, Core 1개, 로컬 Ollama 1개로 제한하고 부하 측정 후 버전 설정으로만 변경한다. |
| MAO-061 | 우선순위는 `보유 포지션 위험 > 신규진입 Core > 신규진입 Scout > 정보 보강 > 진단/SHADOW 비교`다. |
| MAO-062 | queue 지연으로 입력 유효시간을 넘을 것으로 예상되면 호출하지 않고 `TIMED_OUT/STALE_BEFORE_START`를 기록한다. |
| MAO-063 | 비용·호출량 한도 도달 시 신규매수 분석을 fail-closed하고 기존 포지션 Guard는 계속 작동한다. |

## 10. 프롬프트 주입과 도구 안전

| ID | 요구사항 |
| --- | --- |
| MAO-070 | 웹·뉴스·공시 원문은 `UNTRUSTED_EXTERNAL_DATA` 경계 안에 전달하고 포함된 지시, 도구 호출 요청, 비밀 요구를 실행하지 않는다. |
| MAO-071 | Intel의 네트워크 접근은 등록된 `ResearchSourceAdapter`에만 허용하고 임의 URL fetch, 내부 IP, loopback, metadata endpoint와 리디렉션 우회를 차단한다. |
| MAO-072 | Core에는 web search, URL fetch, MCP, 코드 실행과 주문 도구를 제공하지 않는다. |
| MAO-073 | tool 호출이 필요한 Intel은 허용된 tool 이름과 JSON Schema를 사용하고 호출 수·응답 크기·총 시간을 제한한다. |
| MAO-074 | 외부 원문을 로그·UI에 표시할 때 active content를 실행하지 않고 HTML을 제거하거나 안전하게 escape한다. |

## 11. SHADOW와 활성화 게이트

| ID | 요구사항 |
| --- | --- |
| MAO-080 | 신규 agent, provider, model, prompt 또는 DAG version은 처음에 `SHADOW`로만 실행한다. |
| MAO-081 | 공개·수동 `DIAGNOSTIC` SHADOW run은 운영 판단과 동일 입력을 사용할 수 있지만 실행 결과·승인·주문을 생성하지 않는다. scheduler 소유 `TRADING_ADVISORY`는 모델 출력을 직접 실행하지 않고 [AI 판단 계약 AI-124~130](AI_DECISION_SPEC.md)의 서버 결합 입력으로만 사용할 수 있다. |
| MAO-082 | 활성 후보는 고정 fixture schema 통과율 100%, 허용되지 않은 행동 0건, 증거 환각 0건, timeout·비용·지연 목표와 회귀평가를 통과해야 한다. |
| MAO-083 | `APPROVAL_ONLY` 진입은 사용자 TOTP 재인증, 변경 사유, 활성 DAG·route·prompt·model version과 시험 근거를 요구한다. 자동 주문 확대는 별도 제품 실행 단계 게이트를 다시 통과해야 한다. |

## 12. 구현 단위

권장 Backend 경계:

```text
app/agents/contracts.py       공통 schema와 enum
app/agents/orchestrator.py    DAG 계획·run 생성·stage 전이
app/agents/evidence.py        증거 정규화·검증·bundle 생성
app/agents/scouts.py          역할별 입력 조립
app/agents/core.py            legacy v1~v6 및 POSITION 호환 Core 입력 조립·출력 검증
app/agents/worker.py          queue claim·timeout·heartbeat
app/llm/*                     Provider/Gateway 호출 계층
```

Cresta v2 v7은 Phase 2에서 기존 AgentRun/AgentStageRun worker를 재사용하고 별도 1:1 DecisionContext persistence만 추가하는 것으로 확정됐다. 물리 경계는 DB-157~182를 따르며 실제 module·class 이름은 Phase 3에서 이 책임을 중복하지 않는 범위로 결정한다.

### Historical Agent Runtime v1~v6 first implementation slice

이 절은 기존 v1~v6 Agent Runtime의 역사적 구현 계획이다. Cresta v2 `agent-dag-v7`의 최초 구현 slice를 정의하지 않는다.

첫 구현 slice는 `SHADOW` 전용으로 다음까지만 포함한다.

1. DAG·역할·출력 schema와 DB migration
2. 기존 `deterministic-mock-v2`를 `TECHNICAL_SCOUT`로 감싸는 호환 stage
3. Provider registry와 연결 시험
4. 외부 모델 1개를 사용한 선택적 `NEWS_DISCLOSURE_SCOUT` fixture 실행
5. run·stage·invocation 조회 UI
6. 승인·주문 리소스가 0건임을 검증하는 시험

### 12.1 Agent Runtime v3 고정 계약

현재 구현은 영속성·DAG·안전 경계를 검증하는 비동기 DIAGNOSTIC runtime이며, 외부 LLM과 선택형 OpenDART PRIMARY 공시 수집을 SHADOW 경계에서만 연결한다.

| ID | 요구사항 |
| --- | --- |
| MAO-090 | `POST /ai/agent-runs/diagnostic`은 로그인 사용자, KRX/NXT 종목, 최신 영속 market snapshot, `dag_version=agent-dag-v3`와 명시적인 role별 route ID를 입력으로 받는다. v1/v2 진행 run은 기존 snapshot으로 안전하게 마무리하되 신규 admission에는 재사용하지 않는다. |
| MAO-091 | route가 필요한 역할은 `TECHNICAL_SCOUT`, `NEWS_DISCLOSURE_SCOUT`, `MARKET_SECTOR_SCOUT`, `POSITION_RISK_SCOUT`, `CORE`이다. 각 route는 요청 사용자 소유, `VALIDATED`, `SHADOW`, 해당 role 일치, 검증된 model이어야 한다. 실패 정책은 MAO-110~113의 `FAIL_STOP` 또는 단일 `FAILOVER`만 허용하며 하나라도 준비되지 않으면 run을 만들지 않는다. |
| MAO-092 | `INTEL_COLLECTOR`는 OpenDART가 설정되면 MAO-120~124에 따라 PRIMARY 공시를 수집하고, 비활성 상태에서는 빈 fixture를 만든다. `EVIDENCE_VERIFIER`는 두 경우 모두 다른 출처 coverage가 없으므로 `PARTIAL` bundle로 고정하며 외부 정보 없음은 긍정 신호로 해석하지 않는다. |
| MAO-093 | 실행 순서는 Intel → Verify → 4개 Scout → Candidate Audit → Core로 고정한다. 각 Scout는 Verify 이후 실행하고 Candidate Audit은 네 Scout 이후, Core는 Audit 이후 실행한다. stage dependency를 저장해 worker가 동일 DAG를 재현할 수 있어야 한다. |
| MAO-094 | 각 route stage는 Provider 호출 전 `RUNNING` invocation을 저장하고 완료 후 실제 provider/model, 입력·응답 hash, latency와 서버 측 schema 검증 결과를 기록한다. 외부 Adapter는 `DIAGNOSTIC/SHADOW`에서만 허용한다. 도구는 기본 거부하며 MAO-075와 LLM-PROVIDER-127~133이 허용한 두 Scout의 Provider 웹 검색만 예외다. |
| MAO-095 | Technical은 최신 Watch 지표가 없으면, News는 검증된 외부 증거가 없으면, Position Risk는 열린 position이 없으면 `INSUFFICIENT_DATA`를 출력한다. Market은 정상·최신 snapshot만 평가한다. |
| MAO-096 | Core는 필수 Scout 중 하나라도 `SUCCEEDED`가 아니면 `WAIT`만 출력한다. v1 Core는 `BUY`, 승인, 판단 실행, 주문을 생성할 수 없다. |
| MAO-097 | 멱등 key는 사용자·purpose·market·symbol·market snapshot·DAG version·정렬된 route version map의 canonical SHA-256이다. 같은 key의 재요청은 기존 run을 반환하고 stage·invocation을 추가하지 않는다. |
| MAO-098 | API는 run·stage·evidence bundle·invocation provenance를 반환하되 raw prompt·원문 provider 응답·credential은 반환하지 않는다. Console은 `DIAGNOSTIC · SHADOW · 주문 없음`을 고정 표시한다. |

### 12.2 Agent Worker v2 비동기 실행 계약

| ID | 요구사항 |
| --- | --- |
| MAO-100 | `POST /ai/agent-runs/diagnostic`은 run과 고정 DAG의 8개 `PENDING` stage를 하나의 트랜잭션으로 등록하고 즉시 반환한다. HTTP 요청 프로세스는 stage 또는 LLM 호출을 직접 실행하지 않는다. |
| MAO-101 | 별도 `agent` worker는 의존 stage가 허용된 종료 상태에 도달한 `PENDING` stage만 claim하며, claim 시 `lease_owner_id`, `lease_expires_at`, 증가하는 `fencing_token`, `attempt_count`, `timeout_at`을 원자적으로 기록한다. |
| MAO-102 | stage 완료 쓰기는 현재 `lease_owner_id`와 `fencing_token`이 모두 일치할 때만 허용한다. lease를 잃은 worker의 늦은 결과는 저장하지 않는다. |
| MAO-103 | worker 재시작 또는 lease 만료 시 외부 호출이 시작되지 않은 내부 fixture stage만 다시 `PENDING`으로 돌릴 수 있다. invocation이 생성된 stage는 자동 재전송하지 않고 `TIMED_OUT` 또는 `FAILED`로 격리한다. |
| MAO-104 | 입력 `valid_until` 또는 stage `timeout_at`을 넘긴 작업은 provider를 호출하지 않고 `TIMED_OUT`으로 종료한다. 실패한 의존성을 가진 하위 stage는 `FAILED/AGENT_DEPENDENCY_FAILED`로 종료한다. |
| MAO-105 | route·model version과 유효 generation parameter는 admission 시 `route_versions_json`에 고정한다. 실행 중 현재 ACTIVE route가 변경되어도 이미 등록된 run의 snapshot은 바뀌지 않는다. |
| MAO-106 | Agent Worker v2는 등록·검증된 Mock 또는 외부 Adapter를 `SHADOW`에서 호출한다. `DIAGNOSTIC` 완료는 어떤 거래 리소스도 만들지 않는다. `TRADING_ADVISORY` 완료는 AI-124~130 검증 후 서버 소유 결합 판단만 만들 수 있으며 Adapter와 Agent Core는 승인·주문 API를 직접 호출할 수 없다. |
| MAO-107 | Console은 `CREATED/RUNNING` run이 존재하는 동안 목록을 주기적으로 갱신하고 현재 stage 상태를 표시한다. 버튼은 admission 응답 후 해제하며 전체 DAG 완료까지 HTTP 요청을 붙잡지 않는다. |

### 12.3 운영 단계 단순 실패 계약

| ID | 요구사항 |
| --- | --- |
| MAO-110 | 역할별 기본 실패 정책은 `FAIL_STOP`이다. 사용자가 `FAILOVER`를 선택한 역할만 검증된 예비 모델 1개를 최대 1회 호출한다. SHADOW에서는 두 정책을 시험할 수 있지만 승인·주문을 만들지 않는다. |
| MAO-111 | 기본 모델 실패 시 파라미터를 자동 제거·보정하거나 같은 모델을 자동 재호출하지 않는다. 예비 모델은 자신의 저장된 설정과 동일 입력·prompt·출력 schema를 사용한다. |
| MAO-112 | 최종 실패 역할과 그 하위 stage는 실패로 종료한다. Core 실패 또는 필수 Scout 실패에서는 AI 기반 승인·주문을 만들지 않고 신규매수는 `WAIT` 또는 `RISK_BLOCK`으로 제한한다. |
| MAO-113 | 실패·fallback 이력은 모델, 오류 코드, 시도 순서와 최종 결과를 조회할 수 있어야 한다. AI run 실패와 무관하게 Guard의 손절·비상정지·장마감 청산은 계속 동작한다. |

### 12.4 외부 SHADOW 출력 채택 계약

| ID | 요구사항 |
| --- | --- |
| MAO-114 | 외부 Scout에는 server-owned snapshot·indicator·position·evidence 요약과 허용된 input reference만 전달한다. credential, 주문 도구, 원시 HTML과 내부 객체는 전달하지 않는다. |
| MAO-115 | Provider가 JSON을 반환했다는 사실만으로 성공 처리하지 않는다. 서버는 역할별 `agent-assessment-v1` 또는 `agent-core-v1` Pydantic 계약으로 다시 검증하고, 실패하면 invocation을 `INVALID_OUTPUT/FAILED`로 기록한다. |
| MAO-116 | Scout 모델 출력에는 평가 필드만 허용한다. `stage_run_id`, role, symbol, input reference, 관측·만료 시각은 서버가 현재 run에서 덧붙이며 모델이 위조하거나 변경할 수 없다. 모델의 evidence reference는 실제 입력 reference의 부분집합이어야 한다. |
| MAO-117 | 외부 Core 응답은 SHADOW 계약상 `WAIT`만 허용하고 별도 `shadow_assessment`만 제공한다. `DIAGNOSTIC` 출력은 판단·승인·주문 테이블에 복사하지 않는다. `TRADING_ADVISORY` 출력도 직접 행동으로 복사하지 않고 AI-124~130의 서버 mapping과 멱등 검사를 통과해야 한다. |
| MAO-118 | Mock Adapter는 결정론적 fixture 출력을 계속 사용한다. 외부 primary가 실패해 Mock fallback이 성공한 경우에도 stage 결과는 Mock fixture로 생성하고 두 invocation 이력을 모두 보존한다. |
| MAO-119 | Adapter가 `TIMED_OUT`, `RATE_LIMITED`, `PROVIDER_ERROR`, `INVALID_OUTPUT` 또는 `AMBIGUOUS`로 종료한 invocation은 하위 `FAIL_STOP` stage 예외 처리가 상태·오류 코드·검증 결과를 덮어쓰지 않는다. `AGENT_INVOCATION_OUTCOME_UNKNOWN`은 Adapter 결과가 기록되지 않은 `RUNNING` invocation에만 사용한다. |

## 13. 검증·인수 조건

- 동일 입력·DAG·route version의 run이 중복 생성되지 않는다.
- 필수 stage 실패, timeout, schema 오류와 증거 충돌에서 `BUY`가 생성되지 않는다.
- Core가 원시 웹 문서, 비밀 또는 주문 도구에 접근할 수 없다.
- 모든 Core reason/evidence 참조가 입력 run에 존재한다.
- SHADOW 다중 에이전트 실행은 승인·주문을 생성하지 않는다.
- stage별 모델·prompt·입력·출력·지연·비용·실패가 감사 가능하다.

## 14. 미결정·보류 항목

- 첫 외부 뉴스·검색 공급자와 DART 수집 방식은 공식 이용 조건 확인 후 확정한다.
- `NEWS_DISCLOSURE_SCOUT`, `MARKET_SECTOR_SCOUT`, `POSITION_RISK_SCOUT`의 role별 세부 reason code는 구현 fixture 작성 전에 별도 schema version으로 확정한다.
- Ollama 모델과 context 크기는 N100 실측 전까지 SHADOW 전용이다.

### Provider 검색과 현재 시각 경계 (2026-08-11)

- `MAO-075`: 현재 SHADOW 구현에서 Provider 내장 web search는 `NEWS_DISCLOSURE_SCOUT`와 `MARKET_SECTOR_SCOUT`의 명시적 role route에만 허용하며 Core에는 계속 제공하지 않는다.
- `MAO-076`: Provider 검색 결과는 `UNTRUSTED_EXTERNAL_DATA`이며 검증된 EvidenceBundle로 자동 승격하지 않는다. OpenDART처럼 독립 source Adapter의 검증 정책을 통과하지 않은 Provider citation은 주문·승인 근거가 될 수 없다.
- `MAO-077`: 각 LLM invocation은 UTC와 Asia/Seoul 현재 시각을 서버 소유 runtime context로 받으며 해당 시각은 invocation 이력에 저장한다.

### 역할 비종속 Provider 출처 수집 경계 (2026-08-11)

- `MAO-078`: 웹 검색이 허용되어 실제 요청에 검색 도구가 포함된 invocation은 역할과 관계없이 Provider가 반환한 citation/source metadata를 canonical `EvidenceSourceCandidate`로 정규화한다. 검색 비활성 invocation의 citation 필드는 후보로 저장하지 않는다. 후보에는 HTTPS URL, 제목, 선택적 게시시각과 Provider provenance만 허용하며 원문 응답은 저장하지 않는다.
- `MAO-079`: 정규화된 후보는 해당 run의 `EvidenceItem(source_tier=UNRATED)`으로 보존하되 현재 불변 EvidenceBundle에 자동 추가하지 않는다. Verify를 통과하지 않은 후보는 Scout·Core의 `evidence_refs`나 주문·승인 근거가 될 수 없다.
- `MAO-084`: Scout 입력의 `input_refs`와 `allowed_evidence_refs`를 분리한다. 모델은 `evidence_refs`에 `allowed_evidence_refs`의 부분집합만 반환하며 목록이 비어 있으면 반드시 빈 배열을 반환한다. Provider URL·citation 문자열은 내부 evidence ID가 아니다.
- `MAO-085`: 출력 계약 실패는 원문 없이 `LLM_SCHEMA_VALIDATION_FAILED`, `LLM_EVIDENCE_REF_NOT_ALLOWED`, `LLM_CORE_INCOMPLETE_ROLES_MISMATCH`로 구분해 invocation에 기록한다.
- `MAO-086`: 고정 DAG는 네 Scout 종료 후 Core 전에 내부 `EVIDENCE_CANDIDATE_AUDITOR`를 실행한다. 이 단계는 외부 모델이나 네트워크를 호출하지 않고 현재 run의 `UNRATED EvidenceItem`만 집계한다.
- `MAO-087`: 후보 감사 출력은 후보 ID, 총개수, Provider별 개수와 `NO_PROVIDER_SOURCE_CANDIDATES` 또는 `UNRATED_SOURCE_CANDIDATES_PRESENT` reason code만 포함한다. URL 원문과 모델 응답 원문은 Core 입력에 전달하지 않는다.
- `MAO-088`: Candidate Auditor는 기존 불변 EvidenceBundle을 수정하거나 후보를 `PRIMARY`, `SECONDARY`, `VERIFIED`로 승격하지 않는다. Core에는 후보 감사 ref·개수·reason code만 전달하며 후보 ID는 `evidence_refs`로 취급하지 않는다.
- `MAO-089`: EvidenceBundle이 `VERIFIED`가 아닌 run은 모든 stage가 기술적으로 성공해도 최종 상태를 `PARTIAL`로 유지한다. 출처별 수집·검증 정책이 구현되기 전에는 후보 존재만으로 완전한 판단이 되지 않는다.
- `MAO-120`: `agent-dag-v3`의 `INTEL_COLLECTOR`는 OpenDART가 설정된 경우 공식 `corpCode.xml`에서 6자리 종목코드를 8자리 고유번호로 해석·24시간 메모리 캐시한 뒤 `list.json`을 해당 회사로 제한한다. KST 실행일을 끝 날짜로 최근 3일을 조회하고 응답의 `stock_code`가 run 종목코드와 정확히 같은 공시만 채택한다.
- `MAO-121`: OpenDART `status=000`은 성공, `013`은 정상적인 빈 결과다. 인증·IP·한도·점검·형식 오류, HTTP 오류, timeout, 설정된 최대 page 초과는 안정적인 `DART_*` 오류로 INTEL stage를 fail-closed 처리하며 빈 성공으로 바꾸지 않는다.
- `MAO-122`: 채택한 공시는 접수번호 14자리, 고유번호 8자리, 종목코드 6자리와 접수일을 검증하고 공식 DART viewer URL, 안전한 필드, 수신시각과 canonical hash만 `DART_DISCLOSURE/PRIMARY` EvidenceItem으로 저장한다. API key와 원문 응답은 DB·로그·hash에 저장하지 않는다.
- `MAO-123`: DART 수집이 성공해도 뉴스·거래소·기업 IR coverage가 없으므로 v1 Bundle은 `PARTIAL`을 유지한다. 검증된 DART evidence ID는 Scout allowlist에 포함하되 DART 빈 결과는 `DART_QUERY_COMPLETE_NO_MATCHES`로 구분한다.
- `MAO-124`: DART 활성 여부·설정 상태·source policy version은 run input hash에 포함한다. 같은 snapshot이라도 source 설정이 바뀌면 과거 run을 재사용하지 않는다.
- `MAO-141`: 선택형 KRX OPEN API Adapter는 공식 `https://data-dbg.krx.co.kr`의 유가증권·코스닥 일별매매정보만 호출한다. 인증키는 `AUTH_KEY` 헤더로만 전달하고 조회일은 KST 실행일 이전 최근 7개 달력일로 제한한다.
- `MAO-142`: Adapter는 KOSPI와 KOSDAQ 응답에서 6자리 `ISU_CD`가 run 종목코드와 정확히 일치하는 한 행만 채택한다. `BAS_DD`, 시장, 종목명, OHLC, 등락률, 거래량·거래대금·시가총액만 `KRX_DAILY_MARKET/PRIMARY` EvidenceItem으로 저장하며 원문 응답과 인증키는 저장하지 않는다.
- `MAO-143`: KRX 일별 응답은 거래일·시장 endpoint별로 프로세스 메모리에 캐시하여 같은 날짜 전체 종목 응답을 run마다 재호출하지 않는다. 정상 빈 날짜는 이전 날짜 조회로 진행하고, 7일 안에 종목을 찾지 못하면 `KRX_QUERY_COMPLETE_NO_MATCH`로 구분한다.
- `MAO-144`: HTTP·timeout·인증·quota·형식 오류는 안정적인 `KRX_*` 오류로 INTEL stage를 fail-closed 처리한다. 정상 빈 결과와 장애를 서로 바꾸지 않으며, 채택 데이터의 기준일이 7일을 초과하면 bundle의 허용 evidence에 포함하지 않는다.
- `MAO-145`: INTEL은 활성화된 OpenDART·KRX·NAVER News Adapter를 모두 실행하고 source별 결과·policy version·evidence ID를 기록한다. EvidenceBundle은 각 Adapter의 검증을 통과한 ID만 Scout allowlist에 포함한다. Provider citation은 별도 `UNRATED` 후보로 유지한다.
- `MAO-146`: 선택형 NAVER API HUB News Adapter는 공식 `https://naverapihub.apigw.ntruss.com/search/v1/news`만 호출하고 `sort=date`, 최대 20건, 1회 요청으로 제한한다. 구형 NAVER Developers endpoint와 Provider 내장 검색은 이 Adapter의 대체 경로가 아니다.
- `MAO-147`: 검색어는 같은 INTEL 실행에서 공식 DART 또는 KRX가 확인한 회사명을 우선하고 없으면 정확한 6자리 종목코드를 사용한다. 제목·요약에서 정규화된 검색 identity가 확인되지 않는 결과는 종목 근거로 채택하지 않는다.
- `MAO-148`: 응답의 `pubDate`를 RFC 2822로 검증하고 기본 72시간 이내 결과만 허용 evidence ID에 포함한다. 더 오래된 결과는 `stale_evidence_ids`로 분리하며 미래 시각, 비공개·비HTTPS URL, 잘못된 응답 형식은 채택하지 않는다.
- `MAO-149`: 채택 뉴스는 원문 우선 공개 HTTPS URL, 정제된 제목, 게시시각, 원문 host와 match identity만 `NEWS/SECONDARY` EvidenceItem으로 저장한다. 검색 요약·기사 본문·Provider 원문 응답·인증정보는 저장하거나 Core에 전달하지 않는다.
- `MAO-150`: NAVER News 정상 빈 결과, 모두 비연관 결과와 stale-only 결과는 구분된 reason code로 완료한다. 인증·권한·quota·HTTP·timeout·형식 오류는 안정적인 `NAVER_NEWS_*` 오류로 INTEL을 fail-closed 처리하고 빈 성공으로 바꾸지 않는다. 검색 identity·credential fingerprint별 단기 cache로 호출량을 제한한다.
- `MAO-151`: EvidenceBundle은 활성화된 OpenDART·KRX·NAVER News 조회가 모두 오류 없이 완료되고 최신 KRX 종목 증거가 있을 때만 `VERIFIED`가 될 수 있다. DART 또는 뉴스의 정상 빈 결과는 source coverage 완료로 인정하되 긍정 신호로 해석하지 않는다. 비활성·미완료·오류 source가 있거나 최신 KRX 증거가 없으면 `PARTIAL`로 축소한다.

### Agent Runtime v4 SHADOW 의미 계약

| ID | 요구사항 |
| --- | --- |
| MAO-125 | 신규 admission은 `dag_version=agent-dag-v4`와 `ENTRY` 또는 `POSITION` analysis context를 고정한다. 이미 생성된 v1~v3 run은 당시 DAG와 schema로 끝내며 재해석하거나 소급 변경하지 않는다. |
| MAO-126 | admission은 열린 포지션 유무와 사용한 position snapshot 또는 명시적 `NO_OPEN_POSITION` 표지를 run 입력에 불변으로 저장한다. 실행 중 포지션 변화는 다음 run에서만 반영한다. |
| MAO-127 | `ENTRY`의 `POSITION_RISK_SCOUT`는 `NOT_APPLICABLE`로 정상 종료할 수 있다. 이 상태는 dependency 종료 조건에는 포함하지만 성공률·실패율과 Core의 불완전 필수 역할 집계에서는 제외한다. `POSITION`에서는 같은 역할이 필수다. |
| MAO-128 | 외부 Scout는 `agent-assessment-v2`, Core는 `agent-core-v2`로 검증한다. 평가가 `SUCCEEDED`가 아니면 점수는 null이어야 하며 Core의 실행 action은 계속 `WAIT`로 고정한다. |
| MAO-129 | Core는 action과 분리된 context별 `shadow_assessment`를 반환한다. 필수 역할 실패, schema 오류, 증거 불충분 또는 context 불일치에서는 반드시 `UNKNOWN`으로 축소한다. |
| MAO-130 | v4 idempotency key에는 analysis context와 frozen position snapshot hash를 포함한다. 같은 시장 snapshot이어도 context 또는 position snapshot이 다르면 기존 run을 재사용하지 않는다. |
| MAO-131 | v4 DIAGNOSTIC run은 유효한 shadow assessment를 생성해도 `Decision`, `Approval`, `OrderIntent`, `TradingOrder`를 만들지 않는다. TRADING 연결은 별도 구현 단계와 인수 gate를 요구한다. |
| MAO-132 | 기존 ACTIVE SHADOW route가 v1 출력 schema를 선언했더라도 v4 admission은 route를 재생성하지 않고 사용할 수 있다. 이때 run에는 route의 선언 schema와 v4의 유효 검증 schema를 함께 고정하며, 실제 출력은 반드시 v2 계약으로 검증한다. 신규 route는 v2 schema를 선언할 수 있고 기존 route row와 과거 run은 변경하지 않는다. |

### 12.5 Agent Runtime v5 서버 입력 계약

| ID | 요구사항 |
| --- | --- |
| MAO-133 | 신규 admission은 `agent-dag-v5`와 `agent-server-input-v1`을 run 및 input hash에 고정한다. `agent-dag-v4` run은 context-aware v2 출력 계약으로 계속 처리하되 v5 파생 입력을 소급 생성하지 않는다. |
| MAO-134 | POSITION snapshot 파생값은 admission transaction에서 server-owned calculator가 market snapshot, frozen position과 frozen Risk Policy provenance만 사용해 계산한다. stage worker는 현재 DB position이나 최신 정책을 다시 조회해 값을 바꾸지 않는다. |
| MAO-135 | Market Context는 trusted internal Adapter만 생성하며 Web/API에서 임의 주입하지 않는다. snapshot은 market·symbol, index, sector, breadth 원시값, 서버 계산 비율, source tier·reference, 품질과 시각을 canonical payload와 hash로 보존한다. |
| MAO-136 | admission은 같은 market·symbol에서 `quality=NORMAL`이고 admission 시각까지 관측됐으며 `valid_until`이 지나지 않은 최신 Market Context 하나만 선택한다. 선택 ID와 hash는 run에 고정한다. |
| MAO-137 | Market Context가 없거나 stale·불완전하면 MARKET_SECTOR_SCOUT는 Provider 호출 입력에 null과 결측 reason을 명시하고 결과를 `INSUFFICIENT_DATA`로 강제한다. Provider 웹 검색 결과만으로 서버 Market Context를 대체하지 않는다. |
| MAO-138 | Position·Market Context 파생 입력은 역할별 Provider 요청의 `allowed_input_refs`에 포함하고 Core에는 Scout가 채택한 assessment와 hash만 전달한다. Provider가 새 source ref나 파생값을 생성해 입력 provenance로 승격할 수 없다. |
| MAO-139 | v5 DIAGNOSTIC의 실행 action은 계속 WAIT이며 Decision·Approval·OrderIntent·TradingOrder를 생성하지 않는다. 서버 입력 확장은 거래 권한을 변경하지 않는다. |
| MAO-140 | 신규 admission은 `agent-dag-v6`로 분리한다. v6의 필수 Scout가 불완전하면 Core stage는 LLM invocation을 만들지 않고 서버 축소 결과로 성공 종료한다. run은 Scout 상태에 따라 `PARTIAL` 또는 `FAILED`로 집계하되 Core 자체를 Provider 계약 오류로 실패시키지 않으며, 모든 필수 Scout가 완전할 때만 Core Provider를 호출한다. 기존 v5 run과 idempotency key는 재작성하거나 재사용하지 않는다. |

## 15. Cresta v2 ENTRY Agent Runtime v7

이 절은 신규 `agent-dag-v7`의 목표 계약이다. 기존 `agent-dag-v1`~`agent-dag-v6`, `CORE`, `agent-core-v1/v2` run과 저장 결과는 당시 의미로 보존하며 v7 용어로 재해석하지 않는다.

```text
Intel
  ↓
Verify
  ↓
Technical / News·Disclosure / Market·Sector / Position Risk(*) Scout
  ↓
Candidate Audit
  ↓
DecisionContext Freeze
  ↓
┌───────────────────────┬───────────────────┬──────────────────────┐
│ Conservative Decision │ Balanced Decision │ Aggressive Decision  │
└───────────────────────┴───────────────────┴──────────────────────┘
                              ↓
                    Deterministic Arbiter
                              ↓
                         ArbiterResult
```

논리 도메인 객체명은 `ArbiterResult`이며 `entry-consensus-v1`은 해당 객체의 schema version이다.

`ENTRY`에서 열린 포지션이 없는 `POSITION_RISK_SCOUT`는 기존 v4~v6 계약과 같이 `NOT_APPLICABLE`일 수 있다. 신규 역할 코드는 `CONSERVATIVE_DECISION`, `BALANCED_DECISION`, `AGGRESSIVE_DECISION`, `ENTRY_ARBITER`다. `ENTRY_ARBITER`는 LLM Agent가 아니라 서버 소유 deterministic internal stage다.

| ID | 요구사항 |
| --- | --- |
| MAO-200 | `agent-dag-v7`은 Cresta v2 ENTRY 전용 신규 DAG version이며 기존 v1~v6 run의 의미와 결과를 변경하지 않는다. |
| MAO-201 | v7의 Intel·Verify·Scout·Candidate Audit 단계는 가능한 기존 runtime 계약을 재사용한다. |
| MAO-202 | `DecisionContext`는 Scout와 Candidate Audit 종료 후 한 번만 고정한다. |
| MAO-203 | 세 Decision Agent stage는 동일한 `DecisionContext` ID와 hash를 입력으로 독립 실행한다. |
| MAO-204 | 세 Decision Agent는 서로 dependency를 가지지 않고 가능한 경우 병렬 실행한다. |
| MAO-205 | 한 Decision Agent의 output 또는 prompt를 다른 Decision Agent의 입력에 포함하지 않는다. |
| MAO-206 | Arbiter는 세 Decision Agent stage가 종료 상태에 도달한 후 한 번만 실행한다. |
| MAO-207 | Arbiter stage는 Provider route, model 또는 invocation을 생성하지 않는다. |
| MAO-208 | 필수 Decision Agent가 실패·timeout·invalid output이면 `BUY` consensus를 만들지 않는다. |
| MAO-209 | v7 ENTRY run은 Decision Agent 또는 Arbiter 단계에서 Approval, OrderIntent, TradingOrder 또는 Broker 호출을 생성할 수 없다. |
| MAO-210 | v7 최초 완결 decision slice는 SHADOW/DIAGNOSTIC이며 ArbiterResult 이후 거래 resource를 생성하지 않는다. 이에 앞선 Phase 4 upstream partial slice는 MAO-221~235에 따라 DecisionContext Freeze에서 checkpoint한다. |
| MAO-211 | DIAGNOSTIC v7 결과는 기존 run과 동일하게 TRADING으로 승격할 수 없다. |
| MAO-212 | v7 TRADING admission/finalization은 별도 activation gate가 열렸을 때만 허용한다. |
| MAO-213 | v7 evaluation root는 기존 AgentRun이다. 최초 slice는 admission부터 `purpose=DIAGNOSTIC`이고 activation 이후 scheduler-owned production run은 admission부터 `purpose=TRADING`이며, 두 목적 사이의 상태 전이나 결과 복사를 허용하지 않는다. |
| MAO-214 | DecisionContext Freeze는 AgentStage가 아니라 Scout와 Candidate Audit 이후의 서버 소유 영속 transaction이다. Context commit 전에는 세 Decision Agent stage가 runnable하거나 claim 가능하지 않다. |
| MAO-215 | Freeze는 필수 stage의 존재, 권위 `AgentStageRun.state`와 structured status 일치, output hash, 같은-run EvidenceBundle·stage provenance를 검증한다. ENTRY Position Risk의 명시적 `NOT_APPLICABLE`은 허용하지만 stage 부재로 대체할 수 없다. 세부 terminal matrix는 DB-163을 따른다. |
| MAO-216 | Decision Agent 결과는 각 role의 AgentStageRun output이고 ArbiterResult는 ENTRY_ARBITER AgentStageRun output이다. 별도 DecisionAgentResult/ArbiterResult table을 만들지 않으며 `(run_id, role)` uniqueness를 유지한다. |
| MAO-217 | Provider route가 필요한 v7 role은 네 Scout와 세 Decision Agent다. `ENTRY_ARBITER`는 internal role로서 route·prompt·model·invocation을 가지지 않으며 기존 `CORE` route는 v1~v6에만 유지한다. |
| MAO-218 | run admission은 세 system-owned PolicyProfile ConfigurationVersion을 DecisionContext와 분리된 canonical version map에 고정한다. 세 Agent는 같은 Context와 자신의 PolicyProfile만 입력받는다. |
| MAO-219 | Context, stage result, finalization source와 activation provenance의 물리 영속 계약은 `DATABASE_SPEC.md` DB-157~182를 따르며 DAG layer가 별도 DecisionRun, EvidenceSet 또는 generic context-reference lifecycle을 만들지 않는다. |
| MAO-220 | Context freeze 또는 stage claim 재시도는 기존 unique identity와 hash를 먼저 조회한다. 같은 identity의 다른 hash, cross-run reference 또는 version mismatch는 기존 결과를 갱신하지 않고 fail-closed 한다. |

v7 논리 stage는 Intel 1, Verify 1, Scout 4, Candidate Audit 1, Decision Agent 3, Arbiter 1로 총 11개다. `DecisionContext Freeze`는 이 개수에 포함되는 stage가 아니며 DB-164의 별도 server-owned transaction이다. `agent_stage_runs`의 DB role allowlist는 기존 역할과 신규 네 역할의 합집합을 허용하지만 runtime은 `dag_version`별 stage set을 검증해 v1~v6 row의 의미를 보존한다.

### 15.1 Phase 4 v7 upstream execution slice

Phase 4의 실행 범위는 최종 `agent-dag-v7` 중 Intel, Evidence Verifier, 네 Scout와 Candidate Audit의 7개 stage 및 그 뒤의 server-owned DecisionContext Freeze다. Phase 4 admission은 미구현 C/B/A Decision Agent와 `ENTRY_ARBITER` stage를 선생성·claim·실행하지 않으며, 이 제한을 v7의 최종 stage set으로 해석하지 않는다.

| ID | 요구사항 |
| --- | --- |
| MAO-221 | Phase 4 v7 upstream admission은 `purpose=DIAGNOSTIC`, `analysis_context=ENTRY`만 허용하고 upstream 7개 stage만 materialize한다. `CORE`, C/B/A Decision Agent와 `ENTRY_ARBITER` stage를 만들거나 실행하지 않으며 production scheduler와 TRADING admission에 연결하지 않는다. |
| MAO-222 | upstream stage와 DecisionContext Freeze가 완료돼도 AgentRun을 `SUCCEEDED` 또는 다른 terminal state로 finalize하지 않는다. `dag_version=agent-dag-v7`, `purpose=DIAGNOSTIC`, `analysis_context=ENTRY`, 필수 upstream terminal, DecisionContext 존재, downstream 미활성 조건의 AgentRun은 `RUNNING`을 유지하며 Context가 upstream checkpoint 완료의 영속 증거다. 새 lifecycle enum을 추가하지 않는다. |
| MAO-223 | v1~v6은 terminal `CORE`를 기준으로 기존 finalization을 유지한다. v7 upstream checkpoint는 `CORE`를 조회하지 않으며 CORE 부재가 exception, `FAILED` 또는 `SUCCEEDED` 전이 원인이 되어서는 안 된다. full v7 finalization은 Decision Agent/Arbiter runtime phase에서 별도로 확정한다. |
| MAO-224 | Candidate Audit output을 먼저 commit한 뒤 별도 reconciliation transaction이 eligible v7 upstream run을 조회하고 `freeze_decision_context(run_id)`를 호출한다. 같은 manifest retry는 기존 Context를 반환하고 다른 manifest/hash conflict는 fail-closed 하며, Provider/stage completion transaction 안에서 Context를 freeze하지 않는다. |
| MAO-225 | v7 upstream Scout는 versioned contract registry에서 `AgentAssessmentV2`와 `scout-input-v2` server-owned input path로 명시 등록한다. 등록 범위는 네 Scout뿐이며 C/B/A Decision Agent와 `ENTRY_ARBITER` runtime contract를 Phase 4에서 등록·실행하지 않는다. |
| MAO-226 | v7 upstream의 route-required set은 `TECHNICAL_SCOUT`, `NEWS_DISCLOSURE_SCOUT`, `MARKET_SECTOR_SCOUT`, `POSITION_RISK_SCOUT` 정확히 네 역할이다. `CORE` route를 요구하지 않고 기존 Scout route row를 복제하지 않으며 admission은 실제 선택한 네 route의 immutable ID/version/hash provenance를 고정한다. v1~v6 route set은 변경하지 않는다. |
| MAO-227 | v7 upstream admission은 `scout-input-v2` 확정, 네 Scout route snapshot, DB-172 PolicyProfile map freeze, AgentRun insert와 upstream stage materialization을 한 transaction 경계에서 수행한다. 하나라도 실패하면 partial run/stage를 남기지 않는다. PolicyProfile map은 Context와 Scout input 밖에 유지한다. |

### 15.2 v7 Evidence Verifier와 Candidate Audit result

v1~v6의 historical Verifier·Candidate Audit output은 당시 schema로 보존한다. 다음 envelope은 v7 신규 stage output에만 적용하며 완료된 output의 canonical JSON/hash는 수정하지 않는다.

| ID | 요구사항 |
| --- | --- |
| MAO-228 | v7 Evidence Verifier는 `evidence-verifier-v2` envelope을 사용한다. canonical 필드는 `schema_version`, `stage_run_id`, `role=EVIDENCE_VERIFIER`, `status`, `evidence_bundle_id`, `evidence_bundle_hash`, `observed_at`, `valid_until`, `evidence_policy_version`, `freshness_policy_version`, `freshness_policy_snapshot_hash`, evidence item ID 순으로 정규화한 `verified_item_validity[]`, 정렬·중복 제거한 `reason_codes`다. stage identity/role/state, 같은-run bundle ID/hash와 output hash를 Context Freeze가 직접 검증할 수 있어야 한다. |
| MAO-229 | `evidence-freshness-policy-v1`은 source별 freshness anchor와 duration source를 `DART_DISCLOSURE/PRIMARY = event_at + dart_lookback_days`, `KRX_DAILY_MARKET/PRIMARY = event_at + krx_lookback_days`, `NEWS/SECONDARY = published_at + naver_news_lookback_hours`로 고정한다. duration의 실제 값은 run admission에 적용된 configuration snapshot에서 읽고 그 version/hash를 result provenance에 보존하며 새 hard-coded 숫자를 만들지 않는다. |
| MAO-230 | freshness anchor는 위 정책이 지정한 timezone-aware timestamp여야 하며 다른 timestamp로 임의 대체하지 않는다. 각 item의 `item_valid_until`을 먼저 계산하고 Verifier `valid_until = min(AgentRun.valid_until, Context에 사용하는 모든 verified item의 item_valid_until)`로 계산한다. stale item은 기존 bundle policy대로 제외·표시한다. source rule·timestamp·configuration provenance가 없거나 usable verified item이 0개면 freeze-eligible `SUCCEEDED`로 처리하지 않고 안정적인 reason code와 함께 fail-closed 한다. |
| MAO-231 | v7 Candidate Audit은 기존 internal/provider-free 로직을 재사용하고 `evidence-candidate-audit-v2` envelope을 사용한다. canonical 필드는 `schema_version`, `stage_run_id`, `role=EVIDENCE_CANDIDATE_AUDITOR`, `status`, `observed_at`, `evidence_bundle_id`, `evidence_bundle_hash`, ID 순으로 정규화한 `candidate_ids`, `candidate_count`, key 순으로 정규화한 `provider_counts`와 `source_counts`, 정렬·중복 제거한 `reason_codes`, `audit_policy_version`, `candidate_set_hash`다. EvidenceBundle을 수정·승격하지 않으며 Context Freeze가 stage identity/role/state와 output hash를 직접 검증한다. |
| MAO-232 | ENTRY에 열린 포지션이 없으면 Position Risk stage는 존재하되 Provider invocation을 만들지 않고 AI-255의 명시적 `NOT_APPLICABLE` AgentAssessmentV2를 저장한다. stage 부재는 Context Freeze 실패다. |

### 15.3 v7 Scout role input hash

| ID | 요구사항 |
| --- | --- |
| MAO-233 | 각 v7 Scout stage `input_hash`는 `scout-role-input-v1` canonical material의 SHA-256이다. 공통 필드는 `schema_version`, `role`, `scout_input_snapshot_id`, `scout_input_hash`, `evidence_bundle_id`, `evidence_bundle_hash`, `route_id`, `route_version_hash`, `input_contract_version`이며 역할에 실제 전달되는 Indicator, MarketContext, position provenance만 명시적 nullable reference로 포함한다. |
| MAO-234 | `scout-role-input-v1`은 UUID·role·key 정규 순서와 canonical Decimal/time 규칙을 사용한다. 관련 source/dependency 또는 route/input-contract version 변경은 hash를 변경하고, 해당 역할에 전달되지 않는 다른 Scout output과 PolicyProfile 변경은 hash에 영향을 주지 않는다. |
| MAO-235 | Stage claim·replay는 저장된 input material을 다시 canonicalize해 `input_hash`를 검증한다. 단순 `run_input_hash + role` 조합이나 호출자가 제공한 hash를 v7 Scout 감사 provenance로 사용하지 않는다. |

### 6.10 v7 Decision Agent materialization과 실행

Phase 7B는 최종 논리 DAG를 바꾸지 않고, committed DecisionContext 이후의 세
Decision Agent stage를 실제 runtime에 연결하기 위한 계약만 확정한다. Arbiter는
후속 단계까지 논리 role로만 남으며 materialize하거나 실행하지 않는다.

| ID | 요구사항 |
| --- | --- |
| MAO-236 | Phase 7 이후 신규 v7 admission은 네 Scout와 `CONSERVATIVE_DECISION`, `BALANCED_DECISION`, `AGGRESSIVE_DECISION`의 정확히 일곱 LLM route identity/version/hash를 원자적으로 freeze한다. `ENTRY_ARBITER`와 `CORE` route는 요구하지 않는다. 이미 admission된 네-route Phase 4~6 run은 backfill·변경하지 않고 당시 계약으로 replay한다. |
| MAO-237 | Candidate Audit commit과 DecisionContext freeze 뒤 별도 decision-stage reconciliation transaction이 run과 Context를 잠그고 Context 및 frozen Policy/route map을 검증한 후 C/B/A stage를 원자적으로 materialize한다. initial admission transaction이나 Context freeze transaction에 세 stage를 끼워 넣지 않는다. |
| MAO-238 | reconciliation은 동일 canonical `decision-agent-stage-input-v1`이면 기존 stage를 재사용하고, 정확한 부분집합만 있으면 기존 row의 identity/hash/state를 검증한 뒤 나머지를 한 transaction에서 생성한다. 중복 role 또는 기존 input/route/prompt/policy mismatch는 어떤 row도 변경하지 않고 conflict로 종료한다. |
| MAO-239 | C/B/A 각각의 유일한 stage dependency는 같은 run의 terminal `EVIDENCE_CANDIDATE_AUDITOR`다. committed·유효한 DecisionContext는 별도 claim gate이며 C/B/A 상호 dependency는 없다. 따라서 세 stage는 서로 독립적으로 병렬 claim할 수 있다. |
| MAO-240 | v7 논리 role registry는 Intel, Verifier, 네 Scout, Candidate Audit, C/B/A, Arbiter의 11개 role을 유지한다. phase enablement registry는 Phase 7에서 C/B/A까지만 enabled materialization으로 표시하고 Arbiter는 disabled로 유지한다. enabled set을 하드코딩한 stage-count나 positional index로 DAG 의미를 판정하지 않는다. |
| MAO-241 | worker dispatch는 C/B/A role을 명시적 Decision Agent handler로 보내며 Scout/Core handler로 분류하지 않는다. handler는 AI-256~265의 input resolver, role별 Prompt/Route와 Policy 검증, model-output validation, server result 저장만 수행하고 다른 Decision Agent 결과를 읽지 않는다. |
| MAO-242 | Decision Agent 실행은 (1) 짧은 claim transaction에서 lease/fencing을 commit하고, (2) immutable input을 resolve한 뒤 DB row lock 없이 Provider network call을 수행하며, (3) 별도 completion transaction에서 fencing과 Context·Policy·route·prompt·stage-input validity를 재검증하고 result/state를 함께 commit한다. Provider 호출 동안 run/stage row lock을 유지하지 않는다. |
| MAO-243 | Provider 호출 전후의 모든 권위 terminal 실패도 AI-263의 structured result/output hash를 남긴다. stale worker는 쓰지 못하고 outcome-unknown recovery만 권위 fencing으로 terminalize한다. Decision Agent에는 web search, live data/Broker/position fetch, 파일·코드 실행, Approval·Order·Arbiter tool을 제공하지 않는다. |
| MAO-244 | 세 역할은 공통 system instruction을 공유할 수 있으나 각각 고정된 PromptProfile과 LlmRoleRoute provenance를 가져야 한다. 정책 숫자는 PolicyProfile에만 존재하며 production provisioning 값은 bootstrap 상수로 생성하지 않는다. Arbiter가 후속 활성화될 때는 이 stage/result identity와 hash를 입력으로 재사용하고 C/B/A 계약을 재작성하지 않는다. |
| MAO-245 | application/control-plane의 schema, profile, prompt와 API role validator는 C/B/A 세 role을 DB allowlist와 동일하게 생성·검증·activation할 수 있어야 하고 각 role의 PromptProfile, LlmRoleRoute, ModelProfile binding과 fallback 설정을 검증한다. WEB_SEARCH 또는 동등 external acquisition은 항상 거부하며 필수 row가 없는 환경의 admission/materialization은 fail-closed 한다. |

### 15.3 Phase 8 ENTRY_ARBITER runtime 계약

Phase 8은 Phase 7의 세 immutable DecisionAgentResult를 변경하지 않고 provider-less
`ENTRY_ARBITER`를 연결한다. 세 결과가 commit된 뒤 별도 arbiter-stage reconciliation이
AI-266~275와 DB-197~204의 canonical input을 검증·생성하고 stage를 원자적·멱등적으로
materialize한다. 마지막 완료 Agent의 completion transaction 안에서 consensus를 직접
계산하지 않는다.

Arbiter의 direct dependency는 `CONSERVATIVE_DECISION`, `BALANCED_DECISION`,
`AGGRESSIVE_DECISION` 정확히 세 개의 AND 목록이다. DecisionContext는 stage dependency가
아니라 materialization·claim·completion integrity gate다. 일반 downstream
`DEPENDENCY_OK`를 적용하지 않고, terminal structured Result/hash가 유효하면
Decision Agent의 semantic state가 non-success여도 정상 dependency로 소비한다.

materialization 전 구조 오류·만료는 stage를 만들지 않는다. materialization 후
claim/completion 재검증의 provenance mismatch는 `CONFLICTED`, expiry는 `TIMED_OUT`,
예상치 못한 evaluator failure는 `FAILED`이며 모두 output을 남기지 않는다. 유효한
non-success Agent Result를 정상 평가한 `UNKNOWN` consensus는 Arbiter stage
`SUCCEEDED`다.

worker는 기존 짧은 claim/lease/fencing lifecycle을 재사용하되 `ENTRY_ARBITER`를
Scout/Core/Decision Agent handler가 아닌 전용 provider-less handler로 dispatch한다.
claim 전 Context와 세 Result, canonical input hash, null route/invocation을 검증한다.
pure evaluation 뒤 completion transaction은 fencing·ownership·Context expiry/hash,
세 stage identity/output hash/result와 Arbiter input hash를 다시 검증하고 exact
ArbiterResult/state를 함께 commit한다. Decision Agent completion 뒤 opportunistic trigger와
idle worker recovery는 같은 reconciliation helper를 호출할 수 있다.

| ID | 요구사항 |
| --- | --- |
| MAO-246 | arbiter-stage reconciliation은 C/B/A terminal structured Result commit 뒤 별도 transaction에서 실행하며 exact eligible set 전에는 ENTRY_ARBITER를 만들지 않는다. |
| MAO-247 | reconciliation은 C/B/A canonical order와 `entry-arbiter-input-v1` hash로 stage를 materialize하고 `(run_id, role)` unique를 재사용한다. 같은 input은 기존 stage를 반환하고 다른 input hash는 row를 수정하지 않고 conflict로 종료한다. |
| MAO-248 | ENTRY_ARBITER의 direct dependencies는 C/B/A 정확히 세 role의 AND 목록이고 Context는 별도 integrity gate다. |
| MAO-249 | ENTRY_ARBITER dependency eligibility는 operational success가 아니라 terminal structured Result와 valid output hash다. 따라서 structured `INSUFFICIENT_DATA | CONFLICTED | TIMED_OUT | FAILED | INVALID_OUTPUT`도 정상 입력이다. |
| MAO-250 | worker는 기존 claim/lease/fencing으로 ENTRY_ARBITER를 claim하고 전용 provider-less handler로 dispatch한다. 새 worker/service와 Provider transaction은 만들지 않는다. |
| MAO-251 | claim 전에는 v7 DIAGNOSTIC/ENTRY run, non-terminal state, Context와 C/B/A identity/schema/hash/state/Policy/validity, rebuilt input hash, null route/invocation을 검증한다. |
| MAO-252 | completion은 fencing·ownership/state, Context identity/hash/expiry, ordered C/B/A stage/output identity, strict Result와 input hash, null route/invocation을 다시 검증한다. mismatch는 output 없는 CONFLICTED, expiry는 output 없는 TIMED_OUT이다. |
| MAO-253 | pure consensus evaluator는 DB·clock·network·configuration side effect 없이 normalized status/action을 action/pattern/reason으로 변환한다. internal failure는 output 없는 FAILED다. |
| MAO-254 | Decision Agent completion 후 trigger와 idle recovery는 같은 reconciliation helper를 사용하며 completion role/order와 process crash가 Arbiter identity/hash/result를 변경하지 않는다. |
| MAO-255 | ENTRY_ARBITER는 ArbiterResult만 생성하고 DIAGNOSTIC run을 Decision·Approval·Order·Broker·Finalizer·Activation·Execution으로 연결하지 않는다. |

### 15.4 Phase 9 Activation admission, Finalizer reconciliation과 run lifecycle

v7 production evaluation은 scheduler/server-owned admission만 `purpose=TRADING`으로 생성할
수 있다. 공개 DIAGNOSTIC endpoint와 임의 내부 호출은 TRADING을 선택할 수 없다. 동일
owner/market/symbol/slot에서도 purpose를 포함한 idempotency material을 사용하므로
DIAGNOSTIC과 TRADING은 서로 다른 AgentRun identity이며 기존 run의 purpose 변경, 결과
promotion 또는 복사는 금지한다.

TRADING admission transaction은 `scout-input-v2`, 일곱 Scout/C/B/A route snapshot, 세
PolicyProfile map과 정확히 한 valid `ACTIVE + OPEN` Activation ConfigurationVersion ID/hash를
함께 freeze하고 upstream 7개 stage만 materialize한다. Gate 부재·중복·CLOSED·malformed,
evidence invalid/stale/hash mismatch에서는 AgentRun과 partial stage를 만들지 않는다.
admission 뒤 Context→C/B/A→ENTRY_ARBITER 경로는 DIAGNOSTIC과 같은 frozen data contract를
사용하되 처음부터 끝까지 purpose=TRADING을 유지한다.

TRADING ArbiterResult commit은 Finalizer transaction을 포함하지 않는다. commit 뒤 같은
`finalization reconciliation` helper를 즉시 opportunistic 호출할 수 있고, idle
worker/server sweep와 crash recovery도 같은 helper로 `RUNNING` TRADING run 중 authoritative
ArbiterResult가 있고 Decision이 없는 후보를 다시 찾는다. Finalizer는 AgentStageRun,
claim/lease/fencing 또는 LlmInvocation을 만들지 않는다.

AgentRun terminal mapping은 다음과 같다. `completed_at`은 terminal transition transaction의
server-owned DB timestamp로 null에서 한 번만 설정하며 idempotent retry가 덮어쓰지 않는다.

| case | AgentRun.state | AgentRun.error_code | retry | Decision |
| --- | --- | --- | --- | --- |
| DIAGNOSTIC authoritative ArbiterResult committed | `SUCCEEDED` | `null` | no | 0 |
| DIAGNOSTIC Arbiter stage `CONFLICTED` | `FAILED` | `ENTRY_ARBITER_CONFLICTED` | no | 0 |
| DIAGNOSTIC Arbiter stage `TIMED_OUT` | `FAILED` | `ENTRY_ARBITER_TIMED_OUT` | no | 0 |
| DIAGNOSTIC Arbiter stage `FAILED` | `FAILED` | `ENTRY_ARBITER_FAILED` | no | 0 |
| TRADING Decision inserted 또는 exact existing row reused | `SUCCEEDED` | `null` | no | exactly 1 |
| Gate CLOSED | `CANCELLED` | `ACTIVATION_GATE_CLOSED` | no | 0 |
| Gate superseded | `CANCELLED` | `ACTIVATION_GATE_SUPERSEDED` | no | 0 |
| malformed/missing evidence/hash/ambiguous Gate | `FAILED` | `ACTIVATION_GATE_INVALID` | no | 0 |
| Context/Arbiter expired | `FAILED` | `SOURCE_EXPIRED` | no | 0 |
| source provenance/schema/hash conflict | `FAILED` | `SOURCE_CONFLICTED` | no | 0 |
| finalization identity/unique payload conflict | `FAILED` | `FINALIZATION_IDENTITY_CONFLICT` | no | 0 |
| transient DB/lock failure | 기존 `RUNNING` 유지 | `FINALIZATION_DB_RETRYABLE_FAILURE` | yes | 0 |

`BUY | WAIT | REJECT | UNKNOWN`은 모두 workflow operational success이므로 action으로 run
성공 여부를 바꾸지 않는다. `CANCELLED`는 명시적 live safety denial이고 `FAILED`는 invalid
safety configuration, terminal source/identity integrity 또는 expiry를 뜻한다. transient
failure가 해소되어 성공하면 error code를 null로 지운 뒤 Decision insert와 `SUCCEEDED`
transition을 같은 transaction으로 commit한다. terminal Gate denial은 Decision 부재가
정상이며 persistent audit가 그 이유를 설명한다.

| ID | 요구사항 |
| --- | --- |
| MAO-256 | v7 TRADING은 scheduler/server-owned admission만 생성하고 purpose를 idempotency identity에 포함하며 DIAGNOSTIC mutation·promotion·복사를 금지한다. |
| MAO-257 | TRADING admission은 input, 일곱 route, 세 policy와 정확히 한 valid ACTIVE+OPEN Gate ID/hash를 원자적으로 freeze하며 실패 시 run/stage 0건이다. |
| MAO-258 | TRADING은 purpose 외에는 기존 v7 frozen pipeline contract를 재사용하고 Arbiter까지 purpose를 바꾸지 않는다. |
| MAO-259 | Arbiter commit과 Finalizer transaction은 분리하고 opportunistic trigger·idle sweep·crash recovery가 같은 finalization reconciliation helper를 사용한다. |
| MAO-260 | DIAGNOSTIC 및 TRADING run lifecycle은 위 exact state/error mapping을 사용하고 UNKNOWN action도 authoritative workflow success로 닫는다. |
| MAO-261 | Decision insert/reuse와 TRADING run SUCCEEDED transition은 원자적이며 completed_at은 terminal transition에서 한 번만 설정한다. |
| MAO-262 | retryable DB failure는 RUNNING과 재조정 가능성을 유지하고 terminal denial/failure는 되살리지 않는다. |
