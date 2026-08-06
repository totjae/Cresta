from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_MODELS = 10_000


@dataclass(frozen=True)
class ProviderCatalogItem:
    adapter_type: str
    label: str
    endpoint: str
    models_url: str
    data_policy: str = "EXTERNAL_CLOUD"


@dataclass(frozen=True)
class DiscoveredModel:
    provider_model_id: str
    display_name: str
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None


CATALOG: dict[str, ProviderCatalogItem] = {
    "OPENAI_RESPONSES": ProviderCatalogItem(
        "OPENAI_RESPONSES",
        "OpenAI",
        "https://api.openai.com/v1",
        "https://api.openai.com/v1/models",
    ),
    "ANTHROPIC_MESSAGES": ProviderCatalogItem(
        "ANTHROPIC_MESSAGES",
        "Anthropic",
        "https://api.anthropic.com/v1",
        "https://api.anthropic.com/v1/models",
    ),
    "GEMINI_GENERATE_CONTENT": ProviderCatalogItem(
        "GEMINI_GENERATE_CONTENT",
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta",
        "https://generativelanguage.googleapis.com/v1beta/models",
    ),
}


class ModelDiscoveryError(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def catalog_items() -> list[ProviderCatalogItem]:
    return list(CATALOG.values())


def _headers(adapter_type: str, credential: str) -> dict[str, str]:
    if adapter_type == "OPENAI_RESPONSES":
        return {"Authorization": f"Bearer {credential}"}
    if adapter_type == "ANTHROPIC_MESSAGES":
        return {"x-api-key": credential, "anthropic-version": "2023-06-01"}
    if adapter_type == "GEMINI_GENERATE_CONTENT":
        return {"x-goog-api-key": credential}
    raise ModelDiscoveryError("PROVIDER_DISCOVERY_UNSUPPORTED")


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _parse_models(adapter_type: str, payload: object) -> list[DiscoveredModel]:
    if not isinstance(payload, dict):
        raise ModelDiscoveryError("PROVIDER_RESPONSE_INVALID", 502)
    source = payload.get("models" if adapter_type == "GEMINI_GENERATE_CONTENT" else "data")
    if not isinstance(source, list):
        raise ModelDiscoveryError("PROVIDER_RESPONSE_INVALID", 502)
    if len(source) > MAX_MODELS:
        raise ModelDiscoveryError("PROVIDER_MODEL_LIMIT_EXCEEDED", 502)
    models: list[DiscoveredModel] = []
    seen: set[str] = set()
    for item in source:
        if not isinstance(item, dict):
            continue
        if adapter_type == "GEMINI_GENERATE_CONTENT":
            methods = item.get("supportedGenerationMethods")
            if not isinstance(methods, list) or "generateContent" not in methods:
                continue
            raw_id = item.get("name")
            model_id = raw_id.removeprefix("models/") if isinstance(raw_id, str) else ""
            display = item.get("displayName")
            context = _positive_int(item.get("inputTokenLimit"))
            output = _positive_int(item.get("outputTokenLimit"))
        else:
            raw_id = item.get("id")
            model_id = raw_id if isinstance(raw_id, str) else ""
            display = item.get("display_name") or item.get("displayName")
            context = _positive_int(item.get("context_window"))
            output = _positive_int(item.get("max_output_tokens"))
        model_id = model_id.strip()
        if not model_id or len(model_id) > 128 or model_id in seen:
            continue
        seen.add(model_id)
        models.append(DiscoveredModel(model_id, str(display or model_id)[:128], context, output))
    if not models:
        raise ModelDiscoveryError("PROVIDER_MODELS_EMPTY", 502)
    return models


def discover_models(
    adapter_type: str,
    credential: str,
    *,
    client: httpx.Client | None = None,
) -> list[DiscoveredModel]:
    catalog = CATALOG.get(adapter_type)
    if catalog is None:
        raise ModelDiscoveryError("PROVIDER_DISCOVERY_UNSUPPORTED")
    owned = client is None
    transport = client or httpx.Client(timeout=15.0, follow_redirects=False)
    try:
        response = transport.get(catalog.models_url, headers=_headers(adapter_type, credential))
        if 300 <= response.status_code < 400:
            raise ModelDiscoveryError("PROVIDER_REDIRECT_REJECTED", 502)
        if response.status_code in {401, 403}:
            raise ModelDiscoveryError("PROVIDER_AUTH_FAILED", 422)
        if response.status_code == 429:
            raise ModelDiscoveryError("PROVIDER_RATE_LIMITED", 429)
        if response.status_code >= 400:
            raise ModelDiscoveryError("PROVIDER_UPSTREAM_FAILED", 502)
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
            raise ModelDiscoveryError("PROVIDER_RESPONSE_TOO_LARGE", 502)
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ModelDiscoveryError("PROVIDER_RESPONSE_TOO_LARGE", 502)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelDiscoveryError("PROVIDER_RESPONSE_INVALID", 502) from exc
        return _parse_models(adapter_type, payload)
    except ModelDiscoveryError:
        raise
    except httpx.TimeoutException as exc:
        raise ModelDiscoveryError("PROVIDER_TIMEOUT", 504) from exc
    except httpx.HTTPError as exc:
        raise ModelDiscoveryError("PROVIDER_NETWORK_FAILED", 502) from exc
    finally:
        if owned:
            transport.close()
