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

## 7. Core 종합 계약

| ID | 요구사항 |
| --- | --- |
| MAO-040 | Core 입력은 `scout-input-v1`, 활성 구성·프롬프트·route 버전, 필수 Scout 결과와 선택적 Scout 결과의 명시적 목록으로 고정한다. |
| MAO-041 | 단순 다수결이나 confidence 평균만으로 행동을 결정하지 않는다. Core 출력은 기존 [AI 판단 계약](AI_DECISION_SPEC.md)의 제한 행동 schema를 통과해야 한다. |
| MAO-042 | 신규매수는 모든 활성 필수 Scout가 `SUCCEEDED`이고 입력이 유효한 경우에만 `BUY` 후보가 될 수 있다. 실패·timeout·충돌은 `WAIT` 또는 `RISK_BLOCK`으로 제한한다. |
| MAO-043 | 보유 중 Scout 또는 Core 장애가 발생해도 실시간 손절·비상정지·장마감 규칙은 Guard가 독립 실행한다. |
| MAO-044 | Core는 Scout가 참조하지 않은 새 사실, 가격, 뉴스 또는 증거를 출력 근거로 추가할 수 없다. |
| MAO-045 | 동일 run에서 Core 모델이나 route를 fallback으로 변경하려면 활성 fallback 정책이 명시적으로 허용해야 하며 실제 경로를 기록한다. 기본 정책은 fail-closed다. |

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

### 12.1 Agent Runtime v1 고정 계약

이번 구현은 외부 웹·LLM을 연결하기 전 영속성·DAG·안전 경계를 검증하는 동기식 DIAGNOSTIC runtime이다.

| ID | 요구사항 |
| --- | --- |
| MAO-090 | `POST /ai/agent-runs/diagnostic`은 로그인 사용자, KRX/NXT 종목, 최신 영속 market snapshot, `dag_version=agent-dag-v1`과 명시적인 role별 route ID를 입력으로 받는다. |
| MAO-091 | route가 필요한 역할은 `TECHNICAL_SCOUT`, `NEWS_DISCLOSURE_SCOUT`, `MARKET_SECTOR_SCOUT`, `POSITION_RISK_SCOUT`, `CORE`이다. 각 route는 요청 사용자 소유, `VALIDATED`, `SHADOW`, `fallback=NONE`, 해당 role 일치, 검증된 Mock model이어야 하며 하나라도 실패하면 run을 만들지 않는다. |
| MAO-092 | `INTEL_COLLECTOR`는 외부 네트워크 없이 빈 fixture를 만들고 `EVIDENCE_VERIFIER`는 이를 `PARTIAL` 빈 bundle로 고정한다. 외부 정보 없음은 긍정 신호로 해석하지 않는다. |
| MAO-093 | 실행 순서는 Intel → Verify → 4개 Scout → Core로 고정한다. v1은 한 DB 작업 단위에서 순차 실행하지만 stage dependency를 저장해 이후 병렬 worker가 동일 DAG를 재현할 수 있어야 한다. |
| MAO-094 | 각 route stage는 Mock Adapter 호출 전 `RUNNING` invocation을 저장하고 완료 후 실제 provider/model, 입력·응답 hash, latency와 schema 검증 결과를 기록한다. 외부 Adapter나 도구 사용은 거부한다. |
| MAO-095 | Technical은 최신 Watch 지표가 없으면, News는 검증된 외부 증거가 없으면, Position Risk는 열린 position이 없으면 `INSUFFICIENT_DATA`를 출력한다. Market은 정상·최신 snapshot만 평가한다. |
| MAO-096 | Core는 필수 Scout 중 하나라도 `SUCCEEDED`가 아니면 `WAIT`만 출력한다. v1 Core는 `BUY`, 승인, 판단 실행, 주문을 생성할 수 없다. |
| MAO-097 | 멱등 key는 사용자·purpose·market·symbol·market snapshot·DAG version·정렬된 route version map의 canonical SHA-256이다. 같은 key의 재요청은 기존 run을 반환하고 stage·invocation을 추가하지 않는다. |
| MAO-098 | API는 run·stage·evidence bundle·invocation provenance를 반환하되 raw prompt·원문 provider 응답·credential은 반환하지 않는다. Console은 `DIAGNOSTIC · SHADOW · 주문 없음`을 고정 표시한다. |

### 12.2 Agent Worker v2 비동기 실행 계약

| ID | 요구사항 |
| --- | --- |
| MAO-100 | `POST /ai/agent-runs/diagnostic`은 run과 고정 DAG의 7개 `PENDING` stage를 하나의 트랜잭션으로 등록하고 즉시 반환한다. HTTP 요청 프로세스는 stage 또는 LLM 호출을 직접 실행하지 않는다. |
| MAO-101 | 별도 `agent` worker는 의존 stage가 허용된 종료 상태에 도달한 `PENDING` stage만 claim하며, claim 시 `lease_owner_id`, `lease_expires_at`, 증가하는 `fencing_token`, `attempt_count`, `timeout_at`을 원자적으로 기록한다. |
| MAO-102 | stage 완료 쓰기는 현재 `lease_owner_id`와 `fencing_token`이 모두 일치할 때만 허용한다. lease를 잃은 worker의 늦은 결과는 저장하지 않는다. |
| MAO-103 | worker 재시작 또는 lease 만료 시 외부 호출이 시작되지 않은 내부 fixture stage만 다시 `PENDING`으로 돌릴 수 있다. invocation이 생성된 stage는 자동 재전송하지 않고 `TIMED_OUT` 또는 `FAILED`로 격리한다. |
| MAO-104 | 입력 `valid_until` 또는 stage `timeout_at`을 넘긴 작업은 provider를 호출하지 않고 `TIMED_OUT`으로 종료한다. 실패한 의존성을 가진 하위 stage는 `FAILED/AGENT_DEPENDENCY_FAILED`로 종료한다. |
| MAO-105 | route·model version과 유효 generation parameter는 admission 시 `route_versions_json`에 고정한다. 실행 중 현재 ACTIVE route가 변경되어도 이미 등록된 run의 snapshot은 바뀌지 않는다. |
| MAO-106 | Agent Worker v2는 Mock Adapter만 허용하고 DIAGNOSTIC·SHADOW 경계를 유지한다. worker가 완료한 run에서도 `Decision`, `Approval`, `TradingOrder`는 생성하지 않는다. |
| MAO-107 | Console은 `CREATED/RUNNING` run이 존재하는 동안 목록을 주기적으로 갱신하고 현재 stage 상태를 표시한다. 버튼은 admission 응답 후 해제하며 전체 DAG 완료까지 HTTP 요청을 붙잡지 않는다. |

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
