from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.kiwoom import (
    KiwoomAdapterError,
    KiwoomCancelRequest,
    KiwoomOrderAcknowledgement,
    KiwoomOrderOutcomeUnknownError,
    KiwoomOrderRejectedError,
    KiwoomOrderRequest,
)
from app.broker.pre_send_authority import (
    PreSendStatus,
    validate_created_order_authority,
)
from app.broker.worker_state import LeaseIdentity, lease_is_current
from app.config import Settings, get_settings
from app.execution_stage import (
    EvidenceLoader,
    ExecutionStageValidationPolicy,
)
from app.ids import uuid7
from app.models import BrokerWorkerState, OrderEvent, TradingGate, TradingOrder
from app.reconciliation import ACCOUNT_ALIAS


class OrderClient(Protocol):
    def place_order(self, request: KiwoomOrderRequest) -> KiwoomOrderAcknowledgement: ...

    def cancel_order(self, request: KiwoomCancelRequest) -> KiwoomOrderAcknowledgement: ...


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


@dataclass(frozen=True)
class KiwoomCancelResult:
    order_id: str
    status: str
    requested_quantity: int
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
    settings: Settings | None = None,
    stage_evidence_loader: EvidenceLoader | None = None,
    stage_validation_policy: ExecutionStageValidationPolicy | None = None,
    before_submission_commit: Callable[[], None] | None = None,
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
    return _send_locked_order(
        db,
        client,
        identity,
        order,
        observed_at=observed_at,
        settings=settings or get_settings(),
        stage_evidence_loader=stage_evidence_loader,
        stage_validation_policy=stage_validation_policy,
        before_submission_commit=before_submission_commit,
    )


def send_next_created_order(
    db: Session,
    client: OrderClient,
    identity: LeaseIdentity,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    stage_evidence_loader: EvidenceLoader | None = None,
    stage_validation_policy: ExecutionStageValidationPolicy | None = None,
    before_submission_commit: Callable[[], None] | None = None,
) -> KiwoomSendResult | None:
    """Claim and send at most one FIFO Kiwoom MOCK order for the active worker."""
    observed_at = now or datetime.now(UTC)
    order = db.scalar(_next_created_order_statement())
    if order is None:
        db.rollback()
        return None
    return _send_locked_order(
        db,
        client,
        identity,
        order,
        observed_at=observed_at,
        settings=settings or get_settings(),
        stage_evidence_loader=stage_evidence_loader,
        stage_validation_policy=stage_validation_policy,
        before_submission_commit=before_submission_commit,
    )


