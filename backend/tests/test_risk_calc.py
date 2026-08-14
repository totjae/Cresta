from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import (
    BrokerWorkerState,
    Fill,
    MarketSnapshot,
    MarketStreamState,
    OrderIntent,
    Position,
    TradingGate,
    TradingOrder,
)
from app.risk_calc import (
    broker_connection_ok,
    consecutive_loss_count,
    daily_entry_count,
    daily_loss_pct,
    daily_realized_loss,
    open_position_count,
    open_position_exposure,
    spread_pct,
    unrealized_loss,
)

ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"
NOW = datetime(2026, 8, 13, 1, 30, tzinfo=UTC)  # 10:30 KST


def _snapshot(
    db: Session,
    symbol: str,
    last_price: Decimal,
    *,
    bid: Decimal | None = None,
    ask: Decimal | None = None,
    sequence: int = 1,
) -> MarketSnapshot:
    snap = MarketSnapshot(
        symbol=symbol,
        market="KRX",
        source="TEST",
        sequence_or_hash=f"risk-{symbol}-{sequence}",
        source_sequence=sequence,
        payload_hash="a" * 64,
        last_price=last_price,
        open_price=last_price,
        high_price=last_price,
        low_price=last_price,
        cumulative_volume=10000,
        best_bid_price=bid,
        best_bid_quantity=100,
        best_ask_price=ask,
        best_ask_quantity=100,
        trading_status="TRADING",
        quality="NORMAL",
        recovery_snapshot=False,
        event_at=NOW,
        received_at=NOW,
    )
    db.add(snap)
    db.flush()
    stream = db.get(MarketStreamState, ("KRX", symbol))
    if stream is None:
        stream = MarketStreamState(
            market="KRX",
            symbol=symbol,
            source="TEST",
            current_snapshot_id=snap.id,
            quality="NORMAL",
        )
        db.add(stream)
    else:
        stream.current_snapshot_id = snap.id
        stream.quality = "NORMAL"
    db.flush()
    return snap


def _position(db: Session, symbol: str, quantity: int, average_price: Decimal) -> Position:
    position = Position(
        account_alias=ACCOUNT_ALIAS,
        symbol=symbol,
        quantity=quantity,
        available_quantity=quantity,
        average_price=average_price,
        managed_quantity=quantity,
        managed_average_price=average_price if quantity > 0 else Decimal(0),
        state="OPEN" if quantity > 0 else "CLOSED",
    )
    db.add(position)
    db.flush()
    return position


def _ready_worker(db: Session) -> None:
    gate = TradingGate(
        account_alias=ACCOUNT_ALIAS, environment="MOCK", status="READY", reason="TEST", version=1
    )
    db.add(gate)
    worker = BrokerWorkerState(
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        state="READY",
        fencing_token=1,
        websocket_connected=True,
        subscriptions_ready=True,
        last_heartbeat_at=NOW,
        started_at=NOW,
    )
    db.add(worker)
    db.flush()


def test_spread_pct() -> None:
    class S:
        best_bid_price = Decimal(100)
        best_ask_price = Decimal(101)

    # (101-100)/100.5*100 = 0.9950...
    assert spread_pct(S()) == Decimal("0.9950")
    assert spread_pct(None) is None


def test_unrealized_loss_uses_latest_price(db: Session) -> None:
    _snapshot(db, "005930", Decimal(49000))
    _position(db, "005930", 10, Decimal(50000))
    db.commit()
    # (49000 - 50000) * 10 = -10000 loss
    assert unrealized_loss(db, ACCOUNT_ALIAS, now=NOW) == Decimal(-10000)


def test_daily_realized_loss_from_sells(db: Session) -> None:
    _position(db, "005930", 0, Decimal(50000))
    intent = OrderIntent(
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        symbol="005930",
        market="KRX",
        side="SELL",
        action="FULL_SELL",
        requested_quantity=10,
        correlation_id="c",
    )
    db.add(intent)
    db.flush()
    order = TradingOrder(
        intent_id=intent.id,
        order_group_id=intent.order_group_id,
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        symbol="005930",
        market="KRX",
        side="SELL",
        order_type="LIMIT",
        requested_quantity=10,
        filled_quantity=10,
        remaining_quantity=0,
        status="FILLED",
        idempotency_key="k",
        request_hash="h",
        trading_date=NOW.astimezone(ZoneInfo("Asia/Seoul")).date(),
        correlation_id="c",
    )
    db.add(order)
    db.flush()
    db.add(
        Fill(
            order_id=order.id,
            broker_fill_key="f1",
            quantity=10,
            price=Decimal(49000),
            filled_at=NOW,
        )
    )
    db.commit()
    # (49000 - 50000) * 10 = -10000
    assert daily_realized_loss(db, ACCOUNT_ALIAS, now=NOW) == Decimal(-10000)


