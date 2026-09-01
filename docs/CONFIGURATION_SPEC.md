# 사용자 설정 및 적용 명세

## 1. 목적

Cresta의 거래·리스크·감시 정책을 Web UI에서 안전하게 설정하고, 범위·우선순위·버전·적용 시점과 충돌 검증을 일관되게 관리한다.

### LLM 출력 토큰 기본값 (2026-08-11)

- `CFG-101`: Provider가 별도 값을 제공하지 않은 신규 Model Profile과 신규 역할 배정 초안의 `max_output_tokens` 기본값은 `8192`이다. 역할의 명시적 override가 Model Profile 기본값보다 우선하며 기존 활성·버전 Route는 기본값 변경으로 자동 수정하지 않는다.
- `CFG-102`: 신규 역할 배정의 응답 제한은 120초, Flex 권장값은 300초이며 sampling·reasoning·seed는 모델 기본값을 우선한다. 사용자가 지원하지 않는 값을 명시하면 후보 검증은 fail-closed한다.
- `CFG-103`: service tier 선택지는 현재 모델 Provider가 OpenAI, LLM Gateway 또는 Vercel AI Gateway일 때만 표시한다.

## 2. 적용 범위

- 사용자 기본 설정과 종목별 재정의
- 행동별 자동·승인·비활성
- 거래시간·분석 주기·동시호가·익일 보유
- 주문 가격·미체결·재호가
- Guard 리스크·손절·비상정지·중지 정책
- 설정 초안, 검증, 활성화, 이력과 롤백

## 3. 상세 명세

### 3.1 Web UI 제공 범위

| ID | 요구사항 |
| --- | --- |
| CFG-001 | 사용자가 사용하는 모든 거래 정책은 Web UI에서 조회할 수 있어야 한다. |
| CFG-002 | 거래 행동, 세션, 주문, 익일 보유와 Guard 정책을 Web UI에서 수정할 수 있어야 한다. |
| CFG-003 | 키움 비밀값은 일반 설정 화면에서 조회·수정하지 않고 별도 보안 절차를 사용한다. |
| CFG-004 | 변경 불가 무결성 규칙은 상태만 표시하고 수정 컨트롤을 제공하지 않는다. |

Web UI 설정 영역:

```text
거래 실행 방식
거래·감시 시간
주문 가격 및 미체결
익일 보유 및 장 마감
투자 한도
손실·손절
데이터·연결 위험
비상정지 정책
종목별 설정
설정 변경 이력
```

#### 거래 캘린더 운영 휴장

- 임시 휴장은 날짜 단위 전역 운영 설정이며 기본 공휴일 캘린더보다 우선한다.
- 첫 버전은 `OPERATIONAL_CLOSURE`만 허용하고 강제 개장과 특별 세션 시간 변경은 허용하지 않는다.
- 생성과 해제는 append-only 감사 이력으로 보존하며 이미 완료된 평가에는 소급 적용하지 않는다.

### 3.2 설정 범위와 우선순위

```text
변경 불가 시스템 안전규칙
→ 종목별 재정의
→ 사용자 기본 설정
→ 시스템 기본값
```

| ID | 요구사항 |
| --- | --- |
| CFG-010 | 각 설정 필드는 적용 범위 `SYSTEM_DEFAULT`, `USER_DEFAULT`, `SYMBOL_OVERRIDE`를 가진다. |
| CFG-011 | 종목별 재정의를 제거하면 사용자 기본값으로 자동 복귀한다. |
| CFG-012 | 최종 적용값과 그 값의 출처를 Web UI에 함께 표시한다. |
| CFG-013 | 판단·승인·주문에는 사용한 최종 설정 버전을 기록한다. |

### 3.3 설정 생명주기

```text
DRAFT
→ VALIDATED
→ SCHEDULED 또는 ACTIVE
→ SUPERSEDED

검증 실패 시 REJECTED
```

| ID | 요구사항 |
| --- | --- |
| CFG-020 | 편집 중 값은 `DRAFT`로 저장할 수 있고 거래에 적용하지 않는다. |
| CFG-021 | 서버 검증을 통과한 버전만 활성화할 수 있다. |
| CFG-022 | 설정 활성화는 새 불변 버전을 만들고 기존 버전을 수정하지 않는다. |
| CFG-023 | 사용자는 이전 버전과 차이를 비교하고 새 버전으로 롤백할 수 있어야 한다. |
| CFG-024 | 롤백도 새로운 설정 변경으로 기록하고 검증·확정 절차를 거친다. |

### 3.4 적용 시점

지원 적용 시점:

