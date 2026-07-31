from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.config import Settings
from app.models import Fill, OrderEvent, Position, PositionEvent, TradingOrder
from app.trading.paper import (
    PaperBrokerConflict,
    PaperBrokerError,
    PaperOrderRequest,
    apply_paper_fill,
    confirm_paper_cancel,
    create_paper_order,
    replace_paper_order,
    request_paper_cancel,
    request_paper_replace,
    set_paper_gate,
)

CORRELATION_ID = "019fb7f6-0000-7000-8000-000000000100"


def request(
    key: str,
    *,
    symbol: str = "005930",
    side: str = "BUY",
    quantity: int = 10,
    market: str = "KRX",
    price: Decimal | None = Decimal(70000),
) -> PaperOrderRequest:
    return PaperOrderRequest(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type="LIMIT" if price is not None else "MARKET",
        limit_price=price,
        idempotency_key=key,
        correlation_id=CORRELATION_ID,
        market=market,
    )


def test_gate_environment_market_and_idempotency(
    db: Session,
    settings: Settings,
) -> None:
    with pytest.raises(PaperBrokerError, match="재동기화") as closed:
        create_paper_order(db, request("order-1"), settings)
    assert closed.value.code == "TRADING_GATE_CLOSED"

    set_paper_gate(db, "READY", "TEST_RECONCILED")
    order = create_paper_order(db, request("order-1"), settings)
    assert order.status == "OPEN"
    assert create_paper_order(db, request("order-1"), settings).id == order.id
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1

    with pytest.raises(PaperBrokerConflict) as conflict:
        create_paper_order(db, request("order-1", quantity=11), settings)
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"

    with pytest.raises(PaperBrokerError) as unsupported:
        create_paper_order(db, request("order-nxt", market="NXT"), settings)
    assert unsupported.value.code == "UNSUPPORTED_IN_MOCK"

    live_settings = Settings(
        environment="LIVE",
        database_url="sqlite://",
        totp_encryption_key=settings.totp_encryption_key,
    )
    with pytest.raises(PaperBrokerError) as live:
        create_paper_order(db, request("order-live"), live_settings)
    assert live.value.code == "PAPER_ENVIRONMENT_REQUIRED"


def test_partial_duplicate_and_cancel_race_preserve_quantities_and_position(
    db: Session,
    settings: Settings,
) -> None:
    set_paper_gate(db, "READY", "TEST_RECONCILED")
    order = create_paper_order(db, request("partial-cancel"), settings)
    order, applied = apply_paper_fill(
        db,
        order.id,
        source_key="fill-1",
        quantity=4,
        price=Decimal(70000),
    )
    assert applied is True
    assert (order.status, order.filled_quantity, order.remaining_quantity) == ("PARTIALLY_FILLED", 4, 6)

    duplicate, applied = apply_paper_fill(
        db,
        order.id,
        source_key="fill-1",
        quantity=4,
        price=Decimal(70000),
    )
    assert applied is False
    assert duplicate.filled_quantity == 4

    request_paper_cancel(db, order.id)
    raced, applied = apply_paper_fill(
        db,
        order.id,
        source_key="fill-2",
        quantity=2,
        price=Decimal(71000),
    )
    assert applied is True
    assert raced.status == "CANCEL_PENDING"
    cancelled = confirm_paper_cancel(db, order.id)
    assert cancelled.status == "CANCELLED"
    assert cancelled.requested_quantity == 10
    assert (cancelled.filled_quantity, cancelled.cancelled_quantity, cancelled.remaining_quantity) == (6, 4, 0)

    position = db.scalar(select(Position).where(Position.symbol == "005930"))
    assert position is not None
    assert position.quantity == 6
    assert position.average_price == Decimal("70333.3333")
    assert db.scalar(select(func.count()).select_from(Fill)) == 2
    assert db.scalar(select(func.count()).select_from(PositionEvent)) == 2

    with pytest.raises(PaperBrokerConflict) as changed_duplicate:
        apply_paper_fill(
            db,
            order.id,
            source_key="fill-2",
            quantity=1,
            price=Decimal(71000),
        )
    assert changed_duplicate.value.code == "FILL_SOURCE_CONFLICT"