def cancel_next_expired_buy_once(
    db: Session,
    client: OrderClient,
    identity: LeaseIdentity,
    *,
    now: datetime | None = None,
) -> KiwoomCancelResult | None:
    """Cancel at most one expired entry BUY remainder; never retries a request."""
    observed_at = now or datetime.now(UTC)
    order = db.scalar(
        select(TradingOrder)
        .where(
            TradingOrder.account_alias == ACCOUNT_ALIAS,
            TradingOrder.environment == "MOCK",
            TradingOrder.side == "BUY",
            TradingOrder.unfilled_policy == "CANCEL",
            TradingOrder.status.in_(("ACKNOWLEDGED", "OPEN", "PARTIALLY_FILLED")),
            TradingOrder.remaining_quantity > 0,
            TradingOrder.next_action_at.is_not(None),
            TradingOrder.next_action_at <= observed_at,
        )
        .order_by(TradingOrder.next_action_at, TradingOrder.created_at, TradingOrder.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if order is None:
        db.rollback()
        return None
    _require_worker_ready(db, identity, now=observed_at)
    if not order.broker_order_id:
        order.next_action_at = None
        _transition(db, order, "RECONCILING", occurred_at=observed_at)
        _mark_gate_reconciling(db, "UNFILLED_ORDER_BROKER_ID_MISSING")
        db.commit()
        return KiwoomCancelResult(order.id, order.status, 0, False)

    quantity = order.remaining_quantity
    request = KiwoomCancelRequest(
        original_order_id=order.broker_order_id,
        symbol=order.symbol,
        quantity=quantity,
        market=order.market,
    )
    _transition(db, order, "CANCEL_PENDING", occurred_at=observed_at)
    order.next_action_at = None
    _event(
        db,
        order,
        "UNFILLED_CANCEL_REQUESTED",
        {"remaining_quantity": quantity, "policy": order.unfilled_policy},
        occurred_at=observed_at,
    )
    db.commit()

    try:
        acknowledgement = client.cancel_order(request)
    except KiwoomOrderOutcomeUnknownError:
        return _finish_cancel(
            db,
            identity,
            order.id,
            quantity=quantity,
            status="UNKNOWN",
            event_type="ORDER_CANCEL_OUTCOME_UNKNOWN",
            gate_reason="ORDER_CANCEL_OUTCOME_UNKNOWN",
            occurred_at=observed_at,
        )
    except KiwoomOrderRejectedError as exc:
        return _finish_cancel(
            db,
            identity,
            order.id,
            quantity=quantity,
            status="RECONCILING",
            event_type="ORDER_CANCEL_REJECTED",
            gate_reason="ORDER_CANCEL_REJECTED",
            occurred_at=observed_at,
            broker_result_code=exc.broker_result_code,
            broker_result_message=exc.broker_result_message,
        )
    except KiwoomAdapterError:
        return _finish_cancel(
            db,
            identity,
            order.id,
            quantity=quantity,
            status="RECONCILING",
            event_type="ORDER_CANCEL_REJECTED",
            gate_reason="ORDER_CANCEL_REJECTED",
            occurred_at=observed_at,
        )

    return _finish_cancel(
        db,
        identity,
        order.id,
        quantity=quantity,
        status="CANCEL_PENDING",
        event_type="ORDER_CANCEL_ACKNOWLEDGED",
        occurred_at=observed_at,
        cancel_broker_order_id=acknowledgement.broker_order_id,
    )


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
    settings: Settings,
    stage_evidence_loader: EvidenceLoader | None,
    stage_validation_policy: ExecutionStageValidationPolicy | None,
    before_submission_commit: Callable[[], None] | None,
) -> KiwoomSendResult:
    _require_worker_ready(db, identity, now=observed_at)
    authority = validate_created_order_authority(
        db,
        order,
        settings=settings,
        now=observed_at,
        stage_evidence_loader=stage_evidence_loader,
        stage_validation_policy=stage_validation_policy,
    )
    if authority.status is PreSendStatus.RETRYABLE:
        db.rollback()
        return KiwoomSendResult(order.id, "CREATED", None, False)
    if authority.status is PreSendStatus.REVOKED:
        order_id = order.id
        db.commit()
        return KiwoomSendResult(order_id, "INVALIDATED", None, False)
    _require_worker_ready(db, identity, now=observed_at)
    request = _order_request(order)
    order_id = order.id
    try:
        _transition(db, order, "VALIDATING", occurred_at=observed_at)
        _transition(db, order, "SUBMITTING", occurred_at=observed_at)
        if before_submission_commit is not None:
            before_submission_commit()
        db.commit()
    except Exception:
        db.rollback()
        raise

    try:
        acknowledgement = client.place_order(request)
    except KiwoomOrderRejectedError as exc:
        return _finish_send(
            db,
            identity,
            order_id,
            status="REJECTED",
            event_type="ORDER_REJECTED",
            occurred_at=observed_at,
            broker_result_code=exc.broker_result_code,
            broker_result_message=exc.broker_result_message,
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
    broker_result_code: str | None = None,
    broker_result_message: str | None = None,
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
    if status == "ACKNOWLEDGED" and order.unfilled_policy == "CANCEL":
        order.next_action_at = occurred_at + timedelta(seconds=order.fill_timeout_seconds)
    event_payload: dict[str, object] = {
        "broker_order_id_present": broker_order_id is not None,
        "status": status,
    }
    if broker_result_code is not None:
        event_payload["broker_result_code"] = broker_result_code
    if broker_result_message is not None:
        event_payload["broker_result_message"] = broker_result_message
    _event(
        db,
        order,
        event_type,
        event_payload,
        occurred_at=occurred_at,
    )
    if gate_reason is not None:
        _mark_gate_reconciling(db, gate_reason)
    db.commit()
    db.refresh(order)
    return KiwoomSendResult(order.id, order.status, order.broker_order_id, True)


def _finish_cancel(
    db: Session,
    identity: LeaseIdentity,
    order_id: str,
    *,
    quantity: int,
    status: str,
    event_type: str,
    occurred_at: datetime,
    gate_reason: str | None = None,
    cancel_broker_order_id: str | None = None,
    broker_result_code: str | None = None,
    broker_result_message: str | None = None,
) -> KiwoomCancelResult:
    if not lease_is_current(db, identity):
        db.rollback()
        raise KiwoomOrderSenderError(
            "WORKER_LEASE_LOST_AFTER_CANCEL", "Worker lease was lost after cancellation"
        )
    order = db.scalar(
        select(TradingOrder).where(TradingOrder.id == order_id).with_for_update()
    )
    if order is None:
        db.rollback()
        raise KiwoomOrderSenderError("ORDER_NOT_FOUND", "Order does not exist")
    if order.status != "CANCEL_PENDING":
        db.rollback()
        return KiwoomCancelResult(order.id, order.status, quantity, True)
    if status != "CANCEL_PENDING":
        _transition(db, order, status, occurred_at=occurred_at)
    event_payload: dict[str, object] = {
        "cancel_broker_order_id_present": cancel_broker_order_id is not None,
        "requested_quantity": quantity,
        "status": status,
    }
    if broker_result_code is not None:
        event_payload["broker_result_code"] = broker_result_code
    if broker_result_message is not None:
        event_payload["broker_result_message"] = broker_result_message
    _event(
        db,
        order,
        event_type,
        event_payload,
        occurred_at=occurred_at,
    )
    if gate_reason is not None:
        _mark_gate_reconciling(db, gate_reason)
    db.commit()
    db.refresh(order)
    return KiwoomCancelResult(order.id, order.status, quantity, True)
