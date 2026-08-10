from __future__ import annotations

import json

import httpx
import pytest

from app.ids import uuid7
from app.llm.adapters.anthropic import AnthropicMessagesAdapter
from app.llm.adapters.gemini import GeminiGenerateContentAdapter
from app.llm.adapters.openai import OpenAIResponsesAdapter
from app.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from app.llm.contracts import LlmRequest


def _request(
    service_tier: str = "DEFAULT",
    *,
    web_search: bool = False,
    reasoning_effort: str | None = None,
) -> LlmRequest:
    return LlmRequest(
        invocation_id=uuid7(),
        role="TECHNICAL_SCOUT",
        model_profile_id=uuid7(),
        prompt_version="technical-shadow-v1",
        input_schema_version="scout-input-v1",
        input_hash="a" * 64,
        messages=[
            {"role": "system", "content": "Return only the contracted object."},
            {"role": "user", "content": "Assess the supplied fixture."},
        ],
        output_json_schema={
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        },
        timeout_ms=3000,
        service_tier=service_tier,
        max_output_tokens=256,
        temperature=0,
        top_p=0.9,
        reasoning_effort=reasoning_effort,
        tool_policy="ALLOWLIST" if web_search else "NONE",
        allowed_tools=["WEB_SEARCH"] if web_search else [],
    )


def test_openai_responses_contract_and_usage_normalization() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"x-request-id": "req_openai_1"},
            json={
                "model": "gpt-test",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"status":"OK"}'}],
                    }
                ],
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OpenAIResponsesAdapter(
        endpoint="https://api.openai.com/v1", api_key="openai-secret", client=client
    )
    result = adapter.generate_structured(_request(), "gpt-test")
    body = captured["body"]
    outbound = captured["request"]
    assert isinstance(body, dict) and isinstance(outbound, httpx.Request)
    assert outbound.url == "https://api.openai.com/v1/responses"
    assert outbound.headers["authorization"] == "Bearer openai-secret"
    assert body["store"] is False
    assert "service_tier" not in body
    assert body["text"]["format"]["type"] == "json_schema"
    assert result.status == "SUCCEEDED"
    assert result.output_json == {"status": "OK"}
    assert result.provider_request_id == "req_openai_1"
    assert (result.input_tokens, result.output_tokens) == (12, 4)
    assert "openai-secret" not in result.model_dump_json()


def test_openai_responses_reasoning_model_omits_sampling_by_model_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(503, json={"error": "fixture"})

    adapter = OpenAIResponsesAdapter(
        endpoint="https://api.openai.com/v1",
        api_key="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    adapter.generate_structured(_request(), "gpt-5-mini")
    body = captured["body"]
    assert isinstance(body, dict)
    assert "temperature" not in body
    assert "top_p" not in body
    assert "reasoning" not in body

    adapter.generate_structured(_request(reasoning_effort="HIGH"), "gpt-5-mini")
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["reasoning"] == {"effort": "high"}
    assert "temperature" not in body
    assert "top_p" not in body


def test_anthropic_messages_contract_and_usage_normalization() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"request-id": "req_anthropic_1"},
            json={
                "model": "claude-test",
                "content": [{"type": "text", "text": '{"status":"OK"}'}],
                "usage": {"input_tokens": 8, "output_tokens": 3},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = AnthropicMessagesAdapter(
        endpoint="https://api.anthropic.com/v1", api_key="anthropic-secret", client=client
    )
    result = adapter.generate_structured(_request(), "claude-test")
    body = captured["body"]
    outbound = captured["request"]
    assert isinstance(body, dict) and isinstance(outbound, httpx.Request)
    assert outbound.url == "https://api.anthropic.com/v1/messages"
    assert outbound.headers["x-api-key"] == "anthropic-secret"
    assert outbound.headers["anthropic-version"] == "2023-06-01"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["system"] == "Return only the contracted object."
    assert result.status == "SUCCEEDED"
    assert result.provider_request_id == "req_anthropic_1"
    assert (result.input_tokens, result.output_tokens) == (8, 3)


def test_gemini_generate_content_contract_and_usage_normalization() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"x-goog-request-id": "req_gemini_1"},
            json={
                "modelVersion": "gemini-test-001",
                "candidates": [
                    {"content": {"parts": [{"text": '{"status":"OK"}'}]}}
                ],
                "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = GeminiGenerateContentAdapter(
        endpoint="https://generativelanguage.googleapis.com/v1beta",
        api_key="gemini-secret",
        client=client,
    )
    result = adapter.generate_structured(_request(), "gemini-test")
    body = captured["body"]
    outbound = captured["request"]
    assert isinstance(body, dict) and isinstance(outbound, httpx.Request)
    assert outbound.url == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    )
    assert outbound.headers["x-goog-api-key"] == "gemini-secret"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"]["type"] == "object"
    assert result.status == "SUCCEEDED"
    assert result.actual_model == "gemini-test-001"
    assert (result.input_tokens, result.output_tokens) == (7, 2)


