from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AuditLog, User, WatchlistItem

MAX_WATCHLIST_ITEMS = 3


class WatchlistError(Exception):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def list_items(db: Session, user_id: str) -> list[WatchlistItem]:
    return list(
        db.scalars(
            select(WatchlistItem)
            .where(WatchlistItem.user_id == user_id)
            .order_by(WatchlistItem.created_at, WatchlistItem.id)
        )
    )


def active_kiwoom_symbols(db: Session) -> tuple[str, ...]:
    return tuple(
        db.scalars(
            select(WatchlistItem.symbol)
            .where(WatchlistItem.market == "KRX")
            .distinct()
            .order_by(WatchlistItem.symbol)
        )
    )


def create_item(
    db: Session,
    *,
    user: User,
    symbol: str,
    market: str,
    correlation_id: str,
    request_ip: str,
    user_agent: str,
) -> WatchlistItem:
    if market != "KRX":
        raise WatchlistError("WATCHLIST_MARKET_UNSUPPORTED_IN_MOCK", 422)
    if len(symbol) != 6 or not symbol.isdigit():
        raise WatchlistError("WATCHLIST_SYMBOL_INVALID", 422)

    db.scalar(select(User).where(User.id == user.id).with_for_update())
    duplicate = db.scalar(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user.id,
            WatchlistItem.market == market,
            WatchlistItem.symbol == symbol,
        )
    )
    if duplicate is not None:
        raise WatchlistError("WATCHLIST_ITEM_EXISTS", 409)
    count = db.scalar(
        select(func.count()).select_from(WatchlistItem).where(WatchlistItem.user_id == user.id)
    )
    if int(count or 0) >= MAX_WATCHLIST_ITEMS:
        raise WatchlistError("WATCHLIST_LIMIT_REACHED", 422)

    item = WatchlistItem(user_id=user.id, symbol=symbol, market=market)
    db.add(item)
    db.flush()
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="WATCHLIST_ITEM_CREATED",
            target=item.id,
            result="SUCCESS",
            request_ip=request_ip,
            user_agent=user_agent,
            correlation_id=correlation_id,
            metadata_json=json.dumps({"market": market, "symbol": symbol}, separators=(",", ":")),
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise WatchlistError("WATCHLIST_ITEM_EXISTS", 409) from exc
    db.refresh(item)
    return item


def delete_item(
    db: Session,
    *,
    user: User,
    item_id: str,
    correlation_id: str,
    request_ip: str,
    user_agent: str,
) -> None:
    item = db.scalar(
        select(WatchlistItem).where(
            WatchlistItem.id == item_id, WatchlistItem.user_id == user.id
        )
    )
    if item is None:
        raise WatchlistError("WATCHLIST_ITEM_NOT_FOUND", 404)
    metadata = {"market": item.market, "symbol": item.symbol}
    db.delete(item)
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="WATCHLIST_ITEM_DELETED",
            target=item_id,
            result="SUCCESS",
            request_ip=request_ip,
            user_agent=user_agent,
            correlation_id=correlation_id,
            metadata_json=json.dumps(metadata, separators=(",", ":")),
        )
    )
    db.commit()
