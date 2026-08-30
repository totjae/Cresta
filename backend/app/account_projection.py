from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.kiwoom import BrokerAccountSnapshot, BrokerFillSummary, BrokerOpenOrder
from app.models import Fill, OrderEvent, OrderIntent, Position, PositionEvent, TradingOrder

ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"
ENVIRONMENT = "MOCK"
KST = ZoneInfo("Asia/Seoul")
EVENT_SOURCE = "KIWOOM_RECONCILE"


class AccountProjectionError(RuntimeError):
    code = "RECONCILIATION_PROJECTION_FAILED"


@dataclass(frozen=True)
class ProjectionResult:
    imported_orders: int = 0
    updated_orders: int = 0
    imported_fills: int = 0
    imported_positions: int = 0
    updated_positions: int = 0
    closed_positions: int = 0


def apply_broker_account_projection(
    db: Session,
    snapshot: BrokerAccountSnapshot,
    *,
    run_id: str,
    correlation_id: str,
) -> ProjectionResult:
    """Project unambiguous Kiwoom account facts into the local operational ledger."""
    counts: Counter[str] = Counter()
    orders = _orders_by_broker_id(db)

    for broker_order in snapshot.open_orders:
        order = orders.get(broker_order.broker_order_id)
        if order is None:
            order = _import_open_order(db, broker_order, snapshot, correlation_id)
            orders[broker_order.broker_order_id] = order
            counts["imported_orders"] += 1
        elif _apply_open_order(order, broker_order):
            _add_order_event(
                db,
                order,
                "BROKER_ORDER_PROJECTED",
                _broker_order_payload(broker_order),
                snapshot.observed_at,
                correlation_id,
            )
            counts["updated_orders"] += 1

    fill_totals: dict[str, int] = defaultdict(int)
    for broker_fill in snapshot.fills:
        fill_totals[broker_fill.broker_order_id] += broker_fill.quantity
    overfilled_order_ids = {
        broker_id
        for broker_id, total in fill_totals.items()
        if (order := orders.get(broker_id)) is not None and total > order.requested_quantity
    }
    occurrences: Counter[str] = Counter()
    for broker_fill in snapshot.fills:
        order = orders.get(broker_fill.broker_order_id)
        if order is None or broker_fill.broker_order_id in overfilled_order_ids:
            continue
        base_key = _fill_identity(broker_fill)
        occurrence = occurrences[base_key]
        occurrences[base_key] += 1
        broker_fill_key = f"kiwoom:{_hash({'identity': base_key, 'occurrence': occurrence})}"
        if db.scalar(select(Fill.id).where(Fill.broker_fill_key == broker_fill_key)) is None:
            db.add(
                Fill(
                    order_id=order.id,
                    broker_fill_key=broker_fill_key,
                    quantity=broker_fill.quantity,
                    price=broker_fill.price,
                    fee=broker_fill.fee,
                    tax=broker_fill.tax,
                    filled_at=_broker_time(snapshot.observed_at, broker_fill.order_time),
                )
            )
            counts["imported_fills"] += 1

    open_broker_ids = {item.broker_order_id for item in snapshot.open_orders}
    for broker_id, total in fill_totals.items():
        order = orders.get(broker_id)
        if (
            order is None
            or broker_id in open_broker_ids
            or total > order.requested_quantity
            or order.status == "CANCEL_PENDING"
        ):
            continue
        before = _order_state(order)
        if total == order.requested_quantity:
            order.filled_quantity = total
            order.cancelled_quantity = 0
            order.remaining_quantity = 0
            order.status = "FILLED"
            order.next_action_at = None
        elif total > 0:
            order.filled_quantity = total
            order.cancelled_quantity = 0
            order.remaining_quantity = order.requested_quantity - total
            order.status = "RECONCILING"
        if _order_state(order) != before:
            order.version += 1
            _add_order_event(
                db,
                order,
                "BROKER_FILL_PROJECTED",
                {"broker_order_id": broker_id, "filled_quantity": total},
                snapshot.observed_at,
                correlation_id,
            )
            counts["updated_orders"] += 1

    # A successfully acknowledged cancel is final only after the account
    # snapshot no longer reports the original order. Apply any late fills
    # first, then mark only the confirmed remainder as cancelled.
    for broker_id, order in orders.items():
        if order.status != "CANCEL_PENDING" or broker_id in open_broker_ids:
            continue
        total = fill_totals.get(broker_id, order.filled_quantity)
        if total < 0 or total > order.requested_quantity:
            continue
        before = _order_state(order)
        order.filled_quantity = total
        order.remaining_quantity = 0
        order.cancelled_quantity = order.requested_quantity - total
        order.status = "FILLED" if total == order.requested_quantity else "CANCELLED"
        order.next_action_at = None
        if _order_state(order) != before:
            order.version += 1
            _add_order_event(
                db,
                order,
                "BROKER_CANCEL_PROJECTED",
                {
                    "broker_order_id": broker_id,
                    "filled_quantity": total,
                    "cancelled_quantity": order.cancelled_quantity,
                },
                snapshot.observed_at,
                correlation_id,
            )
            counts["updated_orders"] += 1

    _project_positions(db, snapshot, run_id, correlation_id, counts)
    db.flush()
    return ProjectionResult(
        **{field: counts[field] for field in ProjectionResult.__dataclass_fields__}
    )