@pytest.mark.parametrize(
    ("adapter_factory", "model_id", "assert_web_tool"),
    [
        (
            lambda client: OpenAIResponsesAdapter(
                endpoint="https://api.openai.com/v1", api_key="secret", client=client
            ),
            "gpt-test",
            lambda body: body["tools"] == [{"type": "web_search"}]
            and body["include"] == ["web_search_call.action.sources"],
        ),
        (
            lambda client: AnthropicMessagesAdapter(
                endpoint="https://api.anthropic.com/v1", api_key="secret", client=client
            ),
            "claude-test",
            lambda body: body["tools"][0]["type"] == "web_search_20250305",
        ),
        (
            lambda client: GeminiGenerateContentAdapter(
                endpoint="https://generativelanguage.googleapis.com/v1beta",
                api_key="secret",
                client=client,
            ),
            "gemini-test",
            lambda body: body["tools"] == [{"google_search": {}}],
        ),
    ],
)
def test_native_adapters_enable_only_allowlisted_web_search(
    adapter_factory, model_id: str, assert_web_tool
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(503, json={"error": "fixture"})

    adapter = adapter_factory(httpx.Client(transport=httpx.MockTransport(handler)))
    adapter.generate_structured(_request(web_search=True), model_id)
    assert assert_web_tool(captured["body"])


def test_llm_gateway_compatible_request_uses_provider_web_search_switch() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(503, json={"error": "fixture"})

    adapter = OpenAICompatibleAdapter(
        endpoint="https://api.llmgateway.example/v1",
        api_key="secret",
        chat_path="/chat/completions",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    adapter.generate_structured(_request(web_search=True), "gateway-model")
    assert captured["body"]["web_search"] is True


def test_compatible_reasoning_model_uses_apichat_parameter_policy() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(503, json={"error": "fixture"})

    adapter = OpenAICompatibleAdapter(
        endpoint="https://api.llmgateway.example/v1",
        api_key="secret",
        chat_path="/chat/completions",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = adapter.generate_structured(
        _request(reasoning_effort="MEDIUM"), "gpt-5-mini-2025-08-07"
    )
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["max_completion_tokens"] == 256
    assert body["reasoning_effort"] == "medium"
    assert "max_tokens" not in body
    assert "temperature" not in body
    assert "top_p" not in body
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "cresta_agent_output",
            "strict": True,
            "schema": _request().output_json_schema,
        },
    }
    assert result.status == "PROVIDER_ERROR"
    assert result.retry_count == 0


def test_compatible_non_reasoning_model_keeps_sampling_and_strict_schema() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(503, json={"error": "fixture"})

    adapter = OpenAICompatibleAdapter(
        endpoint="https://api.llmgateway.example/v1",
        api_key="secret",
        chat_path="/chat/completions",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    adapter.generate_structured(_request(), "google-vertex/gemini-3.1-flash-lite")
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["max_tokens"] == 256
    assert body["temperature"] == 0
    assert body["top_p"] == 0.9
    assert "max_completion_tokens" not in body
    assert body["response_format"]["type"] == "json_schema"
    assert "Return exactly one JSON object" in body["messages"][0]["content"]


@pytest.mark.parametrize(
    ("adapter_factory", "model_id"),
    [
        (
            lambda client: OpenAIResponsesAdapter(
                endpoint="https://api.openai.com/v1", api_key="secret", client=client
            ),
            "gpt-test",
        ),
        (
            lambda client: AnthropicMessagesAdapter(
                endpoint="https://api.anthropic.com/v1", api_key="secret", client=client
            ),
            "claude-test",
        ),
        (
            lambda client: GeminiGenerateContentAdapter(
                endpoint="https://generativelanguage.googleapis.com/v1beta",
                api_key="secret",
                client=client,
            ),
            "gemini-test",
        ),
    ],
)
def test_native_adapters_forward_explicit_service_tier(adapter_factory, model_id: str) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(503, json={"error": "fixture"})

    adapter = adapter_factory(httpx.Client(transport=httpx.MockTransport(handler)))
    adapter.generate_structured(_request("PRIORITY"), model_id)
    assert captured["body"]["service_tier"] == "priority"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(429, "RATE_LIMITED"), (500, "PROVIDER_ERROR"), (401, "PROVIDER_ERROR")],
)
def test_external_adapter_errors_are_fail_closed_without_retry(
    status_code: int, expected: str
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json={"error": {"message": "redacted"}})

    adapter = OpenAIResponsesAdapter(
        endpoint="https://api.openai.com/v1",
        api_key="never-return-this",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = adapter.generate_structured(_request(), "gpt-test")
    assert result.status == expected
    assert result.retry_count == 0
    assert calls == 1
    assert "never-return-this" not in result.model_dump_json()


def test_external_adapter_timeout_and_invalid_json_are_normalized() -> None:
    timeout_adapter = OpenAIResponsesAdapter(
        endpoint="https://api.openai.com/v1",
        api_key="secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=request))
            )
        ),
    )
    assert timeout_adapter.generate_structured(_request(), "gpt-test").status == "TIMED_OUT"

    invalid_adapter = OpenAIResponsesAdapter(
        endpoint="https://api.openai.com/v1",
        api_key="secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "not-json"}],
                            }
                        ]
                    },
                )
            )
        ),
    )
    result = invalid_adapter.generate_structured(_request(), "gpt-test")
    assert result.status == "INVALID_OUTPUT"
    assert result.raw_response_hash is not None


def test_completed_http_response_past_total_deadline_is_discarded(monkeypatch) -> None:
    ticks = iter([0.0, 1.1, 1.1])
    monkeypatch.setattr("app.llm.adapters.http_base.time.monotonic", lambda: next(ticks))
    adapter = OpenAIResponsesAdapter(
        endpoint="https://api.openai.com/v1",
        api_key="secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {"type": "output_text", "text": '{"status":"OK"}'}
                                ],
                            }
                        ]
                    },
                )
            )
        ),
    )
    request = _request().model_copy(update={"timeout_ms": 1000})
    result = adapter.generate_structured(request, "gpt-test")
    assert result.status == "TIMED_OUT"
    assert result.output_json is None
