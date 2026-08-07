# Cresta LLM Provider 및 Gateway 명세

## 1. 목적

OpenAI, Anthropic, Google Gemini의 공식 API와 Vercel AI Gateway, OpenAI 호환 Gateway, Ollama를 Cresta의 동일한 구조화 판단 계약으로 호출하기 위한 Adapter, 모델 기능, 라우팅, 실패 처리, 비밀 관리와 운영 기준을 정의한다.

## 2. 적용 범위

- Provider·Gateway·Model profile과 역할별 route
- 공식 API와 호환 API Adapter
- 구조화 출력, tool, web search 등 모델별 capability
- 인증, timeout, 재시도, fallback, 비용·사용량 제한
- 호출 결과 정규화·감사·UI 설정
- 연결 시험, SHADOW 검증과 활성화

## 3. 참고자료와 적용 원칙

### 3.1 공식 규격 확인

2026-08-05 확인 기준:

- OpenAI는 Responses API와 tool/web search를 제공한다: <https://platform.openai.com/docs/quickstart/make-your-first-api-request>
- Anthropic Claude API는 Messages와 구조화 출력을 제공한다: <https://platform.claude.com/docs/en/build-with-claude/structured-outputs>
- Gemini API는 JSON Schema 기반 구조화 출력을 제공한다: <https://ai.google.dev/gemini-api/docs/structured-output>
- Vercel AI Gateway는 통합 endpoint, provider routing과 fallback을 제공한다: <https://vercel.com/docs/ai-gateway>
- Ollama는 OpenAI 호환 API와 로컬 구조화 출력을 제공하지만 지원 endpoint·필드는 부분 호환이다: <https://docs.ollama.com/api/openai-compatibility>, <https://docs.ollama.com/capabilities/structured-outputs>

외부 API 기능은 변경 가능하므로 구현·업그레이드 때 공식 문서를 다시 확인하고 확인일과 Adapter contract fixture를 갱신한다.

### 3.2 Provider Manager 참고 범위

`C:\Users\Jae\Documents\APIchat\provider-manager-v1.10.0.js`는 다음 설계 개념의 참고자료다.

- key/key group, model/model group, router
- provider별 endpoint, header, body override
- OpenAI Chat/Responses, Anthropic Messages, Gemini/Vertex, Bedrock 구분
- retry, proxy, web search, thinking, tool, batch, cache 옵션

이 파일은 압축된 브라우저 플러그인 번들이며 일부 template을 외부 registry에서 조회한다. Cresta는 해당 파일을 runtime dependency로 사용하거나 코드를 복사하지 않는다. 코드 재사용이 필요하면 별도 라이선스·출처·보안 검토를 먼저 수행한다.

## 4. Provider 분류와 Adapter

| Adapter type | 용도 | 기본 endpoint 형태 |
| --- | --- | --- |
| `MOCK` | 외부 통신 없는 contract·UI·route 검증 | 없음 |
| `OPENAI_RESPONSES` | OpenAI 공식 Responses API | `/v1/responses` |
| `ANTHROPIC_MESSAGES` | Anthropic 공식 Messages API | `/v1/messages` |
| `GEMINI_GENERATE_CONTENT` | Gemini 공식 API | provider 공식 model endpoint |
| `VERCEL_AI_GATEWAY` | Vercel Gateway 경유 | Gateway가 제공하는 OpenAI/Anthropic 호환 endpoint |
| `OPENAI_COMPATIBLE` | 승인된 범용 Gateway | 사용자 지정 base URL + 허용 path |
| `OLLAMA_NATIVE` | 로컬 Ollama | `/api/chat` |
| `OLLAMA_OPENAI_COMPATIBLE` | Ollama OpenAI 호환 | `/v1/chat/completions` 또는 지원되는 `/v1/responses` |

