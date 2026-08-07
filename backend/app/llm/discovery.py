from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin

import httpx

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_MODELS = 10_000


@dataclass(frozen=True)
class ProviderConfigurationField:
    key: str
    label: str
    minimum_length: int = 1
    maximum_length: int = 64


@dataclass(frozen=True)
class ProviderCatalogItem:
    template_id: str
    label: str
    adapter_type: str
    endpoint: str | None = None
    model_list_path: str | None = None
    chat_path: str | None = None
    auth_type: Literal["bearer", "bearer-optional", "x-api-key", "google-api-key"] = "bearer"
    response_type: Literal["openai", "google", "generic"] = "generic"
    parameter_profile: Literal["openai", "anthropic", "gemini", "novelai", "routed"] = "openai"
    can_register: bool = True
    support_level: Literal["verified", "compatible", "experimental", "coming_soon"] = "compatible"
    static_models: tuple[str, ...] = ()
    configuration_fields: tuple[ProviderConfigurationField, ...] = ()
    data_policy: str = "EXTERNAL_CLOUD"


@dataclass(frozen=True)
class DiscoveredModel:
    provider_model_id: str
    display_name: str
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None


def _openai(template_id: str, label: str, endpoint: str, *, models: str | None = None,
            chat: str = "/v1/chat/completions", auth: str = "bearer",
            static: tuple[str, ...] = (), fields: tuple[ProviderConfigurationField, ...] = (),
            data_policy: str = "EXTERNAL_CLOUD") -> ProviderCatalogItem:
    return ProviderCatalogItem(
        template_id, label, "OPENAI_COMPATIBLE", endpoint, models, chat,
        auth_type=auth, static_models=static, configuration_fields=fields,
        data_policy=data_policy,
    )


