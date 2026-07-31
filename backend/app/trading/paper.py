from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.ids import uuid7
from app.models import (
    Fill,
    OrderEvent,
    OrderIntent,
    Position,
    PositionEvent,
    TradingGate,
    TradingOrder,
)

ACTIVE_SYMBOL_LOCK_STATES = {"UNKNOWN", "RECONCILING"}
ACTIVE_SELL_STATES = {
    "CREATED",
    "VALIDATING",
    "SUBMITTING",
    "ACKNOWLEDGED",
    "OPEN",
    "PARTIALLY_FILLED",
    "CANCEL_PENDING",
    "REPLACE_PENDING",
    "UNKNOWN",
    "RECONCILING",
}
PRICE_QUANTUM = Decimal("0.0001")
SEOUL = ZoneInfo("Asia/Seoul")


class PaperBrokerError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class PaperBrokerConflict(PaperBrokerError):
    pass


@dataclass(frozen=True)
class PaperOrderRequest:
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Decimal | None
    idempotency_key: str
    correlation_id: str
    market: str = "KRX"
    account_alias: str = "PAPER"
    action: str = "USER_REQUEST"
    trading_date: date | None = None


def utcnow() -> datetime:
    return datetime.now(UTC)


def current_trading_date() -> date:
    return datetime.now(SEOUL).date()


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _price_text(value: Decimal) -> str:
    return str(value.quantize(PRICE_QUANTUM))


def _request_hash(request: PaperOrderRequest, trading_date: date) -> str:
    return _canonical_hash(
        {
            "account_alias": request.account_alias,
            "action": request.action,
            "limit_price": _price_text(request.limit_price) if request.limit_price is not None else None,
            "market": request.market,
            "order_type": request.order_type,
            "quantity": request.quantity,
            "side": request.side,
            "symbol": request.symbol,
            "trading_date": str(trading_date),
        }
    )