- `IMMEDIATE`: 검증·확정 직후 적용
- `NEXT_ENTRY`: 해당 종목의 다음 신규 포지션부터 적용
- `NEXT_TRADING_DAY`: 다음 거래일 장 전 점검부터 적용
- `SCHEDULED`: 사용자가 지정한 거래일·시각에 적용

| ID | 요구사항 |
| --- | --- |
| CFG-030 | 설정 종류별 허용 적용 시점을 서버가 제한한다. |
| CFG-031 | 현재 포지션에 영향을 주는 손절 변경은 `IMMEDIATE`와 `NEXT_ENTRY`를 구분한다. |
| CFG-032 | 거래시간·분석 주기 변경은 현재 실행 중 작업의 다음 안전 경계부터 적용한다. |
| CFG-033 | 한도 완화·손실 제한 완화·중지 해제의 즉시 적용은 재인증을 요구한다. |
| CFG-034 | 예약 설정은 적용 직전에 다시 검증한다. |

### 3.5 검증과 영향 미리보기

활성화 전에 다음 영향을 계산한다.

- 현재 포지션이 새 투자한도를 초과하는지
- 현재가가 새 손절가를 이미 통과했는지
- 미체결 주문이 새 주문시간·가격정책과 충돌하는지
- 익일 보유와 장 마감 청산 설정이 모순되는지
- 자동·승인·비활성 설정이 기존 승인 요청을 무효화하는지
- 모의투자에서 지원하지 않는 NXT·SOR 동작을 요구하는지

| ID | 요구사항 |
| --- | --- |
| CFG-040 | 클라이언트 검증과 무관하게 서버가 전체 설정 조합을 검증한다. |
| CFG-041 | 영향 미리보기에는 경고, 차단 오류와 즉시 발생 가능한 행동을 구분한다. |
| CFG-042 | 미리보기 이후 계좌·포지션·설정 버전이 바뀌면 확정을 거부하고 다시 계산한다. |
| CFG-043 | 모순 설정은 경고만 표시하지 않고 활성화를 차단한다. |

### 3.6 동시 수정

| ID | 요구사항 |
| --- | --- |
| CFG-050 | 설정 버전 또는 ETag를 사용해 낙관적 동시성 검사를 수행한다. |
| CFG-051 | 오래된 화면의 저장 요청은 `CONFIG_VERSION_CONFLICT`로 거부한다. |
| CFG-052 | 설정 활성화와 주문 전 Guard 검사가 경쟁하면 주문은 활성 설정 버전을 다시 읽고 검사한다. |

### 3.7 감사와 표시

모든 설정 변경에 다음 정보를 기록한다.

```yaml
configuration_change:
  configuration_version:
  scope:
  target_symbol:
  changed_by:
  changed_at:
  effective_at:
  apply_mode:
  before:
  after:
  impact_summary:
  reason:
  correlation_id:
```

비밀값은 `before`와 `after`에 저장하지 않는다.

변경 사유는 자동매매 활성화, 투자·손실 한도 완화, 손절 완화·비활성화, 익일 보유 허용, 비상정지 해제와 자격증명 관련 변경에서 필수다. 그 외 변경은 선택 입력이다. 예약 적용은 현재 시점부터 최대 30일 이내로 제한한다.

| ID | 요구사항 |
| --- | --- |
| CFG-060 | 위험 완화 또는 자동화 확대 변경은 공백이 아닌 변경 사유를 요구한다. |
| CFG-061 | 예약 적용은 최대 30일 이내로 제한하고 거래 캘린더상 유효한 시각인지 검증한다. |

## 4. 오류·예외 또는 경계 조건

- Web UI 연결이 끊겨도 확정 응답을 받지 못한 설정을 사용자가 성공으로 오해하지 않도록 다시 조회한다.
- 동일 요청 재전송은 idempotency key로 하나의 설정 버전만 생성한다.
- 예약 적용 시 서버가 중지 상태여도 설정 이력은 보존하며 실제 활성화 성공 여부를 기록한다.
- 종목별 설정 대상 종목이 감시목록에서 제거돼도 과거 판단 재현을 위해 버전을 삭제하지 않는다.
- 설정값이 범위를 벗어나면 자동으로 보정하지 않고 사용자가 수정할 수 있도록 거부한다.

## 5. 검증·인수 조건

- 모든 거래·Guard 정책을 Web UI에서 조회하고 수정할 수 있다.
- 설정 우선순위와 최종값 출처가 정확히 표시된다.
- 초안은 거래에 반영되지 않고 활성 버전만 적용된다.
- 현재 포지션에 영향을 주는 변경의 미리보기가 제공된다.
- 버전 충돌, 모순 설정과 오래된 영향 미리보기는 서버에서 거부된다.
- 설정 롤백과 예약 적용도 감사 가능한 새 버전으로 남는다.
- 판단과 주문 기록에서 사용된 설정 버전을 조회할 수 있다.

