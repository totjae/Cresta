from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.approvals import ApprovalError, approve
from app.config import Settings
from app.decision_execution import route_trading_decision
from app.execution_policy import activate_version, create_draft, validate_draft
from app.models import (
    Approval,
    Decision,
    MarketSnapshot,
    MarketStreamState,
    Position,
    TradingGate,
    TradingOrder,
    User,
)
from app.order_creation import OrderRequest, create_order
from app.schemas import ExecutionPolicyPayload

ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"
NOW = datetime(2026, 8, 13, 1, 30, tzinfo=UTC)  # Thursday 10:30 KST


def _activate_execution_policy(
    db: Session,
    user: User,
    *,
    partial_sell: str = "MANUAL_APPROVAL",
    full_sell: str = "MANUAL_APPROVAL",
) -> None:
    policy = ExecutionPolicyPayload(
        buy="MANUAL_APPROVAL",
        partial_sell=partial_sell,
        full_sell=full_sell,
        take_profit="MANUAL_APPROVAL",
        fixed_stop_loss="AUTOMATIC",
        trailing_stop="AUTOMATIC",
        end_of_day_liquidation="AUTOMATIC",
        emergency_exit="AUTOMATIC",
    )
    draft = create_draft(db, user=user, policy=policy, reason="decision sell test")
    validated = validate_draft(db, user=user, version_id=draft.id)
    activate_version(
        db,
        user=user,
        version_id=validated.id,
        correlation_id="sell-policy",
        request_ip="127.0.0.1",
        user_agent="pytest",
    )


def _seed_market(db: Session, *, bid: Decimal = Decimal(70000)) -> MarketSnapshot:
    snapshot = MarketSnapshot(
        symbol="005930",
        market="KRX",
        source="TEST",
        sequence_or_hash=f"sell-{bid}",
        source_sequence=1,
        payload_hash="c" * 64,
        last_price=bid + Decimal(100),
        open_price=bid,
        high_price=bid + Decimal(200),
        low_price=bid - Decimal(200),
        cumulative_volume=100_000,
        best_bid_price=bid,
        best_bid_quantity=100,
        best_ask_price=bid + Decimal(100),
        best_ask_quantity=100,
        trading_status="TRADING",
        quality="NORMAL",
        recovery_snapshot=False,
        event_at=NOW,
        received_at=NOW,
    )
    db.add(snapshot)
    db.flush()
    db.add(
        MarketStreamState(
            market="KRX",
            symbol="005930",
            source="TEST",
            current_snapshot_id=snapshot.id,
            last_sequence=1,
            last_event_at=NOW,
            last_received_at=NOW,
            cumulative_volume=100_000,
            quality="NORMAL",
        )
    )
    db.add(
        TradingGate(
            account_alias=ACCOUNT_ALIAS,
            environment="MOCK",
            status="READY",
            reason="TEST",
            version=1,
        )
    )
    db.flush()
    return snapshot