| ID | 요구사항 |
| --- | --- |
| LLM-001 | 공식 API는 가능한 경우 native Adapter를 사용하고 `OPENAI_COMPATIBLE` 하나로 모든 provider 차이를 숨기지 않는다. |
| LLM-002 | Gateway는 provider가 아니라 전송 route로도 기록한다. 요청 모델과 실제 provider/model이 다르면 둘 다 저장한다. |
| LLM-003 | Adapter는 Broker, Guard, DB 모델을 import하지 않고 canonical LLM request/response contract만 구현한다. |
| LLM-004 | 사용자 지정 endpoint는 HTTPS를 기본 요구하고 loopback은 `OLLAMA_*` profile에서만 허용한다. private network 예외는 서버 설정 allowlist가 필요하다. |
| LLM-005 | endpoint URL에 credential query parameter, 사용자정보 또는 임의 path traversal을 허용하지 않는다. |

## 5. 설정 엔터티

### 5.1 ProviderProfile

```yaml
provider_profile:
  id: uuidv7
  name: openai-primary
  adapter_type: OPENAI_RESPONSES
  endpoint: https://api.openai.com/v1
  credential_secret_ref: openai_primary_api_key
  organization_ref: null
  enabled: true
  timeout_ms: 15000
  connect_timeout_ms: 3000
  max_connections: 2
  data_policy: EXTERNAL_CLOUD | GATEWAY | LOCAL
  version: 1
```

### 5.2 ModelProfile

```yaml
model_profile:
  id: uuidv7
  provider_profile_id: uuidv7
  alias: core-primary-v1
  provider_model_id: pinned-model-id
  capabilities:
    structured_output: true
    tool_calling: false
    web_search: false
    streaming: false
    reasoning: true
    seed: false
    usage_reporting: true
  max_context_tokens: integer | null
  max_output_tokens: integer
  temperature: 0
  enabled: true
  version: 1
```

### 5.3 RoleRoute

```yaml
role_route:
  id: uuidv7
  role: NEWS_DISCLOSURE_SCOUT
  state: DRAFT | VALIDATED | ACTIVE | SUPERSEDED
  primary_model_profile_id: uuidv7
  fallback_model_profile_ids: []
  fallback_policy: NONE | APPROVED_EQUIVALENT
  timeout_ms: 10000
  max_attempts: 1
  daily_call_limit: 100
  daily_cost_limit_krw: 10000
  prompt_version: news-scout-v1
  output_schema_version: agent-assessment-v1
  activation_reason: string
```

| ID | 요구사항 |
| --- | --- |
| LLM-010 | profile의 이름·endpoint·model·capability·route는 DB에 버전 관리하고 credential 원문은 DB에 저장하지 않는다. |
| LLM-011 | model profile은 움직이는 별칭보다 고정 snapshot ID를 우선한다. provider가 snapshot을 제공하지 않으면 확인된 model ID와 확인시각을 저장하고 변경 감시 대상으로 표시한다. |
| LLM-012 | capability는 provider 단위가 아니라 model profile 단위로 관리하고 연결 시험·contract fixture 결과보다 넓게 선언할 수 없다. |
| LLM-013 | role route 활성화는 `DRAFT → VALIDATED → ACTIVE` 생명주기, 변경 사유, TOTP 재인증과 회귀시험 근거를 요구한다. |
| LLM-014 | 같은 role·scope에는 활성 route가 하나만 존재하며 활성 route는 수정하지 않고 교체한다. |

### 5.4 모델 카탈로그와 역할 배정

Provider, Model과 역할 배정은 서로 다른 자원으로 관리한다.

```text
Provider Profile 1개
  └─ Model Profile 여러 개

Model Profile 1개
  └─ Agent 역할 여러 곳에서 재사용 가능

Agent 역할 1개
  └─ 현재 유효한 Model 배정은 scope별 정확히 1개
```

역할별 배정은 내부적으로 versioned `RoleRoute`로 저장하지만 Web UI에서는 누적 route 생성 목록이 아니라 **현재 모델 배정**으로 표현한다. 과거 route는 감사·재현을 위해 보존하고 기본 화면에서는 숨긴다.

