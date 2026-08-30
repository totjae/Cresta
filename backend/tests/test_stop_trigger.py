from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.execution_authority import ActionMode, ExecutionStage
from app.models import (
    Approval,
    ConfigurationVersion,
    Decision,
    DecisionExecution,
    GuardEvaluation,
    MarketSnapshot,
    MarketStreamState,
    OrderIntent,
    Position,
    RiskEvent,
    StopTrigger,
    TradingGate,
    TradingOrder,
)
from app.risk_events import (
    RISK_EVENT_SCOPE_FIXED_STOP,
    active_risk_events,
    create_risk_event,
    resolve_risk_event,
)
from app.stop_trigger import (
    ACCOUNT_ALIAS,
    FixedStopActionAuthority,
    compute_stop_price,
    recover_exit_pending,
    run_fixed_stop_triggers,
    should_trigger,
)
from tests.test_phase_10c2_sourced_execution import _activate_mode, _activate_shadow

NOW = datetime(2026, 8, 12, 1, 30, tzinfo=UTC)  # 10:30 KST, DUAL_CONTINUOUS


@pytest.fixture(autouse=True)
def _authoritative_shadow_stage(db: Session, admin) -> None:
    _activate_shadow(db, admin, NOW)
    _activate_mode(db, admin, "MANUAL_APPROVAL")


def _set_gate(db: Session, status: str = "READY") -> None:
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    if gate is None:
        gate = TradingGate(account_alias=ACCOUNT_ALIAS)
        db.add(gate)
    gate.status = status
    gate.environment = "MOCK"
    gate.reason = None
    db.flush()


def _position(
    db: Session,
    *,
    symbol: str = "005930",
    quantity: int = 10,
    average_price: Decimal = Decimal(50000),
    state: str = "OPEN",
) -> Position:
    position = Position(
        account_alias=ACCOUNT_ALIAS,
        symbol=symbol,
        quantity=quantity,
        available_quantity=quantity,
        average_price=average_price,
        managed_quantity=quantity,
        managed_average_price=average_price,
        state=state,
        origin="CRESTA_MANAGED",
    )
    db.add(position)
    db.flush()
    return position


def _snapshot(
    db: Session,
    *,
    symbol: str = "005930",
    bid_price: Decimal | None = Decimal(49000),
    quality: str = "NORMAL",
    trading_status: str = "TRADING",
    received_at: datetime | None = None,
    sequence: int = 1,
) -> MarketSnapshot:
    now = received_at or NOW
    snapshot = MarketSnapshot(
        symbol=symbol,
        market="KRX",
        source="TEST",
        sequence_or_hash=f"stop-{symbol}-{sequence}",
        source_sequence=sequence,
        payload_hash="a" * 64,
        last_price=bid_price or Decimal(49000),
        open_price=Decimal(50000),
        high_price=Decimal(50500),
        low_price=Decimal(48900),
        cumulative_volume=10000,
        best_bid_price=bid_price,
        best_bid_quantity=100,
        best_ask_price=Decimal(49010),
        best_ask_quantity=100,
        trading_status=trading_status,
        quality=quality,
        recovery_snapshot=False,
        event_at=now,
        received_at=now,
    )
    db.add(snapshot)
    db.flush()
    stream = db.get(MarketStreamState, ("KRX", symbol))
    if stream is None:
        stream = MarketStreamState(
            market="KRX",
            symbol=symbol,
            source="TEST",
            current_snapshot_id=snapshot.id,
            quality="NORMAL",
        )
        db.add(stream)
    else:
        stream.current_snapshot_id = snapshot.id
        stream.quality = "NORMAL"
    db.flush()
    return snapshot


def _assert_no_orders(db: Session) -> None:
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0


def test_compute_stop_price_negative_pct() -> None:
    assert compute_stop_price(Decimal(50000), Decimal("-2.0")) == Decimal("49000.0000")
    assert compute_stop_price(Decimal(100), Decimal("-0.1")) == Decimal("99.9000")
    assert compute_stop_price(Decimal(100), Decimal("-20.0")) == Decimal("80.0000")


def test_should_trigger_boundary_and_invalid() -> None:
    stop = Decimal(49000)
    assert should_trigger(Decimal(49000), stop) is True
    assert should_trigger(Decimal(48900), stop) is True
    assert should_trigger(Decimal(49100), stop) is False
    assert should_trigger(None, stop) is False
    assert should_trigger(Decimal(0), stop) is False


def test_run_no_op_without_positions(db: Session, settings: Settings) -> None:
    _set_gate(db)
    count = run_fixed_stop_triggers(db, settings=settings, now=NOW)
    assert count == 0
    assert db.scalar(select(func.count()).select_from(StopTrigger)) == 0
    _assert_no_orders(db)


