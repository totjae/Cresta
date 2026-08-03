from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.kiwoom import (
    KiwoomAdapterError,
    KiwoomOrderAcknowledgement,
    KiwoomOrderOutcomeUnknownError,
    KiwoomOrderRejectedError,
    KiwoomOrderRequest,
)
from app.broker.worker_state import LeaseIdentity, lease_is_current
from app.ids import uuid7
from app.models import BrokerWorkerState, OrderEvent, TradingGate, TradingOrder
from app.reconciliation import ACCOUNT_ALIAS


class OrderClient(Protocol):
    def place_order(self, request: KiwoomOrderRequest) -> KiwoomOrderAcknowledgement: ...


class KiwoomOrderSenderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class KiwoomSendResult:
    order_id: str
    status: str
    broker_order_id: str | None
    sent: bool


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _event(
    db: Session,
    order: TradingOrder,
    event_type: str,
    payload: dict[str, object],
    *,
    occurred_at: datetime,
) -> None:
    encoded = _canonical_json(payload)
    db.add(
        OrderEvent(
            order_id=order.id,
            event_type=event_type,
            source="KIWOOM",
            source_key=uuid7(),
            payload_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            payload_json=encoded,
            correlation_id=order.correlation_id,
            occurred_at=occurred_at,
        )
    )


def _transition(
    db: Session, order: TradingOrder, status: str, *, occurred_at: datetime
) -> None:
    previous = order.status
    order.status = status
    order.version += 1
    _event(
        db,
        order,
        "STATUS_CHANGED",
        {"from": previous, "to": status},
        occurred_at=occurred_at,
    )


def _require_worker_ready(
    db: Session, identity: LeaseIdentity, *, now: datetime
) -> None:
    if not lease_is_current(db, identity, now=now):
        raise KiwoomOrderSenderError("WORKER_LEASE_NOT_CURRENT", "Worker lease is not current")
    state = db.get(BrokerWorkerState, ACCOUNT_ALIAS)
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    if (
        state is None
        or state.state != "READY"
        or state.fencing_token != identity.fencing_token
        or not state.websocket_connected
        or not state.subscriptions_ready
        or gate is None
        or gate.status != "READY"
    ):
        raise KiwoomOrderSenderError("BROKER_NOT_READY", "Broker worker is not ready")


def _mark_gate_reconciling(db: Session, reason: str) -> None:
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    if gate is None:
        gate = TradingGate(
            account_alias=ACCOUNT_ALIAS,
            environment="MOCK",
            status="RECONCILING",
            reason=reason,
        )
        db.add(gate)
    else:
        gate.status = "RECONCILING"
        gate.reason = reason
        gate.version += 1


def _order_request(order: TradingOrder) -> KiwoomOrderRequest:
    if order.account_alias != ACCOUNT_ALIAS or order.environment != "MOCK":
        raise KiwoomOrderSenderError(
            "KIWOOM_MOCK_ORDER_REQUIRED", "Order is not for the Kiwoom MOCK account"
        )
    return KiwoomOrderRequest(
        symbol=order.symbol,
        side=order.side,
        quantity=order.remaining_quantity,
        order_type=order.order_type,
        limit_price=order.limit_price,
        market=order.market,
    )


def send_new_order_once(
    db: Session,
    client: OrderClient,
    identity: LeaseIdentity,
    order_id: str,
    *,
    now: datetime | None = None,
) -> KiwoomSendResult:
    """Send one persisted CREATED order once; never creates an order or retries HTTP."""
    observed_at = now or datetime.now(UTC)
    order = db.scalar(
        select(TradingOrder).where(TradingOrder.id == order_id).with_for_update()
    )
    if order is None:
        db.rollback()
        raise KiwoomOrderSenderError("ORDER_NOT_FOUND", "Order does not exist")
    if order.status != "CREATED":
        db.rollback()
        return KiwoomSendResult(order.id, order.status, order.broker_order_id, False)
    return _send_locked_order(db, client, identity, order, observed_at=observed_at)


def send_next_created_order(
    db: Session,
    client: OrderClient,
    identity: LeaseIdentity,
    *,
    now: datetime | None = None,
) -> KiwoomSendResult | None:
    """Claim and send at most one FIFO Kiwoom MOCK order for the active worker."""
    observed_at = now or datetime.now(UTC)
    order = db.scalar(_next_created_order_statement())
    if order is None:
        db.rollback()
        return None
    return _send_locked_order(db, client, identity, order, observed_at=observed_at)


def _next_created_order_statement():
    return (
        select(TradingOrder)
        .where(
            TradingOrder.account_alias == ACCOUNT_ALIAS,
            TradingOrder.environment == "MOCK",
            TradingOrder.status == "CREATED",
        )
        .order_by(TradingOrder.created_at, TradingOrder.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def _send_locked_order(
    db: Session,
    client: OrderClient,
    identity: LeaseIdentity,
    order: TradingOrder,
    *,
    observed_at: datetime,
) -> KiwoomSendResult:
    _require_worker_ready(db, identity, now=observed_at)
    request = _order_request(order)
    order_id = order.id
    _transition(db, order, "VALIDATING", occurred_at=observed_at)
    _transition(db, order, "SUBMITTING", occurred_at=observed_at)
    db.commit()

    try:
        acknowledgement = client.place_order(request)
    except KiwoomOrderRejectedError:
        return _finish_send(
            db,
            identity,
            order_id,
            status="REJECTED",
            event_type="ORDER_REJECTED",
            occurred_at=observed_at,
        )
    except KiwoomOrderOutcomeUnknownError:
        return _finish_send(
            db,
            identity,
            order_id,
            status="UNKNOWN",
            event_type="ORDER_OUTCOME_UNKNOWN",
            occurred_at=observed_at,
            gate_reason="ORDER_OUTCOME_UNKNOWN",
        )
    except KiwoomAdapterError:
        return _finish_send(
            db,
            identity,
            order_id,
            status="REJECTED",
            event_type="ORDER_VALIDATION_REJECTED",
            occurred_at=observed_at,
        )

    return _finish_send(
        db,
        identity,
        order_id,
        status="ACKNOWLEDGED",
        event_type="ORDER_ACKNOWLEDGED",
        occurred_at=observed_at,
        broker_order_id=acknowledgement.broker_order_id,
    )


def _finish_send(
    db: Session,
    identity: LeaseIdentity,
    order_id: str,
    *,
    status: str,
    event_type: str,
    occurred_at: datetime,
    broker_order_id: str | None = None,
    gate_reason: str | None = None,
) -> KiwoomSendResult:
    if not lease_is_current(db, identity):
        db.rollback()
        raise KiwoomOrderSenderError(
            "WORKER_LEASE_LOST_AFTER_SEND", "Worker lease was lost after order transmission"
        )
    order = db.scalar(
        select(TradingOrder).where(TradingOrder.id == order_id).with_for_update()
    )
    if order is None or order.status != "SUBMITTING":
        db.rollback()
        raise KiwoomOrderSenderError(
            "ORDER_STATE_CHANGED_AFTER_SEND", "Order state changed while transmission was pending"
        )
    if broker_order_id is not None:
        order.broker_order_id = broker_order_id
    _transition(db, order, status, occurred_at=occurred_at)
    _event(
        db,
        order,
        event_type,
        {"broker_order_id_present": broker_order_id is not None, "status": status},
        occurred_at=occurred_at,
    )
    if gate_reason is not None:
        _mark_gate_reconciling(db, gate_reason)
    db.commit()
    db.refresh(order)
    return KiwoomSendResult(order.id, order.status, order.broker_order_id, True)