| ID | 요구사항 |
| --- | --- |
| LLM-015 | 사용자는 Provider에 여러 Model Profile을 등록하고, 같은 Model Profile을 여러 agent 역할에 재사용할 수 있다. 역할 배정을 위해 역할 이름과 같은 별도 Model Profile을 만들도록 요구하지 않는다. |
| LLM-016 | 같은 owner·scope·role에는 현재 유효한 배정이 하나만 존재한다. 여러 `VALIDATED` 후보가 존재하더라도 runtime이나 UI가 생성순서로 하나를 임의 선택하지 않으며 사용자가 명시적으로 선택·활성화해야 한다. |
| LLM-017 | 역할의 모델 변경은 기존 route를 수정하거나 삭제하지 않고 새 version을 활성화하고 기존 활성 version을 `SUPERSEDED`로 전환한다. 기존 중복 `VALIDATED` route는 이력으로 보존하되 현재 배정으로 간주하지 않는다. |
| LLM-018 | 역할 배정은 Model Profile 기본 생성 파라미터를 상속하고 지원되는 필드에 한해 역할별 override를 가진다. 우선순위는 `role override → model default → adapter default`이며 invocation에는 계산된 최종값과 설정 version을 기록한다. |
| LLM-019 | canonical 생성 파라미터는 `temperature(null 또는 0~2)`, `top_p(null 또는 0~1)`, `max_output_tokens`, `reasoning_effort(null/LOW/MEDIUM/HIGH)`, `seed(null 또는 정수)`를 지원한다. `null`은 Adapter 기본값 사용을 뜻하며 model capability가 미지원인 필드는 전송하지 않는다. 미지원 필드의 강제 설정은 route 검증 단계에서 거부한다. |

## 6. Canonical 호출 계약

### 6.1 요청

```yaml
llm_request:
  schema_version: llm-request-v1
  invocation_id: uuidv7
  agent_run_id: uuidv7
  stage_run_id: uuidv7
  role: string
  model_profile_id: uuidv7
  prompt_version: string
  input_schema_version: string
  input_hash: sha256
  messages: []
  output_json_schema: object
  timeout_ms: integer
  max_output_tokens: integer
  temperature: number
  tool_policy: NONE | ALLOWLIST
  allowed_tools: []
```

### 6.2 응답

```yaml
llm_result:
  schema_version: llm-result-v1
  invocation_id: uuidv7
  status: SUCCEEDED | REFUSED | TIMED_OUT | RATE_LIMITED | PROVIDER_ERROR | INVALID_OUTPUT | AMBIGUOUS
  requested_provider_profile_id: uuidv7
  requested_model_profile_id: uuidv7
  actual_provider: string | null
  actual_model: string | null
  gateway_request_id: string | null
  provider_request_id: string | null
  output_json: object | null
  raw_response_hash: sha256 | null
  finish_reason: string | null
  input_tokens: integer | null
  output_tokens: integer | null
  cached_tokens: integer | null
  latency_ms: integer
  estimated_cost: decimal | null
  retry_count: integer
  fallback_path: []
  schema_validation: PASSED | FAILED | NOT_RUN
```

| ID | 요구사항 |
| --- | --- |
| LLM-020 | Adapter는 provider 응답을 canonical result로 변환하고 provider 원본 필드를 Core나 실행 오케스트레이터에 직접 노출하지 않는다. |
| LLM-021 | 구조화 출력은 provider의 strict JSON Schema 기능을 우선 사용하고 서버에서 동일 schema를 다시 검증한다. JSON mode만 지원하면 `strict=false` capability로 표시하고 SHADOW 평가 전에는 Core에 사용할 수 없다. |
| LLM-022 | 공통 schema는 provider들이 지원하는 JSON Schema 교집합만 사용한다. 역할별 schema compile 단계에서 미지원 keyword를 발견하면 route validation을 거부한다. |
| LLM-023 | 출력에 허용되지 않은 필드, enum, evidence reference 또는 숫자 범위가 있으면 자동 수정하지 않고 `INVALID_OUTPUT`으로 처리한다. |
| LLM-024 | provider request ID, 실제 model, 사용량, 지연, fallback과 검증 결과를 invocation에 저장하되 credential·Authorization header·전체 민감 원문은 저장하지 않는다. |

## 7. Adapter 인터페이스