## 6. 미결정·보류 항목

첫 버전은 사용자 기본값과 종목별 재정의만 지원하고 전략 템플릿 계층은 제외한다. 설정 화면은 영역별 탭과 위험 변경 확인 단계로 구성하며 상세 반응형 기준은 Web UI 명세를 따른다.

### 6.1 실행 권한 설정 1차 구현 계약

| ID | 요구사항 |
| --- | --- |
| CFG-070 | 첫 구현은 `USER_DEFAULT / EXECUTION_POLICY` 범위에서 매수·부분매도·전량매도·목표수익·고정손절·추적손절·장마감청산·긴급청산 8개 행동을 각각 `AUTOMATIC`, `MANUAL_APPROVAL`, `DISABLED`로 저장한다. |
| CFG-071 | 초기 활성 버전이 없을 때 제품 요구사항의 안전 기본값을 조회 결과로 제공하되 DB 활성 버전으로 오인하지 않는다. |
| CFG-072 | 변경은 `DRAFT → VALIDATED → ACTIVE` 순서로만 진행하고 활성화 시 기존 활성 버전은 `SUPERSEDED`가 된다. |
| CFG-073 | 실행 권한 활성화는 변경 사유와 대상 버전에 결합된 `EXECUTION_POLICY_ACTIVATE` TOTP 재인증 증명을 요구한다. |
| CFG-074 | AI·Guard·주문 생성기는 활성 실행 권한 버전 ID와 최종 행동 모드를 함께 기록할 수 있어야 하며 초안이나 검증 버전을 사용하지 않는다. |

### 6.2 Guard 1차 위험 설정 계약

| ID | 요구사항 |
| --- | --- |
| CFG-080 | 첫 Guard 구현은 `USER_DEFAULT / RISK_POLICY`에 `entry_order_amount`, 1회·종목·전체 한도, 최대 보유종목·일일진입, 고정손절률, 시세 지연, 최대 spread와 가격편차를 저장한다. |
| CFG-081 | `entry_order_amount`는 최소 10,000원이며 `entry_order_amount ≤ max_single_order_amount ≤ max_position_amount_per_symbol ≤ max_total_position_amount`를 위반하면 활성화를 거부한다. |
| CFG-082 | 위험 설정이 활성화되지 않은 상태에서 조회용 시스템 안전 기본값은 제공할 수 있지만 `entry_order_amount` 기본값은 제공하지 않는다. 사용자가 값을 포함한 위험 설정을 활성화하기 전에는 신규매수를 차단한다. |
| CFG-083 | 판단 실행과 Guard 평가는 사용한 risk policy version ID와 각 최종값의 출처를 저장한다. |
| CFG-084 | 실행 단계 `SHADOW | APPROVAL_ONLY | MOCK_AUTOMATIC`은 별도 시스템 gate이며 일반 실행 권한보다 우선한다. 단계 확대는 TOTP 재인증, 변경 사유와 요구 시험 근거를 요구한다. |
| CFG-085 | 행동별 `AUTOMATIC` 설정은 현재 ExecutionStage가 해당 행동의 승인 없는 실행을 허용할 때만 효력이 있다. `FIXED_STOP`을 포함한 Guard trigger의 단계별 실행 의미는 `DECISION_EXECUTION_SPEC.md` EXE-211~213을 따른다. |

### 6.3 에이전트·모델 route 설정 계약

