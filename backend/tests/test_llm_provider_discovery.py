from __future__ import annotations

import httpx
import pytest

from app.llm.discovery import ModelDiscoveryError, catalog_items, discover_models


def test_catalog_contains_40_templates_in_required_order() -> None:
    items = catalog_items()
    assert len(items) == 40
    assert [item.template_id for item in items[:3]] == ["openai", "anthropic", "google"]
    assert [item.label for item in items[3:]] == sorted(
        (item.label for item in items[3:]), key=str.casefold
    )
    assert sum(item.can_register for item in items) == 35
    assert {item.template_id for item in items if not item.can_register} == {
        "bedrock", "gemini-express", "copilot", "novelai", "vertex"
    }


def test_openai_discovery_uses_bearer_and_normalizes_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/models"
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(200, json={"data": [{"id": "gpt-test"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        models = discover_models("OPENAI_RESPONSES", "test-secret", client=client)

    assert [(item.provider_model_id, item.display_name) for item in models] == [
        ("gpt-test", "gpt-test")
    ]


def test_gemini_discovery_keeps_only_generate_content_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "gemini-secret"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-test",
                        "displayName": "Gemini Test",
                        "supportedGenerationMethods": ["generateContent"],
                        "inputTokenLimit": 1234,
                        "outputTokenLimit": 567,
                    },
                    {
                        "name": "models/embedding-test",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        models = discover_models(
            "GEMINI_GENERATE_CONTENT", "gemini-secret", client=client
        )

    assert len(models) == 1
    assert models[0].provider_model_id == "gemini-test"
    assert models[0].max_context_tokens == 1234
    assert models[0].max_output_tokens == 567


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "PROVIDER_AUTH_FAILED"), (429, "PROVIDER_RATE_LIMITED"), (500, "PROVIDER_UPSTREAM_FAILED")],
)
def test_discovery_returns_only_safe_error_codes(status: int, expected: str) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text="secret upstream diagnostic")
        )
    ) as client, pytest.raises(ModelDiscoveryError, match=expected) as error:
        discover_models("ANTHROPIC_MESSAGES", "never-log-me", client=client)

    assert error.value.code == expected
    assert "secret upstream diagnostic" not in str(error.value)


def test_discovery_rejects_redirect_and_oversized_response() -> None:
    responses = iter(
        [
            httpx.Response(302, headers={"location": "https://example.invalid"}),
            httpx.Response(200, headers={"content-length": str(6 * 1024 * 1024)}),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))) as client:
        with pytest.raises(ModelDiscoveryError, match="PROVIDER_REDIRECT_REJECTED"):
            discover_models("OPENAI_RESPONSES", "key", client=client)
        with pytest.raises(ModelDiscoveryError, match="PROVIDER_RESPONSE_TOO_LARGE"):
            discover_models("OPENAI_RESPONSES", "key", client=client)