```python
class LLMProviderAdapter(Protocol):
    async def healthcheck(self, profile) -> ProviderHealth: ...
    async def list_models(self, profile) -> list[DiscoveredModel]: ...
    async def validate_model(self, profile, model) -> CapabilityResult: ...
    async def generate_structured(self, request) -> LLMResult: ...
```

권장 구현 경계:

```text
app/llm/contracts.py
app/llm/registry.py
app/llm/router.py
app/llm/secrets.py
app/llm/adapters/openai_responses.py
app/llm/adapters/anthropic_messages.py
app/llm/adapters/gemini.py
app/llm/adapters/vercel_gateway.py
app/llm/adapters/openai_compatible.py
app/llm/adapters/ollama.py
```

| ID | 요구사항 |
| --- | --- |
| LLM-030 | Adapter는 비동기 timeout과 cancellation을 지원하고 프로세스 전체 global SDK 설정을 변경하지 않는다. |
| LLM-031 | HTTP client는 profile별 허용 host, TLS 검증, response size 상한과 redaction middleware를 사용한다. |
| LLM-032 | model discovery 결과는 후보 정보일 뿐 자동 활성화하지 않는다. 사용자가 capability fixture를 통과시켜 model profile로 저장해야 한다. |
| LLM-033 | provider-specific header/body override는 명세에 등록된 allowlist field만 허용하고 Authorization, host, callback URL과 tool 권한을 임의 override할 수 없다. |

## 8. 재시도·fallback·회로 차단

| 역할 | 기본 재시도 | 기본 fallback | 실패 행동 |
| --- | --- | --- | --- |
| Intel | 네트워크·429·5xx 1회 | 승인된 소스/모델 가능 | 증거 부분 상태 |
| Verify | 네트워크 오류 1회 | 승인된 동급 모델 가능 | `PARTIAL/CONFLICTED` |
| Scout | 연결 전 실패 1회 | `APPROVED_EQUIVALENT`만 | `UNKNOWN`, 신규매수 차단 |
| Core | 0회 | `NONE` | `RISK_BLOCK` 또는 보유 오류 상태 |

| ID | 요구사항 |
| --- | --- |
| LLM-040 | timeout·연결 종료처럼 provider가 요청을 처리했는지 불명확한 결과는 `AMBIGUOUS`로 기록하고 Core 호출을 자동 재전송하지 않는다. |
| LLM-041 | `Retry-After`를 존중하되 결과 유효시간을 넘는 대기는 수행하지 않는다. 인증·schema·4xx 입력 오류는 재시도하지 않는다. |
| LLM-042 | Core route의 기본 fallback은 `NONE`이다. fallback 활성화는 동일 schema fixture와 역할별 회귀평가를 통과한 model profile만 허용한다. |
| LLM-043 | 연속 오류, rate limit 또는 지연 임계 초과 시 provider circuit을 열고 cooldown 동안 신규 호출을 차단한다. 상태는 UI와 scheduler admission에 반영한다. |
| LLM-044 | Gateway 내부 fallback을 사용하면 실제 provider/model 정보를 반환·기록할 수 있는 경우에만 Core route에 허용한다. 불명확하면 Intel/SHADOW로 제한한다. |

## 9. 비용·사용량·성능

| ID | 요구사항 |
| --- | --- |
| LLM-050 | provider·model·role별 분당 호출 수, 동시 호출, 일일 token과 일일 예상비용 한도를 설정한다. |
| LLM-051 | 비용표는 통화, 단위, 확인시각과 출처를 가진 버전 데이터이며 가격을 확인할 수 없으면 비용을 0으로 계산하지 않고 `UNKNOWN`으로 표시한다. |
| LLM-052 | 외부 provider가 반환한 usage와 내부 추정치를 구분해 저장한다. |
| LLM-053 | N100/16GB에서 Ollama는 기본 동시 호출 1개이며 Core 활성 route는 실측 p95 지연·schema 통과율·메모리 여유를 통과하기 전 금지한다. |
| LLM-054 | 비용 한도 도달은 주문 시스템 장애로 취급하지 않지만 신규 AI 매수 판단은 fail-closed하고 알림을 생성한다. |