| ID | 요구사항 |
| --- | --- |
| CFG-090 | 사용자는 Web UI에서 agent 역할별 primary model과 `FAIL_STOP` 또는 `FAILOVER`를 설정한다. 기본값은 `FAIL_STOP`이며 `FAILOVER`에는 검증된 예비 모델을 최대 1개만 지정할 수 있다. |
| CFG-091 | Provider credential은 일반 설정 payload에 포함하지 않고 별도 write-only 보안 흐름으로 관리한다. |
| CFG-092 | DAG, prompt, model profile과 role route는 버전·적용 범위·활성 상태를 가지며 판단에 사용된 최종 version ID를 저장한다. |
| CFG-093 | 역할의 `FAILOVER` 활성화, 외부 데이터 전송 확대, provider endpoint 변경과 route 활성화는 영향 미리보기와 변경 사유를 요구한다. 현재 개발 단계의 인증은 CFG-DEV-001~002를 따른다. |
| CFG-094 | model discovery 결과나 provider 연결 성공은 route 활성화로 간주하지 않으며 schema fixture와 회귀평가를 통과한 `VALIDATED` route만 활성화할 수 있다. |
| CFG-095 | 새 agent·provider·model·prompt는 SHADOW가 기본이며 실행 권한의 `AUTOMATIC` 설정만으로 활성 판단 경로로 승격되지 않는다. |
| CFG-096 | 모델 등록과 역할 배정을 분리한다. 등록된 하나의 model profile을 여러 역할에서 선택할 수 있으며 역할마다 같은 model profile을 복제 생성하지 않는다. |
| CFG-097 | 역할 설정은 현재 선택 모델과 역할별 생성 파라미터 override를 한 구성으로 저장하며 최종 적용값과 `MODEL_DEFAULT`, `ROLE_OVERRIDE`, `ADAPTER_DEFAULT` 출처를 표시한다. |
| CFG-098 | 역할별 현재 활성 배정은 하나만 허용한다. 새 배정 활성화는 기존 배정을 `SUPERSEDED`로 보존하고 실행 중 run은 시작할 때 고정한 이전 version을 계속 사용한다. |
| CFG-099 | `temperature`, `top_p`, `max_output_tokens`, `reasoning_effort`, `seed`는 선택 모델이 검증한 capability와 범위 안에서만 설정한다. 미지원 파라미터는 UI에서 비활성화하고 API에서도 거부한다. |
| CFG-100 | 중복 `VALIDATED` route가 존재하면 자동 선택하지 않고 현재 배정 미확정으로 표시한다. 사용자가 하나를 선택해 활성화하기 전까지 해당 역할의 운영 호출은 차단한다. |

### 6.4 v7 ENTRY Activation Gate 계약

`V7_ENTRY_ACTIVATION`은 별도 실행 mode가 아니라 system-owned
`ConfigurationVersion`이다. 첫 배포의 identity는 `scope=SYSTEM`,
`target_id=MOCK`, `category=V7_ENTRY_ACTIVATION`이고 payload schema는
`activation-gate-v1`이다. `ACTIVE`는 선택 가능한 설정 version이라는 뜻이고 payload의
`gate_state=OPEN`은 해당 version이 Finalizer admission을 허가한다는 뜻이므로 두 상태를
합치지 않는다.

`activation-gate-v1`의 exact top-level field는 다음 아홉 개뿐이며 모두 required다.
unknown field와 implicit default를 거부한다.

```yaml
schema_version: activation-gate-v1
gate_state: OPEN | CLOSED
target: MOCK
version_snapshot:
  dag_version: agent-dag-v7
  decision_context_schema_version: decision-context-v1
  decision_agent_result_schema_version: decision-agent-result-v1
  arbiter_result_schema_version: entry-consensus-v1
  consensus_policy_version: consensus-policy-v1
  policy_profiles: []
  routes: []
version_snapshot_hash: <64 lowercase hex>
safety_evidence: []
validation_policy_version: activation-validation-policy-v1
validated_at: <UTC timestamp>
valid_until: <UTC timestamp>
```

`version_snapshot`도 위 일곱 field만 허용한다. `policy_profiles`는 정확히 세 항목이며
`CONSERVATIVE`, `BALANCED`, `AGGRESSIVE` 순서다. item exact field는
`configuration_version_id`, `category`, `sequence`, `agent_type`, `payload_hash`이고 category와
agent type은 각각 `V7_ENTRY_POLICY_CONSERVATIVE/CONSERVATIVE`,
`V7_ENTRY_POLICY_BALANCED/BALANCED`, `V7_ENTRY_POLICY_AGGRESSIVE/AGGRESSIVE`로 일대일이다.
`sequence`는 1 이상의 integer, hash는 64자리 lowercase hex다.

`routes`는 `TECHNICAL_SCOUT`, `NEWS_DISCLOSURE_SCOUT`, `MARKET_SECTOR_SCOUT`,
`POSITION_RISK_SCOUT`, `CONSERVATIVE_DECISION`, `BALANCED_DECISION`,
`AGGRESSIVE_DECISION` 순서의 정확히 일곱 항목이다. item exact field는 `role`, `route_id`,
`route_version`, `route_version_hash`, `model_id`, `model_version`,
`fallback_model_id`, `fallback_model_version`,
`prompt_profile_id`, `prompt_version`, `prompt_content_hash`다. `route_id`와 `model_id`는
UUID string, `route_version`과 `model_version`은 1 이상의 integer,
`prompt_version`은 non-empty string, `route_version_hash`는 64자리 lowercase hex다.
fallback을 쓰지 않는 route는 `fallback_model_id`와 `fallback_model_version`이 모두 null이고,
쓰는 route는 각각 UUID string과 1 이상의 integer다.
`prompt_profile_id`와 `prompt_content_hash`는 둘 다 non-null 또는 둘 다 JSON null이다.
non-null `prompt_profile_id`는 UUID string이고 `prompt_content_hash`는 64자리 lowercase hex다.
Scout route는 profile이 없을 때 이 두 field만 null일 수 있고 `prompt_version`은 유지한다.
Decision role에서는 세 prompt field가 모두 non-null이어야 한다. 부분 provenance와 대표
model/prompt 합성은 금지한다.

