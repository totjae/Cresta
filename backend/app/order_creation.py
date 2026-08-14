"""Shared order creation service for Cresta-managed MOCK orders.

Generalizes the OrderIntent + TradingOrder(CREATED) construction used by the
Paper broker (``app/trading/paper.py``) and the Kiwoom MOCK connection test
(``app.broker.mock_order_test``) so that approval-based BUY and rule-based
FIXED_STOP SELL share one atomic, idempotent path. The persisted CREATED order
is picked up by the existing broker worker FIFO sender
(``app.broker.order_sender.send_next_created_order``), which requires
``account_alias == KIWOOM_MOCK_PRIMARY`` and ``environment == MOCK``.

This service never decides *whether* to create an order — callers (approval
service, stop trigger, future take-profit) run Guard checks first and only
call here once a decision is final. It never sends the order to the broker;
the worker owns transmission.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AuditLog, OrderEvent, OrderIntent, TradingOrder, User

ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"
ENVIRONMENT = "MOCK"
KST = timezone(timedelta(hours=9))


class OrderCreationError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class OrderRequest:
    """Action-agnostic description of a single order to persist as CREATED.

    ``request_payload`` is canonicalized (sorted, compact JSON) and hashed to
    form ``request_hash``; together with ``idempotency_key`` it enforces that
    the same logical order is persisted exactly once.
    """

    symbol: str
    market: str
    side: str
    action: str
    order_type: str
    limit_price: Decimal | None
    quantity: int
    idempotency_key: str
    request_payload: dict[str, Any]
    correlation_id: str


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _request_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _trading_date(now: datetime | None) -> Any:
    observed = now or datetime.now(UTC)
    return observed.astimezone(KST).date()


def _order_event(
    db: Session, order: TradingOrder, event_type: str, *, payload: dict[str, Any]
) -> None:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    db.add(
        OrderEvent(
            order_id=order.id,
            event_type=event_type,
            source="CRESTA",
            source_key=f"{order.id}:{event_type}",
            payload_hash=hashlib.sha256(body.encode()).hexdigest(),
            payload_json=body,
            correlation_id=order.correlation_id,
            occurred_at=datetime.now(UTC),
        )
    )


def _validate(request: OrderRequest) -> None:
    if request.quantity <= 0:
        raise OrderCreationError("ORDER_QUANTITY_INVALID", 400)
    if request.side not in ("BUY", "SELL"):
        raise OrderCreationError("ORDER_SIDE_INVALID", 400)
    if request.market not in ("KRX", "NXT"):
        raise OrderCreationError("ORDER_MARKET_INVALID", 400)
    if request.order_type == "LIMIT" and request.limit_price is None:
        raise OrderCreationError("LIMIT_PRICE_REQUIRED", 400)
    if request.order_type == "MARKET" and request.limit_price is not None:
        raise OrderCreationError("MARKET_PRICE_NOT_ALLOWED", 400)


def _unfilled_policy(request: OrderRequest) -> tuple[str, int, int]:
    """Return the first safe persisted policy for a newly created order.

    Only entry BUY cancellation is enabled in this milestone. Exit repricing
    stays disabled until cancel/fill races have broker evidence.
    """
    if request.side == "BUY" and request.action == "BUY":
        return "CANCEL", 10, 0
    return "NONE", 0, 0


def create_order(
    db: Session,
    *,
    user: User,
    request: OrderRequest,
    audit_action: str,
    request_ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> TradingOrder:
    """Persist one ``OrderIntent`` + ``TradingOrder(status=CREATED)`` atomically.

    Idempotent on ``idempotency_key``: a repeat request with the same key and
    matching ``request_hash`` returns the existing order; a same key with a
    different payload is rejected (``IDEMPOTENCY_CONFLICT``). The order is left
    in ``CREATED`` for the broker worker to transmit.
    """
    _validate(request)
    fingerprint = _request_hash(request.request_payload)

    existing = db.scalar(
        select(TradingOrder).where(TradingOrder.idempotency_key == request.idempotency_key)
    )
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise OrderCreationError("IDEMPOTENCY_CONFLICT")
        return existing

    intent = OrderIntent(
        account_alias=ACCOUNT_ALIAS,
        environment=ENVIRONMENT,
        symbol=request.symbol,
        market=request.market,
        side=request.side,
        action=request.action,
        requested_quantity=request.quantity,
        correlation_id=request.correlation_id,
    )
    db.add(intent)
    db.flush()
    unfilled_policy, fill_timeout_seconds, max_reprice_attempts = _unfilled_policy(
        request
    )
    order = TradingOrder(
        intent_id=intent.id,
        order_group_id=intent.order_group_id,
        account_alias=ACCOUNT_ALIAS,
        environment=ENVIRONMENT,
        symbol=request.symbol,
        market=request.market,
        side=request.side,
        order_type=request.order_type,
        limit_price=request.limit_price,
        requested_quantity=request.quantity,
        remaining_quantity=request.quantity,
        status="CREATED",
        idempotency_key=request.idempotency_key,
        request_hash=fingerprint,
        unfilled_policy=unfilled_policy,
        fill_timeout_seconds=fill_timeout_seconds,
        max_reprice_attempts=max_reprice_attempts,
        trading_date=_trading_date(now),
        correlation_id=request.correlation_id,
    )
    db.add(order)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raced = db.scalar(
            select(TradingOrder).where(
                TradingOrder.idempotency_key == request.idempotency_key
            )
        )
        if raced is not None and raced.request_hash == fingerprint:
            return raced
        raise OrderCreationError("IDEMPOTENCY_CONFLICT") from None
    _order_event(
        db, order, "ORDER_CREATED", payload={"status": "CREATED", "action": request.action}
    )
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action=audit_action,
            target=order.id,
            result="PASSED",
            request_ip=request_ip,
            user_agent=(user_agent or "")[:256] if user_agent else None,
            correlation_id=request.correlation_id,
            metadata_json=json.dumps(
                {
                    "symbol": request.symbol,
                    "side": request.side,
                    "action": request.action,
                    "order_type": request.order_type,
                    "quantity": request.quantity,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    db.flush()
    return order