def _event(
    db: Session,
    order: TradingOrder,
    event_type: str,
    *,
    source: str = "PAPER",
    source_key: str | None = None,
    payload: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    event_payload = payload or {}
    db.add(
        OrderEvent(
            order_id=order.id,
            event_type=event_type,
            source=source,
            source_key=source_key or uuid7(),
            payload_hash=_canonical_hash(event_payload),
            payload_json=json.dumps(event_payload, separators=(",", ":"), ensure_ascii=False),
            correlation_id=order.correlation_id,
            occurred_at=occurred_at or utcnow(),
        )
    )


def _set_status(db: Session, order: TradingOrder, status: str) -> None:
    previous = order.status
    order.status = status
    order.version += 1
    _event(db, order, "STATUS_CHANGED", payload={"from": previous, "to": status})


def set_paper_gate(db: Session, status: str, reason: str) -> TradingGate:
    if status not in {"STARTING", "RECONCILING", "READY", "DEGRADED", "HALTED"}:
        raise ValueError("Unsupported trading gate status")
    gate = db.get(TradingGate, "PAPER")
    if gate is None:
        gate = TradingGate(account_alias="PAPER", environment="MOCK", status=status, reason=reason)
        db.add(gate)
    else:
        gate.status = status
        gate.reason = reason
        gate.version += 1
    db.commit()
    db.refresh(gate)
    return gate


def _validate_request(db: Session, request: PaperOrderRequest, settings: Settings) -> None:
    if settings.environment.upper() != "MOCK" or settings.live_trading_enabled:
        raise PaperBrokerError("PAPER_ENVIRONMENT_REQUIRED", "Paper Broker는 MOCK 환경에서만 동작합니다.")
    if request.account_alias != "PAPER":
        raise PaperBrokerError("PAPER_ACCOUNT_REQUIRED", "Paper Broker 계좌가 아닙니다.")
    if request.market != "KRX":
        raise PaperBrokerError("UNSUPPORTED_IN_MOCK", "해당 시장은 Paper Broker에서 지원하지 않습니다.")
    if request.side not in {"BUY", "SELL"}:
        raise PaperBrokerError("INVALID_ORDER_SIDE", "지원하지 않는 주문 방향입니다.")
    if request.order_type not in {"LIMIT", "MARKET"}:
        raise PaperBrokerError("INVALID_ORDER_TYPE", "지원하지 않는 주문 유형입니다.")
    if request.quantity <= 0:
        raise PaperBrokerError("INVALID_ORDER_QUANTITY", "주문 수량은 1 이상이어야 합니다.")
    if not request.symbol.isdigit() or len(request.symbol) != 6:
        raise PaperBrokerError("INVALID_SYMBOL", "국내주식 종목코드는 숫자 6자리여야 합니다.")
    if not request.idempotency_key or len(request.idempotency_key) > 128:
        raise PaperBrokerError("INVALID_IDEMPOTENCY_KEY", "유효한 멱등성 키가 필요합니다.")
    if request.order_type == "LIMIT" and (request.limit_price is None or request.limit_price <= 0):
        raise PaperBrokerError("INVALID_LIMIT_PRICE", "지정가 주문에는 유효한 가격이 필요합니다.")
    if request.order_type == "MARKET" and request.limit_price is not None:
        raise PaperBrokerError("MARKET_PRICE_NOT_ALLOWED", "시장가 주문에는 지정 가격을 사용할 수 없습니다.")
    gate = db.get(TradingGate, request.account_alias)
    if gate is None or gate.status != "READY":
        raise PaperBrokerError("TRADING_GATE_CLOSED", "재동기화 완료 전에는 주문할 수 없습니다.", retryable=True)
    locked_order = db.scalar(
        select(TradingOrder.id).where(
            TradingOrder.account_alias == request.account_alias,
            TradingOrder.symbol == request.symbol,
            TradingOrder.status.in_(ACTIVE_SYMBOL_LOCK_STATES),
        )
    )
    if locked_order:
        raise PaperBrokerError("SYMBOL_RECONCILIATION_REQUIRED", "해당 종목 주문을 먼저 재동기화해야 합니다.")
    if request.side == "SELL":
        position = db.scalar(
            select(Position).where(
                Position.account_alias == request.account_alias,
                Position.symbol == request.symbol,
            )
        )
        reserved = db.scalar(
            select(func.coalesce(func.sum(TradingOrder.remaining_quantity), 0)).where(
                TradingOrder.account_alias == request.account_alias,
                TradingOrder.symbol == request.symbol,
                TradingOrder.side == "SELL",
                TradingOrder.status.in_(ACTIVE_SELL_STATES),
            )
        )
        if position is None or position.quantity - int(reserved or 0) < request.quantity:
            raise PaperBrokerError("INSUFFICIENT_POSITION", "매도 가능한 수량이 부족합니다.")


def _submit(db: Session, order: TradingOrder, *, response_lost: bool) -> None:
    _set_status(db, order, "VALIDATING")
    _set_status(db, order, "SUBMITTING")
    if response_lost:
        _set_status(db, order, "UNKNOWN")
        return
    order.broker_order_id = f"PAPER-{uuid7()}"
    _set_status(db, order, "ACKNOWLEDGED")
    _set_status(db, order, "OPEN")


def create_paper_order(
    db: Session,
    request: PaperOrderRequest,
    settings: Settings,
    *,
    response_lost: bool = False,
) -> TradingOrder:
    trading_date = request.trading_date or current_trading_date()
    fingerprint = _request_hash(request, trading_date)
    existing = db.scalar(select(TradingOrder).where(TradingOrder.idempotency_key == request.idempotency_key))
    if existing:
        if existing.request_hash != fingerprint:
            raise PaperBrokerConflict("IDEMPOTENCY_CONFLICT", "같은 멱등성 키의 요청 내용이 다릅니다.")
        return existing
    _validate_request(db, request, settings)
    intent = OrderIntent(
        account_alias=request.account_alias,
        environment="MOCK",
        symbol=request.symbol,
        market=request.market,
        side=request.side,
        action=request.action,
        requested_quantity=request.quantity,
        correlation_id=request.correlation_id,
    )
    db.add(intent)
    db.flush()
    order = TradingOrder(
        intent_id=intent.id,
        order_group_id=intent.order_group_id,
        account_alias=request.account_alias,
        environment="MOCK",
        symbol=request.symbol,
        market=request.market,
        side=request.side,
        order_type=request.order_type,
        limit_price=request.limit_price,
        requested_quantity=request.quantity,
        remaining_quantity=request.quantity,
        idempotency_key=request.idempotency_key,
        request_hash=fingerprint,
        trading_date=trading_date,
        correlation_id=request.correlation_id,
    )
    db.add(order)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raced = db.scalar(select(TradingOrder).where(TradingOrder.idempotency_key == request.idempotency_key))
        if raced and raced.request_hash == fingerprint:
            return raced
        raise PaperBrokerConflict("IDEMPOTENCY_CONFLICT", "멱등성 키 충돌이 발생했습니다.") from None
    _event(db, order, "ORDER_CREATED", payload={"status": "CREATED"})
    _submit(db, order, response_lost=response_lost)
    db.commit()
    db.refresh(order)
    return order


def _position_snapshot(position: Position) -> dict[str, object]:
    return {
        "average_price": str(position.average_price),
        "quantity": position.quantity,
        "state": position.state,
        "version": position.version,
    }


def _apply_position(db: Session, order: TradingOrder, fill: Fill) -> None:
    position = db.scalar(
        select(Position)
        .where(Position.account_alias == order.account_alias, Position.symbol == order.symbol)
        .with_for_update()
    )
    if position is None:
        if order.side == "SELL":
            raise PaperBrokerError("INSUFFICIENT_POSITION", "매도할 포지션이 없습니다.")
        position = Position(account_alias=order.account_alias, symbol=order.symbol)
        db.add(position)
        db.flush()
    before = _position_snapshot(position)
    if order.side == "BUY":
        new_quantity = position.quantity + fill.quantity
        total_cost = position.average_price * position.quantity + fill.price * fill.quantity
        position.average_price = (total_cost / new_quantity).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)
        position.quantity = new_quantity
    else:
        if position.quantity < fill.quantity:
            raise PaperBrokerError("INSUFFICIENT_POSITION", "체결 수량이 보유 수량을 초과합니다.")
        position.quantity -= fill.quantity
        if position.quantity == 0:
            position.average_price = Decimal(0)
    position.state = "OPEN" if position.quantity else "CLOSED"
    position.version += 1
    after = _position_snapshot(position)
    db.add(
        PositionEvent(
            position_id=position.id,
            cause_type="FILL",
            cause_id=fill.id,
            before_json=json.dumps(before, separators=(",", ":")),
            after_json=json.dumps(after, separators=(",", ":")),
            correlation_id=order.correlation_id,
        )
    )