def test_replace_preserves_parent_and_orders_only_actual_remainder(
    db: Session,
    settings: Settings,
) -> None:
    set_paper_gate(db, "READY", "TEST_RECONCILED")
    original = create_paper_order(db, request("replace-parent"), settings)
    apply_paper_fill(
        db,
        original.id,
        source_key="replace-fill",
        quantity=3,
        price=Decimal(70000),
    )
    pending = request_paper_replace(db, original.id)
    assert pending.status == "REPLACE_PENDING"
    pending, applied = apply_paper_fill(
        db,
        original.id,
        source_key="replace-race-fill",
        quantity=2,
        price=Decimal(70000),
    )
    assert applied is True
    assert pending.status == "REPLACE_PENDING"
    replacement = replace_paper_order(
        db,
        original.id,
        new_limit_price=Decimal(69900),
        idempotency_key="replace-child",
    )
    db.refresh(original)
    assert original.status == "REPLACED"
    assert (original.filled_quantity, original.cancelled_quantity, original.remaining_quantity) == (5, 5, 0)
    assert replacement.parent_order_id == original.id
    assert replacement.order_group_id == original.order_group_id
    assert replacement.requested_quantity == 5
    assert replacement.status == "OPEN"
    assert replacement.replacement_sequence == 1
    assert (
        replace_paper_order(
            db,
            original.id,
            new_limit_price=Decimal(69900),
            idempotency_key="replace-child",
        ).id
        == replacement.id
    )
    with pytest.raises(PaperBrokerConflict) as changed_replacement:
        replace_paper_order(
            db,
            original.id,
            new_limit_price=Decimal(69800),
            idempotency_key="replace-child",
        )
    assert changed_replacement.value.code == "IDEMPOTENCY_CONFLICT"

    late_parent, applied = apply_paper_fill(
        db,
        original.id,
        source_key="late-parent-fill",
        quantity=2,
        price=Decimal(70100),
    )
    db.refresh(replacement)
    assert applied is True
    assert late_parent.status == "REPLACED"
    assert (late_parent.filled_quantity, late_parent.cancelled_quantity) == (7, 3)
    assert (replacement.cancelled_quantity, replacement.remaining_quantity) == (2, 3)


def test_unknown_blocks_new_symbol_order_but_same_key_is_safe(
    db: Session,
    settings: Settings,
) -> None:
    set_paper_gate(db, "READY", "TEST_RECONCILED")
    unknown = create_paper_order(db, request("unknown-1"), settings, response_lost=True)
    assert unknown.status == "UNKNOWN"
    assert create_paper_order(db, request("unknown-1"), settings).id == unknown.id

    with pytest.raises(PaperBrokerError) as blocked:
        create_paper_order(db, request("unknown-2"), settings)
    assert blocked.value.code == "SYMBOL_RECONCILIATION_REQUIRED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1
    assert db.scalar(select(func.count()).select_from(OrderEvent)) >= 4


def test_sell_reservations_prevent_oversell_and_fills_close_position(
    db: Session,
    settings: Settings,
) -> None:
    set_paper_gate(db, "READY", "TEST_RECONCILED")
    buy = create_paper_order(db, request("buy-position", quantity=5), settings)
    apply_paper_fill(
        db,
        buy.id,
        source_key="buy-position-fill",
        quantity=5,
        price=Decimal(70000),
    )

    with pytest.raises(PaperBrokerError) as too_large:
        create_paper_order(db, request("sell-too-large", side="SELL", quantity=6), settings)
    assert too_large.value.code == "INSUFFICIENT_POSITION"

    first_sell = create_paper_order(db, request("sell-reserved", side="SELL", quantity=3), settings)
    with pytest.raises(PaperBrokerError) as over_reserved:
        create_paper_order(db, request("sell-over-reserved", side="SELL", quantity=3), settings)
    assert over_reserved.value.code == "INSUFFICIENT_POSITION"

    apply_paper_fill(
        db,
        first_sell.id,
        source_key="sell-partial-fill",
        quantity=2,
        price=Decimal(71000),
    )
    request_paper_cancel(db, first_sell.id)
    confirm_paper_cancel(db, first_sell.id)
    final_sell = create_paper_order(
        db,
        request("sell-final", side="SELL", quantity=3, price=None),
        settings,
    )
    apply_paper_fill(
        db,
        final_sell.id,
        source_key="sell-final-fill",
        quantity=3,
        price=Decimal(69000),
    )
    position = db.scalar(select(Position).where(Position.symbol == "005930"))
    assert position is not None
    assert position.quantity == 0
    assert position.average_price == Decimal(0)
    assert position.state == "CLOSED"


def test_stale_order_version_update_is_rejected(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    with session_factory() as setup:
        set_paper_gate(setup, "READY", "TEST_RECONCILED")
        order_id = create_paper_order(setup, request("version-race"), settings).id

    with session_factory() as first, session_factory() as second:
        first_order = first.get(TradingOrder, order_id)
        second_order = second.get(TradingOrder, order_id)
        assert first_order is not None and second_order is not None
        first_order.status = "CANCEL_PENDING"
        first_order.version += 1
        first.commit()
        second_order.status = "UNKNOWN"
        second_order.version += 1
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()


def test_database_rejects_broken_order_quantity_invariant(
    db: Session,
    settings: Settings,
) -> None:
    set_paper_gate(db, "READY", "TEST_RECONCILED")
    order = create_paper_order(db, request("invalid-invariant"), settings)
    order.remaining_quantity = 9
    order.version += 1
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
