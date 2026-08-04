from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import IndicatorSnapshot, MarketSnapshot, MarketStreamState, MinuteBar
from app.watch import QuoteEvent, WatchError, ingest_quote


def quote(
    key: str,
    sequence: int,
    *,
    market: str = "KRX",
    volume: int = 100,
    at: datetime | None = None,
    recovery: bool = False,
) -> QuoteEvent:
    observed = at or datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
    return QuoteEvent(
        symbol="005930",
        market=market,
        source="KIWOOM_FIXTURE",
        sequence_or_hash=key,
        source_sequence=sequence,
        event_at=observed,
        received_at=observed + timedelta(milliseconds=20),
        last_price=Decimal(70100),
        open_price=Decimal(70000),
        high_price=Decimal(70200),
        low_price=Decimal(69900),
        cumulative_volume=volume,
        best_bid_price=Decimal(70000),
        best_bid_quantity=50,
        best_ask_price=Decimal(70100),
        best_ask_quantity=40,
        trading_status="TRADING",
        recovery_snapshot=recovery,
    )


def test_watch_separates_markets_and_suppresses_identical_duplicates(db: Session) -> None:
    krx = ingest_quote(db, quote("krx-10", 10))
    duplicate = ingest_quote(db, quote("krx-10", 10))
    nxt = ingest_quote(db, quote("nxt-10", 10, market="NXT"))

    assert krx.outcome == "APPLIED"
    assert duplicate.outcome == "DUPLICATE"
    assert duplicate.snapshot.id == krx.snapshot.id
    assert nxt.snapshot.id != krx.snapshot.id
    assert db.scalar(select(func.count()).select_from(MarketSnapshot)) == 2
    assert db.get(MarketStreamState, ("KRX", "005930")).current_snapshot_id == krx.snapshot.id
    assert db.get(MarketStreamState, ("NXT", "005930")).current_snapshot_id == nxt.snapshot.id

    with pytest.raises(WatchError) as conflict:
        ingest_quote(db, replace(quote("krx-10", 10), last_price=Decimal(70200)))
    assert conflict.value.code == "SOURCE_EVENT_CONFLICT"


def test_late_gap_and_volume_regression_keep_last_normal_until_recovery(db: Session) -> None:
    base_at = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
    normal = ingest_quote(db, quote("seq-10", 10, at=base_at, volume=100))
    late = ingest_quote(
        db,
        quote("seq-9", 9, at=base_at - timedelta(seconds=1), volume=90),
    )
    state = db.get(MarketStreamState, ("KRX", "005930"))
    assert late.outcome == "LATE"
    assert state.current_snapshot_id == normal.snapshot.id
    assert state.quality == "NORMAL"

    gap = ingest_quote(
        db,
        quote("seq-12", 12, at=base_at + timedelta(seconds=1), volume=120),
    )
    db.refresh(state)
    assert gap.outcome == "GAP_DETECTED"
    assert state.quality == "GAP_DETECTED"
    assert state.current_snapshot_id == normal.snapshot.id

    ordinary = ingest_quote(
        db,
        quote("seq-11", 11, at=base_at + timedelta(seconds=2), volume=110),
    )
    db.refresh(state)
    assert ordinary.outcome == "GAP_DETECTED"
    assert state.current_snapshot_id == normal.snapshot.id

    recovered = ingest_quote(
        db,
        quote("recovery-20", 20, at=base_at + timedelta(seconds=3), volume=130, recovery=True),
    )
    db.refresh(state)
    assert recovered.outcome == "RECOVERED"
    assert state.quality == "NORMAL"
    assert state.current_snapshot_id == recovered.snapshot.id

    regressed = ingest_quote(
        db,
        quote("seq-21", 21, at=base_at + timedelta(seconds=4), volume=125),
    )
    db.refresh(state)
    assert regressed.outcome == "GAP_DETECTED"
    assert state.quality == "GAP_DETECTED"
    assert state.current_snapshot_id == recovered.snapshot.id


def test_invalid_quote_is_rejected_before_persistence(db: Session) -> None:
    with pytest.raises(WatchError) as invalid:
        ingest_quote(db, replace(quote("invalid", 1), high_price=Decimal(69000)))
    assert invalid.value.code == "INVALID_PRICE"
    assert db.scalar(select(func.count()).select_from(MarketSnapshot)) == 0


def test_minute_bars_and_indicators_are_deterministic(db: Session) -> None:
    base = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)
    prices = [100, 101, 102, 104, 103]
    latest = None
    for index, price in enumerate(prices):
        latest = ingest_quote(
            db,
            replace(
                quote(
                    f"bar-{index}", index + 1,
                    at=base + timedelta(minutes=index), volume=100 + index * 10,
                ),
                last_price=Decimal(price),
                open_price=Decimal(100),
                high_price=Decimal(105),
                low_price=Decimal(99),
                best_bid_price=Decimal("102.9"),
                best_ask_price=Decimal("103.1"),
            ),
        )
    assert latest is not None
    bars = list(db.scalars(select(MinuteBar).order_by(MinuteBar.bucket_start)))
    assert len(bars) == 5
    assert [bar.volume for bar in bars] == [0, 10, 10, 10, 10]
    assert bars[-1].open_price == Decimal(103)
    assert bars[-1].close_price == Decimal(103)

    indicator = db.scalar(
        select(IndicatorSnapshot).where(
            IndicatorSnapshot.market_snapshot_id == latest.snapshot.id
        )
    )
    assert indicator is not None
    assert indicator.calculator_version == "watch-indicators-v1"
    assert indicator.vwap == Decimal("102.5000")
    assert indicator.sma5 == Decimal("102.0000")
    assert indicator.session_high == Decimal("105.0000")
    assert indicator.drawdown_from_high_pct == Decimal("-1.904762")
    assert indicator.spread_pct == Decimal("0.194175")

    orderbook = ingest_quote(
        db,
        replace(
            quote("book-only", 6, at=base + timedelta(minutes=4, seconds=10), volume=140),
            last_price=Decimal(103), open_price=Decimal(100), high_price=Decimal(105),
            low_price=Decimal(99), best_bid_price=Decimal("102.8"),
            best_ask_price=Decimal("103.2"), updates_trade=False,
        ),
    )
    db.refresh(bars[-1])
    assert bars[-1].event_count == 1
    book_indicator = db.scalar(
        select(IndicatorSnapshot).where(
            IndicatorSnapshot.market_snapshot_id == orderbook.snapshot.id
        )
    )
    assert book_indicator is not None
    assert book_indicator.spread_pct == Decimal("0.388350")


def test_new_kst_trading_date_allows_cumulative_volume_reset(db: Session) -> None:
    first = datetime(2026, 8, 4, 6, 0, tzinfo=UTC)
    ingest_quote(db, replace(quote("day-one", 1, at=first, volume=1000), source_sequence=None))
    next_day = ingest_quote(
        db,
        replace(
            quote("day-two", 2, at=first + timedelta(days=1), volume=5),
            source_sequence=None,
        ),
    )
    assert next_day.outcome == "APPLIED"
    state = db.get(MarketStreamState, ("KRX", "005930"))
    assert state is not None
    assert state.cumulative_volume == 5
