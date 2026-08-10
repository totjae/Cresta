from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.llm.adapters.http_base import ExternalHttpAdapter, parse_json_text, safe_model_id
from app.llm.contracts import LlmRequest, ModelCapabilities
from app.llm.source_candidates import candidates_from_values


class GeminiGenerateContentAdapter(ExternalHttpAdapter):
    adapter_type = "GEMINI_GENERATE_CONTENT"
    provider_name = "GOOGLE_GEMINI"
    capabilities = ModelCapabilities(
        structured_output=True,
        reasoning=True,
        seed=True,
        usage_reporting=True,
        web_search=True,
    )

    def _request_parts(
        self, request: LlmRequest, model_id: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        model = quote(safe_model_id(model_id).removeprefix("models/"), safe="-._")
        contents: list[dict[str, Any]] = []
        system_parts: list[str] = []
        for message in request.messages:
            content = message.get("content", "")
            if message.get("role") == "system":
                system_parts.append(str(content))
            elif message.get("role") in {"user", "assistant", "model"}:
                role = "model" if message.get("role") in {"assistant", "model"} else "user"
                contents.append({"role": role, "parts": [{"text": str(content)}]})
        generation: dict[str, Any] = {
            "maxOutputTokens": request.max_output_tokens,
            "temperature": request.temperature,
            "responseMimeType": "application/json",
            "responseJsonSchema": request.output_json_schema,
        }
        if request.top_p is not None:
            generation["topP"] = request.top_p
        if request.seed is not None:
            generation["seed"] = request.seed
        body: dict[str, Any] = {"contents": contents, "generationConfig": generation}
        if request.service_tier != "DEFAULT":
            body["service_tier"] = request.service_tier.lower()
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if request.tool_policy == "ALLOWLIST" and "WEB_SEARCH" in request.allowed_tools:
            body["tools"] = [{"google_search": {}}]
        return (
            f"{self.endpoint}/models/{model}:generateContent",
            {"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
            body,
        )

    def _parse_success(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None, int | None, int | None]:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        usage = payload.get("usageMetadata") or {}
        return (
            parse_json_text(text),
            payload.get("modelVersion"),
            usage.get("promptTokenCount"),
            usage.get("candidatesTokenCount"),
        )

    def _extract_source_candidates(self, payload: dict[str, Any]):
        values: list[object] = []
        for candidate in payload.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            grounding = candidate.get("groundingMetadata") or {}
            if not isinstance(grounding, dict):
                continue
            for chunk in grounding.get("groundingChunks", []):
                if isinstance(chunk, dict):
                    values.append(chunk.get("web") or chunk)
        return candidates_from_values(values)
