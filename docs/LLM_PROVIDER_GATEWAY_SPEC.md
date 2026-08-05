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

### 13.1 LLM Foundation v1 고정 계약

| ID | 요구사항 |
| --- | --- |
| LLM-080 | 첫 migration은 provider/model/role route/invocation 테이블만 생성하고 agent run·evidence 테이블은 다음 오케스트레이션 migration으로 분리한다. |
| LLM-081 | 첫 API는 provider/model/route의 목록·초안 생성, Mock provider 연결 시험, model·route 검증만 제공한다. route 활성화와 credential 등록 endpoint는 제공하지 않는다. |
| LLM-082 | 외부 Adapter profile은 credential 없는 `DRAFT` metadata로만 생성할 수 있다. Foundation API는 `credential_secret_ref`도 거부하며 실제 연결 시험·model 검증은 `ADAPTER_NOT_IMPLEMENTED`로 거부한다. |
| LLM-083 | Mock provider는 endpoint와 credential ref를 허용하지 않고 외부 네트워크를 사용하지 않으며 고정 capability와 정규화된 fixture 결과만 반환한다. |
| LLM-084 | Foundation v1 route는 `SHADOW` execution stage와 `fallback_policy=NONE`만 허용하고 `CORE`를 포함한 어떤 role에서도 판단·승인·주문을 생성하지 않는다. |
| LLM-085 | 첫 UI는 profile·model·route metadata와 검증 상태를 관리하며 API key·token·secret 입력 필드를 제공하지 않는다. |

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
