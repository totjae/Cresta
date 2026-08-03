from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.broker.kiwoom import (
    KiwoomOrderAcknowledgement,
    KiwoomOrderOutcomeUnknownError,
    KiwoomOrderRejectedError,
    KiwoomOrderRequest,
)
from app.broker.order_sender import (
    KiwoomOrderSenderError,
    _next_created_order_statement,
    send_new_order_once,
    send_next_created_order,
)
from app.broker.worker_state import LeaseIdentity, acquire_lease, update_worker_state
from app.models import OrderEvent, OrderIntent, TradingGate, TradingOrder
from app.reconciliation import ACCOUNT_ALIAS


class FakeOrderClient:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.requests: list[KiwoomOrderRequest] = []

    def place_order(self, request: KiwoomOrderRequest) -> KiwoomOrderAcknowledgement:
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        assert isinstance(self.outcome, KiwoomOrderAcknowledgement)
        return self.outcome


def ready_worker(db: Session) -> LeaseIdentity:
    identity = acquire_lease(db, "worker-a", lease_seconds=60)
    assert identity is not None
    assert update_worker_state(
        db,
        identity,
        "READY",
        websocket_connected=True,
        subscriptions_ready=True,
        gate_status="READY",
        gate_reason="WORKER_HEALTHY",
    )
    return identity


