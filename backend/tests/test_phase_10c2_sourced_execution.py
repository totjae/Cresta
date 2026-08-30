from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.sourced_execution as sourced_module
from app.agents.decision_finalizer import finalize_entry_decision
from app.api.decisions import _response
from app.config import Settings
from app.execution_policy import SAFE_DEFAULT_POLICY
from app.execution_stage import (
    EXECUTION_STAGE_CATEGORY,
    StageResolution,
    StageResolutionStatus,
    canonical_stage_json,
    stage_payload_hash,
)
from app.models import (
    Approval,
    AuditLog,
    ConfigurationVersion,
    DecisionExecution,
    GuardEvaluation,
    OrderIntent,
    TradingOrder,
    User,
)
from app.sourced_execution import (
    SourcedExecutionError,
    execute_sourced_entry_decision,
    reconcile_sourced_entry_executions,
)
from tests.test_phase_9d_decision_finalizer import _completed_trading
from tests.test_v7_decision_agent_execution import _valid_output


def _finalized(client, db, admin, monkeypatch, action: str):
    outputs = None
    if action != "BUY":
        outputs = {
            "CONSERVATIVE_DECISION": _valid_output(action),
            "BALANCED_DECISION": _valid_output(action),
            "AGGRESSIVE_DECISION": _valid_output(action),
        }
    run, _, _, _, loader, _ = _completed_trading(
        client, db, admin, monkeypatch, outputs=outputs
    )
    return finalize_entry_decision(db, run_id=run.id, evidence_loader=loader)


