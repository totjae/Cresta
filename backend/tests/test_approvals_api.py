from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.approvals import (
    ApprovalError,
)
from app.approvals import (
    approve as approve_service,
)
from app.approvals import (
    reject as reject_service,
)
from app.config import Settings
from app.decision_execution import route_trading_decision
from app.execution_policy import (
    activate_version as activate_execution_version,
)
from app.execution_policy import (
    create_draft as create_execution_draft,
)
from app.execution_policy import (
    validate_draft as validate_execution_draft,
)
from app.models import (
    Approval,
    AuditLog,
    Decision,
    GuardEvaluation,
    MarketSnapshot,
    MarketStreamState,
    TradingGate,
    TradingOrder,
    User,
    WatchlistItem,
)
from app.risk_policy import (
    activate_risk_version,
    create_risk_draft,
    validate_risk_draft,
)
from app.schemas import ExecutionPolicyPayload, RiskPolicyPayload

ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"


def _gate_ready(db: Session) -> None:
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    if gate is None:
        gate = TradingGate(account_alias=ACCOUNT_ALIAS, environment="MOCK", status="READY", reason="TEST", version=1)
        db.add(gate)
    else:
        gate.status = "READY"
        gate.reason = "TEST"
    # Broker worker state needed by the full Risk Guard's BROKER_CONNECTION_OK.
    from app.models import BrokerWorkerState

    worker = db.get(BrokerWorkerState, ACCOUNT_ALIAS)
    now = datetime.now(UTC)
    if worker is None:
        worker = BrokerWorkerState(
            account_alias=ACCOUNT_ALIAS, environment="MOCK", state="READY",
            fencing_token=1, websocket_connected=True, subscriptions_ready=True,
            last_heartbeat_at=now, started_at=now,
        )
        db.add(worker)
    else:
        worker.state = "READY"
        worker.websocket_connected = True
        worker.subscriptions_ready = True
        worker.last_heartbeat_at = now
    db.flush()


def _watch(db: Session, admin: User, symbol: str = "005930") -> None:
    db.add(WatchlistItem(user_id=admin.id, market="KRX", symbol=symbol, created_at=datetime.now(UTC)))
    db.flush()


def _activate_risk(db: Session, admin: User, *, entry_order_amount: int = 500_000) -> None:
    payload = RiskPolicyPayload(
        entry_order_amount=entry_order_amount,
        max_single_order_amount=1_000_000,
        max_position_amount_per_symbol=1_000_000,
        max_total_position_amount=3_000_000,
        max_open_positions=3,
        max_daily_entries=5,
        fixed_stop_loss_pct="-2.0",
        quote_stale_seconds=2,
        max_spread_pct="0.30",
        max_price_deviation_pct="0.50",
    )
    draft = create_risk_draft(db, user=admin, policy=payload, reason="test risk policy")
    validated = validate_risk_draft(db, user=admin, version_id=draft.id)
    activate_risk_version(
        db, user=admin, version_id=validated.id, correlation_id="risk-activation",
        request_ip="127.0.0.1", user_agent="test",
    )


def _activate_execution(db: Session, admin: User, *, buy: str = "MANUAL_APPROVAL") -> None:
    payload = ExecutionPolicyPayload(
        buy=buy, partial_sell="MANUAL_APPROVAL", full_sell="MANUAL_APPROVAL",
        take_profit="MANUAL_APPROVAL", fixed_stop_loss="AUTOMATIC", trailing_stop="AUTOMATIC",
        end_of_day_liquidation="AUTOMATIC", emergency_exit="AUTOMATIC",
    )
    draft = create_execution_draft(db, user=admin, policy=payload, reason="test exec policy")
    validated = validate_execution_draft(db, user=admin, version_id=draft.id)
    activate_execution_version(
        db, user=admin, version_id=validated.id, correlation_id="exec-activation",
        request_ip="127.0.0.1", user_agent="test",
    )


def _stream_fresh(db: Session, snapshot_id: str) -> None:
    stream = MarketStreamState(
        market="KRX", symbol="005930", source="TEST", current_snapshot_id=snapshot_id,
        quality="NORMAL", last_received_at=datetime.now(UTC),
    )
    db.add(stream)
    db.flush()


