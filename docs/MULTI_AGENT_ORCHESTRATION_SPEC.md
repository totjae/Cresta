# Cresta 다중 에이전트 오케스트레이션 명세

## 1. 목적

Cresta의 정보 수집, 증거 검증, 기술·뉴스·시장·포지션 평가와 최종 행동 판단을 역할별 에이전트로 분리하고, 각 단계가 불변 입력과 구조화 출력으로만 연결되도록 정의한다. 이 문서는 자유 대화형 에이전트 군집이 아니라 재현·감사·실패 격리가 가능한 방향성 비순환 그래프(DAG)를 구현 기준으로 삼는다.

## 2. 적용 범위

- 거래 판단에 사용할 외부 정보의 수집·정규화·검증
- `Technical`, `News/Disclosure`, `Market/Sector`, `Position Risk` Scout
- Scout 결과를 종합하는 Core
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

`Guard`, `Execution Orchestrator`, `Broker`는 에이전트가 아니며 결정론적 서비스다. 어떤 에이전트에도 주문 API, Broker 자격증명, 파일시스템 또는 임의 네트워크 도구를 제공하지 않는다.

## 4. 오케스트레이션 그래프

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
| MAO-081 | SHADOW run은 운영 판단과 동일 입력을 사용할 수 있지만 실행 결과·승인·주문을 생성하지 않는다. |
| MAO-082 | 활성 후보는 고정 fixture schema 통과율 100%, 허용되지 않은 행동 0건, 증거 환각 0건, timeout·비용·지연 목표와 회귀평가를 통과해야 한다. |
| MAO-083 | `APPROVAL_ONLY` 진입은 사용자 TOTP 재인증, 변경 사유, 활성 DAG·route·prompt·model version과 시험 근거를 요구한다. 자동 주문 확대는 별도 제품 실행 단계 게이트를 다시 통과해야 한다. |

## 12. 구현 단위

권장 Backend 경계:

```text
app/agents/contracts.py       공통 schema와 enum
app/agents/orchestrator.py    DAG 계획·run 생성·stage 전이
app/agents/evidence.py        증거 정규화·검증·bundle 생성
app/agents/scouts.py          역할별 입력 조립
app/agents/core.py            Core 입력 조립·출력 검증
app/agents/worker.py          queue claim·timeout·heartbeat
app/llm/*                     Provider/Gateway 호출 계층
```

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
| MAO-106 | Agent Worker v2는 등록·검증된 Mock 또는 외부 Adapter를 `DIAGNOSTIC/SHADOW`에서 호출하고 SHADOW 경계를 유지한다. worker가 완료한 run에서도 `Decision`, `Approval`, `TradingOrder`는 생성하지 않는다. |
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
| MAO-117 | 외부 Core 응답은 현재 SHADOW 계약상 `WAIT`만 허용한다. 유효한 외부 응답은 stage output에 저장하지만 판단·승인·주문 테이블에는 복사하지 않는다. |
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
- `MAO-145`: INTEL은 활성화된 OpenDART와 KRX Adapter를 모두 실행하고 source별 결과·policy version·evidence ID를 기록한다. EvidenceBundle은 검증된 DART·KRX ID만 Scout allowlist에 포함하되 계약된 뉴스 coverage가 없으므로 계속 `PARTIAL`이다. Provider citation은 별도 `UNRATED` 후보로 유지한다.

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