_ITEMS = [
    ProviderCatalogItem("openai", "OpenAI", "OPENAI_RESPONSES", "https://api.openai.com/v1", "/models", "/responses", response_type="openai", parameter_profile="openai"),
    ProviderCatalogItem("anthropic", "Anthropic", "ANTHROPIC_MESSAGES", "https://api.anthropic.com/v1", "/models", "/messages", auth_type="x-api-key", response_type="openai", parameter_profile="anthropic"),
    ProviderCatalogItem("google", "Google AI Studio", "GEMINI_GENERATE_CONTENT", "https://generativelanguage.googleapis.com", "/v1beta/models", "/v1beta/models/{model}:generateContent", auth_type="google-api-key", response_type="google", parameter_profile="gemini"),
    _openai("ai-novelist", "AI Novelist", "https://api.tringpt.com/v1", chat="/chat/completions", static=("spiko_ultra",)),
    _openai("arliai", "ArliAI", "https://api.arliai.com", models="/model/all"),
    ProviderCatalogItem("bedrock", "AWS Bedrock", "OPENAI_COMPATIBLE", can_register=False, support_level="coming_soon", parameter_profile="routed"),
    _openai("cerebras", "Cerebras", "https://api.cerebras.ai", models="/public/v1/models?format=openrouter", auth="bearer-optional"),
    _openai("cloudflare-ai-gateway", "Cloudflare AI Gateway", "https://api.cloudflare.com/client/v4/accounts/{accountId}/ai", chat="/v1/chat/completions", fields=(ProviderConfigurationField("accountId", "Cloudflare Account ID"),), static=("openai/gpt-5.4-mini", "anthropic/claude-sonnet-4.6", "google/gemini-3.5-flash"), data_policy="GATEWAY"),
    _openai("crof-ai", "CrofAI", "https://crof.ai", models="/v1/models"),
    _openai("deepseek", "DeepSeek", "https://api.deepseek.com", models="/models", chat="/chat/completions", static=("deepseek-v4-flash", "deepseek-v4-pro")),
    _openai("digitalocean", "DigitalOcean", "https://inference.do-ai.run", models="/v1/models"),
    _openai("featherless", "Featherless", "https://api.featherless.ai/v1", models="/models", chat="/chat/completions"),
    _openai("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference", models="/v1/models", static=("accounts/fireworks/routers/kimi-k2p5-turbo",)),
    ProviderCatalogItem("gemini-express", "Gemini Express Mode", "GEMINI_GENERATE_CONTENT", can_register=False, support_level="experimental", parameter_profile="gemini"),
    ProviderCatalogItem("copilot", "GitHub Copilot", "OPENAI_COMPATIBLE", can_register=False, support_level="coming_soon", parameter_profile="routed"),
    _openai("heroku-eu", "Heroku (EU)", "https://eu.inference.heroku.com", static=("claude-sonnet-4-6", "kimi-k2-5", "glm-4-7")),
    _openai("heroku-us", "Heroku (US)", "https://us.inference.heroku.com", static=("claude-sonnet-4-6", "kimi-k2-5", "glm-4-7")),
    _openai("lightning-ai", "Lightning AI", "https://lightning.ai", models="/api/v1/models", chat="/api/v1/chat/completions", auth="bearer-optional"),
    _openai("llm-gateway", "LLM Gateway", "https://api.llmgateway.io/v1", models="/models?exclude_deprecated=true", chat="/chat/completions", data_policy="GATEWAY"),
    _openai("xiaomi-mimo-token-plan-ams", "MiMo Token Plan (Europe)", "https://token-plan-ams.xiaomimimo.com", static=("mimo-v2.5-pro", "mimo-v2.5")),
    _openai("xiaomi-mimo-token-plan-sgp", "MiMo Token Plan (Singapore)", "https://token-plan-sgp.xiaomimimo.com", static=("mimo-v2.5-pro", "mimo-v2.5")),
    _openai("nano-gpt", "NanoGPT", "https://nano-gpt.com/api", models="/v1/models?detailed=true", auth="bearer-optional"),
    _openai("nano-gpt-subscription", "NanoGPT Subscription", "https://nano-gpt.com/api/subscription/v1", models="/models?detailed=true", chat="/chat/completions", auth="bearer-optional"),
    _openai("neuralwatt", "Neuralwatt Cloud", "https://api.neuralwatt.com/v1", models="/models", chat="/chat/completions", auth="bearer-optional"),
    ProviderCatalogItem("novelai", "NovelAI", "OPENAI_COMPATIBLE", can_register=False, support_level="coming_soon", parameter_profile="novelai"),
    _openai("novita", "Novita AI", "https://api.novita.ai/openai", models="/v1/models"),
    _openai("novita-coding", "Novita Coding", "https://api.novita.ai/openai", static=("glm-5", "kimi-k2.5", "deepseek-v3.2")),
    _openai("ollama-cloud", "Ollama Cloud", "https://ollama.com", models="/v1/models"),
    _openai("opencode-go", "OpenCode Go", "https://opencode.ai/zen/go", models="/v1/models", auth="bearer-optional"),
    _openai("openrouter", "OpenRouter", "https://openrouter.ai/api", models="/v1/models", data_policy="GATEWAY"),
    _openai("siliconflow", "SiliconFlow", "https://api.siliconflow.com/v1", models="/models?sub_type=chat", chat="/chat/completions"),
    _openai("synthetic", "Synthetic", "https://api.synthetic.new", models="/v1/models"),
    _openai("together", "Together AI", "https://api.together.xyz/v1", models="/models", chat="/chat/completions"),
    _openai("venice-ai", "Venice AI", "https://api.venice.ai/api/v1", models="/models?type=text", chat="/chat/completions"),
    _openai("vercel-ai", "Vercel AI Gateway", "https://ai-gateway.vercel.sh", models="/v1/models", data_policy="GATEWAY"),
    ProviderCatalogItem("vertex", "Vertex AI", "GEMINI_GENERATE_CONTENT", can_register=False, support_level="coming_soon", parameter_profile="routed"),
    _openai("wellspring", "Wellspring", "https://wellspring.encrypt.gay/v1", models="/models", chat="/chat/completions"),
    _openai("xiaomi-mimo", "Xiaomi MiMo", "https://api.xiaomimimo.com", static=("mimo-v2.5-pro", "mimo-v2.5")),
    _openai("z-ai", "Z.ai", "https://api.z.ai/api/paas/v4", chat="/chat/completions", static=("glm-5.2", "glm-5", "glm-4.7")),
    _openai("z-ai-coding", "Z.ai GLM Coding Plan", "https://api.z.ai/api/coding/paas/v4", chat="/chat/completions", static=("glm-5.2", "glm-5-turbo", "glm-4.7")),
]

FEATURED = ("openai", "anthropic", "google")
CATALOG: dict[str, ProviderCatalogItem] = {item.template_id: item for item in _ITEMS}
LEGACY_TEMPLATE_IDS = {
    "OPENAI_RESPONSES": "openai",
    "ANTHROPIC_MESSAGES": "anthropic",
    "GEMINI_GENERATE_CONTENT": "google",
}