def _decision(db: Session, *, action: str = "BUY", ask: Decimal = Decimal("101.1")) -> Decision:
    now = datetime.now(UTC)
    snapshot = MarketSnapshot(
        symbol="005930", market="KRX", source="TEST", sequence_or_hash=f"approval-{action}-{ask}",
        source_sequence=1, payload_hash="a" * 64, last_price=ask,
        open_price=ask, high_price=ask, low_price=ask, cumulative_volume=10000,
        best_bid_price=ask - Decimal("0.1"), best_bid_quantity=100,
        best_ask_price=ask, best_ask_quantity=100,
        trading_status="TRADING", quality="NORMAL", recovery_snapshot=False,
        event_at=now, received_at=now,
    )
    db.add(snapshot)
    db.flush()
    _stream_fresh(db, snapshot.id)
    decision = Decision(
        purpose="TRADING", evaluation_request_id=f"approval-evaluation-{action}-{ask}",
        input_snapshot_id=snapshot.id, symbol="005930", market="KRX", decision_kind="ENTRY",
        model_provider="CRESTA", model_id="deterministic-mock-v1", prompt_version="test-v1",
        schema_version="1.0", scout_output_json="{}", core_output_json="{}", action=action,
        confidence=Decimal("0.75"), risk_level="MEDIUM", reason_codes_json="[]",
        valid_until=now + timedelta(minutes=5), configuration_version_id=None,
        execution_mode=None, execution_outcome="NO_ACTION", validation_status="VALID", latency_ms=0,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return decision


def _advance_snapshot(
    db: Session,
    *,
    ask: Decimal,
    received_at: datetime | None = None,
    quality: str = "NORMAL",
) -> MarketSnapshot:
    observed = received_at or datetime.now(UTC)
    snapshot = MarketSnapshot(
        symbol="005930",
        market="KRX",
        source="TEST",
        sequence_or_hash=f"approval-latest-{ask}-{quality}",
        source_sequence=2,
        payload_hash="b" * 64,
        last_price=ask,
        open_price=ask,
        high_price=ask,
        low_price=ask,
        cumulative_volume=10001,
        best_bid_price=ask - Decimal("0.1"),
        best_bid_quantity=100,
        best_ask_price=ask,
        best_ask_quantity=100,
        trading_status="TRADING",
        quality=quality,
        recovery_snapshot=False,
        event_at=observed,
        received_at=observed,
    )
    db.add(snapshot)
    db.flush()
    stream = db.get(MarketStreamState, ("KRX", "005930"))
    assert stream is not None
    stream.current_snapshot_id = snapshot.id
    stream.quality = quality
    stream.last_received_at = observed
    db.commit()
    return snapshot


def _approval_only_settings(settings: Settings) -> Settings:
    settings.execution_stage = "APPROVAL_ONLY"
    return settings


def _seed_full(db: Session, admin: User, settings: Settings, *, buy_mode: str = "MANUAL_APPROVAL") -> Decision:
    _approval_only_settings(settings)
    _gate_ready(db)
    _watch(db, admin)
    _activate_risk(db, admin)
    _activate_execution(db, admin, buy=buy_mode)
    return _decision(db)


def test_buy_manual_approval_creates_pending_approval_no_order(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _seed_full(db, admin, settings)
    execution = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="approval-correlation", settings=settings
    )
    assert execution is not None
    assert execution.state == "APPROVAL_PENDING"
    assert execution.approval_id is not None
    approval = db.get(Approval, execution.approval_id)
    assert approval is not None and approval.state == "PENDING"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_approve_pending_creates_created_order(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _seed_full(db, admin, settings)
    execution = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="approval-correlation", settings=settings
    )
    approval = approve_service(
        db, approval_id=execution.approval_id, user=admin, settings=settings,
        correlation_id="approve-correlation", idempotency_key="approve-key-0001",
    )
    assert approval.state == "APPROVED"
    assert approval.order_id is not None
    order = db.get(TradingOrder, approval.order_id)
    assert order is not None and order.status == "CREATED"
    assert order.side == "BUY" and order.requested_quantity > 0
    db.refresh(execution)
    assert execution.state == "ORDER_CREATED"


def test_approve_uses_latest_snapshot_and_keeps_reference_quantity(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _seed_full(db, admin, settings)
    execution = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="approval-correlation", settings=settings
    )
    pending = db.get(Approval, execution.approval_id)
    assert pending is not None
    scope = json.loads(pending.scope_snapshot_json)
    reference_quantity = int(scope["quantity"])
    latest = _advance_snapshot(db, ask=Decimal("100.7"))

    approval = approve_service(
        db,
        approval_id=execution.approval_id,
        user=admin,
        settings=settings,
        correlation_id="approve-latest",
        idempotency_key="approve-latest-key",
    )

    order = db.get(TradingOrder, approval.order_id)
    assert order is not None
    assert order.limit_price == Decimal("100.7")
    assert order.requested_quantity == reference_quantity
    guard = db.scalar(
        select(GuardEvaluation).where(
            GuardEvaluation.execution_id == execution.id,
            GuardEvaluation.subject_type == "APPROVAL",
        )
    )
    assert guard is not None and guard.snapshot_id == latest.id
    assert scope["reference_snapshot_id"] == decision.input_snapshot_id
    audit = db.scalar(
        select(AuditLog).where(AuditLog.action == "APPROVAL_APPROVED")
    )
    assert audit is not None
    metadata = json.loads(audit.metadata_json)
    assert metadata["reference_snapshot_id"] == decision.input_snapshot_id
    assert metadata["approval_snapshot_id"] == latest.id


def test_approve_invalidates_when_latest_snapshot_is_stale(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _seed_full(db, admin, settings)
    execution = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="approval-correlation", settings=settings
    )
    current = datetime.now(UTC)
    latest = _advance_snapshot(
        db,
        ask=Decimal("101.2"),
        received_at=current - timedelta(seconds=10),
    )

    with pytest.raises(ApprovalError) as exc_info:
        approve_service(
            db,
            approval_id=execution.approval_id,
            user=admin,
            settings=settings,
            correlation_id="approve-stale",
            idempotency_key="approve-stale-key",
            now=current,
        )

    assert exc_info.value.code == "MARKET_DATA_STALE"
    approval = db.get(Approval, execution.approval_id)
    assert approval is not None and approval.state == "INVALIDATED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    guard = db.scalar(
        select(GuardEvaluation).where(
            GuardEvaluation.execution_id == execution.id,
            GuardEvaluation.subject_type == "APPROVAL",
        )
    )
    assert guard is not None and guard.snapshot_id == latest.id
    audit = db.scalar(
        select(AuditLog).where(AuditLog.action == "APPROVAL_INVALIDATED")
    )
    assert audit is not None
    metadata = json.loads(audit.metadata_json)
    assert metadata["reference_snapshot_id"] == decision.input_snapshot_id
    assert metadata["approval_snapshot_id"] == latest.id


