from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MarketSnapshot, MarketStreamState
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
