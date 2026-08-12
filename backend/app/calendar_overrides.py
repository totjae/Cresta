from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.market_calendar import TradingDayDecision, evaluate_krx_trading_day
from app.models import AuditLog, MarketCalendarOverride, User

KST = ZoneInfo("Asia/Seoul")


class CalendarOverrideError(Exception):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def list_calendar_overrides(
    db: Session, *, limit: int = 100
) -> list[MarketCalendarOverride]:
    return list(
        db.scalars(
            select(MarketCalendarOverride)
            .order_by(
                MarketCalendarOverride.market_date.desc(),
                MarketCalendarOverride.created_at.desc(),
            )
            .limit(limit)
        )
    )


def active_calendar_override(
    db: Session, market_date: date
) -> MarketCalendarOverride | None:
    return db.scalar(
        select(MarketCalendarOverride).where(
            MarketCalendarOverride.market_date == market_date,
            MarketCalendarOverride.state == "ACTIVE",
        )
    )


def resolve_trading_day(
    db: Session, market_date: date
) -> tuple[TradingDayDecision, MarketCalendarOverride | None]:
    override = active_calendar_override(db, market_date)
    if override is not None:
        return (
            TradingDayDecision(
                status="CLOSED",
                reason="OPERATIONAL_CLOSURE",
                override_id=override.id,
            ),
            override,
        )
    return evaluate_krx_trading_day(market_date), None


def create_calendar_override(
    db: Session,
    *,
    user: User,
    market_date: date,
    reason: str,
    source_reference: str,
    correlation_id: str,
    request_ip: str,
    user_agent: str,
    now: datetime | None = None,
) -> MarketCalendarOverride:
    current = (now or datetime.now(UTC)).astimezone(KST).date()
    if market_date < current or market_date > current + timedelta(days=730):
        raise CalendarOverrideError("CALENDAR_OVERRIDE_DATE_OUT_OF_RANGE", 422)
    normalized_reason = reason.strip()
    normalized_source = source_reference.strip()
    if not 5 <= len(normalized_reason) <= 200:
        raise CalendarOverrideError("CALENDAR_OVERRIDE_REASON_INVALID", 422)
    if not 3 <= len(normalized_source) <= 200:
        raise CalendarOverrideError("CALENDAR_OVERRIDE_SOURCE_INVALID", 422)
    if active_calendar_override(db, market_date) is not None:
        raise CalendarOverrideError("CALENDAR_OVERRIDE_ALREADY_ACTIVE", 409)

    item = MarketCalendarOverride(
        market_date=market_date,
        override_type="OPERATIONAL_CLOSURE",
        state="ACTIVE",
        reason=normalized_reason,
        source_reference=normalized_source,
        created_by=user.id,
    )
    db.add(item)
    db.flush()
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="MARKET_CALENDAR_OVERRIDE_CREATED",
            target=item.id,
            result="SUCCESS",
            request_ip=request_ip,
            user_agent=user_agent,
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {"market_date": market_date.isoformat(), "override_type": item.override_type},
                separators=(",", ":"),
            ),
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise CalendarOverrideError("CALENDAR_OVERRIDE_ALREADY_ACTIVE", 409) from exc
    db.refresh(item)
    return item


def revoke_calendar_override(
    db: Session,
    *,
    user: User,
    override_id: str,
    correlation_id: str,
    request_ip: str,
    user_agent: str,
    now: datetime | None = None,
) -> MarketCalendarOverride:
    item = db.scalar(
        select(MarketCalendarOverride)
        .where(
            MarketCalendarOverride.id == override_id,
            MarketCalendarOverride.state == "ACTIVE",
        )
        .with_for_update()
    )
    if item is None:
        raise CalendarOverrideError("CALENDAR_OVERRIDE_NOT_FOUND", 404)
    item.state = "REVOKED"
    item.revoked_by = user.id
    item.revoked_at = now or datetime.now(UTC)
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="MARKET_CALENDAR_OVERRIDE_REVOKED",
            target=item.id,
            result="SUCCESS",
            request_ip=request_ip,
            user_agent=user_agent,
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {"market_date": item.market_date.isoformat()}, separators=(",", ":")
            ),
        )
    )
    db.commit()
    db.refresh(item)
    return item