`safety_evidence` item은 다음 exact field를 모두 가진다.

```yaml
test_id: <non-empty string>
requirement_ids: [<non-empty string>]
result: PASSED
code_revision: <40 lowercase hexadecimal Git commit SHA>
test_plan_version: <non-empty string>
spec_version: <non-empty string>
executed_at: <UTC timestamp>
valid_until: <UTC timestamp> | null
freshness_contract: <non-empty string> | null
evidence_ref: <non-empty string>
evidence_hash: <64 lowercase hex>
```

`requirement_ids`는 중복 없는 문자열 오름차순이며 비어 있을 수 없다. `valid_until`과
`freshness_contract`는 둘 중 정확히 하나만 non-null이어야 한다. `result`는 `PASSED`만
허용하고 boolean `passed`, 누락 시 pass, `FAILED` evidence의 OPEN payload 포함을 모두
금지한다. `safety_evidence`는 `test_id` 오름차순이고 중복 test ID가 없어야 하며
`AI_DECISION_SPEC.md` 7.11.1의 activation acceptance set을 빠짐없이 포함한다. 각 evidence는
payload 검증 시 identity/version 대상 일치, evidence hash, 실행시각과 명시된
validity/freshness를 모두 통과해야 한다. `evidence_hash`는 `evidence_ref`가 가리키는
불변 시험 report artifact bytes의 SHA-256이다. `freshness_contract`는
`activation-validation-policy-v1`에 등록된 versioned contract ID여야 하며 unknown ID는
invalid다. 현재 Activation artifact의 `EXACT_REVISION`은 wall-clock duration이 아니라
code/test-plan/spec/migration/environment/required-set authority exact match로 평가한다. direct
`valid_until`과 미래의 명시적 time-based contract만 DB-authoritative time을 평가한다.

Canonical JSON은 UTF-8, `ensure_ascii=false`, key 사전순, 불필요한 공백 없는 separator를
사용한다. timestamp는 UTC ISO-8601 `+00:00`로 정규화하고 0인 소수 초는 생략한다. null은
명시적 JSON null로 보존한다. `version_snapshot_hash`는 해당 field를 제외할 필요가 없는
독립 `version_snapshot` object의 canonical bytes SHA-256이고, ConfigurationVersion
`payload_hash`는 `version_snapshot_hash`를 포함한 payload 전체 canonical bytes의 SHA-256이다.
목록은 위 고정 순서를 사용하므로 입력 순서를 자동 의미로 받아들이지 않는다.
`validated_at < valid_until`이어야 하며 Gate validator의 DB-authoritative time이
`valid_until` 이상이면 OPEN으로 평가하지 않는다.

`safety_evidence`가 참조하는 독립 artifact의 body, `sha256:<64hex>` namespace,
content-addressed filesystem store, publisher, exact revision·migration·environment binding과
production resolver의 단일 상세 기준은 [Activation Evidence Artifact 및 Resolver 명세](ACTIVATION_EVIDENCE_SPEC.md)다.
Gate API는 artifact를 생성·수정·삭제하지 않으며, deployment가 기대하는 authority와 resolved
artifact body를 exact 비교하지 않고 evidence item끼리 일치하는지만 확인해서는 OPEN할 수 없다.
현재 `EXACT_REVISION` freshness는 artifact에 적용되고 이 payload의 `valid_until`은 별도의
Gate lifecycle expiry로 계속 적용한다.

Gate outcome precedence는 deterministic하다. DB read/lock failure는 retryable
infrastructure failure다. ACTIVE ambiguity 또는 selected payload의 schema/canonical/hash
오류는 `ACTIVATION_GATE_INVALID`다. ACTIVE row 부재 또는 structurally valid payload의
`gate_state=CLOSED`는 `ACTIVATION_GATE_CLOSED`다. OPEN payload의 target/version/evidence/
freshness/validity 오류는 `ACTIVATION_GATE_INVALID`다. Finalizer에서 여기까지 유효한 current
OPEN Gate의 ID/hash가 frozen ID/hash와 다르면 `ACTIVATION_GATE_SUPERSEDED`이고, 같을 때만
PASS다. 따라서 새 CLOSED version이 frozen Gate와 달라도 CLOSED가 supersession보다 먼저다.

