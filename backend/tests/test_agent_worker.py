from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.worker import (
    claim_next_stage,
    execute_claimed_stage,
    process_agent_work_once,
    recover_expired_stages,
)
from app.llm.adapters.mock import MockProviderAdapter
from app.llm.contracts import LlmRequest, LlmResult
from app.llm.registry import provider_registry
from app.models import (
    AgentRun,
    AgentStageRun,
    Approval,
    Decision,
    LlmInvocation,
    LlmModelProfile,
    LlmRoleRoute,
    TradingOrder,
)
from tests.test_agent_runtime import _login, _market_fixture, _routes


def _admit(client: TestClient, db: Session) -> str:
    _market_fixture(db)
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": _routes(client, headers),
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "CREATED"
    return response.json()["run_id"]


def test_expired_internal_claim_is_recovered_and_old_fence_cannot_complete(
    client: TestClient, db: Session
) -> None:
    run_id = _admit(client, db)
    now = datetime.now(UTC)
    old_claim = claim_next_stage(
        db,
        worker_id="worker-old",
        lease_seconds=30,
        now=now,
    )
    assert old_claim is not None
    stage = db.get(AgentStageRun, old_claim.stage_id)
    assert stage is not None
    assert stage.role == "INTEL_COLLECTOR"
    assert stage.fencing_token == 1
    stage.lease_expires_at = now - timedelta(seconds=1)
    db.commit()

    assert recover_expired_stages(db, now=now) == 1
    db.refresh(stage)
    assert stage.state == "PENDING"
    assert stage.error_code == "AGENT_LEASE_EXPIRED_RETRY"

    new_claim = claim_next_stage(
        db,
        worker_id="worker-new",
        lease_seconds=30,
        now=now,
    )
    assert new_claim is not None
    assert new_claim.stage_id == old_claim.stage_id
    assert new_claim.fencing_token == 2
    assert not execute_claimed_stage(
        db,
        claim=old_claim,
        worker_id="worker-old",
        now=now,
    )
    assert execute_claimed_stage(
        db,
        claim=new_claim,
        worker_id="worker-new",
        now=now,
    )

    for _ in range(6):
        assert process_agent_work_once(
            db,
            worker_id="worker-new",
            lease_seconds=30,
        )
    run = db.get(AgentRun, run_id)
    assert run is not None
    assert run.state == "PARTIAL"
    assert run.core_action == "WAIT"
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_expired_claim_with_started_invocation_is_ambiguous_and_never_retried(
    client: TestClient, db: Session
) -> None:
    _admit(client, db)
    for _ in range(2):
        assert process_agent_work_once(
            db,
            worker_id="worker-a",
            lease_seconds=30,
        )
    now = datetime.now(UTC)
    claim = claim_next_stage(
        db,
        worker_id="worker-a",
        lease_seconds=30,
        now=now,
    )
    assert claim is not None
    stage = db.get(AgentStageRun, claim.stage_id)
    assert stage is not None and stage.role == "TECHNICAL_SCOUT"
    route = db.get(LlmRoleRoute, stage.route_id)
    assert route is not None
    model = db.get(LlmModelProfile, route.primary_model_profile_id)
    assert model is not None
    invocation = LlmInvocation(
        stage_run_id=stage.id,
        requested_provider_profile_id=model.provider_profile_id,
        requested_model_profile_id=model.id,
        state="RUNNING",
        input_hash=stage.input_hash,
    )
    db.add(invocation)
    db.flush()
    stage.invocation_id = invocation.id
    stage.lease_expires_at = now - timedelta(seconds=1)
    db.commit()

    assert recover_expired_stages(db, now=now) == 1
    db.refresh(stage)
    db.refresh(invocation)
    assert stage.state == "TIMED_OUT"
    assert stage.error_code == "AGENT_INVOCATION_OUTCOME_UNKNOWN"
    assert invocation.state == "AMBIGUOUS"
    assert not execute_claimed_stage(
        db,
        claim=claim,
        worker_id="worker-a",
        now=now,
    )


def test_failed_primary_uses_one_fallback_and_preserves_both_invocations(
    client: TestClient, db: Session, monkeypatch
) -> None:
    _market_fixture(db)
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    route_ids = _routes(client, headers)
    route = db.get(LlmRoleRoute, route_ids["TECHNICAL_SCOUT"])
    assert route is not None
    fallback = db.get(LlmModelProfile, route.primary_model_profile_id)
    assert fallback is not None
    primary = LlmModelProfile(
        provider_profile_id=fallback.provider_profile_id,
        alias="always-fails",
        provider_model_id="always-fails",
        capabilities_json=fallback.capabilities_json,
        max_context_tokens=fallback.max_context_tokens,
        max_output_tokens=fallback.max_output_tokens,
        temperature=fallback.temperature,
        top_p=fallback.top_p,
        reasoning_effort=fallback.reasoning_effort,
        seed=fallback.seed,
        state="VALIDATED",
        validated_at=datetime.now(UTC),
    )
    db.add(primary)
    db.flush()
    route.primary_model_profile_id = primary.id
    route.fallback_policy = "FAILOVER"
    route.fallback_model_profile_ids_json = json.dumps([fallback.id])
    route.version += 1
    db.commit()

    mock = MockProviderAdapter()

    class ConditionalAdapter:
        def generate_structured(self, request: LlmRequest, model_id: str) -> LlmResult:
            if model_id == "always-fails":
                return LlmResult(
                    invocation_id=request.invocation_id,
                    status="PROVIDER_ERROR",
                    actual_provider="CRESTA_MOCK",
                    actual_model=model_id,
                    latency_ms=1,
                )
            return mock.generate_structured(request, model_id)

    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: ConditionalAdapter())
    admitted = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": route_ids,
        },
    )
    assert admitted.status_code == 201, admitted.text
    for _ in range(7):
        assert process_agent_work_once(db, worker_id="fallback-worker", lease_seconds=30)

    completed = client.get(f"/api/v1/ai/agent-runs/{admitted.json()['run_id']}")
    assert completed.status_code == 200
    technical = next(
        stage
        for stage in completed.json()["stages"]
        if stage["role"] == "TECHNICAL_SCOUT"
    )
    assert [item["state"] for item in technical["invocations"]] == [
        "PROVIDER_ERROR",
        "SUCCEEDED",
    ]
    assert technical["invocations"][0]["error_code"] == "LLM_PROVIDER_ERROR"
    assert technical["invocations"][1]["actual_model"] == "deterministic-mock-v2"
    assert technical["invocations"][1]["fallback_path"] == [primary.id, fallback.id]
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0

    route.fallback_policy = "FAIL_STOP"
    route.fallback_model_profile_ids_json = "[]"
    route.version += 1
    db.commit()
    fail_stop_run = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": route_ids,
        },
    )
    assert fail_stop_run.status_code == 201, fail_stop_run.text
    for _ in range(10):
        process_agent_work_once(db, worker_id="fail-stop-worker", lease_seconds=30)
        run = db.get(AgentRun, fail_stop_run.json()["run_id"])
        assert run is not None
        db.refresh(run)
        if run.state == "FAILED":
            break
    assert run.state == "FAILED"
    failed = client.get(f"/api/v1/ai/agent-runs/{run.id}").json()
    failed_technical = next(
        stage for stage in failed["stages"] if stage["role"] == "TECHNICAL_SCOUT"
    )
    assert failed_technical["state"] == "FAILED"
    assert failed_technical["error_code"] == "AGENT_LLM_FAIL_STOP"
    assert len(failed_technical["invocations"]) == 1
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
