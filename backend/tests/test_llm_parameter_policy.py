from app.llm.parameter_policy import (
    is_openai_reasoning_model,
    uses_completion_token_parameter,
)
from app.llm.profiles import _discovered_capabilities


def test_apichat_reasoning_model_family_matching() -> None:
    for model_id in (
        "gpt-5-mini",
        "GPT-5.6-sol",
        "o1",
        "o1-mini",
        "o3",
        "o4-mini",
    ):
        assert is_openai_reasoning_model(model_id)
        assert uses_completion_token_parameter(model_id)

    for model_id in ("gpt-4.1", "gemini-3.1-flash-lite", "claude-sonnet-4"):
        assert not is_openai_reasoning_model(model_id)
        assert not uses_completion_token_parameter(model_id)


def test_llm_gateway_discovers_reasoning_capability_per_model() -> None:
    reasoning = _discovered_capabilities(
        "OPENAI_COMPATIBLE", "llm-gateway", "gpt-5-mini"
    )
    regular = _discovered_capabilities(
        "OPENAI_COMPATIBLE", "llm-gateway", "gemini-3.1-flash-lite"
    )
    assert reasoning.reasoning is True
    assert reasoning.web_search is True
    assert regular.reasoning is False
    assert regular.web_search is True