| ID | 요구사항 |
| --- | --- |
| CFG-104 | `activation-gate-v1`은 위 exact field, enum, nullability, ordering과 canonical hash 규칙을 적용하고 unknown/missing/default field를 거부한다. |
| CFG-105 | Gate state는 `OPEN | CLOSED`, target은 현재 `MOCK`만 허용하며 Gate state를 Decision action 또는 ExecutionStage mode로 변환하지 않는다. |
| CFG-106 | safety evidence는 required acceptance set 전체의 identity/version, PASSED, hash와 freshness를 검증하며 boolean pass 또는 누락·malformed·stale evidence를 OPEN으로 해석하지 않는다. |
| CFG-107 | TRADING admission은 같은 transaction snapshot에서 정확히 한 `ACTIVE + OPEN` Gate를 선택하고 그 ConfigurationVersion ID와 payload hash를 AgentRun에 고정한다. 부재·중복·CLOSED·invalid는 fail-closed 한다. |
| CFG-108 | Finalizer는 frozen historical Policy와 달리 Gate를 live safety control로 취급해 현재 ACTIVE ID/hash가 frozen Gate와 같고 계속 OPEN·유효한지 write boundary에서 재검증한다. supersession 또는 closure 시 기존 run을 새 Gate로 승격하지 않는다. |
| CFG-109 | Gate validation policy와 payload는 사용자 임의 설정이 아니며 system-owned control plane만 생성·검증·활성화한다. LIVE target은 이 schema version에서 지원하지 않는다. |
| CFG-110 | ACTIVE lifecycle 변경과 payload hash는 기존 ConfigurationVersion 불변·원자 활성화·감사 계약을 재사용하고 기존 payload를 수정해 OPEN/CLOSED를 바꾸지 않는다. |
| CFG-111 | Activation Gate는 immutable Decision 생성 허가만 제어하며 `SHADOW | APPROVAL_ONLY | MOCK_AUTOMATIC` 또는 행동별 execution mode를 포함하거나 대체하지 않는다. |

### 6.5 v7 ENTRY ExecutionStage control-plane 계약

Sourced ENTRY execution의 authoritative current stage는 기존 `ConfigurationVersion`을
재사용하는 system-owned control-plane이다. identity는 `scope=SYSTEM`, `target_id=MOCK`,
`category=V7_ENTRY_EXECUTION_STAGE`이고 payload schema는 `execution-stage-control-v1`이다.
process `Settings.execution_stage`는 legacy/bootstrap 호환값일 뿐 sourced authority가 아니다.

Payload는 다음 exact 일곱 field만 가지며 모두 required다.

```yaml
schema_version: execution-stage-control-v1
stage: SHADOW | APPROVAL_ONLY | MOCK_AUTOMATIC
target: MOCK
validation_policy_version: execution-stage-validation-policy-v1
safety_evidence: []
validated_at: <UTC timestamp>
valid_until: <UTC timestamp>
```

Unknown/missing/default field, non-MOCK target, `validated_at >= valid_until`, DB-authoritative
time 기준 expired payload를 거부한다. canonical JSON/timestamp/null/hash 규칙은 CFG-104의
공통 canonicalization 규칙을 사용하되 Activation Gate의 version snapshot이나 acceptance
set을 복사하지 않는다. ConfigurationVersion `payload_hash`는 위 전체 payload canonical
bytes의 SHA-256 lowercase hex다.

`safety_evidence` item의 exact field는 `test_id`, `requirement_ids`, `result`,
`code_revision`, `test_plan_version`, `executed_at`, `valid_until`, `freshness_contract`,
`evidence_ref`, `evidence_hash`다. `result=PASSED`, non-empty immutable build/revision/report
identity와 64 lowercase hex evidence hash를 요구한다. `requirement_ids`는 중복 없는 문자열
오름차순이고, `valid_until`과 registered `freshness_contract` 중 정확히 하나만 non-null이다.
목록은 test_id 오름차순이며 boolean shortcut, unknown freshness contract와 stale/malformed
evidence를 거부한다.

Stage별 required acceptance set은 다음과 같다.

