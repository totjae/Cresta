from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.agents.decision_finalizer as finalizer_module
from app.activation_gate import GateOutcome, GateResolution
from app.agents.decision_context import canonical_context_json, context_digest
from app.agents.decision_finalizer import (
    DecisionFinalizationError,
    build_entry_finalization_identity,
    finalization_evaluation_request_id,
    finalization_identity_json,
    finalize_entry_decision,
    reconcile_v7_entry_finalizations,
)
from app.api.decisions import _response
from app.config import Settings
from app.decision_contracts import DecisionRepresentationError
from app.decision_execution import NO_ACTIONS, route_trading_decision
from app.models import (
    AgentStageRun,
    Approval,
    AuditLog,
    Decision,
    DecisionExecution,
    GuardEvaluation,
    LlmInvocation,
    OrderIntent,
    TradingOrder,
    User,
)
from tests.test_phase_9d_decision_finalizer import _completed_trading
from tests.test_v7_decision_agent_execution import _valid_output


def test_phase_9e_finalization_identity_is_deterministic_and_lineage_sensitive(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, context, stage, _, _, _ = _completed_trading(
        client, db, admin, monkeypatch
    )
    result = finalizer_module.ArbiterResult.model_validate_json(stage.output_json)
    identity = build_entry_finalization_identity(
        run=run,
        context=context,
        arbiter_stage=stage,
        arbiter_result=result,
    )
    encoded = finalization_identity_json(identity)
    evaluation_id = finalization_evaluation_request_id(identity)

    assert finalization_evaluation_request_id(identity) == evaluation_id
    assert re.fullmatch(r"v7fin-[0-9a-f]{58}", evaluation_id)
    assert len(evaluation_id) == 64
    assert set(json.loads(encoded)) == {
        "schema_version",
        "agent_run_id",
        "decision_context_id",
        "decision_context_hash",
        "arbiter_stage_run_id",
        "arbiter_output_hash",
        "consensus_policy_version",
    }
    assert "action" not in encoded and "gate" not in encoded
    for field in (
        "agent_run_id",
        "decision_context_id",
        "decision_context_hash",
        "arbiter_stage_run_id",
        "arbiter_output_hash",
        "consensus_policy_version",
    ):
        changed = identity.model_copy(update={field: "b" * 64})
        assert finalization_evaluation_request_id(changed) != evaluation_id


@pytest.mark.parametrize(
    "corruption",
    (
        "STATE",
        "ROLE",
        "ROUTE",
        "INVOCATION",
        "OUTPUT_MISSING",
        "HASH_MISSING",
        "HASH_MISMATCH",
        "MALFORMED",
        "POLICY",
        "CONTEXT_ID",
        "CONTEXT_HASH",
        "CBA_LINEAGE",
    ),
)
def test_phase_9e_source_stage_and_output_corruption_fails_closed(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    corruption: str,
) -> None:
    run, _, stage, _, loader, _ = _completed_trading(
        client, db, admin, monkeypatch
    )
    payload = json.loads(stage.output_json)
    if corruption == "STATE":
        stage.state = "CONFLICTED"
    elif corruption == "ROLE":
        stage.role = "CORE"
    elif corruption == "ROUTE":
        stage.route_id = db.scalar(
            select(AgentStageRun.route_id).where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.route_id.is_not(None),
            )
        )
    elif corruption == "INVOCATION":
        invocation_id = db.scalar(
            select(LlmInvocation.id).where(LlmInvocation.stage_run_id != stage.id)
        )
        donor = db.scalar(
            select(AgentStageRun).where(AgentStageRun.invocation_id == invocation_id)
        )
        assert donor is not None and invocation_id is not None
        donor.invocation_id = None
        db.flush()
        stage.invocation_id = invocation_id
    elif corruption == "OUTPUT_MISSING":
        stage.output_json = None
    elif corruption == "HASH_MISSING":
        stage.output_hash = None
    elif corruption == "HASH_MISMATCH":
        stage.output_hash = "b" * 64
    elif corruption == "MALFORMED":
        stage.output_json = "{}"
        stage.output_hash = context_digest(stage.output_json)
    else:
        if corruption == "POLICY":
            payload["policy_version"] = "consensus-policy-v999"
        elif corruption == "CONTEXT_ID":
            payload["decision_context_id"] = "different-context"
        elif corruption == "CONTEXT_HASH":
            payload["decision_context_hash"] = "b" * 64
        else:
            payload["input_results"][0]["output_hash"] = "b" * 64
        stage.output_json = canonical_context_json(payload)
        stage.output_hash = context_digest(stage.output_json)
    db.commit()

    with pytest.raises(DecisionFinalizationError, match="SOURCE_CONFLICTED"):
        finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    db.refresh(run)
    assert run.state == "FAILED" and run.error_code == "SOURCE_CONFLICTED"
    assert db.scalar(select(func.count()).select_from(Decision)) == 0