def test_run_evaluates_even_when_gate_not_ready(db: Session, settings: Settings) -> None:
    # A non-READY gate does not skip evaluation; it records EXIT_PENDING so the
    # stop signal persists and is rechecked after recovery (GRD-085, EXE-053).
    _set_gate(db, status="RECONCILING")
    _position(db)
    _snapshot(db, bid_price=Decimal(48000))
    count = run_fixed_stop_triggers(db, settings=settings, now=NOW)
    assert count == 1
    trigger = db.scalar(select(StopTrigger))
    assert trigger is not None
    assert trigger.state == "EXIT_PENDING"


def test_does_not_fire_when_bid_above_stop(db: Session, settings: Settings) -> None:
    _set_gate(db)
    _position(db, average_price=Decimal(50000))
    _snapshot(db, bid_price=Decimal(49900))  # above 49000 stop
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    assert db.scalar(select(func.count()).select_from(StopTrigger)) == 0


def test_fires_shadow_recorded_when_guard_passes(db: Session, settings: Settings) -> None:
    _set_gate(db)
    _position(db, average_price=Decimal(50000))
    _snapshot(db, bid_price=Decimal(49000))
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    trigger = db.scalar(select(StopTrigger))
    assert trigger is not None
    assert trigger.state == "SHADOW_RECORDED"
    assert trigger.result_code == "SHADOW_ONLY"
    assert trigger.stop_price == Decimal("49000.0000")
    assert trigger.trigger_price == Decimal(49000)
    guard = db.scalar(select(GuardEvaluation))
    assert guard is not None
    assert guard.result == "PASSED"
    assert guard.subject_type == "STOP_TRIGGER"
    assert db.scalar(select(func.count()).select_from(RiskEvent)) == 0
    _assert_no_orders(db)


def test_exit_pending_when_broker_not_ready(db: Session, settings: Settings) -> None:
    _set_gate(db, status="READY")
    _position(db, average_price=Decimal(50000))
    _snapshot(db, bid_price=Decimal(48000))
    # Downgrade gate after snapshot setup so evaluation sees non-READY.
    _set_gate(db, status="RECONCILING")
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    trigger = db.scalar(select(StopTrigger))
    assert trigger is not None
    assert trigger.state == "EXIT_PENDING"
    assert trigger.result_code == "BROKER_READY"
    event = db.scalar(select(RiskEvent))
    assert event is not None
    assert event.scope == RISK_EVENT_SCOPE_FIXED_STOP
    assert event.state == "ACTIVE"
    _assert_no_orders(db)


def test_exit_pending_recovers_to_shadow_recorded(db: Session, settings: Settings) -> None:
    _set_gate(db, status="RECONCILING")
    _position(db, average_price=Decimal(50000))
    _snapshot(db, bid_price=Decimal(48000))
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    trigger = db.scalar(select(StopTrigger))
    assert trigger is not None
    assert trigger.state == "EXIT_PENDING"
    event_id = trigger.risk_event_id
    assert event_id is not None

    # Broker recovers; re-evaluation should pass.
    _set_gate(db, status="READY")
    _snapshot(db, bid_price=Decimal(49000), sequence=2)
    recover_exit_pending(db, settings=settings, now=NOW)
    db.refresh(trigger)
    assert trigger.state == "SHADOW_RECORDED"
    event = db.get(RiskEvent, event_id)
    assert event is not None
    assert event.state == "RESOLVED"
    assert event.resolution == "SHADOW_ONLY"
    _assert_no_orders(db)


def test_idempotency_re_evaluates_in_place(db: Session, settings: Settings) -> None:
    _set_gate(db)
    _position(db, average_price=Decimal(50000))
    _snapshot(db, bid_price=Decimal(49000))
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    assert db.scalar(select(func.count()).select_from(StopTrigger)) == 1
    # Two guard evaluations (one per evaluation), but a single trigger row.
    assert db.scalar(select(func.count()).select_from(GuardEvaluation)) == 2
    _assert_no_orders(db)


def test_position_version_change_supersedes(db: Session, settings: Settings) -> None:
    _set_gate(db)
    position = _position(db, average_price=Decimal(50000))
    _snapshot(db, bid_price=Decimal(49000))
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    first = db.scalar(select(StopTrigger))
    assert first is not None
    assert first.state == "SHADOW_RECORDED"

    # Simulate a fill that bumps position version.
    position.version += 1
    db.flush()
    _snapshot(db, bid_price=Decimal(49000), sequence=2)
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    triggers = list(db.scalars(select(StopTrigger).order_by(StopTrigger.created_at)))
    assert len(triggers) == 2
    db.refresh(first)
    assert first.state == "SUPERSEDED"
    assert triggers[1].state == "SHADOW_RECORDED"
    _assert_no_orders(db)