| stage | required evidence |
| --- | --- |
| SHADOW | 빈 목록 허용. strict payload·ACTIVE exact-one·expiry 검증은 생략하지 않는다. |
| APPROVAL_ONLY | `T-V2-EXE-AUTH-001`, `T-V2-EXE-AUTH-002`, `T-V2-EXE-AUTH-003`, `T-V2-EXE-AUTH-004`, `T-V2-EXE-AUTH-005`, `T-V2-EXE-AUTH-006`, `T-V2-EXE-AUTH-007`, `T-V2-EXE-AUTH-008`, `T-V2-EXE-AUTH-011`, `T-V2-EXE-AUTH-012`, `T-V2-EXE-AUTH-015`, `T-V2-EXE-AUTH-016` |
| MOCK_AUTOMATIC | `T-V2-EXE-AUTH-001`, `T-V2-EXE-AUTH-002`, `T-V2-EXE-AUTH-003`, `T-V2-EXE-AUTH-004`, `T-V2-EXE-AUTH-005`, `T-V2-EXE-AUTH-006`, `T-V2-EXE-AUTH-007`, `T-V2-EXE-AUTH-008`, `T-V2-EXE-AUTH-009`, `T-V2-EXE-AUTH-010`, `T-V2-EXE-AUTH-011`, `T-V2-EXE-AUTH-012`, `T-V2-EXE-AUTH-013`, `T-V2-EXE-AUTH-014`, `T-V2-EXE-AUTH-015`, `T-V2-EXE-AUTH-016` |

Stage authority는 `SHADOW < APPROVAL_ONLY < MOCK_AUTOMATIC`이다. DecisionExecution 생성 시
selected version ID/hash/stage를 freeze하지만 current ACTIVE stage는 Approval 생성·승인,
Order 생성과 broker pre-send마다 다시 읽는다. effective authority는 frozen/current 중 더
낮은 값이다. ACTIVE version ID가 바뀌어도 새 payload가 strict valid이면 authority level로
비교하고, current stage의 부재·복수 ACTIVE·malformed·expired·evidence invalid는
`EXECUTION_STAGE_UNAVAILABLE`로 fail-closed한다. 더 permissive한 current version은 기존
execution을 자동 승격하지 않는다.

| ID | 요구사항 |
| --- | --- |
| CFG-112 | sourced ENTRY current stage identity는 `SYSTEM / MOCK / V7_ENTRY_EXECUTION_STAGE` ConfigurationVersion exact-one ACTIVE이며 `execution-stage-control-v1` 외 payload와 Settings 기반 authority를 거부한다. |
| CFG-113 | stage payload는 위 exact 일곱 field, 세 stage, MOCK target, UTC validity와 canonical hash를 사용하고 unknown/missing/default/expired 값을 거부한다. |
| CFG-114 | stage safety evidence는 위 exact item shape와 stage별 required acceptance set을 사용한다. boolean pass, stale evidence, unknown freshness contract와 incomplete set은 authority를 부여하지 않는다. |
| CFG-115 | stage authority 순서는 `SHADOW < APPROVAL_ONLY < MOCK_AUTOMATIC`이며 execution은 selected version ID/hash/stage를 freeze하고 current와 frozen의 minimum만 사용한다. |
| CFG-116 | current stage supersession은 ID 변경만으로 기존 execution을 cancel하지 않지만 more-permissive stage로 자동 승격하지 않는다. same/lower valid stage는 current safety authority로 즉시 적용한다. |
| CFG-117 | current stage 부재·ACTIVE ambiguity·schema/hash/evidence/validity 오류는 `EXECUTION_STAGE_UNAVAILABLE`로 fail-closed하며 APPROVAL_ONLY/MOCK_AUTOMATIC default를 합성하지 않는다. |
| CFG-118 | action policy도 selected version ID와 effective mode를 freeze하고 current mode가 더 restrictive하면 `DISABLED < MANUAL_APPROVAL < AUTOMATIC` 순서의 minimum을 적용한다. current 완화는 기존 execution을 확대하지 않는다. |
| CFG-119 | Risk Policy는 initial version을 freeze하고 later authority boundary에서 frozen/current 정책을 모두 평가한다. current 완화는 수량·한도·권한을 확대하지 않고 어느 한쪽의 block도 우회하지 않는다. |
| CFG-120 | LIVE stage/target은 이 schema에 없고 MOCK_AUTOMATIC activation은 full required evidence, 변경 사유, version conflict 검사와 적용되는 재인증 정책을 모두 통과해야 한다. |

### 6.6 금융 authority freshness 설정 계약

