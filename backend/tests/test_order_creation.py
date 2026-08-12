from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import OrderEvent, OrderIntent, TradingOrder, User
from app.order_creation import OrderCreationError, OrderRequest, create_order


def _request(symbol: str = "005930", *, side: str = "BUY", action: str = "BUY", key: str | None = None, quantity: int = 1) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        market="KRX",
        side=side,
        action=action,
        order_type="LIMIT",
        limit_price=Decimal(50000),
        quantity=quantity,
        idempotency_key=key or f"test-order-{symbol}-{side}",
        request_payload={
            "environment": "MOCK",
            "symbol": symbol,
            "market": "KRX",
            "side": side,
            "action": action,
            "order_type": "LIMIT",
            "limit_price": "50000",
            "quantity": quantity,
            "idempotency_key": key or f"test-order-{symbol}-{side}",
        },
        correlation_id="test-correlation",
    )


def test_create_order_persists_created_intent_and_order(db: Session, admin: User) -> None:
    order = create_order(
        db,
        user=admin,
        request=_request(),
        audit_action="TEST_ORDER_CREATED",
    )
    db.commit()
    assert order.status == "CREATED"
    assert order.account_alias == "KIWOOM_MOCK_PRIMARY"
    assert order.environment == "MOCK"
    assert order.requested_quantity == 1
    assert order.remaining_quantity == 1
    intent = db.get(OrderIntent, order.intent_id)
    assert intent is not None and intent.side == "BUY"
    events = db.query(OrderEvent).filter(OrderEvent.order_id == order.id).all()
    assert any(e.event_type == "ORDER_CREATED" for e in events)


def test_create_order_is_idempotent_on_same_key(db: Session, admin: User) -> None:
    first = create_order(db, user=admin, request=_request(), audit_action="TEST_ORDER_CREATED")
    db.commit()
    second = create_order(db, user=admin, request=_request(), audit_action="TEST_ORDER_CREATED")
    db.commit()
    assert second.id == first.id
    assert db.query(TradingOrder).count() == 1


def test_create_order_rejects_conflicting_payload_same_key(db: Session, admin: User) -> None:
    create_order(db, user=admin, request=_request(), audit_action="TEST_ORDER_CREATED")
    db.commit()
    conflict = OrderRequest(
        symbol="005930",
        market="KRX",
        side="BUY",
        action="BUY",
        order_type="LIMIT",
        limit_price=Decimal(60000),
        quantity=1,
        idempotency_key="test-order-005930-BUY",
        request_payload={
            "environment": "MOCK",
            "symbol": "005930",
            "market": "KRX",
            "side": "BUY",
            "action": "BUY",
            "order_type": "LIMIT",
            "limit_price": "60000",
            "quantity": 1,
            "idempotency_key": "test-order-005930-BUY",
        },
        correlation_id="test-correlation",
    )
    with pytest.raises(OrderCreationError) as exc:
        create_order(db, user=admin, request=conflict, audit_action="TEST_ORDER_CREATED")
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"


def test_create_order_validates_inputs(db: Session, admin: User) -> None:
    bad = OrderRequest(
        symbol="005930",
        market="KRX",
        side="BUY",
        action="BUY",
        order_type="LIMIT",
        limit_price=Decimal(50000),
        quantity=0,
        idempotency_key="test-order-zero-qty",
        request_payload={"quantity": 0, "idempotency_key": "test-order-zero-qty"},
        correlation_id="test-correlation",
    )
    with pytest.raises(OrderCreationError):
        create_order(db, user=admin, request=bad, audit_action="TEST_ORDER_CREATED")
