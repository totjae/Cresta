from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MarketSnapshot, MarketStreamState

PRICE_QUANTUM = Decimal("0.0001")
SUPPORTED_MARKETS = {"KRX", "NXT"}
SUPPORTED_TRADING_STATUSES = {
    "PRE_MARKET",
    "TRADING",
    "VI",
    "HALTED",
    "CLOSING_AUCTION",
    "CLOSED",
    "NO_QUOTES",
}


class WatchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class QuoteEvent:
    symbol: str
    market: str
    source: str
    sequence_or_hash: str
    event_at: datetime
    received_at: datetime
    last_price: Decimal
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    cumulative_volume: int
    trading_status: str
    source_sequence: int | None = None
    best_bid_price: Decimal | None = None
    best_bid_quantity: int | None = None
    best_ask_price: Decimal | None = None
    best_ask_quantity: int | None = None
    recovery_snapshot: bool = False


@dataclass(frozen=True)
class IngestResult:
    snapshot: MarketSnapshot
    outcome: str
    stream_quality: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _price(value: Decimal | None) -> str | None:
    return str(value.quantize(PRICE_QUANTUM)) if value is not None else None


def _payload(event: QuoteEvent) -> dict[str, object]:
    return {
        "best_ask_price": _price(event.best_ask_price),
        "best_ask_quantity": event.best_ask_quantity,
        "best_bid_price": _price(event.best_bid_price),
        "best_bid_quantity": event.best_bid_quantity,
        "cumulative_volume": event.cumulative_volume,
        "event_at": _utc(event.event_at).isoformat(),
        "high_price": _price(event.high_price),
        "last_price": _price(event.last_price),
        "low_price": _price(event.low_price),
        "market": event.market,
        "open_price": _price(event.open_price),
        "received_at": _utc(event.received_at).isoformat(),
        "recovery_snapshot": event.recovery_snapshot,
        "sequence_or_hash": event.sequence_or_hash,
        "source": event.source,
        "source_sequence": event.source_sequence,
        "symbol": event.symbol,
        "trading_status": event.trading_status,
    }