def test_daily_loss_pct_realized_plus_unrealized(db: Session) -> None:
    _snapshot(db, "005930", Decimal(49000))
    _position(db, "005930", 10, Decimal(50000))
    db.commit()
    # unrealized = -10000, denominator = 100000 -> 10%
    pct = daily_loss_pct(
        db, ACCOUNT_ALIAS, basis="REALIZED_PLUS_UNREALIZED", now=NOW, denominator=Decimal(100000)
    )
    assert pct == Decimal(10)


def test_daily_loss_pct_realized_only(db: Session) -> None:
    _snapshot(db, "005930", Decimal(49000))
    _position(db, "005930", 10, Decimal(50000))
    db.commit()
    # no realized sells -> 0%
    pct = daily_loss_pct(
        db, ACCOUNT_ALIAS, basis="REALIZED_ONLY", now=NOW, denominator=Decimal(100000)
    )
    assert pct == Decimal(0)


def test_open_position_exposure(db: Session) -> None:
    _snapshot(db, "005930", Decimal(50000))
    _position(db, "005930", 10, Decimal(40000))
    db.commit()
    per_symbol, total = open_position_exposure(db, ACCOUNT_ALIAS, now=NOW)
    assert per_symbol["005930"] == Decimal(500000)
    assert total == Decimal(500000)


def test_open_position_count(db: Session) -> None:
    _position(db, "005930", 10, Decimal(40000))
    _position(db, "005931", 5, Decimal(60000))
    db.commit()
    assert open_position_count(db, ACCOUNT_ALIAS) == 2


def test_daily_entry_count(db: Session) -> None:
    intent = OrderIntent(
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        symbol="005930",
        market="KRX",
        side="BUY",
        action="BUY",
        requested_quantity=1,
        correlation_id="c",
    )
    db.add(intent)
    db.flush()
    db.add(
        TradingOrder(
            intent_id=intent.id,
            order_group_id=intent.order_group_id,
            account_alias=ACCOUNT_ALIAS,
            environment="MOCK",
            symbol="005930",
            market="KRX",
            side="BUY",
            order_type="LIMIT",
            requested_quantity=1,
            remaining_quantity=1,
            status="CREATED",
            idempotency_key="k",
            request_hash="h",
            trading_date=NOW.astimezone(ZoneInfo("Asia/Seoul")).date(),
            correlation_id="c",
        )
    )
    db.commit()
    assert daily_entry_count(db, ACCOUNT_ALIAS, now=NOW) == 1


def test_consecutive_loss_count(db: Session) -> None:
    _position(db, "005930", 0, Decimal(50000))
    # Most recent first by filled_at: profit (50500), then two losses.
    for i, price in enumerate((Decimal(50500), Decimal(48500), Decimal(49000))):
        intent = OrderIntent(
            account_alias=ACCOUNT_ALIAS,
            environment="MOCK",
            symbol="005930",
            market="KRX",
            side="SELL",
            action="FULL_SELL",
            requested_quantity=1,
            correlation_id=f"c{i}",
        )
        db.add(intent)
        db.flush()
        order = TradingOrder(
            intent_id=intent.id,
            order_group_id=intent.order_group_id,
            account_alias=ACCOUNT_ALIAS,
            environment="MOCK",
            symbol="005930",
            market="KRX",
            side="SELL",
            order_type="LIMIT",
            requested_quantity=1,
            filled_quantity=1,
            remaining_quantity=0,
            status="FILLED",
            idempotency_key=f"k{i}",
            request_hash="h",
            trading_date=NOW.astimezone(ZoneInfo("Asia/Seoul")).date(),
            correlation_id=f"c{i}",
        )
        db.add(order)
        db.flush()
        # i=0 is most recent (largest filled_at), i=2 is oldest.
        db.add(
            Fill(
                order_id=order.id,
                broker_fill_key=f"f{i}",
                quantity=1,
                price=price,
                filled_at=NOW - timedelta(hours=i),
            )
        )
    db.commit()
    # Most recent fill is a profit (50500 > 50000) -> consecutive loss count is 0.
    assert consecutive_loss_count(db, ACCOUNT_ALIAS) == 0


def test_broker_connection_ok(db: Session) -> None:
    _ready_worker(db)
    db.commit()
    ok, code = broker_connection_ok(db, ACCOUNT_ALIAS, now=NOW)
    assert ok and code == "OK"


def test_broker_connection_blocked_when_reconciling(db: Session) -> None:
    gate = TradingGate(
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        status="RECONCILING",
        reason="TEST",
        version=1,
    )
    db.add(gate)
    worker = BrokerWorkerState(
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        state="READY",
        fencing_token=1,
        websocket_connected=True,
        subscriptions_ready=True,
        last_heartbeat_at=NOW,
        started_at=NOW,
    )
    db.add(worker)
    db.commit()
    ok, code = broker_connection_ok(db, ACCOUNT_ALIAS, now=NOW)
    assert not ok and code == "BROKER_GATE_NOT_READY"
