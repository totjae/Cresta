from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.worker import process_agent_work_once
from app.llm.contracts import LlmRequest, LlmResult
from app.llm.registry import provider_registry
from app.models import (
    AgentRun,
    Approval,
    Decision,
    LlmModelProfile,
    LlmProviderProfile,
    LlmRoleRoute,
    TradingOrder,
)
from tests.test_agent_runtime import _login, _market_fixture, _routes


class ExternalFixtureAdapter:
    def __init__(self, *, invalid_role: str | None = None) -> None:
        self.invalid_role = invalid_role
        self.requests: list[LlmRequest] = []

    def generate_structured(self, request: LlmRequest, model_id: str) -> LlmResult:
        self.requests.append(request)
        role_input = json.loads(request.messages[-1]["content"])
        if request.role == self.invalid_role:
            output: dict[str, object] = {"status": "SUCCEEDED"}
        elif request.role == "CORE":
            output = {
                "action": "WAIT",
                "confidence": 0.6,
                "risk_level": "MEDIUM",
                "reason_codes": ["EXTERNAL_SHADOW_WAIT"],
                "incomplete_roles": role_input["required_incomplete_roles"],
            }
        else:
            output = {
                "status": "SUCCEEDED",
                "stance": "NEUTRAL",
                "entry_score": 50,
                "exit_risk_score": 50,
                "confidence": 0.6,
                "uncertainty": 0.4,
                "reason_codes": ["EXTERNAL_SHADOW_ASSESSMENT"],
                "evidence_refs": [],
            }
        return LlmResult(
            invocation_id=request.invocation_id,
            status="SUCCEEDED",
            actual_provider="EXTERNAL_FIXTURE",
            actual_model=model_id,
            provider_request_id=f"provider-{request.role.lower()}",
            output_json=output,
            raw_response_hash="b" * 64,
            latency_ms=2,
            input_tokens=10,
            output_tokens=5,
            schema_validation="PASSED",
        )


def _external_routes(
    client: TestClient,
    db: Session,
    monkeypatch,
    *,
    invalid_role: str | None = None,
) -> tuple[dict[str, str], ExternalFixtureAdapter, str]:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    route_ids = _routes(client, headers)
    route = db.get(LlmRoleRoute, next(iter(route_ids.values())))
    assert route is not None
    model = db.get(LlmModelProfile, route.primary_model_profile_id)
    assert model is not None
    provider = db.get(LlmProviderProfile, model.provider_profile_id)
    assert provider is not None
    provider.adapter_type = "OPENAI_RESPONSES"
    provider.endpoint = "https://provider.invalid/v1"
    provider.credential_secret_ref = "external-fixture.secret"
    db.commit()

    adapter = ExternalFixtureAdapter(invalid_role=invalid_role)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        "app.agents.runtime.LlmSecretStore.read", lambda *args, **kwargs: "test-secret"
    )
    return route_ids, adapter, csrf


def _run_until_terminal(db: Session, run_id: str) -> AgentRun:
    for _ in range(12):
        process_agent_work_once(db, worker_id="external-worker", lease_seconds=30)
        run = db.get(AgentRun, run_id)
        assert run is not None
        db.refresh(run)
        if run.state in {"SUCCEEDED", "PARTIAL", "FAILED"}:
            return run
    raise AssertionError("agent run did not terminate")


def test_external_outputs_are_server_validated_and_adopted_without_trading(
    client: TestClient, db: Session, monkeypatch
) -> None:
    _market_fixture(db)
    route_ids, adapter, csrf = _external_routes(client, db, monkeypatch)
    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": route_ids,
        },
    )
    assert response.status_code == 201, response.text
    run = _run_until_terminal(db, response.json()["run_id"])
    assert run.state == "SUCCEEDED"
    assert run.core_action == "WAIT"

    completed = client.get(f"/api/v1/ai/agent-runs/{run.id}").json()
    technical = next(
        stage for stage in completed["stages"] if stage["role"] == "TECHNICAL_SCOUT"
    )
    assert technical["output"]["schema_version"] == "agent-assessment-v1"
    assert technical["output"]["stage_run_id"] == technical["stage_run_id"]
    assert technical["output"]["symbol"] == "005930"
    assert technical["output"]["reason_codes"] == ["EXTERNAL_SHADOW_ASSESSMENT"]
    assert technical["invocation"]["actual_provider"] == "EXTERNAL_FIXTURE"
    assert technical["invocation"]["validation_status"] == "PASSED"
    assert technical["invocation"]["web_search_enabled"] is False
    assert technical["invocation"]["runtime_context_at"] is not None

    scout_request = next(
        request for request in adapter.requests if request.role == "TECHNICAL_SCOUT"
    )
    assert scout_request.output_json_schema["additionalProperties"] is False
    scout_input = json.loads(scout_request.messages[-1]["content"])
    assert scout_input["market_snapshot"]["last_price"] == "70000.0000"
    assert scout_input["indicator_snapshot"]["price_vs_vwap_pct"] == "0.143062"
    assert "credential" not in scout_input
    assert scout_request.tool_policy == "NONE"
    runtime_context = scout_request.messages[-2]["content"]
    assert "[Cresta runtime context v1]" in runtime_context
    assert "Current time:" in runtime_context
    assert "Asia/Seoul" in runtime_context
    assert "Web search is disabled" in runtime_context
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_external_invalid_scout_output_fails_closed(
    client: TestClient, db: Session, monkeypatch
) -> None:
    _market_fixture(db)
    route_ids, _, csrf = _external_routes(
        client, db, monkeypatch, invalid_role="TECHNICAL_SCOUT"
    )
    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": route_ids,
        },
    )
    assert response.status_code == 201, response.text
    run = _run_until_terminal(db, response.json()["run_id"])
    assert run.state == "FAILED"
    completed = client.get(f"/api/v1/ai/agent-runs/{run.id}").json()
    technical = next(
        stage for stage in completed["stages"] if stage["role"] == "TECHNICAL_SCOUT"
    )
    assert technical["state"] == "FAILED"
    assert technical["error_code"] == "AGENT_LLM_FAIL_STOP"
    assert technical["invocation"]["state"] == "INVALID_OUTPUT"
    assert technical["invocation"]["validation_status"] == "FAILED"
    assert technical["invocation"]["error_code"] == "LLM_INVALID_OUTPUT"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