def test_approve_invalidates_when_latest_price_exceeds_deviation(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _seed_full(db, admin, settings)
    execution = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="approval-correlation", settings=settings
    )
    latest = _advance_snapshot(db, ask=Decimal("102.0"))

    with pytest.raises(ApprovalError) as exc_info:
        approve_service(
            db,
            approval_id=execution.approval_id,
            user=admin,
            settings=settings,
            correlation_id="approve-deviation",
            idempotency_key="approve-deviation-key",
        )

    assert exc_info.value.code == "PRICE_DEVIATION_EXCEEDED"
    approval = db.get(Approval, execution.approval_id)
    assert approval is not None and approval.state == "INVALIDATED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    guard = db.scalar(
        select(GuardEvaluation).where(
            GuardEvaluation.execution_id == execution.id,
            GuardEvaluation.subject_type == "APPROVAL",
        )
    )
    assert guard is not None and guard.snapshot_id == latest.id


def test_approve_retry_with_stale_version_conflicts_without_duplicate(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _seed_full(db, admin, settings)
    execution = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="approval-correlation", settings=settings
    )
    first = approve_service(
        db, approval_id=execution.approval_id, user=admin, settings=settings,
        correlation_id="approve-1", idempotency_key="approve-key-0002",
    )
    with pytest.raises(ApprovalError) as exc_info:
        approve_service(
            db, approval_id=execution.approval_id, user=admin, settings=settings,
            correlation_id="approve-2", idempotency_key="approve-key-0002",
        )
    assert exc_info.value.code == "APPROVAL_VERSION_CONFLICT"
    assert first.state == "APPROVED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1


def test_reject_pending_terminates_without_order(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _seed_full(db, admin, settings)
    execution = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="approval-correlation", settings=settings
    )
    approval = reject_service(
        db, approval_id=execution.approval_id, user=admin, correlation_id="reject-correlation",
    )
    assert approval.state == "REJECTED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    db.refresh(execution)
    assert execution.state == "REJECTED"


def test_shadow_stage_creates_no_approval(
    db: Session, admin: User, settings: Settings
) -> None:
    # SHADOW stage (default): even with everything configured, no approval/order.
    _gate_ready(db)
    _watch(db, admin)
    _activate_risk(db, admin)
    _activate_execution(db, admin, buy="MANUAL_APPROVAL")
    decision = _decision(db)
    execution = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="shadow-correlation", settings=settings
    )
    assert execution is not None
    assert execution.state == "SHADOW_RECORDED"
    assert execution.approval_id is None
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_approve_expired_approval_rejected(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _seed_full(db, admin, settings)
    execution = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="approval-correlation", settings=settings
    )
    # Force expiry by moving the approval's expires_at into the past.
    approval = db.get(Approval, execution.approval_id)
    approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(ApprovalError):
        approve_service(
            db, approval_id=execution.approval_id, user=admin, settings=settings,
            correlation_id="approve-late", idempotency_key="approve-key-0003",
        )
    db.refresh(approval)
    assert approval.state == "EXPIRED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_buy_automatic_in_approval_only_fails_closed_without_order(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _seed_full(db, admin, settings, buy_mode="AUTOMATIC")
    execution = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="auto-correlation", settings=settings
    )
    assert execution is not None
    assert execution.state == "FAILED_SAFE"
    assert execution.result_code == "AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY"
    assert execution.order_intent_id is None
    assert execution.approval_id is None
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
