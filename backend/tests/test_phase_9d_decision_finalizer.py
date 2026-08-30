from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import app.agents.decision_finalizer as finalizer_module
from app.activation_gate import (
    GateOutcome,
    GateResolution,
    VersionSnapshot,
    activate_activation_gate,
    create_activation_gate_draft,
    validate_activation_gate_draft,
    version_snapshot_hash,
)
from app.agents.contracts import ArbiterResult
from app.agents.decision_agents import materialize_decision_agent_stages
from app.agents.decision_finalizer import (
    FINALIZATION_IDENTITY_SCHEMA,
    DecisionFinalizationError,
    build_entry_finalization_identity,
    build_sourced_entry_decision_intent,
    finalization_evaluation_request_id,
    finalization_identity_json,
    finalize_entry_decision,
    reconcile_v7_diagnostic_lifecycle,
    reconcile_v7_entry_finalizations,
    validate_finalization_source,
)
from app.agents.worker import process_agent_work_once
from app.decision_execution import NO_ACTIONS
from app.llm.registry import provider_registry
from app.models import (
    AgentStageRun,
    Approval,
    AuditLog,
    Decision,
    DecisionExecution,
    LlmInvocation,
    OrderIntent,
    TradingOrder,
    User,
)
from tests.test_phase_9c1_foundation import _gate_payload
from tests.test_phase_9c2_trading_runtime import _activate_gate, _admit_trading
from tests.test_v7_decision_agent_execution import (
    DecisionFixtureAdapter,
    _run_decisions,
    _valid_output,
)
from tests.test_v7_decision_agent_foundation import _complete_upstream
from tests.test_v7_entry_arbiter import _complete_decisions
from tests.test_v7_entry_arbiter_acceptance import _execute_arbiter


def _completed_trading(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    *,
    outputs: dict[str, dict[str, object] | str] | None = None,
):
    run, _, settings, gate, _, loader, _ = _admit_trading(
        client, db, admin, monkeypatch
    )
    context = _complete_upstream(db, run, monkeypatch, settings)
    materialize_decision_agent_stages(db, run_id=run.id, now=datetime.now(UTC))
    adapter = DecisionFixtureAdapter(db, outputs=outputs)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    _run_decisions(db)
    assert process_agent_work_once(
        db,
        worker_id="phase9d-arbiter-reconcile",
        lease_seconds=60,
        now=datetime.now(UTC),
    )
    assert process_agent_work_once(
        db,
        worker_id="phase9d-arbiter-execute",
        lease_seconds=60,
        now=datetime.now(UTC),
    )
    db.refresh(run)
    assert run.state == "RUNNING" and run.completed_at is None
    stage = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == "ENTRY_ARBITER",
        )
    )
    assert stage is not None and stage.state == "SUCCEEDED"
    return run, context, stage, gate, loader, adapter


