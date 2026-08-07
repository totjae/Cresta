from __future__ import annotations

from app.llm.adapters.anthropic import AnthropicMessagesAdapter
from app.llm.adapters.gemini import GeminiGenerateContentAdapter
from app.llm.adapters.mock import MockProviderAdapter
from app.llm.adapters.openai import OpenAIResponsesAdapter
from app.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from app.llm.contracts import LlmProviderAdapter


class AdapterNotImplementedError(Exception):
    pass


class ProviderRegistry:
    def __init__(self) -> None:
        self._mock = MockProviderAdapter()

    def resolve(
        self,
        adapter_type: str,
        *,
        endpoint: str | None = None,
        credential: str | None = None,
        chat_path: str | None = None,
        client=None,
    ) -> LlmProviderAdapter:
        if adapter_type == "MOCK":
            return self._mock
        adapters = {
            "OPENAI_RESPONSES": OpenAIResponsesAdapter,
            "ANTHROPIC_MESSAGES": AnthropicMessagesAdapter,
            "GEMINI_GENERATE_CONTENT": GeminiGenerateContentAdapter,
            "OPENAI_COMPATIBLE": OpenAICompatibleAdapter,
        }
        adapter = adapters.get(adapter_type)
        if adapter is not None:
            if not endpoint or not credential:
                raise AdapterNotImplementedError(f"{adapter_type}:CREDENTIAL_REQUIRED")
            kwargs = {"endpoint": endpoint, "api_key": credential, "client": client}
            if adapter_type == "OPENAI_COMPATIBLE" and chat_path:
                kwargs["chat_path"] = chat_path
            return adapter(**kwargs)
        raise AdapterNotImplementedError(adapter_type)


provider_registry = ProviderRegistry()
