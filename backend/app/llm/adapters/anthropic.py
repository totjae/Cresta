from __future__ import annotations

from typing import Any

from app.llm.adapters.http_base import ExternalHttpAdapter, parse_json_text, safe_model_id
from app.llm.contracts import LlmRequest, ModelCapabilities


class AnthropicMessagesAdapter(ExternalHttpAdapter):
    adapter_type = "ANTHROPIC_MESSAGES"
    provider_name = "ANTHROPIC"
    capabilities = ModelCapabilities(structured_output=True, usage_reporting=True)

    def _request_parts(
        self, request: LlmRequest, model_id: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        system_parts = [item["content"] for item in request.messages if item.get("role") == "system"]
        messages = [item for item in request.messages if item.get("role") in {"user", "assistant"}]
        body: dict[str, Any] = {
            "model": safe_model_id(model_id),
            "max_tokens": request.max_output_tokens,
            "messages": messages,
            "temperature": min(request.temperature, 1),
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": request.output_json_schema,
                }
            },
        }
        if system_parts:
            body["system"] = "\n\n".join(str(item) for item in system_parts)
        if request.top_p is not None:
            body["top_p"] = request.top_p
        return (
            f"{self.endpoint}/messages",
            {
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            body,
        )

    def _parse_success(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None, int | None, int | None]:
        text = next(item["text"] for item in payload["content"] if item.get("type") == "text")
        usage = payload.get("usage") or {}
        return (
            parse_json_text(text),
            payload.get("model"),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