def _close_gate(db: Session, admin: User, gate) -> None:
    now = datetime.now(UTC)
    payload, _ = _gate_payload(state="CLOSED")
    payload["version_snapshot"] = json.loads(gate.payload_json)["version_snapshot"]
    payload["version_snapshot_hash"] = version_snapshot_hash(payload["version_snapshot"])
    payload["validated_at"] = now - timedelta(minutes=1)
    payload["valid_until"] = now + timedelta(hours=1)
    closed = create_activation_gate_draft(
        db,
        user=admin,
        payload=payload,
        reason="Phase 9D closure",
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


@pytest.mark.parametrize(
    ("outputs", "expected_action"),
    (
        ({}, "BUY"),
        (
            {
                "CONSERVATIVE_DECISION": _valid_output("WAIT"),
                "BALANCED_DECISION": _valid_output("WAIT"),
                "AGGRESSIVE_DECISION": _valid_output("WAIT"),
            },
            "WAIT",
        ),
        (
            {
                "CONSERVATIVE_DECISION": _valid_output("REJECT"),
                "BALANCED_DECISION": _valid_output("REJECT"),
            },
            "REJECT",
        ),
        ({"BALANCED_DECISION": "TIMEOUT"}, "UNKNOWN"),
    ),
)
def test_finalizer_preserves_all_four_actions_and_exact_sourced_payload(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    outputs: dict[str, dict[str, object] | str],
    expected_action: str,
) -> None:
    run, context, stage, _, loader, adapter = _completed_trading(
        client, db, admin, monkeypatch, outputs=outputs
    )
    invocation_count = db.scalar(select(func.count()).select_from(LlmInvocation))
    decision = finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    arbiter = ArbiterResult.model_validate_json(stage.output_json)
    db.refresh(run)

    assert decision.action == expected_action == arbiter.action
    assert decision.reason_codes_json == json.dumps(
        arbiter.reason_codes, separators=(",", ":"), sort_keys=True
    )
    assert (
        decision.schema_version,
        decision.purpose,
        decision.decision_kind,
        decision.validation_status,
    ) == ("sourced-entry-decision-v1", "TRADING", "ENTRY", "VALID")
    assert decision.decision_input_id == context.decision_input_snapshot_id
    assert (
        decision.source_agent_run_id,
        decision.source_stage_run_id,
        decision.source_stage_output_hash,
    ) == (run.id, stage.id, stage.output_hash)
    assert all(
        getattr(decision, field) is None
        for field in (
            "confidence",
            "risk_level",
            "model_provider",
            "model_id",
            "prompt_version",
            "scout_output_json",
            "core_output_json",
            "latency_ms",
            "execution_mode",
            "execution_outcome",
            "configuration_version_id",
        )
    )
    assert run.state == "SUCCEEDED" and run.error_code is None
    assert run.completed_at is not None
    assert len(adapter.requests) == 3
    assert db.scalar(select(func.count()).select_from(LlmInvocation)) == invocation_count
    assert db.scalar(select(func.count()).select_from(Decision)) == 1
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.target == run.id,
            AuditLog.action == "FINALIZATION_SUCCEEDED",
        )
    )
    assert audit is not None and audit.result == "SUCCEEDED"
    assert set(json.loads(audit.metadata_json)) == {
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
    response = client.get(f"/api/v1/decisions/{decision.id}")
    assert response.status_code == 200, response.text
    assert response.json()["action"] == expected_action
    assert response.json()["schema_version"] == "sourced-entry-decision-v1"
    list_response = client.get("/api/v1/decisions")
    assert list_response.status_code == 200, list_response.text
    listed = next(
        item for item in list_response.json()["items"] if item["decision_id"] == decision.id
    )
    assert listed["action"] == expected_action
    assert listed["schema_version"] == "sourced-entry-decision-v1"


def test_finalization_identity_is_exact_canonical_and_excludes_action_and_gate(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, context, stage, _, _, _ = _completed_trading(
        client, db, admin, monkeypatch
    )
    result = ArbiterResult.model_validate_json(stage.output_json)
    identity = build_entry_finalization_identity(
        run=run,
        context=context,
        arbiter_stage=stage,
        arbiter_result=result,
    )
    encoded = finalization_identity_json(identity)
    request_id = finalization_evaluation_request_id(identity)
    assert identity.schema_version == FINALIZATION_IDENTITY_SCHEMA
    assert set(json.loads(encoded)) == {
        "schema_version",
        "agent_run_id",
        "decision_context_id",
        "decision_context_hash",
        "arbiter_stage_run_id",
        "arbiter_output_hash",
        "consensus_policy_version",
    }
    assert len(request_id) == 64 and request_id.startswith("v7fin-")
    assert "action" not in encoded and "gate" not in encoded


@pytest.mark.parametrize(
    ("transition", "expected_code", "expected_state"),
    (
        ("SUPERSEDED", "ACTIVATION_GATE_SUPERSEDED", "CANCELLED"),
        ("CLOSED", "ACTIVATION_GATE_CLOSED", "CANCELLED"),
        ("INVALID", "ACTIVATION_GATE_INVALID", "FAILED"),
    ),
)
def test_live_gate_denials_terminalize_without_decision(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    transition: str,
    expected_code: str,
    expected_state: str,
) -> None:
    run, _, _, gate, loader, _ = _completed_trading(client, db, admin, monkeypatch)
    if transition == "SUPERSEDED":
        snapshot = VersionSnapshot.model_validate(
            json.loads(gate.payload_json)["version_snapshot"]
        )
        _, loader = _activate_gate(db, admin, snapshot, now=datetime.now(UTC))
    elif transition == "CLOSED":
        _close_gate(db, admin, gate)
        loader = None
    else:
        gate.payload_json = "{}"
        db.commit()

    with pytest.raises(DecisionFinalizationError, match=expected_code):
        finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    db.refresh(run)
    assert run.state == expected_state and run.error_code == expected_code
    assert run.completed_at is not None
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    audit = db.scalar(
        select(AuditLog).where(AuditLog.target == run.id, AuditLog.action == expected_code)
    )
    assert audit is not None


@pytest.mark.parametrize("corruption", ("EXPIRED", "HASH", "ROLE"))
def test_source_expiry_and_corruption_fail_closed(
    client, db: Session, admin: User, monkeypatch, corruption: str
) -> None:
    run, context, stage, _, loader, _ = _completed_trading(
        client, db, admin, monkeypatch
    )
    if corruption == "EXPIRED":
        context.valid_until = datetime.now(UTC) - timedelta(seconds=1)
        expected = "SOURCE_EXPIRED"
    elif corruption == "HASH":
        stage.output_hash = "b" * 64
        expected = "SOURCE_CONFLICTED"
    else:
        stage.role = "CORE"
        expected = "SOURCE_CONFLICTED"
    db.commit()

    with pytest.raises(DecisionFinalizationError, match=expected):
        finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    db.refresh(run)
    assert run.state == "FAILED" and run.error_code == expected
    assert db.scalar(select(func.count()).select_from(Decision)) == 0


def test_success_retry_is_exact_once_and_preserves_completed_at(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _, _, loader, _ = _completed_trading(client, db, admin, monkeypatch)
    first = finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    db.refresh(run)
    completed_at = run.completed_at
    second = finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    db.refresh(run)
    assert second.id == first.id
    assert run.completed_at == completed_at
    assert db.scalar(select(func.count()).select_from(Decision)) == 1
    assert db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.target == run.id, AuditLog.action == "FINALIZATION_SUCCEEDED")
    ) == 1


def test_conflicting_existing_decision_is_not_mutated(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _, _, loader, _ = _completed_trading(client, db, admin, monkeypatch)
    source = validate_finalization_source(db, run=run, now=datetime.now(UTC))
    intent = build_sourced_entry_decision_intent(source)
    conflicting = dict(intent.values)
    conflicting["action"] = "WAIT" if conflicting["action"] != "WAIT" else "REJECT"
    existing = Decision(**conflicting)
    db.add(existing)
    db.commit()
    original_action = existing.action

    with pytest.raises(DecisionFinalizationError, match="FINALIZATION_IDENTITY_CONFLICT"):
        finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    db.refresh(existing)
    db.refresh(run)
    assert existing.action == original_action
    assert db.scalar(select(func.count()).select_from(Decision)) == 1
    assert run.state == "FAILED"
    assert run.error_code == "FINALIZATION_IDENTITY_CONFLICT"


def test_post_flush_database_failure_rolls_back_and_remains_retryable(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _, _, loader, _ = _completed_trading(client, db, admin, monkeypatch)

    def fail_after_flush() -> None:
        raise SQLAlchemyError("injected post-flush failure")

    with pytest.raises(
        DecisionFinalizationError, match="FINALIZATION_DB_RETRYABLE_FAILURE"
    ):
        finalize_entry_decision(
            db,
            run_id=run.id,
            evidence_loader=loader,
            write_boundary_hook=fail_after_flush,
        )
    db.refresh(run)
    assert run.state == "RUNNING" and run.completed_at is None
    assert run.error_code == "FINALIZATION_DB_RETRYABLE_FAILURE"
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.target == run.id, AuditLog.action == "FINALIZATION_SUCCEEDED")
    ) == 0


def test_gate_database_failure_is_retryable_and_non_terminal(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _, _, loader, _ = _completed_trading(client, db, admin, monkeypatch)
    monkeypatch.setattr(
        finalizer_module,
        "verify_frozen_v7_entry_activation_gate",
        lambda *args, **kwargs: GateResolution(GateOutcome.DB_RETRYABLE_FAILURE),
    )
    with pytest.raises(
        DecisionFinalizationError, match="FINALIZATION_DB_RETRYABLE_FAILURE"
    ):
        finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    db.refresh(run)
    assert run.state == "RUNNING" and run.completed_at is None
    assert run.error_code == "FINALIZATION_DB_RETRYABLE_FAILURE"
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.target == run.id,
            AuditLog.action == "FINALIZATION_DB_RETRYABLE_FAILURE",
        )
    )
    assert audit is not None and json.loads(audit.metadata_json)["retryable"] is True


