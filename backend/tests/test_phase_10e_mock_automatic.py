from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.sourced_execution as sourced_module
from app.account_authority import (
    append_account_funds_snapshot,
    append_order_capacity_snapshot,
)
from app.config import Settings
from app.execution_authority import ExecutionStage, order_authority_key
from app.execution_policy import SAFE_DEFAULT_POLICY
from app.execution_stage import (
    EXECUTION_STAGE_CATEGORY,
    StageResolution,
    StageResolutionStatus,
    canonical_stage_json,
    stage_payload_hash,
)
from app.financial_authority import build_buy_financial_context
from app.models import (
    Approval,
    AuditLog,
    ConfigurationVersion,
    DecisionExecution,
    GuardEvaluation,
    MarketSnapshot,
    OrderIntent,
    ReauthProof,
    RiskEvent,
    StopTrigger,
    TradingOrder,
    User,
)
from app.sourced_execution import execute_sourced_entry_decision
from app.stop_trigger import recover_exit_pending, run_fixed_stop_triggers
from tests.test_approvals_api import _activate_risk
from tests.test_phase_10c1_foundation import NOW as PHASE_NOW
from tests.test_phase_10c1_foundation import _stage_payload
from tests.test_phase_10c2_sourced_execution import _activate_mode, _finalized
from tests.test_phase_10d_execution_authority import (
    _capacity,
    _funds,
    _policy,
    _stage_resolution,
)
from tests.test_stop_trigger import _position, _set_gate, _snapshot


def _automatic_buy_ready(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
):
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    now = datetime.now(UTC)
    _activate_risk(db, admin, entry_order_amount=500_000)
    _activate_mode(db, admin, "AUTOMATIC")
    resolution = _stage_resolution(db, admin, now, ExecutionStage.MOCK_AUTOMATIC)
    monkeypatch.setattr(
        sourced_module, "resolve_current_execution_stage", lambda *a, **k: resolution
    )
    monkeypatch.setattr(sourced_module, "classify_session", lambda value: "KRX_ONLY")
    monkeypatch.setattr(
        sourced_module,
        "buy_pre_order_guard_rules",
        lambda *a, **k: [{"code": "BASE", "result": "PASSED"}],
    )
    snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
    assert snapshot is not None and snapshot.best_ask_price is not None
    quantity = int(Decimal(500_000) // snapshot.best_ask_price)
    policy = _policy()
    context = build_buy_financial_context(
        symbol=decision.symbol,
        price=snapshot.best_ask_price,
        quantity=quantity,
        frozen_policy=policy,
        current_policy=policy,
    )
    append_account_funds_snapshot(db, _funds(now))
    append_order_capacity_snapshot(db, _capacity(context.request, now))
    db.commit()
    return decision, now


def _activate_stage(
    db: Session, admin: User, stage: ExecutionStage
) -> tuple[ConfigurationVersion, object]:
    payload, artifacts = _stage_payload(stage)
    encoded = canonical_stage_json(payload)
    version = ConfigurationVersion(
        scope="SYSTEM",
        target_id="MOCK",
        category=EXECUTION_STAGE_CATEGORY,
        sequence=1,
        state="ACTIVE",
        payload_json=encoded,
        payload_hash=stage_payload_hash(encoded),
        reason="Phase 10E test",
        created_by=admin.id,
        validated_at=PHASE_NOW,
        activated_at=PHASE_NOW,
    )
    db.add(version)
    db.commit()
    return version, artifacts.__getitem__


def _activate_fixed_stop_policy(db: Session, admin: User) -> ConfigurationVersion:
    payload = SAFE_DEFAULT_POLICY.model_copy(update={"fixed_stop_loss": "AUTOMATIC"})
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
        reason="Phase 10E fixed stop",
        created_by=admin.id,
    )
    db.add(version)
    db.commit()
    return version