def _adjust_replacement_child_for_late_fill(
    db: Session,
    original: TradingOrder,
    late_fill_quantity: int,
) -> None:
    child = db.scalar(
        select(TradingOrder)
        .where(TradingOrder.parent_order_id == original.id)
        .order_by(TradingOrder.replacement_sequence.desc())
        .with_for_update()
    )
    if child is None:
        return
    absorbed = min(late_fill_quantity, child.remaining_quantity)
    child.remaining_quantity -= absorbed
    child.cancelled_quantity += absorbed
    if absorbed < late_fill_quantity:
        _set_status(db, child, "RECONCILING")
    elif child.filled_quantity == child.requested_quantity:
        _set_status(db, child, "FILLED")
    elif child.remaining_quantity == 0:
        _set_status(db, child, "CANCELLED")
    elif child.filled_quantity > 0:
        _set_status(db, child, "PARTIALLY_FILLED")
    else:
        _set_status(db, child, "OPEN")
    _event(
        db,
        child,
        "PARENT_LATE_FILL_ADJUSTED",
        payload={
            "absorbed_quantity": absorbed,
            "late_fill_quantity": late_fill_quantity,
            "parent_order_id": original.id,
        },
    )


def apply_paper_fill(
    db: Session,
    order_id: str,
    *,
    source_key: str,
    quantity: int,
    price: Decimal,
    fee: Decimal = Decimal(0),
    tax: Decimal = Decimal(0),
    filled_at: datetime | None = None,
) -> tuple[TradingOrder, bool]:
    duplicate = db.scalar(select(Fill).where(Fill.broker_fill_key == source_key))
    if duplicate:
        if (
            duplicate.order_id != order_id
            or duplicate.quantity != quantity
            or duplicate.price != price.quantize(PRICE_QUANTUM)
        ):
            raise PaperBrokerConflict(
                "FILL_SOURCE_CONFLICT",
                "같은 체결 식별자에 서로 다른 체결 내용이 수신되었습니다.",
            )
        duplicate_order = db.get(TradingOrder, duplicate.order_id)
        if duplicate_order is None:  # pragma: no cover - foreign key invariant
            raise PaperBrokerError("ORDER_NOT_FOUND", "체결의 원주문을 찾을 수 없습니다.")
        return duplicate_order, False
    order = db.scalar(select(TradingOrder).where(TradingOrder.id == order_id).with_for_update())
    if order is None:
        raise PaperBrokerError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
    if quantity <= 0 or price <= 0:
        raise PaperBrokerError("INVALID_FILL", "체결 수량과 가격은 0보다 커야 합니다.")
    if fee < 0 or tax < 0:
        raise PaperBrokerError("INVALID_FILL_COST", "수수료와 세금은 0 이상이어야 합니다.")
    available = order.remaining_quantity + order.cancelled_quantity
    if quantity > available:
        raise PaperBrokerError("FILL_QUANTITY_EXCEEDED", "체결 수량이 주문 잔여 수량을 초과합니다.")
    if order.side == "SELL":
        position = db.scalar(
            select(Position).where(
                Position.account_alias == order.account_alias,
                Position.symbol == order.symbol,
            )
        )
        if position is None or position.quantity < quantity:
            raise PaperBrokerError("INSUFFICIENT_POSITION", "체결 수량이 보유 수량을 초과합니다.")
    previous_status = order.status
    fill = Fill(
        order_id=order.id,
        broker_fill_key=source_key,
        quantity=quantity,
        price=price.quantize(PRICE_QUANTUM),
        fee=fee.quantize(PRICE_QUANTUM),
        tax=tax.quantize(PRICE_QUANTUM),
        filled_at=filled_at or utcnow(),
    )
    db.add(fill)
    db.flush()
    from_remaining = min(quantity, order.remaining_quantity)
    order.remaining_quantity -= from_remaining
    order.cancelled_quantity -= quantity - from_remaining
    order.filled_quantity += quantity
    if previous_status == "REPLACED":
        _adjust_replacement_child_for_late_fill(db, order, quantity)
    _apply_position(db, order, fill)
    if order.filled_quantity == order.requested_quantity:
        _set_status(db, order, "FILLED")
    elif previous_status in {"CANCEL_PENDING", "REPLACE_PENDING", "CANCELLED", "REPLACED"}:
        _set_status(db, order, previous_status)
    elif order.remaining_quantity > 0:
        _set_status(db, order, "PARTIALLY_FILLED")
    else:
        _set_status(db, order, "CANCELLED")
    _event(
        db,
        order,
        "FILL_APPLIED",
        source_key=f"fill:{source_key}",
        payload={"fill_id": fill.id, "price": str(fill.price), "quantity": quantity},
        occurred_at=fill.filled_at,
    )
    db.commit()
    db.refresh(order)
    return order, True