## 10. 비밀과 데이터 보호

| ID | 요구사항 |
| --- | --- |
| LLM-060 | API key, OAuth token, service account private key와 Gateway credential은 `/home/totquf4171/cresta/secrets` 또는 동등한 secret backend에서 UID 10001 전용으로 읽는다. |
| LLM-061 | Web UI는 credential을 write-only로 등록·교체하고 이후에는 secret 참조 이름, provider, 마지막 검증시각과 상태만 표시한다. |
| LLM-062 | secret 값은 API 응답, DB, 로그, tracing, 오류, prompt, evidence와 invocation metadata에 포함하지 않는다. |
| LLM-063 | 모델 입력에는 계좌번호, 사용자 ID, 세션, TOTP, Broker 자격증명, 미체결 broker 원문과 불필요한 개인정보를 포함하지 않는다. |
| LLM-064 | provider별 데이터 보존·학습·지역 정책을 profile에 기록하고 사용자가 확인하지 않은 외부 provider는 `SHADOW_DISABLED`로 유지한다. |
| LLM-065 | 사용자 지정 Gateway의 endpoint 변경, credential 교체와 외부 전송 확대는 감사 기록과 TOTP 재인증을 요구한다. |

## 11. API와 Web UI 계약

### 11.1 REST 자원

```text
GET    /api/v1/ai/providers
POST   /api/v1/ai/providers
PATCH  /api/v1/ai/providers/{provider_id}
POST   /api/v1/ai/providers/{provider_id}/test
POST   /api/v1/ai/providers/{provider_id}/models:discover
GET    /api/v1/ai/models
POST   /api/v1/ai/models
POST   /api/v1/ai/models/{model_id}/validate
GET    /api/v1/ai/routes
POST   /api/v1/ai/routes
POST   /api/v1/ai/routes/{route_id}/validate
POST   /api/v1/ai/routes/{route_id}/activate
GET    /api/v1/ai/role-assignments
PUT    /api/v1/ai/role-assignments/{role}/draft
POST   /api/v1/ai/role-assignments/{role}/validate
POST   /api/v1/ai/role-assignments/{role}/activate
GET    /api/v1/ai/agent-runs
GET    /api/v1/ai/agent-runs/{run_id}
GET    /api/v1/ai/invocations
```

| ID | 요구사항 |
| --- | --- |
| LLM-070 | provider 연결 시험은 credential 원문, provider 응답 원문 또는 내부 endpoint 상세를 반환하지 않고 단계별 상태와 안전한 오류 코드만 반환한다. |
| LLM-071 | model discovery, route validation과 활성화는 서로 다른 작업이며 discovery만으로 운영 route가 변경되지 않는다. |
| LLM-072 | provider·model·route mutation은 CSRF, 세션과 낙관적 version 검사를 요구하고 route 활성화·credential 변경은 TOTP 재인증을 요구한다. |
| LLM-073 | UI는 역할별 primary/fallback, capability, 예상 외부 전송, 최근 health, p50/p95 지연, 오류율, 사용량·비용과 SHADOW 상태를 표시한다. |
| LLM-074 | UI의 request/response 진단에는 redacted·크기 제한된 구조화 필드만 표시하고 raw prompt와 raw provider response는 기본적으로 표시하지 않는다. |
| LLM-075 | 역할 배정 조회는 역할마다 현재 활성 배정, 검증 중 초안, 선택 가능한 Model Profile과 최종 적용 파라미터를 한 항목으로 반환한다. route 전체 이력은 별도 history 조회로 분리한다. |
| LLM-076 | 역할 배정 draft 저장은 해당 역할의 작업 중 draft를 갱신하거나 교체하고 목록에 동일 목적의 행을 계속 추가하지 않는다. 활성화는 LLM-013~019의 version·재인증·단일 활성 규칙을 적용한다. |
| LLM-077 | 여러 역할 배정은 사용자가 선택한 route ID map의 canonical hash에 결합된 TOTP proof 하나로 일괄 활성화할 수 있다. 활성화 transaction은 선택된 모든 역할을 검증한 뒤 기존 ACTIVE를 `SUPERSEDED`하고 새 ACTIVE를 원자 적용하며 일부 역할만 변경된 상태를 남기지 않는다. |

