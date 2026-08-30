from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.execution_authority import ExecutionStage
from app.models import AuditLog, ConfigurationVersion, User

EXECUTION_STAGE_CATEGORY = "V7_ENTRY_EXECUTION_STAGE"
EXECUTION_STAGE_SCOPE = "SYSTEM"
EXECUTION_STAGE_TARGET = "MOCK"
EXECUTION_STAGE_SCHEMA = "execution-stage-control-v1"
EXECUTION_STAGE_VALIDATION_POLICY = "execution-stage-validation-policy-v1"
HASH_PATTERN = r"^[0-9a-f]{64}$"

APPROVAL_ONLY_TEST_IDS = tuple(
    f"T-V2-EXE-AUTH-{number:03d}"
    for number in (1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 15, 16)
)
MOCK_AUTOMATIC_TEST_IDS = tuple(
    f"T-V2-EXE-AUTH-{number:03d}" for number in range(1, 17)
)
REQUIRED_TEST_IDS = {
    ExecutionStage.SHADOW: (),
    ExecutionStage.APPROVAL_ONLY: APPROVAL_ONLY_TEST_IDS,
    ExecutionStage.MOCK_AUTOMATIC: MOCK_AUTOMATIC_TEST_IDS,
}


class StageStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(UTC)


class StageSafetyEvidence(StageStrictModel):
    test_id: str = Field(min_length=1)
    requirement_ids: list[str]
    result: Literal["PASSED"]
    code_revision: str = Field(min_length=1)
    test_plan_version: str = Field(min_length=1)
    executed_at: datetime
    valid_until: datetime | None
    freshness_contract: str | None = Field(default=None, min_length=1)
    evidence_ref: str = Field(min_length=1)
    evidence_hash: str = Field(pattern=HASH_PATTERN)

    @field_validator("requirement_ids", mode="before")
    @classmethod
    def normalize_requirements(cls, value: object) -> object:
        return sorted(value) if isinstance(value, list) else value

    @field_validator("executed_at", "valid_until")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_evidence(self) -> StageSafetyEvidence:
        if (
            not self.requirement_ids
            or len(self.requirement_ids) != len(set(self.requirement_ids))
            or any(not item for item in self.requirement_ids)
        ):
            raise ValueError("requirement IDs must be non-empty and unique")
        if (self.valid_until is None) == (self.freshness_contract is None):
            raise ValueError("exactly one evidence validity mechanism is required")
        if self.valid_until is not None and self.executed_at >= self.valid_until:
            raise ValueError("evidence valid_until must follow executed_at")
        return self


