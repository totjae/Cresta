from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.ids import uuid7
from app.llm.adapters.mock import MockProviderAdapter
from app.llm.contracts import LlmRequest
from app.llm.discovery import DiscoveredModel
from app.models import (
    Approval,
    AuditLog,
    LlmInvocation,
    LlmModelProfile,
    LlmProviderProfile,
    LlmRoleRoute,
    TradingOrder,
)
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET


def _login(client: TestClient) -> str:
    now = datetime.now(UTC)
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    response = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(now),
        },
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_provider_delete_uses_session_csrf_and_is_hidden(client: TestClient, db: Session) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    created = client.post(
        "/api/v1/ai/providers",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "delete-me",
            "adapter_type": "MOCK",
            "endpoint": None,
            "credential_secret_ref": None,
            "data_policy": "NONE",
        },
    )
    assert created.status_code == 201, created.text
    provider_id = created.json()["id"]
    assert client.post(f"/api/v1/ai/providers/{provider_id}/test", headers=headers).status_code == 200
    model = client.post(
        "/api/v1/ai/models",
        headers=headers,
        json={
            "schema_version": "1.0",
            "provider_profile_id": provider_id,
            "alias": "deleted-provider-history-model",
            "provider_model_id": "deterministic-mock-v2",
            "capabilities": {
                "structured_output": True,
                "tool_calling": False,
                "web_search": False,
                "streaming": False,
                "reasoning": False,
                "seed": True,
                "usage_reporting": True,
                "local_execution": True,
            },
            "max_context_tokens": 4096,
            "max_output_tokens": 512,
            "temperature": "0",
        },
    ).json()
    assert client.post(f"/api/v1/ai/models/{model['id']}/validate", headers=headers).status_code == 200
    route = client.post(
        "/api/v1/ai/routes",
        headers=headers,
        json={
            "schema_version": "1.0",
            "role": "TECHNICAL_SCOUT",
            "primary_model_profile_id": model["id"],
            "timeout_ms": 10000,
            "daily_call_limit": 100,
            "daily_cost_limit_krw": "0",
            "prompt_version": "deleted-provider-history-v1",
            "output_schema_version": "agent-assessment-v1",
            "reason": "삭제 후 이력 조회 회귀 검증",
        },
    ).json()
    assert client.post(f"/api/v1/ai/routes/{route['id']}/validate", headers=headers).status_code == 200
    preview = client.post(
        f"/api/v1/ai/providers/{provider_id}/delete-preview", headers=headers
    )
    assert preview.status_code == 200, preview.text
    deleted = client.request(
        "DELETE",
        f"/api/v1/ai/providers/{provider_id}",
        headers=headers,
        json={"schema_version": "1.0"},
    )
    assert deleted.status_code == 204, deleted.text
    assert client.get("/api/v1/ai/providers", headers=headers).json()["items"] == []
    tombstone = db.get(LlmProviderProfile, provider_id)
    assert tombstone is not None
    assert tombstone.deleted_at is not None
    assert tombstone.state == "DISABLED"
    history = client.get("/api/v1/ai/routes", headers=headers)
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["state"] == "SUPERSEDED"
    assert history.json()["items"][0]["primary_model_alias"] == "deleted-provider-history-model"
    assignments = client.get("/api/v1/ai/role-assignments", headers=headers)
    assert assignments.status_code == 200, assignments.text
    technical = next(
        item for item in assignments.json()["items"] if item["role"] == "TECHNICAL_SCOUT"
    )
    assert technical["current"] is None
    assert technical["candidates"] == []
    legacy_route = db.get(LlmRoleRoute, route["id"])
    assert legacy_route is not None
    legacy_route.state = "VALIDATED"
    db.commit()
    legacy_history = client.get("/api/v1/ai/routes", headers=headers)
    assert legacy_history.status_code == 200, legacy_history.text
    legacy_assignments = client.get("/api/v1/ai/role-assignments", headers=headers)
    assert legacy_assignments.status_code == 200, legacy_assignments.text
    legacy_technical = next(
        item
        for item in legacy_assignments.json()["items"]
        if item["role"] == "TECHNICAL_SCOUT"
    )
    assert legacy_technical["candidates"] == []