## 12. 상태와 관측성

```text
Provider health: UNKNOWN | READY | DEGRADED | RATE_LIMITED | AUTH_FAILED | DISABLED
Invocation: CREATED | RUNNING | SUCCEEDED | REFUSED | TIMED_OUT |
            RATE_LIMITED | PROVIDER_ERROR | INVALID_OUTPUT | AMBIGUOUS
```

필수 metric:

- role/provider/model별 호출 수·성공률·schema 실패율
- p50/p95/p99 latency와 queue time
- input/output/cached token
- fallback·retry·circuit open 횟수
- 추정 비용과 비용 미확정 건수
- agent run 대비 provider 실패 영향

## 13. 구현 순서

1. `contracts`, DB profile/invocation schema와 redaction 시험
2. Provider registry, secret reference와 `OPENAI_COMPATIBLE`이 아닌 Mock Adapter
3. Web UI provider/model/route 조회·초안·연결 시험
4. `OPENAI_RESPONSES`, `ANTHROPIC_MESSAGES`, `GEMINI_GENERATE_CONTENT` native Adapter
5. Vercel Gateway와 Ollama Adapter
6. role route·limit·circuit breaker
7. 다중 에이전트 SHADOW stage 연결
8. 회귀평가 후 선택한 Scout만 활성화

첫 구현 PR은 1~3과 deterministic Mock Adapter까지만 포함하며 외부 모델 응답으로 Core 판단이나 주문을 생성하지 않는다.

Provider·Model·Route Foundation의 누적형 화면은 역할 배정 관리 단계에서 Provider 카탈로그, Model 카탈로그, 역할별 현재 배정과 이력으로 분리했다. v1은 Mock Adapter와 5개 Scout·Core SHADOW 배정만 지원하며 외부 credential과 실제 Adapter는 계속 차단한다.

### 13.1 LLM Foundation v1 고정 계약

| ID | 요구사항 |
| --- | --- |
| LLM-080 | 첫 migration은 provider/model/role route/invocation 테이블만 생성하고 agent run·evidence 테이블은 다음 오케스트레이션 migration으로 분리한다. |
| LLM-081 | 첫 API는 provider/model/route의 목록·초안 생성, Mock provider 연결 시험, model·route 검증만 제공한다. route 활성화와 credential 등록 endpoint는 제공하지 않는다. |
| LLM-082 | 외부 Adapter profile은 credential 없는 `DRAFT` metadata로만 생성할 수 있다. Foundation API는 `credential_secret_ref`도 거부하며 실제 연결 시험·model 검증은 `ADAPTER_NOT_IMPLEMENTED`로 거부한다. |
| LLM-083 | Mock provider는 endpoint와 credential ref를 허용하지 않고 외부 네트워크를 사용하지 않으며 고정 capability와 정규화된 fixture 결과만 반환한다. |
| LLM-084 | Foundation v1 route는 `SHADOW` execution stage와 `fallback_policy=NONE`만 허용하고 `CORE`를 포함한 어떤 role에서도 판단·승인·주문을 생성하지 않는다. |
| LLM-085 | 첫 UI는 profile·model·route metadata와 검증 상태를 관리하며 API key·token·secret 입력 필드를 제공하지 않는다. |

### 13.2 Native Adapter Foundation v2

이 절은 외부 credential을 계속 금지하던 LLM-081·082·085의 Foundation v1 제한을 다음 단계에서 대체한다. 역할 route 실행과 주문 연결은 대체하지 않는다.