def _activate_shadow(db: Session, admin: User, now: datetime) -> ConfigurationVersion:
    payload = {
        "schema_version": "execution-stage-control-v1",
        "stage": "SHADOW",
        "target": "MOCK",
        "validation_policy_version": "execution-stage-validation-policy-v1",
        "safety_evidence": [],
        "validated_at": now - timedelta(minutes=1),
        "valid_until": now + timedelta(hours=1),
    }
    encoded = canonical_stage_json(payload)
    version = ConfigurationVersion(
        scope="SYSTEM",
        target_id="MOCK",
        category=EXECUTION_STAGE_CATEGORY,
        sequence=1,
        state="ACTIVE",
        payload_json=encoded,
        payload_hash=stage_payload_hash(encoded),
        reason="Phase 10C.2 test",
        created_by=admin.id,
        validated_at=now,
        activated_at=now,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def _activate_mode(db: Session, admin: User, mode: str) -> ConfigurationVersion:
    payload = SAFE_DEFAULT_POLICY.model_copy(update={"buy": mode})
    encoded = json.dumps(
        payload.model_dump(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    version = ConfigurationVersion(
        scope="USER_DEFAULT",
        target_id=admin.id,
        category="EXECUTION_POLICY",
        sequence=1,
        state="ACTIVE",
        payload_json=encoded,
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        reason="Phase 10C.2 test",
        created_by=admin.id,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


@pytest.mark.parametrize("action", ("WAIT", "REJECT", "UNKNOWN"))
def test_no_action_is_exact_once_and_configuration_independent(
    client, db: Session, admin: User, monkeypatch, settings: Settings, action: str
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, action)
    first = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id=f"no-action-{action}",
        settings=settings,
    )
    second = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id="different-retry",
        settings=settings,
    )
    assert first.id == second.id
    assert (first.action, first.mode, first.stage, first.state, first.result_code) == (
        "NO_ACTION",
        None,
        None,
        "NO_ACTION",
        action,
    )
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 1
    assert db.scalar(select(func.count()).select_from(GuardEvaluation)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "SOURCED_DECISION_EXECUTION_TERMINAL"
    )) == 1
    response = _response("request", decision, db)
    assert response.execution is not None
    assert response.execution.mode is None and response.execution.stage is None
    assert decision.execution_mode is None and decision.execution_outcome is None


def test_buy_source_invalid_expired_and_stage_absent_fail_safe(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    decision.source_stage_output_hash = "b" * 64
    db.commit()
    invalid = execute_sourced_entry_decision(
        db, decision=decision, correlation_id="invalid", settings=settings
    )
    assert (invalid.state, invalid.result_code, invalid.stage) == (
        "FAILED_SAFE",
        "SOURCE_AUTHORITY_INVALID",
        None,
    )


def test_expired_buy_precedes_stage_lookup(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    monkeypatch.setattr(
        sourced_module,
        "resolve_current_execution_stage",
        lambda *args, **kwargs: pytest.fail("stage lookup must not run"),
    )
    execution = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id="expired",
        settings=settings,
        now=decision.valid_until + timedelta(seconds=1),
    )
    assert (execution.state, execution.result_code) == ("FAILED_SAFE", "DECISION_EXPIRED")
    assert execution.guard_evaluation_id is None


def test_stage_absent_and_db_retryable_are_distinct(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    unavailable = execute_sourced_entry_decision(
        db, decision=decision, correlation_id="absent", settings=settings
    )
    assert (unavailable.state, unavailable.result_code) == (
        "FAILED_SAFE",
        "EXECUTION_STAGE_UNAVAILABLE",
    )

    db.delete(unavailable)
    db.commit()
    monkeypatch.setattr(
        sourced_module,
        "resolve_current_execution_stage",
        lambda *args, **kwargs: StageResolution(StageResolutionStatus.DB_RETRYABLE_FAILURE),
    )
    with pytest.raises(SourcedExecutionError, match="EXECUTION_STAGE_DB_RETRYABLE_FAILURE"):
        execute_sourced_entry_decision(
            db, decision=decision, correlation_id="retryable", settings=settings
        )
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 0


def test_stage_invalid_ambiguous_and_expired_share_safe_disposition(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    for status in (
        StageResolutionStatus.INVALID,
        StageResolutionStatus.AMBIGUOUS,
        StageResolutionStatus.EXPIRED,
    ):
        monkeypatch.setattr(
            sourced_module,
            "resolve_current_execution_stage",
            lambda *args, _status=status, **kwargs: StageResolution(_status),
        )
        execution = execute_sourced_entry_decision(
            db,
            decision=decision,
            correlation_id=f"stage-{status.value}",
            settings=settings,
        )
        assert (execution.state, execution.result_code, execution.stage) == (
            "FAILED_SAFE",
            "EXECUTION_STAGE_UNAVAILABLE",
            None,
        )
        db.delete(execution)
        db.commit()


def test_expiry_at_shadow_commit_boundary_rolls_back_guard(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    valid_until = decision.valid_until.replace(tzinfo=UTC)
    before = valid_until - timedelta(seconds=1)
    _activate_shadow(db, admin, before)
    monkeypatch.setattr(
        sourced_module,
        "buy_pre_order_guard_rules",
        lambda *args, **kwargs: [{"code": "OK", "result": "PASSED"}],
    )
    moments = iter((before, valid_until + timedelta(seconds=1)))
    execution = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id="expiry-race",
        settings=settings,
        clock=lambda: next(moments),
    )
    assert (execution.state, execution.result_code, execution.stage) == (
        "FAILED_SAFE",
        "DECISION_EXPIRED",
        None,
    )
    assert db.scalar(select(func.count()).select_from(GuardEvaluation)) == 0


@pytest.mark.parametrize(
    ("mode", "rules", "expected_state", "guard_count"),
    (
        ("DISABLED", None, "DISABLED", 0),
        ("MANUAL_APPROVAL", [{"code": "OK", "result": "PASSED"}], "SHADOW_RECORDED", 1),
        ("AUTOMATIC", [{"code": "OK", "result": "PASSED"}], "SHADOW_RECORDED", 1),
        ("MANUAL_APPROVAL", [{"code": "TEST_BLOCK", "result": "BLOCKED"}], "GUARD_BLOCKED", 1),
    ),
)
def test_shadow_modes_guard_and_typed_subject_have_no_downstream_authority(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    mode: str,
    rules,
    expected_state: str,
    guard_count: int,
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    now = datetime.now(UTC)
    stage = _activate_shadow(db, admin, now)
    policy = _activate_mode(db, admin, mode)
    if rules is not None:
        monkeypatch.setattr(
            sourced_module, "buy_pre_order_guard_rules", lambda *args, **kwargs: rules
        )
    execution = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id=f"shadow-{mode}-{expected_state}",
        settings=settings,
        now=now,
    )
    assert execution.state == expected_state
    assert execution.stage == "SHADOW" and execution.mode == mode
    assert execution.execution_stage_version_id == stage.id
    assert execution.execution_stage_payload_hash == stage.payload_hash
    assert execution.execution_policy_version_id == policy.id
    assert db.scalar(select(func.count()).select_from(GuardEvaluation)) == guard_count
    if guard_count:
        guard = db.get(GuardEvaluation, execution.guard_evaluation_id)
        assert guard is not None
        assert (guard.subject_type, guard.subject_id, guard.execution_id) == (
            "DECISION_EXECUTION",
            execution.id,
            execution.id,
        )
        assert guard.stop_trigger_id is None
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_reconciliation_is_manual_deterministic_and_duplicate_safe(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "WAIT")
    original_commit = db.commit
    commit_calls = 0

    def commit_then_report_ambiguity():
        nonlocal commit_calls
        commit_calls += 1
        original_commit()
        if commit_calls == 1:
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("simulated unique-loser/commit ambiguity", {}, None)

    monkeypatch.setattr(db, "commit", commit_then_report_ambiguity)
    result = reconcile_sourced_entry_executions(
        db,
        settings=settings,
        correlation_id_factory=lambda item: f"reconcile-{item.id}",
    )
    again = reconcile_sourced_entry_executions(
        db,
        settings=settings,
        correlation_id_factory=lambda item: f"reconcile-{item.id}",
    )
    assert (result.scanned, result.completed, again.scanned) == (1, 1, 0)
    assert db.scalar(select(func.count()).select_from(DecisionExecution).where(
        DecisionExecution.decision_id == decision.id
    )) == 1
