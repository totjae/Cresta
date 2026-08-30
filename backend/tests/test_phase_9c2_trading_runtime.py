from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.agents.runtime as runtime_module
from app.activation_gate import (
    GateOutcome,
    GateResolution,
    VersionSnapshot,
    activate_activation_gate,
    build_actual_version_snapshot,
    create_activation_gate_draft,
    validate_activation_gate_draft,
    version_snapshot_hash,
)
from app.agents.contracts import ArbiterResult
from app.agents.decision_agents import V7_LLM_ROUTE_ROLES, materialize_decision_agent_stages
from app.agents.entry_arbiter import build_entry_arbiter_input
from app.agents.runtime import AgentRuntimeError, create_v7_upstream_trading_run
from app.agents.worker import process_agent_work_once
from app.config import Settings
from app.llm.profiles import ASSIGNMENT_ROLES, activate_assignments
from app.llm.registry import provider_registry
from app.models import (
    AgentRun,
    AgentStageRun,
    Approval,
    AuditLog,
    Decision,
    DecisionExecution,
    DecisionInputSnapshot,
    OrderIntent,
    TradingOrder,
    User,
)
from tests.test_agent_runtime import _login, _market_fixture, _routes
from tests.test_phase_9c1_foundation import _gate_payload
from tests.test_policy_profile_admission import _all_profiles
from tests.test_v7_decision_agent_execution import (
    DecisionFixtureAdapter,
    _run_decisions,
    _valid_output,
)
from tests.test_v7_decision_agent_foundation import _complete_upstream
from tests.test_v7_upstream_runtime import _extend_v7_decision_routes


def _setup(client, db: Session, admin: User, monkeypatch):
    _market_fixture(db)
    _all_profiles(db, admin)
    db.commit()
    csrf = _login(client)
    routes = _routes(client, {"Origin": "https://testserver", "X-CSRF-Token": csrf})
    _extend_v7_decision_routes(client, db, routes, csrf)
    settings = Settings(quote_stale_seconds=30)
    monkeypatch.setattr(runtime_module, "get_settings", lambda: settings)
    route_ids = {role: routes[role] for role in V7_LLM_ROUTE_ROLES}
    activate_assignments(
        db,
        user=admin,
        route_ids={role: routes[role] for role in ASSIGNMENT_ROLES},
        correlation_id=str(uuid4()),
    )
    bindings = runtime_module._load_routes(
        db,
        owner_id=admin.id,
        route_ids=route_ids,
        required_roles=V7_LLM_ROUTE_ROLES,
    )
    frozen = runtime_module.select_active_policy_profiles(db)
    snapshot = build_actual_version_snapshot(
        policy_version_map=json.loads(frozen.manifest_json),
        route_versions=runtime_module._route_version_snapshot(db, bindings),
    )
    return route_ids, settings, snapshot


def _activate_gate(db: Session, admin: User, snapshot, *, now: datetime):
    payload, artifacts = _gate_payload()
    payload["version_snapshot"] = snapshot.model_dump()
    payload["version_snapshot_hash"] = version_snapshot_hash(snapshot)
    payload["validated_at"] = now - timedelta(minutes=2)
    payload["valid_until"] = now + timedelta(hours=1)
    for evidence in payload["safety_evidence"]:
        evidence["executed_at"] = now - timedelta(minutes=3)
        evidence["valid_until"] = now + timedelta(hours=1)
    loader = artifacts.__getitem__
    version = create_activation_gate_draft(
        db,
        user=admin,
        payload=payload,
        reason="Phase 9C.2 trading admission",
        now=now,
        evidence_loader=loader,
    )
    validate_activation_gate_draft(
        db,
        version_id=version.id,
        now=now,
        evidence_loader=loader,
    )
    activate_activation_gate(
        db,
        user=admin,
        version_id=version.id,
        now=now,
        evidence_loader=loader,
        correlation_id=str(uuid4()),
        request_ip="test",
        user_agent="pytest",
    )
    return version, loader