def _orders_by_broker_id(db: Session) -> dict[str, TradingOrder]:
    rows = db.scalars(
        select(TradingOrder).where(
            TradingOrder.account_alias == ACCOUNT_ALIAS,
            TradingOrder.environment == ENVIRONMENT,
            TradingOrder.broker_order_id.is_not(None),
        )
    ).all()
    return {row.broker_order_id: row for row in rows if row.broker_order_id}


def _import_open_order(
    db: Session,
    broker: BrokerOpenOrder,
    snapshot: BrokerAccountSnapshot,
    correlation_id: str,
) -> TradingOrder:
    cancelled = broker.requested_quantity - broker.filled_quantity - broker.remaining_quantity
    if (
        broker.requested_quantity <= 0
        or min(broker.filled_quantity, broker.remaining_quantity, cancelled) < 0
    ):
        raise AccountProjectionError("Invalid broker open-order quantities")
    intent = OrderIntent(
        account_alias=ACCOUNT_ALIAS,
        environment=ENVIRONMENT,
        symbol=broker.symbol,
        market=broker.market,
        side=broker.side,
        action="BROKER_IMPORTED",
        requested_quantity=broker.requested_quantity,
        source_type="BROKER_IMPORTED",
        source_id=broker.broker_order_id,
        correlation_id=correlation_id,
    )
    db.add(intent)
    db.flush()
    payload = _broker_order_payload(broker)
    order = TradingOrder(
        intent_id=intent.id,
        order_group_id=intent.order_group_id,
        account_alias=ACCOUNT_ALIAS,
        environment=ENVIRONMENT,
        symbol=broker.symbol,
        market=broker.market,
        side=broker.side,
        order_type="LIMIT" if broker.limit_price is not None else "MARKET",
        limit_price=broker.limit_price,
        requested_quantity=broker.requested_quantity,
        filled_quantity=broker.filled_quantity,
        cancelled_quantity=cancelled,
        remaining_quantity=broker.remaining_quantity,
        status="PARTIALLY_FILLED" if broker.filled_quantity else "OPEN",
        idempotency_key=f"broker-import:{ENVIRONMENT}:{ACCOUNT_ALIAS}:{broker.broker_order_id}",
        request_hash=_hash(payload),
        broker_order_id=broker.broker_order_id,
        trading_date=snapshot.observed_at.astimezone(KST).date(),
        correlation_id=correlation_id,
    )
    db.add(order)
    db.flush()
    _add_order_event(
        db,
        order,
        "BROKER_ORDER_IMPORTED",
        payload,
        snapshot.observed_at,
        correlation_id,
    )
    return order


def _apply_open_order(order: TradingOrder, broker: BrokerOpenOrder) -> bool:
    cancelled = broker.requested_quantity - broker.filled_quantity - broker.remaining_quantity
    if (
        broker.requested_quantity <= 0
        or min(broker.filled_quantity, broker.remaining_quantity, cancelled) < 0
    ):
        raise AccountProjectionError("Invalid broker open-order quantities")
    before = _order_state(order)
    order.symbol = broker.symbol
    order.market = broker.market
    order.side = broker.side
    order.order_type = "LIMIT" if broker.limit_price is not None else "MARKET"
    order.limit_price = broker.limit_price
    order.requested_quantity = broker.requested_quantity
    order.filled_quantity = broker.filled_quantity
    order.cancelled_quantity = cancelled
    order.remaining_quantity = broker.remaining_quantity
    order.status = "PARTIALLY_FILLED" if broker.filled_quantity else "OPEN"
    changed = _order_state(order) != before
    if changed:
        order.version += 1
    return changed


