from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import ResourceNotFoundError
from app.models import MarketSnapshot, MarketStreamState
from app.schemas import QuoteResponse
from app.watch import quote_age_seconds

router = APIRouter(prefix="/quotes", tags=["quotes"])


@router.get("/{symbol}", response_model=QuoteResponse)
def get_quote(
    symbol: str,
    request: Request,
    market: Literal["KRX", "NXT"] = Query(default="KRX"),
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> QuoteResponse:
    state = db.get(MarketStreamState, (market, symbol))
    if state is None or state.current_snapshot_id is None:
        raise ResourceNotFoundError("QUOTE_NOT_FOUND", "시세 snapshot을 찾을 수 없습니다.")
    snapshot = db.get(MarketSnapshot, state.current_snapshot_id)
    if snapshot is None:  # pragma: no cover - foreign key invariant
        raise ResourceNotFoundError("QUOTE_NOT_FOUND", "시세 snapshot을 찾을 수 없습니다.")
    age = quote_age_seconds(snapshot)
    return QuoteResponse(
        request_id=request.state.request_id,
        symbol=snapshot.symbol,
        market=snapshot.market,
        source=snapshot.source,
        sequence_or_hash=snapshot.sequence_or_hash,
        source_sequence=snapshot.source_sequence,
        last_price=snapshot.last_price,
        open_price=snapshot.open_price,
        high_price=snapshot.high_price,
        low_price=snapshot.low_price,
        cumulative_volume=snapshot.cumulative_volume,
        best_bid_price=snapshot.best_bid_price,
        best_bid_quantity=snapshot.best_bid_quantity,
        best_ask_price=snapshot.best_ask_price,
        best_ask_quantity=snapshot.best_ask_quantity,
        trading_status=snapshot.trading_status,
        quality=state.quality,
        age_seconds=age,
        is_fresh=state.quality == "NORMAL" and age <= settings.quote_stale_seconds,
        event_at=snapshot.event_at,
        received_at=snapshot.received_at,
        stream_version=state.version,
    )