def _seed_position(
    db: Session,
    *,
    quantity: int = 10,
    available_quantity: int = 8,
    managed_quantity: int = 6,
) -> Position:
    origin = (
        "EXTERNAL"
        if managed_quantity == 0
        else "CRESTA_MANAGED"
        if managed_quantity == quantity
        else "MIXED"
    )
    position = Position(
        account_alias=ACCOUNT_ALIAS,
        symbol="005930",
        quantity=quantity,
        available_quantity=available_quantity,
        managed_quantity=managed_quantity,
        average_price=Decimal(68000),
        managed_average_price=Decimal(68000) if managed_quantity else Decimal(0),
        state="OPEN",
        origin=origin,
        version=3,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(position)
    db.flush()
    return position


def _advance_market(db: Session, *, bid: Decimal) -> MarketSnapshot:
    snapshot = MarketSnapshot(
        symbol="005930",
        market="KRX",
        source="TEST",
        sequence_or_hash=f"sell-latest-{bid}",
        source_sequence=2,
        payload_hash="d" * 64,
        last_price=bid + Decimal(100),
        open_price=bid,
        high_price=bid + Decimal(200),
        low_price=bid - Decimal(200),
        cumulative_volume=100_100,
        best_bid_price=bid,
        best_bid_quantity=100,
        best_ask_price=bid + Decimal(100),
        best_ask_quantity=100,
        trading_status="TRADING",
        quality="NORMAL",
        recovery_snapshot=False,
        event_at=NOW,
        received_at=NOW,
    )
    db.add(snapshot)
    db.flush()
    stream = db.get(MarketStreamState, ("KRX", "005930"))
    assert stream is not None
    stream.current_snapshot_id = snapshot.id
    stream.last_sequence = 2
    stream.last_event_at = NOW
    stream.last_received_at = NOW
    db.commit()
    return snapshot


def _decision(
    db: Session,
    snapshot: MarketSnapshot,
    *,
    action: str,
    sell_ratio: str | None = None,
) -> Decision:
    core_output: dict[str, object] = {"action": action}
    if sell_ratio is not None:
        core_output["sell_ratio"] = sell_ratio
    decision = Decision(
        purpose="TRADING",
        evaluation_request_id=f"sell-{action}-{sell_ratio}-{snapshot.id}",
        input_snapshot_id=snapshot.id,
        symbol="005930",
        market="KRX",
        decision_kind="POSITION",
        model_provider="CRESTA",
        model_id="deterministic-mock-v1",
        prompt_version="sell-test-v1",
        schema_version="1.0",
        scout_output_json="{}",
        core_output_json=json.dumps(core_output),
        action=action,
        confidence=Decimal("0.80"),
        risk_level="HIGH",
        reason_codes_json="[]",
        valid_until=NOW + timedelta(minutes=5),
        execution_mode=None,
        execution_outcome="NO_ACTION",
        validation_status="VALID",
        latency_ms=0,
        created_at=NOW,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return decision


def _settings(settings: Settings) -> Settings:
    settings.execution_stage = "APPROVAL_ONLY"
    return settings


def test_partial_sell_manual_approval_uses_floor_of_managed_sellable_quantity(
    db: Session, admin: User, settings: Settings
) -> None:
    _activate_execution_policy(db, admin)
    snapshot = _seed_market(db)
    position = _seed_position(db, quantity=20, available_quantity=8, managed_quantity=10)
    decision = _decision(db, snapshot, action="PARTIAL_SELL", sell_ratio="0.5")

    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="partial-manual",
        settings=_settings(settings),
        now=NOW,
    )

    assert execution is not None and execution.state == "APPROVAL_PENDING"
    approval = db.get(Approval, execution.approval_id)
    assert approval is not None
    scope = json.loads(approval.scope_snapshot_json)
    assert scope["quantity"] == 4
    assert scope["position_id"] == position.id
    assert scope["position_version"] == 3
    assert scope["reference_price"] == "70000"


def test_partial_sell_below_one_share_is_guard_blocked(
    db: Session, admin: User, settings: Settings
) -> None:
    _activate_execution_policy(db, admin)
    snapshot = _seed_market(db)
    _seed_position(db, quantity=5, available_quantity=5, managed_quantity=5)
    decision = _decision(db, snapshot, action="PARTIAL_SELL", sell_ratio="0.01")

    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="partial-zero",
        settings=_settings(settings),
        now=NOW,
    )

    assert execution is not None and execution.state == "GUARD_BLOCKED"
    assert execution.result_code == "QUANTITY_BELOW_ONE"
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_full_sell_automatic_creates_sell_for_managed_quantity_only(
    db: Session, admin: User, settings: Settings
) -> None:
    _activate_execution_policy(db, admin, full_sell="AUTOMATIC")
    snapshot = _seed_market(db)
    position = _seed_position(db, quantity=10, available_quantity=8, managed_quantity=6)
    decision = _decision(db, snapshot, action="FULL_SELL")

    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="full-auto",
        settings=_settings(settings),
        now=NOW,
    )

    assert execution is not None and execution.state == "ORDER_CREATED"
    order = db.scalar(select(TradingOrder))
    assert order is not None
    assert order.side == "SELL"
    assert order.requested_quantity == position.managed_quantity == 6
    assert order.limit_price == Decimal(70000)
    assert order.unfilled_policy == "NONE"


def test_external_only_position_is_never_sold(
    db: Session, admin: User, settings: Settings
) -> None:
    _activate_execution_policy(db, admin, full_sell="AUTOMATIC")
    snapshot = _seed_market(db)
    _seed_position(db, quantity=10, available_quantity=10, managed_quantity=0)
    decision = _decision(db, snapshot, action="FULL_SELL")

    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="external-blocked",
        settings=_settings(settings),
        now=NOW,
    )

    assert execution is not None and execution.state == "GUARD_BLOCKED"
    assert execution.result_code == "POSITION_MANAGED_QUANTITY_POSITIVE"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_existing_sell_reservation_blocks_duplicate_decision_sell(
    db: Session, admin: User, settings: Settings
) -> None:
    _activate_execution_policy(db, admin, full_sell="AUTOMATIC")
    snapshot = _seed_market(db)
    _seed_position(db, quantity=10, available_quantity=8, managed_quantity=6)
    create_order(
        db,
        user=admin,
        request=OrderRequest(
            symbol="005930",
            market="KRX",
            side="SELL",
            action="FIXED_STOP",
            order_type="LIMIT",
            limit_price=Decimal(70000),
            quantity=2,
            idempotency_key="existing-sell-reservation",
            request_payload={"kind": "existing-sell", "quantity": 2},
            correlation_id="existing-sell",
        ),
        audit_action="TEST_EXISTING_SELL",
        now=NOW,
    )
    db.commit()
    decision = _decision(db, snapshot, action="FULL_SELL")

    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="duplicate-sell-blocked",
        settings=_settings(settings),
        now=NOW,
    )

    assert execution is not None and execution.state == "GUARD_BLOCKED"
    assert execution.result_code == "NO_ACTIVE_OR_UNKNOWN_ORDER"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1