def _project_positions(
    db: Session,
    snapshot: BrokerAccountSnapshot,
    run_id: str,
    correlation_id: str,
    counts: Counter[str],
) -> None:
    internal = {
        row.symbol: row
        for row in db.scalars(select(Position).where(Position.account_alias == ACCOUNT_ALIAS)).all()
    }
    broker_symbols: set[str] = set()
    for broker in snapshot.positions:
        broker_symbols.add(broker.symbol)
        position = internal.get(broker.symbol)
        replayed = _replay_managed_position(db, broker.symbol)
        managed_quantity, managed_average_price = _managed_projection(
            position=position,
            broker_quantity=broker.quantity,
            replayed=replayed,
        )
        origin = _position_origin(broker.quantity, managed_quantity)
        if position is None:
            position = Position(
                account_alias=ACCOUNT_ALIAS,
                symbol=broker.symbol,
                quantity=broker.quantity,
                available_quantity=broker.available_quantity,
                average_price=broker.average_price,
                managed_quantity=managed_quantity,
                managed_average_price=managed_average_price,
                state="OPEN" if broker.quantity else "CLOSED",
                origin=origin,
            )
            db.add(position)
            db.flush()
            _add_position_event(db, position, run_id, {}, correlation_id)
            counts["imported_positions"] += 1
            continue
        before = _position_state(position)
        before_comparison = _position_comparison_state(position)
        position.quantity = broker.quantity
        position.available_quantity = broker.available_quantity
        position.average_price = broker.average_price
        position.managed_quantity = managed_quantity
        position.managed_average_price = managed_average_price
        position.state = "OPEN" if broker.quantity else "CLOSED"
        if broker.quantity:
            position.origin = origin
        if _position_comparison_state(position) != before_comparison:
            position.version += 1
            _add_position_event(db, position, run_id, before, correlation_id)
            counts["updated_positions"] += 1

    for symbol, position in internal.items():
        if position.state != "OPEN" or symbol in broker_symbols:
            continue
        before = _position_state(position)
        position.quantity = 0
        position.available_quantity = 0
        position.average_price = Decimal(0)
        position.managed_quantity = 0
        position.managed_average_price = Decimal(0)
        position.state = "CLOSED"
        position.version += 1
        _add_position_event(db, position, run_id, before, correlation_id)
        counts["closed_positions"] += 1


def _replay_managed_position(db: Session, symbol: str) -> tuple[int, Decimal] | None:
    """Replay only Cresta-originated fills for one account and symbol.

    ``None`` means there is no retained Cresta fill history. That distinction
    lets upgraded installations preserve a legacy managed attribution until a
    real Cresta fill ledger exists, while new/external positions stay external.
    """
    rows = db.execute(
        select(Fill, TradingOrder.side)
        .join(TradingOrder, TradingOrder.id == Fill.order_id)
        .join(OrderIntent, OrderIntent.id == TradingOrder.intent_id)
        .where(
            TradingOrder.account_alias == ACCOUNT_ALIAS,
            TradingOrder.environment == ENVIRONMENT,
            TradingOrder.symbol == symbol,
            OrderIntent.action != "BROKER_IMPORTED",
        )
        .order_by(Fill.filled_at.asc(), Fill.id.asc())
    ).all()
    if not rows:
        return None

    quantity = 0
    average_price = Decimal(0)
    for fill, side in rows:
        if side == "BUY":
            new_quantity = quantity + fill.quantity
            total_cost = average_price * quantity + fill.price * fill.quantity
            average_price = total_cost / new_quantity
            quantity = new_quantity
        elif side == "SELL":
            quantity = max(0, quantity - fill.quantity)
            if quantity == 0:
                average_price = Decimal(0)
    return quantity, average_price


def _managed_projection(
    *,
    position: Position | None,
    broker_quantity: int,
    replayed: tuple[int, Decimal] | None,
) -> tuple[int, Decimal]:
    if broker_quantity <= 0:
        return 0, Decimal(0)
    if replayed is None:
        legacy_quantity = 0 if position is None else position.managed_quantity
        legacy_average = Decimal(0) if position is None else position.managed_average_price
        managed_quantity = min(max(0, legacy_quantity), broker_quantity)
        return (
            managed_quantity,
            legacy_average if managed_quantity > 0 else Decimal(0),
        )
    quantity, average_price = replayed
    managed_quantity = min(max(0, quantity), broker_quantity)
    return managed_quantity, average_price if managed_quantity > 0 else Decimal(0)


def _position_origin(quantity: int, managed_quantity: int) -> str:
    if managed_quantity <= 0:
        return "EXTERNAL"
    if managed_quantity >= quantity:
        return "CRESTA_MANAGED"
    return "MIXED"


def _add_order_event(
    db: Session,
    order: TradingOrder,
    event_type: str,
    payload: dict[str, object],
    occurred_at: datetime,
    correlation_id: str,
) -> None:
    payload_hash = _hash(payload)
    source_key = f"{order.broker_order_id}:{event_type}:{payload_hash[:32]}"
    if (
        db.scalar(
            select(OrderEvent.id).where(
                OrderEvent.source == EVENT_SOURCE,
                OrderEvent.source_key == source_key,
            )
        )
        is not None
    ):
        return
    db.add(
        OrderEvent(
            order_id=order.id,
            event_type=event_type,
            source=EVENT_SOURCE,
            source_key=source_key,
            payload_hash=payload_hash,
            payload_json=_json(payload),
            correlation_id=correlation_id,
            occurred_at=occurred_at,
        )
    )


def _add_position_event(
    db: Session,
    position: Position,
    run_id: str,
    before: dict[str, object],
    correlation_id: str,
) -> None:
    db.add(
        PositionEvent(
            position_id=position.id,
            cause_type="RECONCILIATION",
            cause_id=run_id,
            before_json=_json(before),
            after_json=_json(_position_state(position)),
            correlation_id=correlation_id,
        )
    )


def _broker_time(observed_at: datetime, raw: str) -> datetime:
    normalized = raw.strip().zfill(6)[-6:]
    try:
        parsed = time(int(normalized[:2]), int(normalized[2:4]), int(normalized[4:6]))
    except ValueError as exc:
        raise AccountProjectionError("Invalid broker order time") from exc
    return datetime.combine(observed_at.astimezone(KST).date(), parsed, tzinfo=KST).astimezone(
        observed_at.tzinfo
    )


def _fill_identity(fill: BrokerFillSummary) -> str:
    return _json(
        {
            "broker_order_id": fill.broker_order_id,
            "symbol": fill.symbol,
            "side": fill.side,
            "quantity": fill.quantity,
            "price": format(fill.price, "f"),
            "fee": format(fill.fee, "f"),
            "tax": format(fill.tax, "f"),
            "order_time": fill.order_time,
            "market": fill.market,
        }
    )


def _broker_order_payload(order: BrokerOpenOrder) -> dict[str, object]:
    return {
        "broker_order_id": order.broker_order_id,
        "symbol": order.symbol,
        "market": order.market,
        "side": order.side,
        "requested_quantity": order.requested_quantity,
        "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity,
        "limit_price": format(order.limit_price, "f") if order.limit_price is not None else None,
        "order_time": order.order_time,
    }


def _order_state(order: TradingOrder) -> tuple[object, ...]:
    return (
        order.symbol,
        order.market,
        order.side,
        order.order_type,
        order.limit_price,
        order.requested_quantity,
        order.filled_quantity,
        order.cancelled_quantity,
        order.remaining_quantity,
        order.status,
    )


def _position_state(position: Position) -> dict[str, object]:
    return {
        "symbol": position.symbol,
        "quantity": position.quantity,
        "available_quantity": position.available_quantity,
        "average_price": format(position.average_price, "f"),
        "managed_quantity": position.managed_quantity,
        "managed_average_price": format(position.managed_average_price, "f"),
        "external_quantity": position.quantity - position.managed_quantity,
        "state": position.state,
        "origin": position.origin,
    }


def _position_comparison_state(position: Position) -> tuple[object, ...]:
    return (
        position.symbol,
        position.quantity,
        position.available_quantity,
        position.average_price,
        position.managed_quantity,
        position.managed_average_price,
        position.state,
        position.origin,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
