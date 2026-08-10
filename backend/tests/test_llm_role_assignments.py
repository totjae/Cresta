from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Approval, LlmModelProfile, LlmRoleRoute, TradingOrder
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET

ROLES = (
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
    "CORE",
)


def _login(client: TestClient) -> str:
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    response = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(
                datetime.now(UTC) - timedelta(seconds=30)
            ),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _foundation(client: TestClient, headers: dict[str, str]) -> str:
    provider = client.post(
        "/api/v1/ai/providers",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "assignment-mock",
            "adapter_type": "MOCK",
            "endpoint": None,
            "credential_secret_ref": None,
            "data_policy": "NONE",
        },
    ).json()
    assert client.post(f"/api/v1/ai/providers/{provider['id']}/test", headers=headers).status_code == 200
    model_response = client.post(
        "/api/v1/ai/models",
        headers=headers,
        json={
            "schema_version": "1.0",
            "provider_profile_id": provider["id"],
            "alias": "shared-model",
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
            "temperature": "0.2",
            "top_p": "0.9",
            "reasoning_effort": None,
            "seed": 7,
        },
    )
    assert model_response.status_code == 201, model_response.text
    model = model_response.json()
    assert client.post(f"/api/v1/ai/models/{model['id']}/validate", headers=headers).status_code == 200
    return model["id"]


def _candidate(
    client: TestClient,
    headers: dict[str, str],
    *,
    role: str,
    model_id: str,
    reasoning: str | None = None,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/ai/routes",
        headers=headers,
        json={
            "schema_version": "1.0",
            "role": role,
            "primary_model_profile_id": model_id,
            "timeout_ms": 10000,
            "daily_call_limit": 100,
            "daily_cost_limit_krw": "0",
            "prompt_version": f"{role.lower()}-shadow-v1",
            "output_schema_version": "agent-core-v1"
            if role == "CORE"
            else "agent-assessment-v1",
            "temperature_override": "0.1",
            "top_p_override": None,
            "max_output_tokens_override": 512,
            "reasoning_effort_override": reasoning,
            "seed_override": 11,
            "reason": "역할별 모델 배정 검증",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_role_assignments_reuse_model_and_activate_atomically(
    client: TestClient, db: Session
) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    model_id = _foundation(client, headers)
    selected: dict[str, str] = {}
    for role in ROLES:
        route = _candidate(client, headers, role=role, model_id=model_id)
        validated = client.post(f"/api/v1/ai/routes/{route['id']}/validate", headers=headers)
        assert validated.status_code == 200, validated.text
        selected[role] = str(route["id"])
        assert validated.json()["effective_parameters"]["temperature"] == "0.100"
        assert validated.json()["effective_parameters"]["temperature_source"] == "ROLE_OVERRIDE"
        assert validated.json()["effective_parameters"]["top_p"] == "0.900"
        assert validated.json()["effective_parameters"]["top_p_source"] == "MODEL_DEFAULT"

    duplicate = _candidate(client, headers, role="TECHNICAL_SCOUT", model_id=model_id)
    assert client.post(f"/api/v1/ai/routes/{duplicate['id']}/validate", headers=headers).status_code == 200
    assignments = client.get("/api/v1/ai/role-assignments").json()["items"]
    technical = next(item for item in assignments if item["role"] == "TECHNICAL_SCOUT")
    assert technical["status"] == "AMBIGUOUS"
    assert technical["current"] is None
    assert len(technical["candidates"]) == 2

    preview = client.post(
        "/api/v1/ai/role-assignments/activation-preview",
        headers=headers,
        json={"schema_version": "1.0", "route_ids": selected},
    )
    assert preview.status_code == 200, preview.text
    activated = client.post(
        "/api/v1/ai/role-assignments/activate",
        headers=headers,
        json={
            "schema_version": "1.0",
            "route_ids": selected,
        },
    )
    assert activated.status_code == 200, activated.text
    assert {route["state"] for route in activated.json()["routes"]} == {"ACTIVE"}
    assert db.scalar(
        select(func.count()).select_from(LlmRoleRoute).where(LlmRoleRoute.state == "ACTIVE")
    ) == 5
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_role_candidate_rejects_unsupported_reasoning_parameter(client: TestClient) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    model_id = _foundation(client, headers)
    route = _candidate(
        client,
        headers,
        role="CORE",
        model_id=model_id,
        reasoning="HIGH",
    )
    response = client.post(f"/api/v1/ai/routes/{route['id']}/validate", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODEL_PARAMETER_UNSUPPORTED_REASONING"


def test_role_candidate_accepts_one_explicit_fallback_model(
    client: TestClient, db: Session
) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    primary_id = _foundation(client, headers)
    primary = db.get(LlmModelProfile, primary_id)
    assert primary is not None
    fallback_response = client.post(
        "/api/v1/ai/models",
        headers=headers,
        json={
            "schema_version": "1.0",
            "provider_profile_id": primary.provider_profile_id,
            "alias": "fallback-model",
            "provider_model_id": "deterministic-mock-fallback-v1",
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
            "seed": 7,
        },
    )
    assert fallback_response.status_code == 201, fallback_response.text
    fallback = fallback_response.json()
    assert (
        client.post(
            f"/api/v1/ai/models/{fallback['id']}/validate", headers=headers
        ).status_code
        == 200
    )
    route_response = client.post(
        "/api/v1/ai/routes",
        headers=headers,
        json={
            "schema_version": "1.0",
            "role": "CORE",
            "primary_model_profile_id": primary_id,
            "failure_policy": "FAILOVER",
            "fallback_model_profile_id": fallback["id"],
            "prompt_version": "core-failover-v1",
            "output_schema_version": "agent-core-v1",
            "reason": "단일 fallback 검증",
        },
    )
    assert route_response.status_code == 201, route_response.text
    route = route_response.json()
    assert route["failure_policy"] == "FAILOVER"
    assert route["fallback_model_profile_id"] == fallback["id"]
    assert route["fallback_model_alias"] == "fallback-model"
    assert (
        client.post(f"/api/v1/ai/routes/{route['id']}/validate", headers=headers).status_code
        == 200
    )

    invalid = client.post(
        "/api/v1/ai/routes",
        headers=headers,
        json={
            "schema_version": "1.0",
            "role": "CORE",
            "primary_model_profile_id": primary_id,
            "failure_policy": "FAILOVER",
            "fallback_model_profile_id": primary_id,
            "prompt_version": "core-invalid-fallback-v1",
            "output_schema_version": "agent-core-v1",
            "reason": "동일 모델 fallback 거부",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "ROUTE_FALLBACK_EQUALS_PRIMARY"