def _admit_trading(client, db: Session, admin: User, monkeypatch):
    route_ids, settings, snapshot = _setup(client, db, admin, monkeypatch)
    now = datetime.now(UTC)
    gate, loader = _activate_gate(db, admin, snapshot, now=now)
    run, created = create_v7_upstream_trading_run(
        db,
        user=admin,
        market="KRX",
        symbol="005930",
        route_ids=route_ids,
        correlation_id=str(uuid4()),
        evidence_loader=loader,
        now=now,
    )
    return run, created, settings, gate, route_ids, loader, now


def test_trading_admission_requires_gate_and_persists_exact_denial_audit(
    client, db: Session, admin: User, monkeypatch
) -> None:
    route_ids, _, _ = _setup(client, db, admin, monkeypatch)
    correlation_id = str(uuid4())
    with pytest.raises(AgentRuntimeError, match="ACTIVATION_GATE_CLOSED"):
        create_v7_upstream_trading_run(
            db,
            user=admin,
            market="KRX",
            symbol="005930",
            route_ids=route_ids,
            correlation_id=correlation_id,
            evidence_loader=None,
            now=datetime.now(UTC),
        )
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert db.scalar(select(func.count()).select_from(AgentStageRun)) == 0
    assert db.scalar(select(func.count()).select_from(DecisionInputSnapshot)) == 0
    audit = db.scalar(select(AuditLog).where(AuditLog.correlation_id == correlation_id))
    assert audit is not None
    assert (audit.action, audit.result, audit.target) == (
        "ACTIVATION_GATE_CLOSED",
        "BLOCKED",
        "V7_ENTRY_ACTIVATION:MOCK",
    )
    metadata = json.loads(audit.metadata_json)
    assert set(metadata) == {
        "schema_version",
        "agent_run_id",
        "decision_id",
        "evaluation_request_id",
        "decision_context_id",
        "source_stage_run_id",
        "source_stage_output_hash",
        "activation_gate_version_id",
        "activation_gate_version_hash",
        "retryable",
    }
    assert metadata["evaluation_request_id"] == correlation_id
    assert metadata["retryable"] is False


