from __future__ import annotations

import re

_OPENAI_REASONING_MODEL = re.compile(r"^(?:gpt-5|o[134](?:-|$))", re.IGNORECASE)


def is_openai_reasoning_model(model_id: str) -> bool:
    """Return whether an OpenAI-compatible model uses reasoning-model parameters."""

    return _OPENAI_REASONING_MODEL.search(model_id.strip()) is not None


def uses_completion_token_parameter(model_id: str) -> bool:
    """Match APIchat's working chat-completions token parameter selection."""

    return is_openai_reasoning_model(model_id)