| ID | 요구사항 |
| --- | --- |
| LLM-086 | v2는 `OPENAI_RESPONSES`, `ANTHROPIC_MESSAGES`, `GEMINI_GENERATE_CONTENT` native Adapter의 구조화 출력 request·response 정규화만 지원한다. Vercel·범용 Gateway·Ollama는 계속 `ADAPTER_NOT_IMPLEMENTED`다. |
| LLM-087 | credential은 Provider UUID에서 서버가 생성한 파일명으로만 저장하며 임의 path를 받지 않는다. 디렉터리는 `0700`, 파일은 Linux에서 `0400`이고 DB에는 secret ref만 저장한다. |
| LLM-088 | credential 등록은 CSRF와 Provider version에 결합된 1회용 TOTP proof를 요구한다. API·UI·감사 로그는 credential 원문을 반환하거나 기록하지 않는다. |
| LLM-089 | API container만 secret directory를 write할 수 있고 Agent container는 read-only로 mount한다. 다른 서비스에는 mount하지 않는다. |
| LLM-090 | Adapter는 요청마다 최대 1회만 전송한다. timeout은 `TIMED_OUT`, 429는 `RATE_LIMITED`, 5xx·명시 오류는 `PROVIDER_ERROR`, 전송 결과를 확정할 수 없는 transport 오류는 `AMBIGUOUS`, JSON 계약 오류는 `INVALID_OUTPUT`으로 정규화한다. 자동 재전송과 fallback은 금지한다. |
| LLM-091 | 정규화 결과는 실제 provider/model, provider·gateway request ID, input/output token, latency와 원문 hash만 저장할 수 있다. Authorization·API key·raw response 본문은 저장하지 않는다. |
| LLM-092 | Provider 연결 계약 검증은 secret 가독성·endpoint·Adapter capability만 확인하며 과금되는 외부 호출을 하지 않는다. 실제 model 호출은 별도 Agent runtime 단계 전까지 비활성이다. |
| LLM-093 | 외부 Provider·Model metadata와 credential은 UI에서 등록할 수 있지만 외부 Model route 검증은 `EXTERNAL_RUNTIME_NOT_IMPLEMENTED`로 fail-closed한다. Mock ACTIVE route와 주문 0건 경계는 유지한다. |

### 13.3 간편 Provider 등록과 모델 발견

| ID | 요구사항 |
| --- | --- |
| LLM-094 | 기본 등록 화면은 서비스 제공자, 사용자가 정한 연결 이름, write-only API key만 입력받는다. 공식 Provider endpoint와 data policy는 서버 카탈로그가 결정하며 사용자가 URL을 입력하지 않는다. |
| LLM-095 | 등록은 대상에 결합된 TOTP 재인증 후 실제 Provider 모델 목록 API를 호출한다. 인증과 응답 계약 검증이 성공한 경우에만 Provider, secret ref와 발견 모델을 한 묶음으로 저장하며 실패한 Provider 초안을 남기지 않는다. |
| LLM-096 | 모델 발견 요청은 15초 timeout, redirect 금지, 최대 5 MiB 응답, 최대 10,000개 모델 제한을 적용하고 credential·Authorization header·Provider 원문 오류를 응답이나 로그에 노출하지 않는다. |
| LLM-097 | 발견 모델은 기본적으로 `DRAFT`이며 사용자가 `사용`으로 전환한 모델만 `VALIDATED`가 되어 역할 배정 선택지에 나타난다. 같은 모델은 여러 역할에 재사용할 수 있다. |
| LLM-098 | 등록된 Provider의 모델 동기화는 저장된 write-only credential로 모델 목록을 다시 조회하여 새 모델을 추가한다. 기존 활성 모델과 역할 이력은 자동 삭제하거나 재배정하지 않는다. |
| LLM-099 | 외부 모델은 등록·활성화·역할 변경 후보 선택까지 가능하지만 Agent 외부 호출 runtime이 검증되기 전 route 활성화와 주문 영향은 `EXTERNAL_RUNTIME_NOT_IMPLEMENTED`로 차단한다. |

## 14. 검증·인수 조건

- 같은 canonical request fixture가 모든 Adapter에서 동일한 내부 schema로 정규화된다.
- capability가 부족한 model/route는 활성화되지 않는다.
- credential과 Authorization header가 DB·로그·API·UI·오류에 나타나지 않는다.
- Core timeout·ambiguous·invalid output에서 재전송·fallback·주문이 발생하지 않는다.
- Gateway가 실제 provider/model을 밝히지 못하면 Core에 활성화되지 않는다.
- provider disabled·rate limited·비용 한도 상태가 scheduler와 UI에 일관되게 반영된다.