def test_mock_provider_model_and_shadow_route_lifecycle(client: TestClient, db: Session) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}

    provider_response = client.post(
        "/api/v1/ai/providers",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "cresta-mock",
            "adapter_type": "MOCK",
            "endpoint": None,
            "credential_secret_ref": None,
            "data_policy": "NONE",
        },
    )
    assert provider_response.status_code == 201, provider_response.text
    provider = provider_response.json()
    assert provider["credential_configured"] is False
    assert "credential_secret_ref" not in provider
    assert provider["state"] == "DRAFT"

    tested = client.post(f"/api/v1/ai/providers/{provider['id']}/test", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["provider"]["state"] == "VALIDATED"
    assert tested.json()["external_network_used"] is False
    assert tested.json()["capabilities"]["structured_output"] is True

    model_response = client.post(
        "/api/v1/ai/models",
        headers=headers,
        json={
            "schema_version": "1.0",
            "provider_profile_id": provider["id"],
            "alias": "deterministic-shadow-v1",
            "provider_model_id": "deterministic-mock-v2",
            "capabilities": {
                "structured_output": True,
                "tool_calling": False,
                "web_search": False,
                "streaming": False,
                "reasoning": False,
                "seed": True,
                "usage_reporting": True,
                "local_execution": True,
            },
            "max_context_tokens": 4096,
            "max_output_tokens": 1024,
            "temperature": "0",
        },
    )
    assert model_response.status_code == 201, model_response.text
    model = model_response.json()
    assert model["state"] == "DRAFT"
    validated_model = client.post(f"/api/v1/ai/models/{model['id']}/validate", headers=headers)
    assert validated_model.status_code == 200, validated_model.text
    assert validated_model.json()["state"] == "VALIDATED"

    route_response = client.post(
        "/api/v1/ai/routes",
        headers=headers,
        json={
            "schema_version": "1.0",
            "role": "TECHNICAL_SCOUT",
            "primary_model_profile_id": model["id"],
            "timeout_ms": 600000,
            "daily_call_limit": 100,
            "daily_cost_limit_krw": "0",
            "service_tier": "DEFAULT",
            "prompt_version": "technical-shadow-v1",
            "output_schema_version": "agent-assessment-v1",
            "reason": "foundation SHADOW 계약 검증",
        },
    )
    assert route_response.status_code == 201, route_response.text
    route = route_response.json()
    assert route["execution_stage"] == "SHADOW"
    assert route["failure_policy"] == "FAIL_STOP"
    assert route["timeout_ms"] == 600000
    assert route["service_tier"] == "DEFAULT"
    assert route["fallback_model_profile_id"] is None
    validated_route = client.post(f"/api/v1/ai/routes/{route['id']}/validate", headers=headers)
    assert validated_route.status_code == 200, validated_route.text
    assert validated_route.json()["state"] == "VALIDATED"

    assert db.scalar(select(func.count()).select_from(LlmProviderProfile)) == 1
    assert db.scalar(select(func.count()).select_from(LlmModelProfile)) == 1
    assert db.scalar(select(func.count()).select_from(LlmRoleRoute)) == 1
    assert db.scalar(select(func.count()).select_from(LlmInvocation)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_external_adapter_is_metadata_only_and_never_called(
    client: TestClient, db: Session
) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    response = client.post(
        "/api/v1/ai/providers",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "openai-draft",
            "adapter_type": "OPENAI_RESPONSES",
            "endpoint": "https://api.openai.com/v1",
            "credential_secret_ref": None,
            "data_policy": "EXTERNAL_CLOUD",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["credential_configured"] is False
    assert "credential_secret_ref" not in response.text

    blocked = client.post(f"/api/v1/ai/providers/{payload['id']}/test", headers=headers)
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "PROVIDER_CREDENTIAL_REQUIRED"
    assert db.scalar(select(func.count()).select_from(LlmInvocation)) == 0
    audit_text = "\n".join(db.scalars(select(AuditLog.metadata_json)).all())
    assert "credential_secret_ref" not in audit_text


def test_provider_rejects_mock_secrets_ssrf_and_unknown_fields(client: TestClient) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    mock_secret = client.post(
        "/api/v1/ai/providers",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "bad-mock",
            "adapter_type": "MOCK",
            "credential_secret_ref": "must-not-exist",
            "data_policy": "NONE",
        },
    )
    assert mock_secret.status_code == 422
    assert mock_secret.json()["error"]["code"] == "MOCK_CREDENTIAL_FORBIDDEN"

    external_secret = client.post(
        "/api/v1/ai/providers",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "credential-draft",
            "adapter_type": "OPENAI_RESPONSES",
            "endpoint": "https://api.openai.com/v1",
            "credential_secret_ref": "must-not-be-registered-yet",
            "data_policy": "EXTERNAL_CLOUD",
        },
    )
    assert external_secret.status_code == 422
    assert external_secret.json()["error"]["code"] == "FOUNDATION_CREDENTIAL_FORBIDDEN"

    ssrf = client.post(
        "/api/v1/ai/providers",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "local-http",
            "adapter_type": "OPENAI_COMPATIBLE",
            "endpoint": "http://127.0.0.1:11434/v1",
            "data_policy": "GATEWAY",
        },
    )
    assert ssrf.status_code == 422
    assert ssrf.json()["error"]["code"] == "PROVIDER_ENDPOINT_NOT_ALLOWED"

    ollama_loopback = client.post(
        "/api/v1/ai/providers",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "ollama-local-draft",
            "adapter_type": "OLLAMA_NATIVE",
            "endpoint": "http://127.0.0.1:11434/api",
            "data_policy": "LOCAL",
        },
    )
    assert ollama_loopback.status_code == 201

    raw_secret = client.post(
        "/api/v1/ai/providers",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "raw-secret",
            "adapter_type": "OPENAI_RESPONSES",
            "endpoint": "https://api.openai.com/v1",
            "data_policy": "EXTERNAL_CLOUD",
            "api_key": "this-field-must-never-be-accepted",
        },
    )
    assert raw_secret.status_code == 400