def test_trading_admission_freezes_gate_and_is_idempotent_and_purpose_separated(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, created, _, gate, route_ids, loader, now = _admit_trading(
        client, db, admin, monkeypatch
    )
    assert created is True
    assert run.purpose == "TRADING"
    assert (run.activation_gate_version_id, run.activation_gate_version_hash) == (
        gate.id,
        gate.payload_hash,
    )
    assert len(
        list(db.scalars(select(AgentStageRun).where(AgentStageRun.run_id == run.id)))
    ) == 7
    decision_input = db.scalar(
        select(DecisionInputSnapshot).where(DecisionInputSnapshot.input_hash == run.input_hash)
    )
    assert decision_input is not None and decision_input.purpose == "TRADING"
    assert json.loads(decision_input.input_json)["purpose"] == "TRADING"

    retried, retry_created = create_v7_upstream_trading_run(
        db,
        user=admin,
        market="KRX",
        symbol="005930",
        route_ids=route_ids,
        correlation_id=str(uuid4()),
        evidence_loader=loader,
        now=now,
    )
    assert retry_created is False and retried.id == run.id

    diagnostic, diagnostic_created = runtime_module.create_v7_upstream_diagnostic_run(
        db,
        user=admin,
        market="KRX",
        symbol="005930",
        route_ids=route_ids,
        now=now,
    )
    assert diagnostic_created is True
    assert diagnostic.id != run.id and diagnostic.input_hash != run.input_hash
    assert diagnostic.activation_gate_version_id is None


def test_same_trading_identity_with_changed_gate_fails_closed_without_mutation(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _, gate_a, route_ids, _, now = _admit_trading(
        client, db, admin, monkeypatch
    )
    gate_snapshot = VersionSnapshot.model_validate(
        json.loads(gate_a.payload_json)["version_snapshot"]
    )
    gate_b, loader_b = _activate_gate(db, admin, gate_snapshot, now=now)
    correlation_id = str(uuid4())
    with pytest.raises(AgentRuntimeError, match="ACTIVATION_GATE_SUPERSEDED"):
        create_v7_upstream_trading_run(
            db,
            user=admin,
            market="KRX",
            symbol="005930",
            route_ids=route_ids,
            correlation_id=correlation_id,
            evidence_loader=loader_b,
            now=now,
        )
    db.refresh(run)
    assert (run.activation_gate_version_id, run.activation_gate_version_hash) == (
        gate_a.id,
        gate_a.payload_hash,
    )
    assert gate_b.id != gate_a.id
    assert len(
        list(db.scalars(select(AgentStageRun).where(AgentStageRun.run_id == run.id)))
    ) == 7
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 1
    audit = db.scalar(select(AuditLog).where(AuditLog.correlation_id == correlation_id))
    assert audit is not None and audit.action == "ACTIVATION_GATE_SUPERSEDED"


@pytest.mark.parametrize(
    "mutation",
    ("policy", "scout_route", "decision_route", "model", "prompt"),
)
def test_trading_admission_rejects_every_actual_snapshot_mismatch(
    client, db: Session, admin: User, monkeypatch, mutation: str
) -> None:
    route_ids, _, snapshot = _setup(client, db, admin, monkeypatch)
    now = datetime.now(UTC)
    gate, loader = _activate_gate(db, admin, snapshot, now=now)
    changed = snapshot.model_dump()
    if mutation == "policy":
        changed["policy_profiles"][0]["payload_hash"] = "b" * 64
    elif mutation == "scout_route":
        changed["routes"][0]["route_version_hash"] = "b" * 64
    elif mutation == "decision_route":
        changed["routes"][4]["route_version_hash"] = "b" * 64
    elif mutation == "model":
        changed["routes"][0]["model_version"] += 1
    elif mutation == "prompt":
        changed["routes"][4]["prompt_content_hash"] = "b" * 64
    monkeypatch.setattr(
        runtime_module,
        "build_actual_version_snapshot",
        lambda **_kwargs: VersionSnapshot.model_validate(changed),
    )
    correlation_id = str(uuid4())
    with pytest.raises(AgentRuntimeError, match="ACTIVATION_GATE_INVALID"):
        create_v7_upstream_trading_run(
            db,
            user=admin,
            market="KRX",
            symbol="005930",
            route_ids=route_ids,
            correlation_id=correlation_id,
            evidence_loader=loader,
            now=now,
        )
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
    audit = db.scalar(select(AuditLog).where(AuditLog.correlation_id == correlation_id))
    assert audit is not None and audit.action == "ACTIVATION_GATE_INVALID"
    assert json.loads(audit.metadata_json)["activation_gate_version_id"] == gate.id


@pytest.mark.parametrize(
    ("outcome", "action", "result", "retryable"),
    (
        (GateOutcome.INVALID, "ACTIVATION_GATE_INVALID", "INVALID", False),
        (
            GateOutcome.DB_RETRYABLE_FAILURE,
            "ACTIVATION_GATE_DB_RETRYABLE_FAILURE",
            "RETRYABLE_FAILURE",
            True,
        ),
    ),
)
def test_trading_gate_failure_classification_is_audited_without_partial_run(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    outcome: GateOutcome,
    action: str,
    result: str,
    retryable: bool,
) -> None:
    route_ids, _, _ = _setup(client, db, admin, monkeypatch)
    monkeypatch.setattr(
        runtime_module,
        "select_current_v7_entry_activation_gate",
        lambda *args, **kwargs: GateResolution(outcome),
    )
    correlation_id = str(uuid4())
    with pytest.raises(AgentRuntimeError, match=action):
        create_v7_upstream_trading_run(
            db,
            user=admin,
            market="KRX",
            symbol="005930",
            route_ids=route_ids,
            correlation_id=correlation_id,
            evidence_loader=None,
            now=datetime.now(UTC),
        )
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
    audit = db.scalar(select(AuditLog).where(AuditLog.correlation_id == correlation_id))
    assert audit is not None and (audit.action, audit.result) == (action, result)
    assert json.loads(audit.metadata_json)["retryable"] is retryable


@pytest.mark.parametrize(
    ("gate_transition", "outputs", "expected_action"),
    (
        ("UNCHANGED", {}, "BUY"),
        (
            "UNCHANGED",
            {
                "CONSERVATIVE_DECISION": _valid_output("WAIT"),
                "BALANCED_DECISION": _valid_output("WAIT"),
                "AGGRESSIVE_DECISION": _valid_output("WAIT"),
            },
            "WAIT",
        ),
        (
            "UNCHANGED",
            {
                "CONSERVATIVE_DECISION": _valid_output("REJECT"),
                "BALANCED_DECISION": _valid_output("REJECT"),
            },
            "REJECT",
        ),
        ("UNCHANGED", {"BALANCED_DECISION": "TIMEOUT"}, "UNKNOWN"),
        ("SUPERSEDED", {}, "BUY"),
        ("CLOSED", {}, "BUY"),
    ),
)
def test_trading_runs_full_v7_pipeline_to_arbiter_without_downstream_authority(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    gate_transition: str,
    outputs: dict[str, object],
    expected_action: str,
) -> None:
    run, _, settings, gate, _, _, _ = _admit_trading(client, db, admin, monkeypatch)
    frozen_gate = (run.activation_gate_version_id, run.activation_gate_version_hash)
    if gate_transition == "SUPERSEDED":
        gate_snapshot = VersionSnapshot.model_validate(
            json.loads(gate.payload_json)["version_snapshot"]
        )
        _activate_gate(db, admin, gate_snapshot, now=datetime.now(UTC))
    elif gate_transition == "CLOSED":
        now = datetime.now(UTC)
        payload, _ = _gate_payload(state="CLOSED")
        payload["version_snapshot"] = json.loads(gate.payload_json)["version_snapshot"]
        payload["version_snapshot_hash"] = version_snapshot_hash(
            payload["version_snapshot"]
        )
        payload["validated_at"] = now - timedelta(minutes=1)
        payload["valid_until"] = now + timedelta(hours=1)
        closed = create_activation_gate_draft(
            db,
            user=admin,
            payload=payload,
            reason="Phase 9C.2 mid-pipeline closure",
            now=now,
            evidence_loader=None,
        )
        validate_activation_gate_draft(
            db, version_id=closed.id, now=now, evidence_loader=None
        )
        activate_activation_gate(
            db,
            user=admin,
            version_id=closed.id,
            now=now,
            evidence_loader=None,
            correlation_id=str(uuid4()),
            request_ip="test",
            user_agent="pytest",
        )
    context = _complete_upstream(db, run, monkeypatch, settings)
    materialize_decision_agent_stages(db, run_id=run.id, now=datetime.now(UTC))
    adapter = DecisionFixtureAdapter(db, outputs=outputs)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    _run_decisions(db)
    assert process_agent_work_once(
        db, worker_id="phase9c2-arbiter-reconcile", lease_seconds=60, now=datetime.now(UTC)
    )
    assert process_agent_work_once(
        db, worker_id="phase9c2-arbiter-execute", lease_seconds=60, now=datetime.now(UTC)
    )
    arbiter = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == "ENTRY_ARBITER",
        )
    )
    assert arbiter is not None and arbiter.state == "SUCCEEDED"
    result = ArbiterResult.model_validate_json(arbiter.output_json)
    assert result.decision_context_id == context.id and result.action == expected_action
    assert len(adapter.requests) == 3
    assert run.purpose == "TRADING"
    assert (run.activation_gate_version_id, run.activation_gate_version_hash) == frozen_gate
    assert run.state == "RUNNING" and run.completed_at is None
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    provider_payload = json.dumps(
        [request.messages for request in adapter.requests], sort_keys=True
    )
    assert "activation_gate" not in provider_payload and "gate_state" not in provider_payload
    arbiter_input = build_entry_arbiter_input(db, run_id=run.id, now=datetime.now(UTC))
    arbiter_payload = arbiter_input.model_dump_json()
    assert "activation_gate" not in arbiter_payload and "gate_state" not in arbiter_payload
