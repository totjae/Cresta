from __future__ import annotations

from typing import Any

from app.llm.adapters.http_base import ExternalHttpAdapter, parse_json_text, safe_model_id
from app.llm.contracts import LlmRequest, ModelCapabilities


class OpenAICompatibleAdapter(ExternalHttpAdapter):
    adapter_type = "OPENAI_COMPATIBLE"
    provider_name = "OPENAI_COMPATIBLE"
    capabilities = ModelCapabilities(structured_output=True, usage_reporting=True)

    def __init__(self, *, endpoint: str, api_key: str, chat_path: str = "/v1/chat/completions", client=None) -> None:
        super().__init__(endpoint=endpoint, api_key=api_key, client=client)
        self.chat_path = "/" + chat_path.lstrip("/")

    def _request_parts(
        self, request: LlmRequest, model_id: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        body: dict[str, Any] = {
            "model": safe_model_id(model_id),
            "messages": request.messages,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "response_format": {"type": "json_object"},
        }
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.seed is not None:
            body["seed"] = request.seed
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