def request_paper_cancel(db: Session, order_id: str) -> TradingOrder:
    order = db.scalar(select(TradingOrder).where(TradingOrder.id == order_id).with_for_update())
    if order is None:
        raise PaperBrokerError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
    if order.status not in {"OPEN", "PARTIALLY_FILLED"}:
        raise PaperBrokerConflict("INVALID_ORDER_TRANSITION", "현재 상태에서는 취소를 요청할 수 없습니다.")
    _set_status(db, order, "CANCEL_PENDING")
    db.commit()
    db.refresh(order)
    return order


def confirm_paper_cancel(db: Session, order_id: str) -> TradingOrder:
    order = db.scalar(select(TradingOrder).where(TradingOrder.id == order_id).with_for_update())
    if order is None:
        raise PaperBrokerError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
    if order.status == "FILLED":
        return order
    if order.status != "CANCEL_PENDING":
        raise PaperBrokerConflict("INVALID_ORDER_TRANSITION", "취소 대기 주문이 아닙니다.")
    order.cancelled_quantity += order.remaining_quantity
    order.remaining_quantity = 0
    _set_status(db, order, "CANCELLED")
    db.commit()
    db.refresh(order)
    return order


def request_paper_replace(db: Session, order_id: str) -> TradingOrder:
    order = db.scalar(select(TradingOrder).where(TradingOrder.id == order_id).with_for_update())
    if order is None:
        raise PaperBrokerError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
    if order.status not in {"OPEN", "PARTIALLY_FILLED"}:
        raise PaperBrokerConflict("INVALID_ORDER_TRANSITION", "현재 상태에서는 정정을 요청할 수 없습니다.")
    _set_status(db, order, "REPLACE_PENDING")
    db.commit()
    db.refresh(order)
    return order