def _payload_hash(event: QuoteEvent) -> str:
    encoded = json.dumps(_payload(event), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate(event: QuoteEvent) -> None:
    if not event.symbol.isdigit() or len(event.symbol) != 6:
        raise WatchError("INVALID_SYMBOL", "국내주식 종목코드는 숫자 6자리여야 합니다.")
    if event.market not in SUPPORTED_MARKETS:
        raise WatchError("UNSUPPORTED_MARKET", "지원하지 않는 시세 시장입니다.")
    if not event.source or len(event.source) > 32:
        raise WatchError("INVALID_SOURCE", "유효한 시세 원본 식별자가 필요합니다.")
    if not event.sequence_or_hash or len(event.sequence_or_hash) > 128:
        raise WatchError("INVALID_SOURCE_KEY", "유효한 시세 이벤트 식별자가 필요합니다.")
    if event.event_at.tzinfo is None or event.received_at.tzinfo is None:
        raise WatchError("TIMEZONE_REQUIRED", "시세 시각은 timezone을 포함해야 합니다.")
    if _utc(event.received_at) < _utc(event.event_at):
        raise WatchError("INVALID_EVENT_TIME", "수신 시각은 이벤트 시각보다 빠를 수 없습니다.")
    if event.source_sequence is not None and event.source_sequence < 0:
        raise WatchError("INVALID_SEQUENCE", "시세 순번은 0 이상이어야 합니다.")
    prices = (event.last_price, event.open_price, event.high_price, event.low_price)
    if any(value <= 0 for value in prices) or event.high_price < event.low_price:
        raise WatchError("INVALID_PRICE", "시세 가격 범위를 확인할 수 없습니다.")
    if event.cumulative_volume < 0:
        raise WatchError("INVALID_VOLUME", "누적 거래량은 0 이상이어야 합니다.")
    if event.trading_status not in SUPPORTED_TRADING_STATUSES:
        raise WatchError("INVALID_TRADING_STATUS", "지원하지 않는 거래 상태입니다.")
    if (event.best_bid_price is None) != (event.best_bid_quantity is None):
        raise WatchError("INVALID_ORDERBOOK", "매수 1호가 가격과 수량은 함께 제공해야 합니다.")
    if (event.best_ask_price is None) != (event.best_ask_quantity is None):
        raise WatchError("INVALID_ORDERBOOK", "매도 1호가 가격과 수량은 함께 제공해야 합니다.")
    if event.best_bid_price is not None and (
        event.best_bid_price <= 0 or event.best_bid_quantity is None or event.best_bid_quantity < 0
    ):
        raise WatchError("INVALID_ORDERBOOK", "매수 1호가를 확인할 수 없습니다.")
    if event.best_ask_price is not None and (
        event.best_ask_price <= 0 or event.best_ask_quantity is None or event.best_ask_quantity < 0
    ):
        raise WatchError("INVALID_ORDERBOOK", "매도 1호가를 확인할 수 없습니다.")


def ingest_quote(db: Session, event: QuoteEvent) -> IngestResult:
    _validate(event)
    payload_hash = _payload_hash(event)
    duplicate = db.scalar(
        select(MarketSnapshot).where(
            MarketSnapshot.source == event.source,
            MarketSnapshot.market == event.market,
            MarketSnapshot.symbol == event.symbol,
            MarketSnapshot.sequence_or_hash == event.sequence_or_hash,
        )
    )
    if duplicate is not None:
        if duplicate.payload_hash != payload_hash:
            raise WatchError("SOURCE_EVENT_CONFLICT", "같은 시세 식별자의 내용이 다릅니다.")
        state = db.get(MarketStreamState, (event.market, event.symbol))
        return IngestResult(duplicate, "DUPLICATE", state.quality if state else duplicate.quality)

    state = db.scalar(
        select(MarketStreamState)
        .where(
            MarketStreamState.market == event.market,
            MarketStreamState.symbol == event.symbol,
        )
        .with_for_update()
    )
    event_at = _utc(event.event_at)
    received_at = _utc(event.received_at)
    late = bool(
        state
        and (
            (state.last_event_at is not None and event_at < _utc(state.last_event_at))
            or (
                event.source_sequence is not None
                and state.last_sequence is not None
                and event.source_sequence <= state.last_sequence
            )
        )
    )
    sequence_gap = bool(
        state
        and event.source_sequence is not None
        and state.last_sequence is not None
        and event.source_sequence > state.last_sequence + 1
    )
    volume_regression = bool(
        state
        and state.cumulative_volume is not None
        and event.cumulative_volume < state.cumulative_volume
    )
    source_changed = bool(state and state.source != event.source and not event.recovery_snapshot)
    gap = bool(state and state.quality == "GAP_DETECTED" and not event.recovery_snapshot)
    gap = gap or sequence_gap or volume_regression or source_changed
    if event.recovery_snapshot:
        late = False
        gap = False
    quality = "LATE" if late else "GAP_DETECTED" if gap else "NORMAL"
    snapshot = MarketSnapshot(
        symbol=event.symbol,
        market=event.market,
        source=event.source,
        sequence_or_hash=event.sequence_or_hash,
        source_sequence=event.source_sequence,
        payload_hash=payload_hash,
        last_price=event.last_price.quantize(PRICE_QUANTUM),
        open_price=event.open_price.quantize(PRICE_QUANTUM),
        high_price=event.high_price.quantize(PRICE_QUANTUM),
        low_price=event.low_price.quantize(PRICE_QUANTUM),
        cumulative_volume=event.cumulative_volume,
        best_bid_price=(
            event.best_bid_price.quantize(PRICE_QUANTUM)
            if event.best_bid_price is not None
            else None
        ),
        best_bid_quantity=event.best_bid_quantity,
        best_ask_price=(
            event.best_ask_price.quantize(PRICE_QUANTUM)
            if event.best_ask_price is not None
            else None
        ),
        best_ask_quantity=event.best_ask_quantity,
        trading_status=event.trading_status,
        quality=quality,
        recovery_snapshot=event.recovery_snapshot,
        event_at=event_at,
        received_at=received_at,
    )
    db.add(snapshot)
    db.flush()

    if state is None:
        state = MarketStreamState(
            market=event.market,
            symbol=event.symbol,
            source=event.source,
            quality="NORMAL",
            version=0,
        )
        db.add(state)
    if quality == "GAP_DETECTED":
        state.quality = "GAP_DETECTED"
        state.version += 1
    elif quality == "NORMAL":
        state.source = event.source
        state.current_snapshot_id = snapshot.id
        state.last_sequence = event.source_sequence
        state.last_event_at = event_at
        state.last_received_at = received_at
        state.cumulative_volume = event.cumulative_volume
        state.quality = "NORMAL"
        state.version += 1
    db.commit()
    db.refresh(snapshot)
    db.refresh(state)
    outcome = "LATE" if late else "GAP_DETECTED" if gap else "RECOVERED" if event.recovery_snapshot else "APPLIED"
    return IngestResult(snapshot, outcome, state.quality)


def quote_age_seconds(snapshot: MarketSnapshot, now: datetime | None = None) -> Decimal:
    reference = _utc(now or datetime.now(UTC))
    age = max(Decimal(0), Decimal(str((reference - _utc(snapshot.received_at)).total_seconds())))
    return age.quantize(Decimal("0.001"))
