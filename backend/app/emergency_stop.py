from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, EmergencyStop, User

ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"


class EmergencyStopError(Exception):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def current_emergency_stop(
    db: Session, account_alias: str = ACCOUNT_ALIAS
) -> EmergencyStop | None:
    return db.scalar(
        select(EmergencyStop).where(EmergencyStop.account_alias == account_alias)
    )


def active_pause_entry(
    db: Session, account_alias: str = ACCOUNT_ALIAS
) -> EmergencyStop | None:
    return db.scalar(
        select(EmergencyStop).where(
            EmergencyStop.account_alias == account_alias,
            EmergencyStop.state == "ACTIVE",
            EmergencyStop.level == "PAUSE_ENTRY",
        )
    )


def _validate(value: str, *, minimum: int, maximum: int, code: str) -> str:
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        raise EmergencyStopError(code, 422)
    return normalized


def activate_pause_entry(
    db: Session,
    *,
    user: User,
    reason: str,
    idempotency_key: str,
    correlation_id: str,
    request_ip: str,
    user_agent: str,
    now: datetime | None = None,
) -> EmergencyStop:
    normalized_reason = _validate(
        reason, minimum=5, maximum=500, code="EMERGENCY_STOP_REASON_INVALID"
    )
    normalized_key = _validate(
        idempotency_key,
        minimum=16,
        maximum=128,
        code="IDEMPOTENCY_KEY_INVALID",
    )
    current = db.scalar(
        select(EmergencyStop)
        .where(EmergencyStop.account_alias == ACCOUNT_ALIAS)
        .with_for_update()
    )
    if current is not None and current.state == "ACTIVE":
        if current.activation_key == normalized_key:
            return current
        raise EmergencyStopError("EMERGENCY_STOP_ALREADY_ACTIVE", 409)

    activated_at = now or datetime.now(UTC)
    if current is None:
        current = EmergencyStop(
            account_alias=ACCOUNT_ALIAS,
            level="PAUSE_ENTRY",
            state="ACTIVE",
            reason=normalized_reason,
            activation_key=normalized_key,
            activated_by=user.id,
            activated_at=activated_at,
        )
        db.add(current)
        db.flush()
    else:
        current.state = "ACTIVE"
        current.reason = normalized_reason
        current.activation_key = normalized_key
        current.release_key = None
        current.activated_by = user.id
        current.activated_at = activated_at
        current.released_by = None
        current.released_at = None
        current.version += 1

    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="PAUSE_ENTRY_ACTIVATED",
            target=current.id,
            result="SUCCESS",
            request_ip=request_ip,
            user_agent=user_agent,
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {"account_alias": ACCOUNT_ALIAS, "level": "PAUSE_ENTRY"},
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    db.refresh(current)
    return current


def release_pause_entry(
    db: Session,
    *,
    user: User,
    reason: str,
    idempotency_key: str,
    correlation_id: str,
    request_ip: str,
    user_agent: str,
    now: datetime | None = None,
) -> EmergencyStop:
    normalized_reason = _validate(
        reason, minimum=5, maximum=500, code="EMERGENCY_STOP_REASON_INVALID"
    )
    normalized_key = _validate(
        idempotency_key,
        minimum=16,
        maximum=128,
        code="IDEMPOTENCY_KEY_INVALID",
    )
    current = db.scalar(
        select(EmergencyStop)
        .where(EmergencyStop.account_alias == ACCOUNT_ALIAS)
        .with_for_update()
    )
    if current is None:
        raise EmergencyStopError("EMERGENCY_STOP_NOT_ACTIVE", 409)
    if current.state == "RELEASED":
        if current.release_key == normalized_key:
            return current
        raise EmergencyStopError("EMERGENCY_STOP_NOT_ACTIVE", 409)

    current.state = "RELEASED"
    current.reason = normalized_reason
    current.release_key = normalized_key
    current.released_by = user.id
    current.released_at = now or datetime.now(UTC)
    current.version += 1
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="PAUSE_ENTRY_RELEASED",
            target=current.id,
            result="SUCCESS",
            request_ip=request_ip,
            user_agent=user_agent,
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {"account_alias": ACCOUNT_ALIAS, "reason": normalized_reason},
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    db.refresh(current)
    return current
