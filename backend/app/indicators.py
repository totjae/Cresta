from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IndicatorSnapshot, MarketSnapshot, MinuteBar
from app.watch import PRICE_QUANTUM, QuoteEvent

KST = ZoneInfo("Asia/Seoul")
PCT_QUANTUM = Decimal("0.000001")
CALCULATOR_VERSION = "watch-indicators-v1"


def trading_date(value: datetime) -> object:
    return value.astimezone(KST).date()


def update_market_analysis(
    db: Session,
    *,
    event: QuoteEvent,
    snapshot: MarketSnapshot,
    previous_cumulative_volume: int | None,
    previous_event_at: datetime | None,
) -> IndicatorSnapshot:
    event_at = event.event_at.astimezone(UTC)
    local_event = event_at.astimezone(KST)
    day_start = datetime.combine(local_event.date(), time.min, tzinfo=KST).astimezone(UTC)
    day_end = day_start + timedelta(days=1)

    if event.updates_trade:
        same_day = (
            previous_event_at is not None
            and trading_date(previous_event_at) == trading_date(event_at)
        )
        delta = (
            max(event.cumulative_volume - previous_cumulative_volume, 0)
            if same_day and previous_cumulative_volume is not None
            else 0
        )
        bucket_start = local_event.replace(second=0, microsecond=0).astimezone(UTC)
        bar = db.scalar(
            select(MinuteBar)
            .where(
                MinuteBar.market == event.market,
                MinuteBar.symbol == event.symbol,
                MinuteBar.bucket_start == bucket_start,
            )
            .with_for_update()
        )
        price = event.last_price.quantize(PRICE_QUANTUM)
        turnover_delta = (price * delta).quantize(PRICE_QUANTUM)
        if bar is None:
            bar = MinuteBar(
                market=event.market,
                symbol=event.symbol,
                bucket_start=bucket_start,
                open_price=price,
                high_price=price,
                low_price=price,
                close_price=price,
                volume=delta,
                turnover=turnover_delta,
                event_count=1,
                first_snapshot_id=snapshot.id,
                last_snapshot_id=snapshot.id,
                version=1,
            )
            db.add(bar)
        else:
            bar.high_price = max(bar.high_price, price)
            bar.low_price = min(bar.low_price, price)
            bar.close_price = price
            bar.volume += delta
            bar.turnover += turnover_delta
            bar.event_count += 1
            bar.last_snapshot_id = snapshot.id
            bar.version += 1
        db.flush()

    bars = list(
        db.scalars(
            select(MinuteBar)
            .where(
                MinuteBar.market == event.market,
                MinuteBar.symbol == event.symbol,
                MinuteBar.bucket_start >= day_start,
                MinuteBar.bucket_start < day_end,
            )
            .order_by(MinuteBar.bucket_start)
        )
    )
    total_volume = sum(bar.volume for bar in bars)
    total_turnover = sum((bar.turnover for bar in bars), Decimal(0))
    vwap = (
        total_turnover / total_volume if total_volume > 0 else event.last_price
    ).quantize(PRICE_QUANTUM)
    recent = bars[-5:]
    sma5 = (
        (sum((bar.close_price for bar in recent), Decimal(0)) / 5).quantize(PRICE_QUANTUM)
        if len(recent) == 5
        else None
    )
    session_high = max(
        [event.high_price, *(bar.high_price for bar in bars)]
    ).quantize(PRICE_QUANTUM)
    drawdown = ((event.last_price / session_high) - 1) * Decimal(100)
    spread = None
    if event.best_bid_price is not None and event.best_ask_price is not None:
        midpoint = (event.best_bid_price + event.best_ask_price) / 2
        if midpoint > 0:
            spread = (
                (event.best_ask_price - event.best_bid_price) / midpoint * Decimal(100)
            ).quantize(PCT_QUANTUM)
    indicator = IndicatorSnapshot(
        market_snapshot_id=snapshot.id,
        market=event.market,
        symbol=event.symbol,
        calculator_version=CALCULATOR_VERSION,
        vwap=vwap,
        sma5=sma5,
        session_high=session_high,
        drawdown_from_high_pct=drawdown.quantize(PCT_QUANTUM),
        spread_pct=spread,
        minute_bar_count=len(bars),
        input_start_at=day_start,
        input_end_at=event_at,
    )
    db.add(indicator)
    db.flush()
    return indicator