def replace_paper_order(
    db: Session,
    order_id: str,
    *,
    new_limit_price: Decimal,
    idempotency_key: str,
) -> TradingOrder:
    existing = db.scalar(select(TradingOrder).where(TradingOrder.idempotency_key == idempotency_key))
    if existing:
        repeated_fingerprint = _canonical_hash(
            {
                "parent_order_id": order_id,
                "price": _price_text(new_limit_price),
                "quantity": existing.requested_quantity,
            }
        )
        if existing.request_hash != repeated_fingerprint:
            raise PaperBrokerConflict("IDEMPOTENCY_CONFLICT", "같은 멱등성 키의 정정 내용이 다릅니다.")
        return existing
    original = db.scalar(select(TradingOrder).where(TradingOrder.id == order_id).with_for_update())
    if original is None:
        raise PaperBrokerError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
    fingerprint = _canonical_hash(
        {
            "parent_order_id": original.id,
            "price": _price_text(new_limit_price),
            "quantity": original.remaining_quantity,
        }
    )
    if original.status == "FILLED":
        raise PaperBrokerConflict("ORDER_ALREADY_FILLED", "정정 대기 중 주문이 전량 체결되었습니다.")
    if original.status != "REPLACE_PENDING" or original.remaining_quantity <= 0:
        raise PaperBrokerConflict("INVALID_ORDER_TRANSITION", "정정 대기 주문이 아닙니다.")
    if new_limit_price <= 0:
        raise PaperBrokerError("INVALID_LIMIT_PRICE", "정정 가격은 0보다 커야 합니다.")
    replacement_quantity = original.remaining_quantity
    original.cancelled_quantity += replacement_quantity
    original.remaining_quantity = 0
    _set_status(db, original, "REPLACED")
    replacement = TradingOrder(
        intent_id=original.intent_id,
        order_group_id=original.order_group_id,
        parent_order_id=original.id,
        account_alias=original.account_alias,
        environment=original.environment,
        symbol=original.symbol,
        market=original.market,
        side=original.side,
        order_type="LIMIT",
        limit_price=new_limit_price,
        requested_quantity=replacement_quantity,
        remaining_quantity=replacement_quantity,
        idempotency_key=idempotency_key,
        request_hash=fingerprint,
        replacement_sequence=original.replacement_sequence + 1,
        trading_date=original.trading_date,
        correlation_id=original.correlation_id,
    )
    db.add(replacement)
    db.flush()
    _event(db, replacement, "ORDER_CREATED", payload={"parent_order_id": original.id})
    _submit(db, replacement, response_lost=False)
    db.commit()
    db.refresh(replacement)
    return replacement