def persisted_order(
    db: Session,
    *,
    idempotency_key: str = "kiwoom-send-once",
    account_alias: str = ACCOUNT_ALIAS,
    status: str = "CREATED",
    created_at: datetime | None = None,
) -> TradingOrder:
    intent = OrderIntent(
        account_alias=account_alias,
        environment="MOCK",
        symbol="005930",
        market="KRX",
        side="BUY",
        action="USER_APPROVED",
        requested_quantity=2,
        correlation_id="corr-order-send",
    )
    db.add(intent)
    db.flush()
    order = TradingOrder(
        intent_id=intent.id,
        order_group_id=intent.order_group_id,
        account_alias=account_alias,
        environment="MOCK",
        symbol="005930",
        market="KRX",
        side="BUY",
        order_type="LIMIT",
        limit_price=Decimal(70000),
        requested_quantity=2,
        remaining_quantity=2,
        status=status,
        idempotency_key=idempotency_key,
        request_hash="a" * 64,
        trading_date=date(2026, 8, 4),
        correlation_id="corr-order-send",
        **({"created_at": created_at} if created_at is not None else {}),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def test_success_is_persisted_as_acknowledged_and_never_resent(db: Session) -> None:
    identity = ready_worker(db)
    order = persisted_order(db)
    client = FakeOrderClient(KiwoomOrderAcknowledgement("1234567", "KRX"))

    first = send_new_order_once(db, client, identity, order.id)
    second = send_new_order_once(db, client, identity, order.id)

    assert first.status == "ACKNOWLEDGED"
    assert first.broker_order_id == "1234567"
    assert first.sent is True
    assert second.status == "ACKNOWLEDGED"
    assert second.sent is False
    assert len(client.requests) == 1
    events = db.scalars(
        select(OrderEvent).where(OrderEvent.order_id == order.id).order_by(OrderEvent.created_at)
    ).all()
    assert [event.event_type for event in events] == [
        "STATUS_CHANGED",
        "STATUS_CHANGED",
        "STATUS_CHANGED",
        "ORDER_ACKNOWLEDGED",
    ]


def test_submitting_is_committed_before_network_call(db: Session) -> None:
    identity = ready_worker(db)
    order = persisted_order(db)

    class InspectingClient:
        def place_order(self, request: KiwoomOrderRequest) -> KiwoomOrderAcknowledgement:
            db.expire_all()
            persisted = db.get(TradingOrder, order.id)
            assert persisted is not None
            assert persisted.status == "SUBMITTING"
            return KiwoomOrderAcknowledgement("1234567", "KRX")

    result = send_new_order_once(db, InspectingClient(), identity, order.id)

    assert result.status == "ACKNOWLEDGED"


def test_ambiguous_outcome_becomes_unknown_and_closes_gate(db: Session) -> None:
    identity = ready_worker(db)
    order = persisted_order(db)
    client = FakeOrderClient(
        KiwoomOrderOutcomeUnknownError("KIWOOM_ORDER_OUTCOME_UNKNOWN", "unknown")
    )

    result = send_new_order_once(db, client, identity, order.id)
    repeated = send_new_order_once(db, client, identity, order.id)

    assert result.status == "UNKNOWN"
    assert repeated.sent is False
    assert len(client.requests) == 1
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    assert gate is not None
    assert gate.status == "RECONCILING"
    assert gate.reason == "ORDER_OUTCOME_UNKNOWN"


def test_explicit_broker_rejection_becomes_rejected(db: Session) -> None:
    identity = ready_worker(db)
    order = persisted_order(db)
    client = FakeOrderClient(
        KiwoomOrderRejectedError("KIWOOM_ORDER_REJECTED", "rejected")
    )

    result = send_new_order_once(db, client, identity, order.id)

    assert result.status == "REJECTED"
    assert len(client.requests) == 1
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    assert gate is not None
    assert gate.status == "READY"


def test_sender_requires_current_ready_worker_before_state_change(db: Session) -> None:
    order = persisted_order(db)
    client = FakeOrderClient(KiwoomOrderAcknowledgement("1234567", "KRX"))
    invalid_identity = LeaseIdentity("missing", 1)

    with pytest.raises(KiwoomOrderSenderError) as blocked:
        send_new_order_once(db, client, invalid_identity, order.id, now=datetime.now(UTC))

    assert blocked.value.code == "WORKER_LEASE_NOT_CURRENT"
    assert db.get(TradingOrder, order.id).status == "CREATED"
    assert client.requests == []


def test_polling_sends_only_oldest_created_kiwoom_order(db: Session) -> None:
    identity = ready_worker(db)
    older = persisted_order(
        db,
        idempotency_key="polling-older",
        created_at=datetime(2026, 8, 4, 0, 0, tzinfo=UTC),
    )
    newer = persisted_order(
        db,
        idempotency_key="polling-newer",
        created_at=datetime(2026, 8, 4, 0, 1, tzinfo=UTC),
    )
    paper = persisted_order(
        db,
        idempotency_key="polling-paper",
        account_alias="PAPER",
        created_at=datetime(2026, 8, 3, 23, 59, tzinfo=UTC),
    )
    acknowledged = persisted_order(
        db,
        idempotency_key="polling-acknowledged",
        status="ACKNOWLEDGED",
        created_at=datetime(2026, 8, 3, 23, 58, tzinfo=UTC),
    )
    client = FakeOrderClient(KiwoomOrderAcknowledgement("1234567", "KRX"))

    result = send_next_created_order(db, client, identity)

    assert result is not None
    assert result.order_id == older.id
    assert result.status == "ACKNOWLEDGED"
    assert len(client.requests) == 1
    assert db.get(TradingOrder, newer.id).status == "CREATED"
    assert db.get(TradingOrder, paper.id).status == "CREATED"
    assert db.get(TradingOrder, acknowledged.id).status == "ACKNOWLEDGED"


def test_polling_returns_none_without_created_kiwoom_order(db: Session) -> None:
    identity = ready_worker(db)
    persisted_order(
        db,
        idempotency_key="polling-terminal",
        status="REJECTED",
    )
    client = FakeOrderClient(KiwoomOrderAcknowledgement("1234567", "KRX"))

    result = send_next_created_order(db, client, identity)

    assert result is None
    assert client.requests == []


def test_polling_query_uses_postgresql_skip_locked() -> None:
    sql = str(
        _next_created_order_statement().compile(dialect=postgresql.dialect())
    ).upper()

    assert "FOR UPDATE SKIP LOCKED" in sql