def test_phase_9e_retryable_gate_failure_recovers_once_and_preserves_taxonomy(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _, _, loader, _ = _completed_trading(client, db, admin, monkeypatch)
    original = finalizer_module.verify_frozen_v7_entry_activation_gate
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
    monkeypatch.setattr(
        finalizer_module, "verify_frozen_v7_entry_activation_gate", original
    )

    decision = finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    db.refresh(run)
    assert decision.source_agent_run_id == run.id and run.state == "SUCCEEDED"
    actions = list(
        db.scalars(
            select(AuditLog.action)
            .where(AuditLog.target == run.id)
            .order_by(AuditLog.created_at)
        )
    )
    assert actions.count("FINALIZATION_DB_RETRYABLE_FAILURE") == 1
    assert actions.count("FINALIZATION_SUCCEEDED") == 1
    assert "ACTIVATION_GATE_DB_RETRYABLE_FAILURE" not in actions


def test_phase_9e_duplicate_reconciliation_and_ambiguous_retry_are_exact_once(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _, _, loader, _ = _completed_trading(client, db, admin, monkeypatch)
    assert reconcile_v7_entry_finalizations(db, evidence_loader=loader, limit=10) == 1
    first = db.scalar(select(Decision).where(Decision.source_agent_run_id == run.id))
    assert first is not None
    db.refresh(run)
    completed_at = run.completed_at

    second = finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    assert reconcile_v7_entry_finalizations(db, evidence_loader=loader, limit=10) == 0
    db.refresh(run)
    assert second.id == first.id and run.completed_at == completed_at
    assert db.scalar(select(func.count()).select_from(Decision)) == 1
    assert db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.target == run.id, AuditLog.action == "FINALIZATION_SUCCEEDED")
    ) == 1


def test_phase_9e_sourced_api_revalidates_persisted_context_and_cba_lineage(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _, _, loader, _ = _completed_trading(client, db, admin, monkeypatch)
    decision = finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    cba = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == "CONSERVATIVE_DECISION",
        )
    )
    assert cba is not None
    cba.output_hash = "b" * 64
    db.commit()

    with pytest.raises(
        DecisionRepresentationError, match="SOURCED_DECISION_LINEAGE_INVALID"
    ):
        _response("phase9e", decision, db)


@pytest.mark.parametrize(
    ("outputs", "action"),
    (
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
def test_phase_9e_wait_reject_unknown_route_to_no_action_without_authority(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    outputs: dict[str, object],
    action: str,
) -> None:
    assert {"WAIT", "REJECT", "UNKNOWN"} <= NO_ACTIONS
    run, _, _, _, loader, _ = _completed_trading(
        client, db, admin, monkeypatch, outputs=outputs
    )
    decision = finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)
    assert decision.action == action
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 0

    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id=f"phase9e-no-action-{action}",
        settings=settings,
    )
    assert execution is not None
    assert execution.action == "NO_ACTION" and execution.state == "NO_ACTION"
    assert execution.guard_evaluation_id is None
    assert db.scalar(select(func.count()).select_from(GuardEvaluation)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