class ExecutionStagePayload(StageStrictModel):
    schema_version: Literal["execution-stage-control-v1"]
    stage: ExecutionStage
    target: Literal["MOCK"]
    validation_policy_version: Literal["execution-stage-validation-policy-v1"]
    safety_evidence: list[StageSafetyEvidence]
    validated_at: datetime
    valid_until: datetime

    @field_validator("safety_evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, value: object) -> object:
        if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
            return sorted(value, key=lambda item: str(item.get("test_id", "")))
        return value

    @field_validator("validated_at", "valid_until")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_payload(self) -> ExecutionStagePayload:
        if self.validated_at >= self.valid_until:
            raise ValueError("stage valid_until must follow validated_at")
        if len({item.test_id for item in self.safety_evidence}) != len(self.safety_evidence):
            raise ValueError("safety evidence test IDs must be unique")
        expected = REQUIRED_TEST_IDS[self.stage]
        if tuple(item.test_id for item in self.safety_evidence) != expected:
            raise ValueError("stage safety evidence does not match the required acceptance set")
        return self


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump())
    if isinstance(value, datetime):
        normalized = _utc(value)
        return normalized.isoformat(
            timespec="seconds" if normalized.microsecond == 0 else "microseconds"
        )
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def canonical_stage_json(value: BaseModel | Mapping[str, Any]) -> str:
    return json.dumps(
        _canonical_value(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def stage_payload_hash(value: ExecutionStagePayload | Mapping[str, Any] | str) -> str:
    if isinstance(value, str):
        material = value.encode("utf-8")
    else:
        parsed = (
            value
            if isinstance(value, ExecutionStagePayload)
            else ExecutionStagePayload.model_validate(value)
        )
        material = canonical_stage_json(parsed).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class ExecutionStageValidationPolicy:
    freshness_contracts: Mapping[str, timedelta] | None = None
    code_revision: str | None = None
    test_plan_version: str | None = None


class ExecutionStageError(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


EvidenceLoader = Callable[[str], bytes]


def validate_stage_payload(
    value: ExecutionStagePayload | Mapping[str, Any] | str,
    *,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    policy: ExecutionStageValidationPolicy | None = None,
) -> ExecutionStagePayload:
    current = _utc(now)
    try:
        if isinstance(value, ExecutionStagePayload):
            payload = value
        elif isinstance(value, str):
            payload = ExecutionStagePayload.model_validate_json(value)
        else:
            payload = ExecutionStagePayload.model_validate(value)
    except (TypeError, ValueError) as exc:
        raise ExecutionStageError("EXECUTION_STAGE_INVALID") from exc
    if current >= payload.valid_until or payload.validated_at > current:
        raise ExecutionStageError("EXECUTION_STAGE_EXPIRED")
    validation_policy = policy or ExecutionStageValidationPolicy()
    freshness = validation_policy.freshness_contracts or {}
    for evidence in payload.safety_evidence:
        if evidence.executed_at > current:
            raise ExecutionStageError("EXECUTION_STAGE_EVIDENCE_INVALID")
        if validation_policy.code_revision and evidence.code_revision != validation_policy.code_revision:
            raise ExecutionStageError("EXECUTION_STAGE_EVIDENCE_INVALID")
        if (
            validation_policy.test_plan_version
            and evidence.test_plan_version != validation_policy.test_plan_version
        ):
            raise ExecutionStageError("EXECUTION_STAGE_EVIDENCE_INVALID")
        if evidence.valid_until is not None:
            if current >= evidence.valid_until:
                raise ExecutionStageError("EXECUTION_STAGE_EVIDENCE_STALE")
        else:
            ttl = freshness.get(str(evidence.freshness_contract))
            if ttl is None or current >= evidence.executed_at + ttl:
                raise ExecutionStageError("EXECUTION_STAGE_EVIDENCE_STALE")
        if evidence_loader is None:
            raise ExecutionStageError("EXECUTION_STAGE_EVIDENCE_INVALID")
        try:
            actual_hash = hashlib.sha256(evidence_loader(evidence.evidence_ref)).hexdigest()
        except Exception as exc:
            raise ExecutionStageError("EXECUTION_STAGE_EVIDENCE_INVALID") from exc
        if actual_hash != evidence.evidence_hash:
            raise ExecutionStageError("EXECUTION_STAGE_EVIDENCE_INVALID")
    return payload


class StageResolutionStatus(StrEnum):
    PASS = "PASS"
    ABSENT = "ABSENT"
    INVALID = "INVALID"
    AMBIGUOUS = "AMBIGUOUS"
    EXPIRED = "EXPIRED"
    DB_RETRYABLE_FAILURE = "DB_RETRYABLE_FAILURE"


@dataclass(frozen=True)
class StageResolution:
    status: StageResolutionStatus
    version: ConfigurationVersion | None = None
    payload: ExecutionStagePayload | None = None


def resolve_current_execution_stage(
    db: Session,
    *,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    policy: ExecutionStageValidationPolicy | None = None,
) -> StageResolution:
    try:
        versions = list(
            db.scalars(
                select(ConfigurationVersion)
                .where(
                    ConfigurationVersion.scope == EXECUTION_STAGE_SCOPE,
                    ConfigurationVersion.target_id == EXECUTION_STAGE_TARGET,
                    ConfigurationVersion.category == EXECUTION_STAGE_CATEGORY,
                    ConfigurationVersion.state == "ACTIVE",
                )
                .order_by(ConfigurationVersion.sequence.desc())
                .limit(2)
            )
        )
    except SQLAlchemyError:
        return StageResolution(StageResolutionStatus.DB_RETRYABLE_FAILURE)
    if not versions:
        return StageResolution(StageResolutionStatus.ABSENT)
    if len(versions) != 1:
        return StageResolution(StageResolutionStatus.AMBIGUOUS)
    version = versions[0]
    try:
        payload = validate_stage_payload(
            version.payload_json, now=now, evidence_loader=evidence_loader, policy=policy
        )
        if (
            canonical_stage_json(payload) != version.payload_json
            or stage_payload_hash(payload) != version.payload_hash
        ):
            return StageResolution(StageResolutionStatus.INVALID, version=version)
    except ExecutionStageError as exc:
        status = (
            StageResolutionStatus.EXPIRED
            if exc.code == "EXECUTION_STAGE_EXPIRED"
            else StageResolutionStatus.INVALID
        )
        return StageResolution(status, version=version)
    return StageResolution(StageResolutionStatus.PASS, version=version, payload=payload)


def _normalize_reason(reason: str) -> str:
    normalized = reason.strip()
    if not normalized:
        raise ExecutionStageError("CONFIGURATION_REASON_REQUIRED", 400)
    return normalized


def _stage_version(db: Session, version_id: str, *, lock: bool = False) -> ConfigurationVersion:
    statement = select(ConfigurationVersion).where(ConfigurationVersion.id == version_id)
    if lock:
        statement = statement.with_for_update()
    version = db.scalar(statement)
    if (
        version is None
        or version.scope != EXECUTION_STAGE_SCOPE
        or version.target_id != EXECUTION_STAGE_TARGET
        or version.category != EXECUTION_STAGE_CATEGORY
    ):
        raise ExecutionStageError("CONFIGURATION_VERSION_NOT_FOUND", 404)
    return version


def create_execution_stage_draft(
    db: Session,
    *,
    user: User,
    payload: ExecutionStagePayload | Mapping[str, Any],
    reason: str,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    policy: ExecutionStageValidationPolicy | None = None,
) -> ConfigurationVersion:
    parsed = validate_stage_payload(
        payload, now=now, evidence_loader=evidence_loader, policy=policy
    )
    payload_json = canonical_stage_json(parsed)
    current = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.scope == EXECUTION_STAGE_SCOPE,
            ConfigurationVersion.target_id == EXECUTION_STAGE_TARGET,
            ConfigurationVersion.category == EXECUTION_STAGE_CATEGORY,
            ConfigurationVersion.state == "ACTIVE",
        )
    )
    sequence = int(
        db.scalar(
            select(func.max(ConfigurationVersion.sequence)).where(
                ConfigurationVersion.scope == EXECUTION_STAGE_SCOPE,
                ConfigurationVersion.target_id == EXECUTION_STAGE_TARGET,
                ConfigurationVersion.category == EXECUTION_STAGE_CATEGORY,
            )
        )
        or 0
    ) + 1
    version = ConfigurationVersion(
        scope=EXECUTION_STAGE_SCOPE,
        target_id=EXECUTION_STAGE_TARGET,
        category=EXECUTION_STAGE_CATEGORY,
        sequence=sequence,
        state="DRAFT",
        payload_json=payload_json,
        payload_hash=stage_payload_hash(payload_json),
        reason=_normalize_reason(reason),
        created_by=user.id,
        base_active_version_id=current.id if current else None,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def validate_execution_stage_draft(
    db: Session,
    *,
    version_id: str,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    policy: ExecutionStageValidationPolicy | None = None,
) -> ConfigurationVersion:
    version = _stage_version(db, version_id)
    if version.state == "VALIDATED":
        return version
    if version.state != "DRAFT":
        raise ExecutionStageError("CONFIGURATION_STATE_INVALID", 409)
    payload = validate_stage_payload(
        version.payload_json, now=now, evidence_loader=evidence_loader, policy=policy
    )
    if canonical_stage_json(payload) != version.payload_json or stage_payload_hash(
        payload
    ) != version.payload_hash:
        raise ExecutionStageError("EXECUTION_STAGE_INVALID")
    version.state = "VALIDATED"
    version.validated_at = _utc(now)
    db.commit()
    db.refresh(version)
    return version


def activate_execution_stage(
    db: Session,
    *,
    user: User,
    version_id: str,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    correlation_id: str,
    request_ip: str,
    user_agent: str,
    policy: ExecutionStageValidationPolicy | None = None,
) -> ConfigurationVersion:
    version = _stage_version(db, version_id, lock=True)
    if version.state == "ACTIVE":
        return version
    if version.state != "VALIDATED":
        raise ExecutionStageError("CONFIGURATION_NOT_VALIDATED", 409)
    payload = validate_stage_payload(
        version.payload_json, now=now, evidence_loader=evidence_loader, policy=policy
    )
    if canonical_stage_json(payload) != version.payload_json or stage_payload_hash(
        payload
    ) != version.payload_hash:
        raise ExecutionStageError("EXECUTION_STAGE_INVALID")
    current = db.scalar(
        select(ConfigurationVersion)
        .where(
            ConfigurationVersion.scope == EXECUTION_STAGE_SCOPE,
            ConfigurationVersion.target_id == EXECUTION_STAGE_TARGET,
            ConfigurationVersion.category == EXECUTION_STAGE_CATEGORY,
            ConfigurationVersion.state == "ACTIVE",
        )
        .with_for_update()
    )
    if version.base_active_version_id != (current.id if current else None):
        raise ExecutionStageError("CONFIGURATION_VERSION_CONFLICT", 409)
    if current is not None:
        current.state = "SUPERSEDED"
        db.flush()
    version.state = "ACTIVE"
    version.activated_at = _utc(now)
    db.add(
        AuditLog(
            actor_type="SYSTEM",
            actor_id=user.id,
            action="V7_ENTRY_EXECUTION_STAGE_ACTIVATED",
            target=version.id,
            result="PASSED",
            request_ip=request_ip,
            user_agent=user_agent,
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {"sequence": version.sequence, "payload_hash": version.payload_hash},
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    )
    db.commit()
    db.refresh(version)
    return version


def execution_stage_history(db: Session) -> list[ConfigurationVersion]:
    return list(
        db.scalars(
            select(ConfigurationVersion)
            .where(
                ConfigurationVersion.scope == EXECUTION_STAGE_SCOPE,
                ConfigurationVersion.target_id == EXECUTION_STAGE_TARGET,
                ConfigurationVersion.category == EXECUTION_STAGE_CATEGORY,
            )
            .order_by(ConfigurationVersion.sequence.desc())
            .limit(50)
        )
    )
