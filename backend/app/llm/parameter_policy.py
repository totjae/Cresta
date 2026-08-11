from __future__ import annotations

import re

_OPENAI_REASONING_MODEL = re.compile(
    r"^(?:openai/)?(?:gpt-5|o[134](?:-|$))", re.IGNORECASE
)
_GEMINI_3_MODEL = re.compile(r"(?:^|/)gemini-3(?:[.-]|$)", re.IGNORECASE)
_SERVICE_TIER_TEMPLATES = frozenset({"openai", "llm-gateway", "vercel-ai"})


def is_openai_reasoning_model(model_id: str) -> bool:
    """Return whether an OpenAI-compatible model uses reasoning-model parameters."""

    return _OPENAI_REASONING_MODEL.search(model_id.strip()) is not None


def uses_completion_token_parameter(model_id: str) -> bool:
    """Match APIchat's working chat-completions token parameter selection."""

    return is_openai_reasoning_model(model_id)


def is_gemini_3_model(model_id: str) -> bool:
    return _GEMINI_3_MODEL.search(model_id.strip()) is not None


def supports_service_tier(provider_template_id: str | None) -> bool:
    return provider_template_id in _SERVICE_TIER_TEMPLATES