def test_sourced_mock_automatic_creates_exact_one_order_without_approval(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    decision, now = _automatic_buy_ready(
        client, db, admin, monkeypatch, settings
    )
    immutable = (
        decision.action,
        decision.reason_codes_json,
        decision.execution_mode,
        decision.execution_outcome,
    )
    first = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id="phase10e-auto",
        settings=settings,
        now=now,
    )
    second = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id="phase10e-retry",
        settings=settings,
        now=now,
    )
    assert first.id == second.id
    assert (first.state, first.result_code) == ("ORDER_CREATED", "ORDER_CREATED")
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 1
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1
    order = db.scalar(select(TradingOrder))
    assert order is not None and order.status == "CREATED" and order.environment == "MOCK"
    intent = db.get(OrderIntent, order.intent_id)
    assert intent is not None
    expected = order_authority_key(
        source_type="DECISION_EXECUTION", source_id=first.id, approval_id=None
    )
    assert intent.authority_key == expected
    assert intent.source_type == "DECISION_EXECUTION"
    assert intent.decision_execution_id == first.id
    assert intent.stop_trigger_id is None and intent.approval_id is None
    assert intent.guard_evaluation_id == first.guard_evaluation_id
    assert immutable == (
        decision.action,
        decision.reason_codes_json,
        decision.execution_mode,
        decision.execution_outcome,
    )


def test_sourced_automatic_transaction_rolls_back_after_order_flush(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    decision, now = _automatic_buy_ready(
        client, db, admin, monkeypatch, settings
    )
    with pytest.raises(RuntimeError, match="phase10e rollback"):
        execute_sourced_entry_decision(
            db,
            decision=decision,
            correlation_id="phase10e-rollback",
            settings=settings,
            now=now,
            before_commit=lambda: (_ for _ in ()).throw(
                RuntimeError("phase10e rollback")
            ),
        )
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 0
    assert db.scalar(select(func.count()).select_from(GuardEvaluation)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    assert db.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "SOURCED_AUTOMATIC_ORDER_CREATED"
        )
    ) == 0


def test_sourced_automatic_strict_mock_mismatch_creates_no_order(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    decision, now = _automatic_buy_ready(
        client, db, admin, monkeypatch, settings
    )
    settings.environment = "LIVE"
    execution = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id="phase10e-live-denied",
        settings=settings,
        now=now,
        financial_client=object(),  # type: ignore[arg-type]
    )
    assert (execution.state, execution.result_code) == (
        "GUARD_BLOCKED",
        "STRICT_MOCK_AUTHORITY",
    )
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


