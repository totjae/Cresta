from __future__ import annotations

from app.llm.adapters.mock import MockProviderAdapter
from app.llm.contracts import LlmProviderAdapter


class AdapterNotImplementedError(Exception):
    pass


class ProviderRegistry:
    def __init__(self) -> None:
        self._mock = MockProviderAdapter()

    def resolve(self, adapter_type: str) -> LlmProviderAdapter:
        if adapter_type == "MOCK":
            return self._mock
        raise AdapterNotImplementedError(adapter_type)


provider_registry = ProviderRegistry()
