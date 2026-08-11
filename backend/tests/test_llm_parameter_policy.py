from app.llm.parameter_policy import (
    is_gemini_3_model,
    is_openai_reasoning_model,
    uses_completion_token_parameter,
)
from app.llm.profiles import _discovered_capabilities


def test_apichat_reasoning_model_family_matching() -> None:
    for model_id in (
        "gpt-5-mini",
        "openai/gpt-5-mini",
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
    assert is_gemini_3_model("google-vertex/gemini-3.6-flash")


def test_llm_gateway_discovers_reasoning_capability_per_model() -> None:
    reasoning = _discovered_capabilities(
        "OPENAI_COMPATIBLE", "llm-gateway", "gpt-5-mini"
    )
    gemini = _discovered_capabilities(
        "OPENAI_COMPATIBLE", "llm-gateway", "gemini-3.1-flash-lite"
    )
    assert reasoning.reasoning is True
    assert reasoning.web_search is True
    assert gemini.reasoning is True
    assert gemini.web_search is True