@pytest.mark.parametrize("boundary_failure", ("GATE", "EXPIRY"))
def test_write_boundary_recheck_rolls_back_staged_decision(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    boundary_failure: str,
) -> None:
    run, context, _, _, loader, _ = _completed_trading(
        client, db, admin, monkeypatch
    )
    expected = (
        "ACTIVATION_GATE_SUPERSEDED" if boundary_failure == "GATE" else "SOURCE_EXPIRED"
    )
    if boundary_failure == "GATE":
        calls = 0
        original = finalizer_module._gate_resolution

        def changed_gate(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original(*args, **kwargs)
            return GateResolution(GateOutcome.SUPERSEDED)

        monkeypatch.setattr(finalizer_module, "_gate_resolution", changed_gate)

        def boundary_hook() -> None:
            return None

    else:

        def boundary_hook() -> None:
            context.valid_until = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(DecisionFinalizationError, match=expected):
        finalize_entry_decision(
            db,
            run_id=run.id,
            evidence_loader=loader,
            write_boundary_hook=boundary_hook,
        )
    db.refresh(run)
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert run.state in {"FAILED", "CANCELLED"} and run.error_code == expected


def test_worker_idle_reconciliation_is_opportunistic_and_exact_once(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _, _, loader, _ = _completed_trading(client, db, admin, monkeypatch)
    assert process_agent_work_once(
        db,
        worker_id="phase9d-idle-finalizer",
        lease_seconds=60,
        now=datetime.now(UTC),
        finalization_evidence_loader=loader,
    )
    db.refresh(run)
    assert run.state == "SUCCEEDED"
    assert db.scalar(select(func.count()).select_from(Decision)) == 1


def test_reconciliation_recovers_completed_arbiter_and_sourced_api_roundtrips_unknown(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, context, _, _, loader, _ = _completed_trading(
        client,
        db,
        admin,
        monkeypatch,
        outputs={"BALANCED_DECISION": "TIMEOUT"},
    )
    assert reconcile_v7_entry_finalizations(db, evidence_loader=loader, limit=10) == 1
    decision = db.scalar(select(Decision).where(Decision.source_agent_run_id == run.id))
    assert decision is not None and decision.action == "UNKNOWN"

    response = client.get(f"/api/v1/decisions/{decision.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "sourced-entry-decision-v1"
    assert body["action"] == "UNKNOWN"
    assert body["confidence"] is None and body["risk_level"] is None
    assert body["execution"] is None
    assert body["lineage"]["decision_context_id"] == context.id
    assert len(body["lineage"]["input_results"]) == 3
    assert "UNKNOWN" in NO_ACTIONS


@pytest.mark.parametrize(
    "outputs",
    (
        {},
        {
            "CONSERVATIVE_DECISION": _valid_output("WAIT"),
            "BALANCED_DECISION": _valid_output("WAIT"),
            "AGGRESSIVE_DECISION": _valid_output("WAIT"),
        },
        {
            "CONSERVATIVE_DECISION": _valid_output("REJECT"),
            "BALANCED_DECISION": _valid_output("REJECT"),
        },
        {"BALANCED_DECISION": "TIMEOUT"},
    ),
    ids=("BUY", "WAIT", "REJECT", "UNKNOWN"),
)
def test_diagnostic_arbiter_success_closes_run_without_decision(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    outputs: dict[str, dict[str, object] | str],
) -> None:
    run, _, _ = _complete_decisions(
        client,
        db,
        admin,
        monkeypatch,
        outputs=outputs,
    )
    _execute_arbiter(db, prefix="phase9d-diagnostic")
    db.refresh(run)
    assert run.state == "SUCCEEDED"
    assert run.completed_at is not None and run.error_code is None
    assert db.scalar(select(func.count()).select_from(Decision)) == 0


@pytest.mark.parametrize(
    ("stage_state", "expected_code"),
    (
        ("CONFLICTED", "ENTRY_ARBITER_CONFLICTED"),
        ("TIMED_OUT", "ENTRY_ARBITER_TIMED_OUT"),
        ("FAILED", "ENTRY_ARBITER_FAILED"),
    ),
)
def test_diagnostic_arbiter_failures_close_with_exact_error(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    stage_state: str,
    expected_code: str,
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    assert process_agent_work_once(
        db,
        worker_id=f"phase9d-diag-materialize-{stage_state}",
        lease_seconds=60,
        now=datetime.now(UTC),
    )
    stage = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == "ENTRY_ARBITER",
        )
    )
    assert stage is not None
    stage.state = stage_state
    stage.output_json = None
    stage.output_hash = None
    stage.completed_at = datetime.now(UTC)
    db.commit()
    assert reconcile_v7_diagnostic_lifecycle(db, run_id=run.id, limit=1) == 1
    db.refresh(run)
    assert run.state == "FAILED" and run.error_code == expected_code
    assert run.completed_at is not None
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