class ModelDiscoveryError(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def catalog_items() -> list[ProviderCatalogItem]:
    return [CATALOG[item] for item in FEATURED] + sorted(
        (item for item in _ITEMS if item.template_id not in FEATURED),
        key=lambda item: (item.label.casefold(), item.template_id),
    )


def get_template(template_or_adapter: str) -> ProviderCatalogItem:
    key = LEGACY_TEMPLATE_IDS.get(template_or_adapter, template_or_adapter)
    try:
        return CATALOG[key]
    except KeyError as exc:
        raise ModelDiscoveryError("PROVIDER_DISCOVERY_UNSUPPORTED") from exc


def resolve_endpoint(template: ProviderCatalogItem, configuration: dict[str, str] | None) -> str:
    if not template.can_register or not template.endpoint:
        raise ModelDiscoveryError("PROVIDER_REGISTRATION_UNAVAILABLE")
    endpoint = template.endpoint
    supplied = configuration or {}
    for config in template.configuration_fields:
        value = supplied.get(config.key, "").strip()
        if not (config.minimum_length <= len(value) <= config.maximum_length) or not all(
            char.isalnum() or char in "_-" for char in value
        ):
            raise ModelDiscoveryError("PROVIDER_CONFIGURATION_INVALID")
        endpoint = endpoint.replace("{" + config.key + "}", value)
    if "{" in endpoint:
        raise ModelDiscoveryError("PROVIDER_CONFIGURATION_INVALID")
    return endpoint


def _headers(template: ProviderCatalogItem, credential: str) -> dict[str, str]:
    if template.auth_type == "x-api-key":
        return {"x-api-key": credential, "anthropic-version": "2023-06-01"}
    if template.auth_type == "google-api-key":
        return {"x-goog-api-key": credential}
    return {"Authorization": f"Bearer {credential}"}


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _parse_models(template: ProviderCatalogItem, payload: object) -> list[DiscoveredModel]:
    if not isinstance(payload, dict):
        raise ModelDiscoveryError("PROVIDER_RESPONSE_INVALID", 502)
    source = payload.get("models" if template.response_type == "google" else "data")
    if not isinstance(source, list):
        # Several compatible registries expose the model array directly under `models`.
        source = payload.get("models")
    if not isinstance(source, list):
        raise ModelDiscoveryError("PROVIDER_RESPONSE_INVALID", 502)
    if len(source) > MAX_MODELS:
        raise ModelDiscoveryError("PROVIDER_MODEL_LIMIT_EXCEEDED", 502)
    models: list[DiscoveredModel] = []
    seen: set[str] = set()
    for item in source:
        if isinstance(item, str):
            model_id, display, context, output = item, item, None, None
        elif isinstance(item, dict) and template.response_type == "google":
            methods = item.get("supportedGenerationMethods")
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            raw_id = item.get("name")
            model_id = raw_id.removeprefix("models/") if isinstance(raw_id, str) else ""
            display = item.get("displayName") or model_id
            context = _positive_int(item.get("inputTokenLimit"))
            output = _positive_int(item.get("outputTokenLimit"))
        elif isinstance(item, dict):
            raw_id = item.get("id") or item.get("model") or item.get("name")
            model_id = raw_id if isinstance(raw_id, str) else ""
            display = item.get("display_name") or item.get("displayName") or model_id
            context = _positive_int(item.get("context_window"))
            output = _positive_int(item.get("max_output_tokens"))
        else:
            continue
        model_id = model_id.strip()
        if template.template_id == "openai" and any(
            marker in model_id.casefold()
            for marker in (
                "dall-e",
                "embedding",
                "moderation",
                "realtime",
                "transcribe",
                "tts",
                "whisper",
                "image",
                "audio",
            )
        ):
            continue
        if not model_id or len(model_id) > 128 or model_id in seen:
            continue
        seen.add(model_id)
        models.append(DiscoveredModel(model_id, str(display)[:128], context, output))
    return models


def discover_models(
    template_or_adapter: str,
    credential: str,
    *,
    configuration: dict[str, str] | None = None,
    endpoint_override: str | None = None,
    client: httpx.Client | None = None,
) -> list[DiscoveredModel]:
    template = get_template(template_or_adapter)
    endpoint = endpoint_override or resolve_endpoint(template, configuration)
    models = {item: DiscoveredModel(item, item) for item in template.static_models}
    if template.model_list_path:
        owned = client is None
        transport = client or httpx.Client(timeout=15.0, follow_redirects=False)
        try:
            response = transport.get(urljoin(endpoint.rstrip("/") + "/", template.model_list_path.lstrip("/")), headers=_headers(template, credential))
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
            for item in _parse_models(template, response.json()):
                models[item.provider_model_id] = item
        except ModelDiscoveryError:
            raise
        except httpx.TimeoutException as exc:
            raise ModelDiscoveryError("PROVIDER_TIMEOUT", 504) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelDiscoveryError("PROVIDER_NETWORK_FAILED", 502) from exc
        finally:
            if owned:
                transport.close()
    if not models:
        raise ModelDiscoveryError("PROVIDER_MODELS_EMPTY", 502)
    return list(models.values())[:MAX_MODELS]
