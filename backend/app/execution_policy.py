from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditLog, ConfigurationVersion, User
from app.schemas import ExecutionPolicyPayload

SCOPE = "USER_DEFAULT"
CATEGORY = "EXECUTION_POLICY"

SAFE_DEFAULT_POLICY = ExecutionPolicyPayload(
    buy="MANUAL_APPROVAL",
    partial_sell="MANUAL_APPROVAL",
    full_sell="MANUAL_APPROVAL",
    take_profit="MANUAL_APPROVAL",
    fixed_stop_loss="AUTOMATIC",
    trailing_stop="AUTOMATIC",
    end_of_day_liquidation="AUTOMATIC",
    emergency_exit="AUTOMATIC",
)


class ExecutionPolicyError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _canonical(policy: ExecutionPolicyPayload) -> str:
    return json.dumps(policy.model_dump(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def active_policy(db: Session, user_id: str) -> ConfigurationVersion | None:
    return db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.scope == SCOPE,
            ConfigurationVersion.target_id == user_id,
            ConfigurationVersion.category == CATEGORY,
            ConfigurationVersion.state == "ACTIVE",
        )
    )


def policy_payload(version: ConfigurationVersion | None) -> ExecutionPolicyPayload:
    if version is None:
        return SAFE_DEFAULT_POLICY
    return ExecutionPolicyPayload.model_validate_json(version.payload_json)


def create_draft(
    db: Session,
    *,
    user: User,
    policy: ExecutionPolicyPayload,
    reason: str,
) -> ConfigurationVersion:
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ExecutionPolicyError("CONFIGURATION_REASON_REQUIRED", 400)
    current = active_policy(db, user.id)
    sequence = int(
        db.scalar(
            select(func.max(ConfigurationVersion.sequence)).where(
                ConfigurationVersion.scope == SCOPE,
                ConfigurationVersion.target_id == user.id,
                ConfigurationVersion.category == CATEGORY,
            )
        )
        or 0
    ) + 1
    payload_json = _canonical(policy)
    version = ConfigurationVersion(
        scope=SCOPE,
        target_id=user.id,
        category=CATEGORY,
        sequence=sequence,
        state="DRAFT",
        payload_json=payload_json,
        payload_hash=hashlib.sha256(payload_json.encode()).hexdigest(),
        reason=normalized_reason,
        created_by=user.id,
        base_active_version_id=current.id if current else None,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def validate_draft(db: Session, *, user: User, version_id: str) -> ConfigurationVersion:
    version = db.get(ConfigurationVersion, version_id)
    if version is None or version.target_id != user.id or version.category != CATEGORY:
        raise ExecutionPolicyError("CONFIGURATION_VERSION_NOT_FOUND", 404)
    if version.state == "VALIDATED":
        return version
    if version.state != "DRAFT":
        raise ExecutionPolicyError("CONFIGURATION_STATE_INVALID")
    policy_payload(version)
    version.state = "VALIDATED"
    version.validated_at = datetime.now(UTC)
    db.commit()
    db.refresh(version)
    return version


def activate_version(
    db: Session,
    *,
    user: User,
    version_id: str,
    correlation_id: str,
    request_ip: str,
    user_agent: str,
) -> ConfigurationVersion:
    version = db.scalar(
        select(ConfigurationVersion)
        .where(ConfigurationVersion.id == version_id)
        .with_for_update()
    )
    if version is None or version.target_id != user.id or version.category != CATEGORY:
        raise ExecutionPolicyError("CONFIGURATION_VERSION_NOT_FOUND", 404)
    if version.state == "ACTIVE":
        return version
    if version.state != "VALIDATED":
        raise ExecutionPolicyError("CONFIGURATION_NOT_VALIDATED")
    current = active_policy(db, user.id)
    current_id = current.id if current else None
    if version.base_active_version_id != current_id:
        raise ExecutionPolicyError("CONFIGURATION_VERSION_CONFLICT")
    if current is not None:
        current.state = "SUPERSEDED"
        db.flush()
    version.state = "ACTIVE"
    version.activated_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="EXECUTION_POLICY_ACTIVATED",
            target=version.id,
            result="PASSED",
            request_ip=request_ip,
            user_agent=user_agent,
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {"sequence": version.sequence, "payload_hash": version.payload_hash},
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    db.refresh(version)
    return version


def history(db: Session, user_id: str) -> list[ConfigurationVersion]:
    return list(
        db.scalars(
            select(ConfigurationVersion)
            .where(
                ConfigurationVersion.scope == SCOPE,
                ConfigurationVersion.target_id == user_id,
                ConfigurationVersion.category == CATEGORY,
            )
            .order_by(ConfigurationVersion.sequence.desc())
            .limit(50)
        )
    )
