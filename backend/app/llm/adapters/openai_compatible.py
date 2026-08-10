from __future__ import annotations

import json
from typing import Any

from app.llm.adapters.http_base import ExternalHttpAdapter, parse_json_text, safe_model_id
from app.llm.contracts import LlmRequest, ModelCapabilities
from app.llm.parameter_policy import (
    is_openai_reasoning_model,
    uses_completion_token_parameter,
)


class OpenAICompatibleAdapter(ExternalHttpAdapter):
    adapter_type = "OPENAI_COMPATIBLE"
    provider_name = "OPENAI_COMPATIBLE"
    capabilities = ModelCapabilities(
        structured_output=True, web_search=True, usage_reporting=True
    )

    def __init__(self, *, endpoint: str, api_key: str, chat_path: str = "/v1/chat/completions", client=None) -> None:
        super().__init__(endpoint=endpoint, api_key=api_key, client=client)
        self.chat_path = "/" + chat_path.lstrip("/")

    def _request_parts(
        self, request: LlmRequest, model_id: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        normalized_model_id = safe_model_id(model_id)
        schema_instruction = (
            "Return exactly one JSON object matching this JSON Schema. "
            "Do not use Markdown or add fields outside the schema: "
            + json.dumps(request.output_json_schema, ensure_ascii=False, separators=(",", ":"))
        )
        body: dict[str, Any] = {
            "model": normalized_model_id,
            "messages": [
                {"role": "system", "content": schema_instruction},
                *request.messages,
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "cresta_agent_output",
                    "strict": True,
                    "schema": request.output_json_schema,
                },
            },
        }
        if uses_completion_token_parameter(normalized_model_id):
            body["max_completion_tokens"] = request.max_output_tokens
        else:
            body["max_tokens"] = request.max_output_tokens
        if not is_openai_reasoning_model(normalized_model_id):
            body["temperature"] = request.temperature
            if request.top_p is not None:
                body["top_p"] = request.top_p
        if request.reasoning_effort is not None:
            body["reasoning_effort"] = request.reasoning_effort.lower()
        if request.seed is not None:
            body["seed"] = request.seed
        if request.service_tier != "DEFAULT":
            body["service_tier"] = request.service_tier.lower()
        if request.tool_policy == "ALLOWLIST" and "WEB_SEARCH" in request.allowed_tools:
            body["web_search"] = True
        return (
            f"{self.endpoint}{self.chat_path}",
            {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            body,
        )

    def _parse_success(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None, int | None, int | None]:
        message = payload["choices"][0]["message"]
        usage = payload.get("usage") or {}
        return (
            parse_json_text(message["content"]),
            payload.get("model"),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )
