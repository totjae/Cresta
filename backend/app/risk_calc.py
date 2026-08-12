"""Risk calculation helpers for the full Risk Guard (#2 milestone).

Pure read-side functions over the trading ledger (positions, fills, orders,
market snapshots, broker worker state). The Guard in ``decision_execution``
calls these to evaluate exposure, daily loss, entry count, spread and
connection risk before allowing a BUY. Results feed ``risk_events`` rows on
block (GRD-080). These functions never mutate state.

Conventions:
- Loss is reported as a non-positive ``Decimal`` (0 = no loss, negative = loss).
- "Today" is the KST trading day of ``now``.
- "Current price" for a symbol is the latest NORMAL KRX snapshot's ``last_price``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    BrokerWorkerState,
    Fill,
    MarketSnapshot,
    MarketStreamState,
    Position,
    TradingGate,
    TradingOrder,
)

KST = ZoneInfo("Asia/Seoul")
PRICE_QUANTUM = Decimal("0.0001")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _kst_date(now: datetime) -> object:
    return _utc(now).astimezone(KST).date()


def _latest_price(db: Session, symbol: str, market: str = "KRX") -> Decimal | None:
    stream = db.get(MarketStreamState, (market, symbol))
    if stream is None or stream.current_snapshot_id is None:
        return None
    snapshot = db.get(MarketSnapshot, stream.current_snapshot_id)
    if snapshot is None or snapshot.quality != "NORMAL":
        return None
    return snapshot.last_price


def spread_pct(snapshot: MarketSnapshot | None) -> Decimal | None:
    """Bid/ask spread as a percentage of the midpoint (matches indicators.py)."""
    if snapshot is None:
        return None
    bid = snapshot.best_bid_price
    ask = snapshot.best_ask_price
    if bid is None or ask is None:
        return None
    midpoint = (bid + ask) / 2
    if midpoint <= 0:
        return None
    return ((ask - bid) / midpoint * Decimal(100)).quantize(Decimal("0.0001"))


def daily_realized_loss(db: Session, account_alias: str, *, now: datetime) -> Decimal:
    """Realized loss for the KST trading day of ``now``.

    Sums (sell fill price - position average cost) * fill quantity for every
    SELL fill today. A loss is negative. Average cost is the position's
    ``average_price`` at fill time; we approximate using the current position
    row's average_price (the ledger does not snapshot per-fill cost basis), so
    this is a conservative same-day estimate.
    """
    today = _kst_date(now)
    rows = db.execute(
        select(Fill.quantity, Fill.price, TradingOrder.symbol)
        .join(TradingOrder, TradingOrder.id == Fill.order_id)
        .where(
            TradingOrder.account_alias == account_alias,
            TradingOrder.side == "SELL",
            TradingOrder.trading_date == today,
        )
    ).all()
    loss = Decimal(0)
    for quantity, fill_price, symbol in rows:
        position = db.scalar(
            select(Position).where(
                Position.account_alias == account_alias, Position.symbol == symbol
            )
        )
        cost = position.average_price if position is not None else Decimal(0)
        loss += (fill_price - cost) * quantity
    return loss


def unrealized_loss(db: Session, account_alias: str, *, now: datetime) -> Decimal:
    """Unrealized loss across open positions at the latest NORMAL price.

    Sums (current price - average_price) * quantity. A loss is negative.
    Positions with no fresh price are skipped (treated as 0, conservative).
    """
    positions = list(
        db.scalars(
            select(Position).where(
                Position.account_alias == account_alias,
                Position.state == "OPEN",
                Position.quantity > 0,
            )
        )
    )
    loss = Decimal(0)
    for position in positions:
        price = _latest_price(db, position.symbol)
        if price is None:
            continue
        loss += (price - position.average_price) * position.quantity
    return loss


def daily_loss_pct(
    db: Session,
    account_alias: str,
    *,
    basis: str,
    now: datetime,
    denominator: Decimal,
) -> Decimal:
    """Daily loss as a percentage of ``denominator`` (the account basis).

    ``basis`` is ``REALIZED_ONLY`` or ``REALIZED_PLUS_UNREALIZED``. Loss is
    reported as a positive percentage (e.g. 3.5 means -3.5%); 0 means no loss.
    """
    realized = daily_realized_loss(db, account_alias, now=now)
    total_loss = realized
    if basis == "REALIZED_PLUS_UNREALIZED":
        total_loss += unrealized_loss(db, account_alias, now=now)
    if denominator <= 0:
        return Decimal(0)
    pct = (-total_loss / denominator * Decimal(100))
    return pct if pct > 0 else Decimal(0)


def open_position_exposure(
    db: Session, account_alias: str, *, now: datetime
) -> tuple[dict[str, Decimal], Decimal]:
    """Per-symbol and total open position exposure at the latest price.

    Symbols without a fresh price use ``average_price * quantity`` (cost basis)
    so exposure is never understated.
    """
    positions = list(
        db.scalars(
            select(Position).where(
                Position.account_alias == account_alias,
                Position.state == "OPEN",
                Position.quantity > 0,
            )
        )
    )
    per_symbol: dict[str, Decimal] = {}
    total = Decimal(0)
    for position in positions:
        price = _latest_price(db, position.symbol)
        value = (price or position.average_price) * position.quantity
        per_symbol[position.symbol] = per_symbol.get(position.symbol, Decimal(0)) + value
        total += value
    return per_symbol, total


def open_position_count(db: Session, account_alias: str) -> int:
    return int(
        db.scalar(
            select(func.count(Position.id)).where(
                Position.account_alias == account_alias,
                Position.state == "OPEN",
                Position.quantity > 0,
            )
        )
        or 0
    )


def daily_entry_count(db: Session, account_alias: str, *, now: datetime) -> int:
    """Number of BUY orders created today (any state past CREATED intent)."""
    today = _kst_date(now)
    return int(
        db.scalar(
            select(func.count(TradingOrder.id)).where(
                TradingOrder.account_alias == account_alias,
                TradingOrder.side == "BUY",
                TradingOrder.trading_date == today,
            )
        )
        or 0
    )


def consecutive_loss_count(db: Session, account_alias: str) -> int:
    """Count of consecutive losing SELL fills, most-recent first.

    A losing fill is one where the fill price is below the position's
    average cost. Stops at the first non-loss fill. Returns 0 when there
    are no SELL fills.
    """
    rows = db.execute(
        select(Fill.quantity, Fill.price, TradingOrder.symbol)
        .join(TradingOrder, TradingOrder.id == Fill.order_id)
        .where(TradingOrder.account_alias == account_alias, TradingOrder.side == "SELL")
        .order_by(Fill.filled_at.desc(), Fill.id.desc())
        .limit(50)
    ).all()
    count = 0
    for quantity, fill_price, symbol in rows:
        position = db.scalar(
            select(Position).where(
                Position.account_alias == account_alias, Position.symbol == symbol
            )
        )
        cost = position.average_price if position is not None else Decimal(0)
        if fill_price < cost:
            count += 1
        else:
            break
    return count


def broker_connection_ok(
    db: Session, account_alias: str, *, now: datetime, heartbeat_stale_seconds: int = 30
) -> tuple[bool, str]:
    """Whether the broker is connected and not mid-reconciliation.

    Returns (ok, reason_code). The BUY Guard blocks when the worker is not
    READY, the websocket dropped, subscriptions are stale, or the gate is in
    RECONCILING/DEGRADED/HALTED (GRD-044: do not resume new buys before
    reconnection+resync completes).
    """
    worker = db.get(BrokerWorkerState, account_alias)
    gate = db.get(TradingGate, account_alias)
    if worker is None:
        return False, "BROKER_WORKER_UNKNOWN"
    if worker.state != "READY":
        return False, "BROKER_NOT_READY"
    if not worker.websocket_connected:
        return False, "BROKER_WEBSOCKET_DISCONNECTED"
    if not worker.subscriptions_ready:
        return False, "BROKER_SUBSCRIPTIONS_NOT_READY"
    heartbeat = _utc(worker.last_heartbeat_at)
    if (_utc(now) - heartbeat).total_seconds() > heartbeat_stale_seconds:
        return False, "BROKER_HEARTBEAT_STALE"
    if gate is None or gate.status != "READY":
        return False, "BROKER_GATE_NOT_READY"
    return True, "OK"
