from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RiskEvent

RISK_EVENT_SCOPE_FIXED_STOP = "FIXED_STOP"
RISK_EVENT_SCOPE_DAILY_LOSS = "DAILY_LOSS"
RISK_EVENT_SCOPE_EXPOSURE = "EXPOSURE"
RISK_EVENT_SCOPE_SPREAD = "SPREAD"
RISK_EVENT_SCOPE_CONNECTION = "CONNECTION"


def _canonical(input_record: dict[str, object]) -> str:
    return json.dumps(
        input_record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def create_risk_event(
    db: Session,
    *,
    scope: str,
    rule_code: str,
    severity: str,
    account_alias: str,
    input_record: dict[str, object],
    correlation_id: str,
    symbol: str | None = None,
    input_snapshot_id: str | None = None,
    now: datetime | None = None,
) -> RiskEvent:
    """Append one immutable ``ACTIVE`` risk event row.

    Risk events are a generic ledger; callers (fixed stop trigger today, daily
    loss / spread / connection risk later) are differentiated by ``scope``.
    Secrets and full account numbers must never be placed in ``input_record``.
    """
    evaluated_at = now or datetime.now(UTC)
    event = RiskEvent(
        scope=scope,
        rule_code=rule_code,
        severity=severity,
        state="ACTIVE",
        account_alias=account_alias,
        symbol=symbol,
        input_snapshot_id=input_snapshot_id,
        input_json=_canonical(input_record),
        correlation_id=correlation_id,
        created_at=evaluated_at,
        updated_at=evaluated_at,
    )
    db.add(event)
    db.flush()
    return event


def resolve_risk_event(
    db: Session,
    risk_event_id: str,
    *,
    resolution: str,
    now: datetime | None = None,
) -> RiskEvent | None:
    event = db.get(RiskEvent, risk_event_id)
    if event is None or event.state == "RESOLVED":
        return event
    event.state = "RESOLVED"
    event.resolution = resolution
    event.resolved_at = now or datetime.now(UTC)
    event.updated_at = now or datetime.now(UTC)
    db.flush()
    return event


def active_risk_events(
    db: Session, *, scope: str | None = None, account_alias: str | None = None
) -> list[RiskEvent]:
    stmt = select(RiskEvent).where(RiskEvent.state == "ACTIVE")
    if scope is not None:
        stmt = stmt.where(RiskEvent.scope == scope)
    if account_alias is not None:
        stmt = stmt.where(RiskEvent.account_alias == account_alias)
    return list(db.scalars(stmt.order_by(RiskEvent.created_at.desc()).limit(200)))
