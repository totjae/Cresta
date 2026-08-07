from __future__ import annotations

from typing import Any

from app.llm.adapters.http_base import ExternalHttpAdapter, parse_json_text, safe_model_id
from app.llm.contracts import LlmRequest, ModelCapabilities


class OpenAIResponsesAdapter(ExternalHttpAdapter):
    adapter_type = "OPENAI_RESPONSES"
    provider_name = "OPENAI"
    capabilities = ModelCapabilities(
        structured_output=True,
        reasoning=True,
        usage_reporting=True,
    )

    def _request_parts(
        self, request: LlmRequest, model_id: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        body: dict[str, Any] = {
            "model": safe_model_id(model_id),
            "input": request.messages,
            "max_output_tokens": request.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "cresta_agent_output",
                    "schema": request.output_json_schema,
                    "strict": True,
                }
            },
            "store": False,
        }
        if request.reasoning_effort is None:
            body["temperature"] = request.temperature
        if request.top_p is not None and request.reasoning_effort is None:
            body["top_p"] = request.top_p
        if request.reasoning_effort is not None:
            body["reasoning"] = {"effort": request.reasoning_effort.lower()}
        return (
            f"{self.endpoint}/responses",
            {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            body,
        )

    def _parse_success(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None, int | None, int | None]:
        for item in payload["output"]:
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "refusal":
                    raise ValueError("provider refusal")
                if content.get("type") == "output_text":
                    usage = payload.get("usage") or {}
                    return (
                        parse_json_text(content["text"]),
                        payload.get("model"),
                        usage.get("input_tokens"),
                        usage.get("output_tokens"),
                    )
        raise ValueError("missing output text")