def test_manual_full_sell_approval_creates_sell_order(
    db: Session, admin: User, settings: Settings
) -> None:
    _activate_execution_policy(db, admin)
    snapshot = _seed_market(db)
    position = _seed_position(db, quantity=10, available_quantity=8, managed_quantity=6)
    decision = _decision(db, snapshot, action="FULL_SELL")
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="full-manual",
        settings=_settings(settings),
        now=NOW,
    )

    approval = approve(
        db,
        approval_id=execution.approval_id,
        user=admin,
        settings=settings,
        correlation_id="full-approved",
        idempotency_key="full-sell-approval",
        now=NOW,
    )

    order = db.get(TradingOrder, approval.order_id)
    assert order is not None and order.side == "SELL"
    assert order.requested_quantity == position.managed_quantity == 6
    assert order.limit_price == Decimal(70000)


def test_sell_approval_invalidates_when_position_version_changes(
    db: Session, admin: User, settings: Settings
) -> None:
    _activate_execution_policy(db, admin)
    snapshot = _seed_market(db)
    position = _seed_position(db, quantity=10, available_quantity=8, managed_quantity=6)
    decision = _decision(db, snapshot, action="FULL_SELL")
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="full-stale",
        settings=_settings(settings),
        now=NOW,
    )
    position.version = 4
    db.commit()

    with pytest.raises(ApprovalError) as error:
        approve(
            db,
            approval_id=execution.approval_id,
            user=admin,
            settings=settings,
            correlation_id="full-stale-approved",
            idempotency_key="full-stale-approval",
            now=NOW,
        )

    assert error.value.code == "POSITION_VERSION_MATCH"
    approval = db.get(Approval, execution.approval_id)
    assert approval is not None and approval.state == "INVALIDATED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_sell_approval_invalidates_on_price_deviation(
    db: Session, admin: User, settings: Settings
) -> None:
    _activate_execution_policy(db, admin)
    snapshot = _seed_market(db)
    _seed_position(db, quantity=10, available_quantity=8, managed_quantity=6)
    decision = _decision(db, snapshot, action="FULL_SELL")
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="sell-price-reference",
        settings=_settings(settings),
        now=NOW,
    )
    _advance_market(db, bid=Decimal(69000))

    with pytest.raises(ApprovalError) as error:
        approve(
            db,
            approval_id=execution.approval_id,
            user=admin,
            settings=settings,
            correlation_id="sell-price-deviation",
            idempotency_key="sell-price-deviation",
            now=NOW,
        )

    assert error.value.code == "PRICE_DEVIATION_EXCEEDED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_sell_approval_invalidates_when_an_order_reserves_the_position(
    db: Session, admin: User, settings: Settings
) -> None:
    _activate_execution_policy(db, admin)
    snapshot = _seed_market(db)
    _seed_position(db, quantity=10, available_quantity=8, managed_quantity=6)
    decision = _decision(db, snapshot, action="FULL_SELL")
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="sell-approval-reference",
        settings=_settings(settings),
        now=NOW,
    )
    create_order(
        db,
        user=admin,
        request=OrderRequest(
            symbol="005930",
            market="KRX",
            side="SELL",
            action="FIXED_STOP",
            order_type="LIMIT",
            limit_price=Decimal(70000),
            quantity=2,
            idempotency_key="sell-after-approval",
            request_payload={"kind": "reservation-after-approval", "quantity": 2},
            correlation_id="sell-after-approval",
        ),
        audit_action="TEST_SELL_AFTER_APPROVAL",
        now=NOW,
    )
    db.commit()

    with pytest.raises(ApprovalError) as error:
        approve(
            db,
            approval_id=execution.approval_id,
            user=admin,
            settings=settings,
            correlation_id="sell-approval-conflict",
            idempotency_key="sell-approval-conflict",
            now=NOW,
        )

    assert error.value.code == "NO_ACTIVE_OR_UNKNOWN_ORDER"
    approval = db.get(Approval, execution.approval_id)
    assert approval is not None and approval.state == "INVALIDATED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1


def test_sell_action_disabled_skips_guard_and_order(
    db: Session, admin: User, settings: Settings
) -> None:
    _activate_execution_policy(db, admin, full_sell="DISABLED")
    snapshot = _seed_market(db)
    _seed_position(db)
    decision = _decision(db, snapshot, action="FULL_SELL")

    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="full-disabled",
        settings=_settings(settings),
        now=NOW,
    )

    assert execution is not None and execution.state == "DISABLED"
    assert execution.result_code == "ACTION_DISABLED"
    assert execution.guard_evaluation_id is None
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