## 15. 미결정·보류 항목

- 첫 외부 provider와 과금 계정은 사용자가 credential을 준비할 때 결정한다.
- Vercel AI Gateway의 실제 provider pinning·usage metadata는 구현 시 공식 API fixture로 재검증한다.
- Ollama에 배치할 모델과 quantization은 N100/16GB benchmark 후 결정한다.
- 사용자 지정 Gateway allowlist에 포함할 제품은 별도 보안·이용약관 검토 후 추가한다.
# 2026-08-07 Provider catalog and external SHADOW rules

- `LLM-PROVIDER-100`: Provider selection uses a server-owned template catalog. The first entries are OpenAI, Anthropic, and Google AI Studio; the remaining entries are sorted by English display name.
- `LLM-PROVIDER-101`: The catalog contains the 40 templates reviewed from the read-only APIchat reference. Thirty-five single-key templates are registrable. AWS Bedrock, Gemini Express Mode, GitHub Copilot, NovelAI, and Vertex AI remain visible but non-registrable until their separate authentication contracts are implemented.
- `LLM-PROVIDER-102`: A template fixes its HTTPS endpoint, authentication header, model-list path, generation format, parameter profile, and optional non-secret configuration fields. The client cannot submit an arbitrary endpoint.
- `LLM-PROVIDER-103`: Models discovered from a Provider are managed inside its Provider card. There is no separate Models tab.
- `LLM-PROVIDER-104`: External models may be validated and assigned to roles only at `SHADOW`. Validation requires a validated Provider, a configured credential reference, a validated model, a registered adapter, and compatible parameters. It never creates an approval or order.
- `LLM-PROVIDER-105`: OpenAI uses the Responses adapter. Anthropic and Google use their native adapters. Registry services use the OpenAI-compatible chat-completions adapter.
- `LLM-PROVIDER-106`: Static model catalogs do not prove credential validity. Their key is first proven by a SHADOW diagnostic invocation; a failed invocation is fail-closed at the Agent stage.
- `LLM-PROVIDER-107`: Provider deletion requires TOTP reauthentication. An active role blocks deletion. Deletion removes the credential file, disables its models, soft-deletes the Provider, and preserves audit, route, invocation, and decision history.
- `LLM-PROVIDER-108`: Role prompts are immutable versioned profiles. A prompt is created as `DRAFT`, validated independently, then referenced by a SHADOW role route. Editing creates a new profile; existing routes and runs retain the prior version.
- `LLM-PROVIDER-109`: A prompt profile belongs to one role and contains only the system instruction. Runtime market, evidence, indicator, position, and Scout inputs remain a separate structured user message generated by Cresta.
- `LLM-PROVIDER-110`: Prompt text is 20–12000 characters, cannot contain NUL/control characters, and cannot request credentials, TOTP, authorization headers, arbitrary tool execution, or direct order execution. Validation records a SHA-256 content hash.
- `LLM-PROVIDER-111`: A route using `prompt_profile_id` must match the route role and reference a `VALIDATED` prompt. Legacy routes may retain a text-only `prompt_version`, but new Console candidates require a validated Prompt Profile.
- `LLM-PROVIDER-112`: Prompt management API may return prompt content only to the authenticated owner. Agent run, invocation, audit metadata, logs, and decision APIs expose only profile ID, version label, and content hash.
- `LLM-PROVIDER-113`: 현재 개발 단계에서는 Provider 등록·credential 교체·삭제와 역할 배정 활성화에 로그인 세션과 CSRF만 요구하고 TOTP proof는 요구하지 않는다. LLM-013·065·072·077·088·095와 LLM-PROVIDER-107의 TOTP 부분은 서비스 완성 후 선택적으로 재도입할 때까지 보류하며, write-only secret·영향 미리보기·활성 route 삭제 차단·원자 전환은 유지한다.