def test_stale_snapshot_blocks_to_exit_pending(db: Session, settings: Settings) -> None:
    _set_gate(db)
    _position(db, average_price=Decimal(50000))
    stale = NOW - timedelta(seconds=10)
    _snapshot(db, bid_price=Decimal(48000), received_at=stale)
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    trigger = db.scalar(select(StopTrigger))
    assert trigger is not None
    assert trigger.state == "EXIT_PENDING"
    assert trigger.result_code == "MARKET_DATA_FRESH"
    _assert_no_orders(db)


def test_pause_entry_does_not_block_stop(db: Session, settings: Settings) -> None:
    from app.emergency_stop import activate_pause_entry

    _set_gate(db)
    _position(db, average_price=Decimal(50000))
    _snapshot(db, bid_price=Decimal(49000))
    admin = _admin_user(db, settings)
    activate_pause_entry(
        db,
        user=admin,
        reason="manual pause for testing",
        idempotency_key="pause-key-stop-test-0001",
        correlation_id="corr-pause",
        request_ip="127.0.0.1",
        user_agent="test",
    )
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    trigger = db.scalar(select(StopTrigger))
    assert trigger is not None
    # PAUSE_ENTRY blocks BUY only; the stop trigger's sell guard has no
    # EMERGENCY_STOP_ACTIVE rule and should still pass.
    assert trigger.state == "SHADOW_RECORDED"
    _assert_no_orders(db)


def test_blocking_order_causes_exit_pending(db: Session, settings: Settings) -> None:
    _set_gate(db)
    position = _position(db, average_price=Decimal(50000))
    _snapshot(db, bid_price=Decimal(49000))
    # Inject an active SELL order that reserves all quantity.
    intent = OrderIntent(
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        symbol=position.symbol,
        market="KRX",
        side="SELL",
        action="FULL_SELL",
        requested_quantity=10,
        correlation_id="block-correlation",
    )
    db.add(intent)
    db.flush()
    db.add(
        TradingOrder(
            intent_id=intent.id,
            order_group_id=intent.order_group_id,
            account_alias=ACCOUNT_ALIAS,
            environment="MOCK",
            symbol=position.symbol,
            market="KRX",
            side="SELL",
            order_type="LIMIT",
            limit_price=Decimal(49000),
            requested_quantity=10,
            remaining_quantity=10,
            status="OPEN",
            idempotency_key="block-order-key",
            request_hash="x" * 64,
            trading_date=NOW.date(),
            correlation_id="block-correlation",
        )
    )
    db.flush()
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    trigger = db.scalar(select(StopTrigger))
    assert trigger is not None
    assert trigger.state == "EXIT_PENDING"
    # Both SELL_QUANTITY_AVAILABLE and NO_ACTIVE_OR_UNKNOWN_ORDER block; the
    # first blocking rule in order wins as the result code.
    assert trigger.result_code in {
        "SELL_QUANTITY_AVAILABLE",
        "NO_ACTIVE_OR_UNKNOWN_ORDER",
    }
    _assert_no_orders_not_injected(db)


def test_risk_policy_default_used_when_unconfigured(db: Session, settings: Settings) -> None:
    _set_gate(db)
    _position(db, average_price=Decimal(50000))
    _snapshot(db, bid_price=Decimal(49000))
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    trigger = db.scalar(select(StopTrigger))
    assert trigger is not None
    assert trigger.risk_policy_version_id is None
    assert trigger.stop_price == Decimal("49000.0000")  # SAFE_DEFAULT -2.0%
    _assert_no_orders(db)


def test_risk_event_helper_create_and_resolve(db: Session, settings: Settings) -> None:
    event = create_risk_event(
        db,
        scope=RISK_EVENT_SCOPE_FIXED_STOP,
        rule_code="BROKER_READY",
        severity="HIGH",
        account_alias=ACCOUNT_ALIAS,
        input_record={"position_id": "p1", "stop_price": "49000"},
        correlation_id="event-test",
    )
    db.commit()
    assert event.state == "ACTIVE"
    assert active_risk_events(db, scope=RISK_EVENT_SCOPE_FIXED_STOP) == [event]
    resolved = resolve_risk_event(db, event.id, resolution="BROKER_RECOVERED", now=NOW)
    db.commit()
    assert resolved is not None
    assert resolved.state == "RESOLVED"
    assert active_risk_events(db, scope=RISK_EVENT_SCOPE_FIXED_STOP) == []
    # No secrets in input_json.
    assert "password" not in event.input_json
    assert "49000" in event.input_json