@pytest.mark.parametrize(
    ("downgraded", "code"),
    (
        (ExecutionStage.APPROVAL_ONLY, "AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY"),
        (ExecutionStage.SHADOW, "EXECUTION_STAGE_DOWNGRADED"),
    ),
)
def test_sourced_automatic_rechecks_stage_before_order_creation(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    downgraded: ExecutionStage,
    code: str,
) -> None:
    decision, now = _automatic_buy_ready(
        client, db, admin, monkeypatch, settings
    )
    version = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.category == EXECUTION_STAGE_CATEGORY
        )
    )
    assert version is not None
    resolutions = iter(
        (
            StageResolution(
                StageResolutionStatus.PASS,
                version=version,
                payload=SimpleNamespace(stage=ExecutionStage.MOCK_AUTOMATIC),  # type: ignore[arg-type]
            ),
            StageResolution(
                StageResolutionStatus.PASS,
                version=version,
                payload=SimpleNamespace(stage=ExecutionStage.MOCK_AUTOMATIC),  # type: ignore[arg-type]
            ),
            StageResolution(
                StageResolutionStatus.PASS,
                version=version,
                payload=SimpleNamespace(stage=downgraded),  # type: ignore[arg-type]
            ),
        )
    )
    monkeypatch.setattr(
        sourced_module,
        "resolve_current_execution_stage",
        lambda *a, **k: next(resolutions),
    )
    execution = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id=f"phase10e-stage-{downgraded}",
        settings=settings,
        now=now,
    )
    assert (execution.state, execution.result_code) == ("FAILED_SAFE", code)
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_sourced_automatic_mode_downgrade_uses_manual_path_without_order(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    decision, now = _automatic_buy_ready(
        client, db, admin, monkeypatch, settings
    )
    stage_version = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.category == EXECUTION_STAGE_CATEGORY
        )
    )
    execution_version = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.category == "EXECUTION_POLICY"
        )
    )
    assert stage_version is not None and execution_version is not None
    resolution = StageResolution(
        StageResolutionStatus.PASS,
        version=stage_version,
        payload=SimpleNamespace(stage=ExecutionStage.MOCK_AUTOMATIC),  # type: ignore[arg-type]
    )
    calls = 0

    def downgrade_on_current_lookup(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            payload = SAFE_DEFAULT_POLICY.model_copy(update={"buy": "MANUAL_APPROVAL"})
            encoded = json.dumps(
                payload.model_dump(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            execution_version.payload_json = encoded
            execution_version.payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
            db.flush()
        return resolution

    monkeypatch.setattr(
        sourced_module, "resolve_current_execution_stage", downgrade_on_current_lookup
    )
    execution = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id="phase10e-mode-downgrade",
        settings=settings,
        now=now,
    )
    assert (execution.state, execution.result_code) == (
        "APPROVAL_PENDING",
        "APPROVAL_PENDING",
    )
    assert db.scalar(select(func.count()).select_from(Approval)) == 1
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_fixed_stop_mock_automatic_uses_typed_exact_one_authority(
    db: Session, admin: User, settings: Settings
) -> None:
    _, loader = _activate_stage(db, admin, ExecutionStage.MOCK_AUTOMATIC)
    _activate_fixed_stop_policy(db, admin)
    _activate_risk(db, admin)
    _set_gate(db, "READY")
    position = _position(db, average_price=Decimal(50000), quantity=10)
    _snapshot(db, bid_price=Decimal(49000), received_at=PHASE_NOW)
    run_fixed_stop_triggers(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    )
    run_fixed_stop_triggers(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    )
    trigger = db.scalar(select(StopTrigger).where(StopTrigger.position_id == position.id))
    assert trigger is not None and trigger.state == "FULFILLED"
    assert db.scalar(select(func.count()).select_from(StopTrigger)) == 1
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 1
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    intent = db.scalar(select(OrderIntent))
    guard = db.get(GuardEvaluation, trigger.guard_evaluation_id)
    assert intent is not None and guard is not None
    assert intent.authority_key == order_authority_key(
        source_type="STOP_TRIGGER", source_id=trigger.id, approval_id=None
    )
    assert intent.source_type == "STOP_TRIGGER" and intent.source_id == trigger.id
    assert intent.stop_trigger_id == trigger.id and intent.decision_execution_id is None
    assert intent.approval_id is None and intent.guard_evaluation_id == guard.id
    assert guard.subject_type == "STOP_TRIGGER"
    assert guard.execution_id is None and guard.stop_trigger_id == trigger.id
    assert intent.execution_stage_version_id is not None
    assert intent.execution_stage_payload_hash is not None
    assert intent.execution_policy_version_id is not None
    assert intent.risk_policy_version_id == trigger.risk_policy_version_id


def test_fixed_stop_pause_entry_does_not_block_mock_risk_reduction(
    db: Session, admin: User, settings: Settings
) -> None:
    from app.emergency_stop import activate_pause_entry

    _, loader = _activate_stage(db, admin, ExecutionStage.MOCK_AUTOMATIC)
    _activate_fixed_stop_policy(db, admin)
    _activate_risk(db, admin)
    _set_gate(db, "READY")
    _position(db, average_price=Decimal(50000), quantity=3)
    _snapshot(db, bid_price=Decimal(49000), received_at=PHASE_NOW)
    activate_pause_entry(
        db,
        user=admin,
        reason="Phase 10E risk reduction",
        idempotency_key="phase10e-pause-entry",
        correlation_id="phase10e-pause",
        request_ip="127.0.0.1",
        user_agent="test",
    )
    run_fixed_stop_triggers(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    )
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1


def test_fixed_stop_transaction_rollback_leaves_no_partial_authority(
    db: Session, admin: User, settings: Settings
) -> None:
    _, loader = _activate_stage(db, admin, ExecutionStage.MOCK_AUTOMATIC)
    _activate_fixed_stop_policy(db, admin)
    _activate_risk(db, admin)
    _set_gate(db, "READY")
    _position(db, average_price=Decimal(50000), quantity=10)
    _snapshot(db, bid_price=Decimal(49000), received_at=PHASE_NOW)
    with pytest.raises(RuntimeError, match="fixed stop rollback"):
        run_fixed_stop_triggers(
            db,
            settings=settings,
            now=PHASE_NOW,
            stage_evidence_loader=loader,
            before_commit=lambda: (_ for _ in ()).throw(
                RuntimeError("fixed stop rollback")
            ),
        )
    assert db.scalar(select(func.count()).select_from(StopTrigger)) == 0
    assert db.scalar(select(func.count()).select_from(GuardEvaluation)) == 0
    assert db.scalar(select(func.count()).select_from(RiskEvent)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_fixed_stop_strict_mock_mismatch_keeps_exit_pending(
    db: Session, admin: User, settings: Settings
) -> None:
    _, loader = _activate_stage(db, admin, ExecutionStage.MOCK_AUTOMATIC)
    _activate_fixed_stop_policy(db, admin)
    _activate_risk(db, admin)
    _set_gate(db, "READY")
    _position(db, average_price=Decimal(50000), quantity=10)
    _snapshot(db, bid_price=Decimal(49000), received_at=PHASE_NOW)
    settings.kiwoom_rest_base_url = "https://api.kiwoom.com"
    run_fixed_stop_triggers(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    )
    trigger = db.scalar(select(StopTrigger))
    assert trigger is not None and trigger.state == "EXIT_PENDING"
    assert trigger.result_code == "STRICT_MOCK_AUTHORITY_REQUIRED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_fixed_stop_missing_explicit_action_authority_never_uses_safe_default(
    db: Session, admin: User, settings: Settings
) -> None:
    _, loader = _activate_stage(db, admin, ExecutionStage.MOCK_AUTOMATIC)
    _activate_risk(db, admin)
    _set_gate(db, "READY")
    _position(db, average_price=Decimal(50000), quantity=10)
    _snapshot(db, bid_price=Decimal(49000), received_at=PHASE_NOW)
    run_fixed_stop_triggers(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    )
    trigger = db.scalar(select(StopTrigger))
    assert trigger is not None and trigger.state == "EXIT_PENDING"
    assert trigger.result_code == "FIXED_STOP_ACTION_AUTHORITY_UNAVAILABLE"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    assert db.scalar(select(func.count()).select_from(ReauthProof)) == 0


def test_exit_pending_recovers_only_under_current_mock_automatic_authority(
    db: Session, admin: User, settings: Settings
) -> None:
    _, loader = _activate_stage(db, admin, ExecutionStage.MOCK_AUTOMATIC)
    _activate_fixed_stop_policy(db, admin)
    _activate_risk(db, admin)
    _set_gate(db, "RECONCILING")
    _position(db, average_price=Decimal(50000), quantity=2)
    _snapshot(db, bid_price=Decimal(49000), received_at=PHASE_NOW)
    run_fixed_stop_triggers(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    )
    trigger = db.scalar(select(StopTrigger))
    assert trigger is not None and trigger.state == "EXIT_PENDING"
    _set_gate(db, "READY")
    _snapshot(db, bid_price=Decimal(49000), received_at=PHASE_NOW, sequence=2)
    recover_exit_pending(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    )
    db.refresh(trigger)
    assert trigger.state == "FULFILLED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1
