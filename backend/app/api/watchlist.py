from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.config import Settings, get_settings
from app.db import get_db
from app.models import IndicatorSnapshot, MarketSnapshot, MarketStreamState, WatchlistItem
from app.schemas import (
    WatchlistCreateRequest,
    WatchlistDeleteResponse,
    WatchlistIndicatorSummary,
    WatchlistItemResponse,
    WatchlistQuoteSummary,
    WatchlistResponse,
)
from app.watch import quote_age_seconds
from app.watchlist import MAX_WATCHLIST_ITEMS, create_item, delete_item, list_items

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _response_item(
    db: Session, item: WatchlistItem, stale_seconds: int
) -> WatchlistItemResponse:
    state = db.get(MarketStreamState, (item.market, item.symbol))
    snapshot = db.get(MarketSnapshot, state.current_snapshot_id) if state and state.current_snapshot_id else None
    quote = None
    indicators = None
    data_status = "WAITING_FOR_DATA"
    if snapshot is not None:
        age = quote_age_seconds(snapshot)
        fresh = age <= stale_seconds and state is not None and state.quality == "NORMAL"
        data_status = "DEGRADED" if state and state.quality != "NORMAL" else "AVAILABLE" if fresh else "STALE"
        quote = WatchlistQuoteSummary(
            last_price=snapshot.last_price,
            cumulative_volume=snapshot.cumulative_volume,
            quality=state.quality if state else snapshot.quality,
            age_seconds=age,
            is_fresh=fresh,
            received_at=snapshot.received_at,
        )
        indicator = db.scalar(
            select(IndicatorSnapshot).where(
                IndicatorSnapshot.market_snapshot_id == snapshot.id
            )
        )
        if indicator is not None:
            indicators = WatchlistIndicatorSummary(
                calculator_version=indicator.calculator_version,
                vwap=indicator.vwap,
                sma5=indicator.sma5,
                session_high=indicator.session_high,
                drawdown_from_high_pct=indicator.drawdown_from_high_pct,
                spread_pct=indicator.spread_pct,
                minute_bar_count=indicator.minute_bar_count,
                calculated_at=indicator.created_at,
            )
    return WatchlistItemResponse(
        id=item.id,
        symbol=item.symbol,
        market=item.market,
        data_status=data_status,
        quote=quote,
        indicators=indicators,
        created_at=item.created_at,
    )


def _list_response(
    request: Request, db: Session, user_id: str, stale_seconds: int
) -> WatchlistResponse:
    items = list_items(db, user_id)
    return WatchlistResponse(
        request_id=request.state.request_id,
        remaining_slots=MAX_WATCHLIST_ITEMS - len(items),
        items=[_response_item(db, item, stale_seconds) for item in items],
    )


@router.get("", response_model=WatchlistResponse)
def get_watchlist(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WatchlistResponse:
    return _list_response(request, db, context.user.id, settings.quote_stale_seconds)


@router.post("", response_model=WatchlistResponse, status_code=status.HTTP_201_CREATED)
def post_watchlist(
    payload: WatchlistCreateRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WatchlistResponse:
    create_item(
        db,
        user=context.user,
        symbol=payload.symbol,
        market=payload.market,
        correlation_id=request.state.request_id,
        request_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", "unknown"),
    )
    return _list_response(request, db, context.user.id, settings.quote_stale_seconds)


@router.delete("/{item_id}", response_model=WatchlistDeleteResponse)
def remove_watchlist(
    item_id: str,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> WatchlistDeleteResponse:
    delete_item(
        db,
        user=context.user,
        item_id=item_id,
        correlation_id=request.state.request_id,
        request_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", "unknown"),
    )
    return WatchlistDeleteResponse(request_id=request.state.request_id)
