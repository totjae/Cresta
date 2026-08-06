from __future__ import annotations

import json

import httpx
import pytest

from app.ids import uuid7
from app.llm.adapters.anthropic import AnthropicMessagesAdapter
from app.llm.adapters.gemini import GeminiGenerateContentAdapter
from app.llm.adapters.openai import OpenAIResponsesAdapter
from app.llm.contracts import LlmRequest


def _request() -> LlmRequest:
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
        max_output_tokens=256,
        temperature=0,
        top_p=0.9,
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
    assert body["text"]["format"]["type"] == "json_schema"
    assert result.status == "SUCCEEDED"
    assert result.output_json == {"status": "OK"}
    assert result.provider_request_id == "req_openai_1"
    assert (result.input_tokens, result.output_tokens) == (12, 4)
    assert "openai-secret" not in result.model_dump_json()


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