def _admin_user(db: Session, settings: Settings):
    from app.auth.crypto import encrypt_totp_secret, hash_password
    from app.models import TotpCredential, User

    user = User(login_id="stop-admin", password_hash=hash_password("Cresta!Test-Pw-2026"))
    db.add(user)
    db.flush()
    db.add(
        TotpCredential(
            user_id=user.id,
            encrypted_secret=encrypt_totp_secret(
                "JBSWY3DPEHPK3PXP", settings.load_totp_encryption_key()
            ),
            verified=True,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _assert_no_orders_not_injected(db: Session) -> None:
    # The injected blocking order is a fixture; only that one order may exist.
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0


def test_approval_only_auto_sell_is_exit_pending_with_no_order(
    db: Session, settings: Settings, monkeypatch
) -> None:
    """APPROVAL_ONLY never creates a synthetic Approval or automatic SELL."""
    settings.execution_stage = "APPROVAL_ONLY"
    stage_version = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.category == "V7_ENTRY_EXECUTION_STAGE"
        )
    )
    execution_version = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.category == "EXECUTION_POLICY"
        )
    )
    assert stage_version is not None and execution_version is not None
    monkeypatch.setattr(
        "app.stop_trigger._fixed_stop_action_authority",
        lambda *a, **k: FixedStopActionAuthority(
            ExecutionStage.APPROVAL_ONLY,
            stage_version,
            execution_version,
            ActionMode.AUTOMATIC,
        ),
    )
    _set_gate(db, "READY")
    position = _position(db, average_price=Decimal(50000), quantity=10)
    # Bid at 49000 <= stop price 49000 -> trigger fires.
    _snapshot(db, bid_price=Decimal(49000))
    count = run_fixed_stop_triggers(db, settings=settings, now=NOW)
    assert count == 1
    trigger = db.scalar(select(StopTrigger).where(StopTrigger.position_id == position.id))
    assert trigger is not None
    assert trigger.state == "EXIT_PENDING"
    assert trigger.result_code == "AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0


def test_shadow_stage_still_creates_no_sell_order(db: Session, settings: Settings) -> None:
    """SHADOW stage keeps the trigger at SHADOW_RECORDED with zero orders."""
    # Default settings.execution_stage is SHADOW.
    _set_gate(db, "READY")
    position = _position(db, average_price=Decimal(50000), quantity=10)
    _snapshot(db, bid_price=Decimal(49000))
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    trigger = db.scalar(select(StopTrigger).where(StopTrigger.position_id == position.id))
    assert trigger is not None
    assert trigger.state == "SHADOW_RECORDED"
    _assert_no_orders(db)
    assert db.scalar(select(func.count()).select_from(StopTrigger)) == 1


def test_external_position_not_auto_sold(db: Session, settings: Settings) -> None:
    """External positions are never auto-sold by the fixed-stop trigger."""
    settings.execution_stage = "APPROVAL_ONLY"
    _set_gate(db, "READY")
    position = _position(db, average_price=Decimal(50000), quantity=10)
    position.origin = "EXTERNAL"
    position.managed_quantity = 0
    position.managed_average_price = Decimal(0)
    db.flush()
    _snapshot(db, bid_price=Decimal(49000))
    run_fixed_stop_triggers(db, settings=settings, now=NOW)
    trigger = db.scalar(select(StopTrigger).where(StopTrigger.position_id == position.id))
    assert trigger is not None
    assert trigger.state == "EXIT_PENDING"
    assert trigger.result_code == "POSITION_MANAGED_QUANTITY_POSITIVE"
    _assert_no_orders(db)


def test_mixed_position_shadow_preserves_managed_quantity_without_order(
    db: Session, settings: Settings
) -> None:
    settings.execution_stage = "APPROVAL_ONLY"
    _set_gate(db, "READY")
    position = _position(db, average_price=Decimal(48000), quantity=10)
    position.available_quantity = 8
    position.managed_quantity = 3
    position.managed_average_price = Decimal(50000)
    position.origin = "MIXED"
    db.flush()
    _snapshot(db, bid_price=Decimal(49000))

    run_fixed_stop_triggers(db, settings=settings, now=NOW)

    trigger = db.scalar(select(StopTrigger).where(StopTrigger.position_id == position.id))
    assert trigger is not None
    assert trigger.stop_price == Decimal("49000.0000")
    assert trigger.state == "SHADOW_RECORDED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