| ID | 요구사항 |
| --- | --- |
| CFG-121 | versioned `USER_DEFAULT / RISK_POLICY` payload의 canonical financial freshness field는 integer `account_funds_stale_seconds`와 `order_capacity_stale_seconds`다. boolean·negative·0·null·unlimited 값은 허용하지 않는다. |
| CFG-122 | `account_funds_stale_seconds`의 canonical default는 30초이고 허용범위는 1..300 inclusive다. `order_capacity_stale_seconds`의 canonical default는 10초이고 허용범위는 1..60 inclusive다. |
| CFG-123 | 신규 field가 없는 기존 valid Risk Policy payload는 canonical read/validation 시 각각 30과 10을 적용한다. missing을 infinite·0 또는 `quote_stale_seconds`로 해석하지 않으며 세 TTL은 독립이다. |
| CFG-124 | 기존 configuration integer parser가 exact integer representation만 명시적으로 정규화하는 경우를 제외하고 float는 거부한다. 범위 밖 값을 clamp하거나 저장된 payload를 묵시적으로 수정하지 않는다. |
| CFG-125 | execution이 freeze한 Risk Policy와 current authoritative Risk Policy의 각 금융 TTL을 모두 검증하고 effective 값은 field별 minimum이다. current looser value는 authority를 확대하지 않고 current stricter value는 즉시 적용한다. |
| CFG-126 | current authoritative Risk Policy의 schema/field/range가 invalid하거나 exact-one ACTIVE selection이 실패하면 financial authority를 fail-closed한다. DB/transient lookup failure는 retryable infrastructure failure로 분류한다. |

### 6.7 sourced ENTRY handoff runtime activation

| ID | 요구사항 |
| --- | --- |
| CFG-127 | process setting `CRESTA_V7_SOURCED_HANDOFF_ENABLED`는 sourced handoff worker lifecycle만 제어하는 non-secret boolean이며 기본값은 `false`다. DB ConfigurationVersion이나 migration으로 만들지 않는다. |
| CFG-128 | false 또는 unset이면 worker는 sweep 없이 정상 종료한다. true일 때만 committed eligible sourced ENTRY Decision을 기존 reconciliation helper로 polling한다. |
| CFG-129 | boolean은 Pydantic Settings의 strict configuration parsing을 사용한다. malformed 값은 startup configuration failure이며 true로 추정하거나 fallback하지 않는다. |
| CFG-130 | 이 flag는 Activation Gate, ExecutionStage, Execution/Risk Policy, TradingGate, PAUSE_ENTRY, Approval, BrokerLease 또는 LIVE authority를 대체·완화하지 않는다. 활성화 자체로 Stage/Gate/Policy/Order를 seed하지 않는다. |
| CFG-131 | 별도 high-frequency setting을 추가하지 않고 기존 `CRESTA_AGENT_WORKER_POLL_SECONDS`(기본 1초, 1..10초)를 handoff cadence로 재사용한다. cadence는 authority semantic이 아니다. |

### 6.8 Phase 11A deployment configuration boundary

| ID | 요구사항 |
| --- | --- |
| CFG-132 | deploy `.env.example`은 `Settings`의 non-secret/defaulted runtime key와 secret file 경로를 분류해 유지한다. password, token, API key, account secret과 완성된 credential URL은 포함하지 않는다. |
| CFG-133 | Compose 기본값은 `CRESTA_ENVIRONMENT=MOCK`, `CRESTA_LIVE_TRADING_ENABLED=false`, `CRESTA_V7_SOURCED_HANDOFF_ENABLED=false`다. startup은 Activation Gate, ExecutionStage 또는 trading policy를 생성·seed하지 않는다. |
| CFG-134 | PostgreSQL password, TOTP encryption key, Kiwoom MOCK credential과 external provider key는 Docker secret/read-only file로만 주입한다. direct secret environment field는 example과 Compose에 두지 않는다. |
| CFG-135 | migration one-shot도 동일 `Settings.validate_safety()`와 password-file URL resolution을 사용하므로 malformed safety config와 non-MOCK/LIVE endpoint는 migration 실패로 runtime startup을 차단한다. |
| CFG-136 | Activation evidence resolver의 server-owned key는 `CRESTA_ARTIFACT_ROOT`이며 API에는 해당 directory가 read-only mount된다. unset·빈 값·부재·non-directory이면 Gate OPEN create/validate/activate가 fail-closed하고 cwd/repository/tmp fallback은 없다. |
| CFG-137 | deployed code authority는 `CRESTA_DEPLOYED_REVISION`의 exact 40-character lowercase Git SHA다. unset·malformed 값은 application 전체를 추정값으로 시작시키지 않고 Activation evidence authority만 unavailable로 유지하며 branch/tag/image `latest` 또는 runtime `git` 조회로 대체하지 않는다. |

### 개발 단계 설정 인증 정책

| ID | 요구사항 |
| --- | --- |
| CFG-DEV-001 | 현재 개발 단계의 모든 설정 생성·검증·활성화는 로그인된 세션과 CSRF로 인가하며 별도 TOTP 재인증을 요구하지 않는다. |
| CFG-DEV-002 | CFG-033·073·084·093의 TOTP 재인증 부분은 서비스 완성 후 고위험 설정을 다시 분류할 때까지 보류한다. 버전·변경 사유·검증·원자 활성화 요구는 그대로 유지한다. |