def test_mock_adapter_is_deterministic_and_has_no_tools() -> None:
    request = LlmRequest(
        invocation_id=uuid7(),
        role="TECHNICAL_SCOUT",
        model_profile_id=uuid7(),
        prompt_version="technical-shadow-v1",
        input_schema_version="scout-input-v1",
        input_hash="a" * 64,
        messages=[{"role": "user", "content": "fixture"}],
        output_json_schema={"type": "object"},
        timeout_ms=10000,
        max_output_tokens=1024,
        temperature=0,
    )
    adapter = MockProviderAdapter()
    first = adapter.generate_structured(request, "deterministic-mock-v2")
    second = adapter.generate_structured(request, "deterministic-mock-v2")
    assert first == second
    assert first.schema_validation == "PASSED"
    assert adapter.healthcheck().external_network_used is False
    assert adapter.healthcheck().capabilities.tool_calling is False


def test_external_credential_is_write_only_and_not_stored_in_db(
    client: TestClient,
    db: Session,
    settings: Settings,
    tmp_path,
) -> None:
    settings.llm_secret_directory = str(tmp_path / "llm-secrets")
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    login = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(
                datetime.now(UTC) - timedelta(seconds=30)
            ),
        },
    )
    headers = {
        "Origin": "https://testserver",
        "X-CSRF-Token": login.json()["csrf_token"],
    }
    provider = client.post(
        "/api/v1/ai/providers",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "openai-shadow",
            "adapter_type": "OPENAI_RESPONSES",
            "endpoint": "https://api.openai.com/v1",
            "credential_secret_ref": None,
            "data_policy": "EXTERNAL_CLOUD",
        },
    ).json()
    preview = client.post(
        f"/api/v1/ai/providers/{provider['id']}/credential-preview", headers=headers
    )
    assert preview.status_code == 200, preview.text
    raw_secret = "sk-test-write-only-value"
    configured = client.post(
        f"/api/v1/ai/providers/{provider['id']}/credential",
        headers=headers,
        json={
            "schema_version": "1.0",
            "credential": raw_secret,
        },
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["credential_configured"] is True
    assert raw_secret not in configured.text
    profile = db.get(LlmProviderProfile, provider["id"])
    assert profile is not None
    assert profile.credential_secret_ref == f"provider-{provider['id']}.key"
    assert raw_secret not in "\n".join(db.scalars(select(AuditLog.metadata_json)).all())
    secret_path = tmp_path / "llm-secrets" / profile.credential_secret_ref
    assert secret_path.read_text(encoding="utf-8").strip() == raw_secret
    if os.name == "posix":
        assert secret_path.stat().st_mode & 0o777 == 0o400

    tested = client.post(f"/api/v1/ai/providers/{provider['id']}/test", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["external_network_used"] is False
    assert raw_secret not in tested.text
    model = client.post(
        "/api/v1/ai/models",
        headers=headers,
        json={
            "schema_version": "1.0",
            "provider_profile_id": provider["id"],
            "alias": "openai-shadow-model",
            "provider_model_id": "gpt-test",
            "capabilities": {
                "structured_output": True,
                "tool_calling": False,
                "web_search": False,
                "streaming": False,
                "reasoning": True,
                "seed": False,
                "usage_reporting": True,
                "local_execution": False,
            },
            "max_context_tokens": 4096,
            "max_output_tokens": 256,
            "temperature": "0",
        },
    ).json()
    validated_model = client.post(
        f"/api/v1/ai/models/{model['id']}/validate", headers=headers
    )
    assert validated_model.status_code == 200, validated_model.text
    route = client.post(
        "/api/v1/ai/routes",
        headers=headers,
        json={
            "schema_version": "1.0",
            "role": "TECHNICAL_SCOUT",
            "primary_model_profile_id": model["id"],
            "timeout_ms": 3000,
            "daily_call_limit": 10,
            "daily_cost_limit_krw": "0",
            "prompt_version": "external-shadow-v1",
            "output_schema_version": "agent-assessment-v1",
            "reason": "runtime activation must remain blocked",
        },
    ).json()
    validated_route = client.post(
        f"/api/v1/ai/routes/{route['id']}/validate", headers=headers
    )
    assert validated_route.status_code == 200, validated_route.text
    assert validated_route.json()["state"] == "VALIDATED"
    assert validated_route.json()["execution_stage"] == "SHADOW"
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0

def test_provider_registration_discovers_models_before_persisting(
    client: TestClient,
    db: Session,
    settings: Settings,
    tmp_path,
    monkeypatch,
) -> None:
    settings.llm_secret_directory = str(tmp_path / "registered-secrets")
    monkeypatch.setattr(
        "app.llm.profiles.discover_models",
        lambda adapter_type, credential: [
            DiscoveredModel("gpt-discovered", "GPT Discovered", 8192, 2048)
        ],
    )
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    login = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(
                datetime.now(UTC) - timedelta(seconds=30)
            ),
        },
    )
    headers = {
        "Origin": "https://testserver",
        "X-CSRF-Token": login.json()["csrf_token"],
    }
    preview = client.post(
        "/api/v1/ai/provider-registrations/preview",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "openai-primary",
            "adapter_type": "OPENAI_RESPONSES",
        },
    )
    assert preview.status_code == 200, preview.text
    raw_secret = "registration-write-only-secret"
    registered = client.post(
        "/api/v1/ai/provider-registrations",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "openai-primary",
            "adapter_type": "OPENAI_RESPONSES",
            "credential": raw_secret,
        },
    )
    assert registered.status_code == 201, registered.text
    payload = registered.json()
    assert payload["provider"]["state"] == "VALIDATED"
    assert payload["provider"]["health_status"] == "READY"
    assert payload["models"][0]["provider_model_id"] == "gpt-discovered"
    assert payload["models"][0]["state"] == "DRAFT"
    assert raw_secret not in registered.text
    assert db.scalar(select(func.count()).select_from(LlmProviderProfile)) == 1
    assert db.scalar(select(func.count()).select_from(LlmModelProfile)) == 1
    secret_files = list((tmp_path / "registered-secrets").glob("provider-*.key"))
    assert len(secret_files) == 1
    assert secret_files[0].read_text(encoding="utf-8").strip() == raw_secret
