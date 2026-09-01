from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.activation_evidence import (
    EXACT_REVISION_FRESHNESS,
    ActivationEvidenceError,
    ActivationEvidenceStoreError,
    EvidenceReference,
    EvidenceStoreFailureCategory,
    parse_canonical_artifact,
)
from app.models import (
    AuditLog,
    ConfigurationVersion,
    LlmModelProfile,
    LlmPromptProfile,
    LlmRoleRoute,
    User,
)

ACTIVATION_CATEGORY = "V7_ENTRY_ACTIVATION"
ACTIVATION_SCOPE = "SYSTEM"
ACTIVATION_TARGET = "MOCK"
HASH_PATTERN = r"^[0-9a-f]{64}$"
UUID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
POLICY_ORDER = (
    "V7_ENTRY_POLICY_CONSERVATIVE",
    "V7_ENTRY_POLICY_BALANCED",
    "V7_ENTRY_POLICY_AGGRESSIVE",
)
POLICY_AGENT_TYPES = {
    "V7_ENTRY_POLICY_CONSERVATIVE": "CONSERVATIVE",
    "V7_ENTRY_POLICY_BALANCED": "BALANCED",
    "V7_ENTRY_POLICY_AGGRESSIVE": "AGGRESSIVE",
}
ROUTE_ORDER = (
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
    "CONSERVATIVE_DECISION",
    "BALANCED_DECISION",
    "AGGRESSIVE_DECISION",
)
DECISION_ROLES = frozenset(ROUTE_ORDER[-3:])


def _expanded(prefix: str, start: int, end: int) -> tuple[str, ...]:
    return tuple(f"{prefix}{number:03d}" for number in range(start, end + 1))


REQUIRED_ACTIVATION_TEST_IDS = tuple(
    sorted(
        {
            *_expanded("T-V2-AI-", 1, 3),
            "T-V2-AI-005",
            *_expanded("T-V2-AI-", 7, 8),
            *_expanded("T-V2-AI-", 10, 13),
            "T-V2-AI-016",
            "T-V2-MAO-002",
            *_expanded("T-V2-MAO-", 4, 6),
            "T-V2-ARB-001",
            *_expanded("T-V2-ARB-", 2, 15),
            "T-V2-EXE-001",
            *_expanded("T-V2-EXE-", 2, 12),
            *_expanded("T-V2-ACT-", 2, 12),
            *_expanded("T-V2-DB-CTX-", 1, 6),
            *_expanded("T-V2-DB-POL-", 1, 2),
            *_expanded("T-V2-DB-ROLE-", 1, 2),
            *_expanded("T-V2-DB-FIN-", 1, 13),
            *_expanded("T-V2-FIN-API-", 1, 4),
            *_expanded("T-V2-FIN-LIFE-", 1, 6),
            *_expanded("T-V2-DB-GATE-", 1, 4),
            *_expanded("T-V2-DB-MIG-", 1, 10),
            *_expanded("T-V2-UPSTREAM-", 1, 10),
            *_expanded("T-V2-INPUT-V2-", 1, 4),
            *_expanded("T-V2-SCOUT-HASH-", 1, 2),
            *_expanded("T-V2-EVIDENCE-V7-", 1, 2),
        }
    )
)


class ActivationStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use UTC")
    return value.astimezone(UTC)


class PolicyProfileSnapshot(ActivationStrictModel):
    configuration_version_id: str = Field(pattern=UUID_PATTERN)
    category: Literal[
        "V7_ENTRY_POLICY_CONSERVATIVE",
        "V7_ENTRY_POLICY_BALANCED",
        "V7_ENTRY_POLICY_AGGRESSIVE",
    ]
    sequence: Annotated[int, Field(strict=True, ge=1)]
    agent_type: Literal["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]
    payload_hash: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_identity(self) -> PolicyProfileSnapshot:
        if POLICY_AGENT_TYPES[self.category] != self.agent_type:
            raise ValueError("policy category and agent type do not match")
        return self


class RouteSnapshot(ActivationStrictModel):
    role: Literal[
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
        "CONSERVATIVE_DECISION",
        "BALANCED_DECISION",
        "AGGRESSIVE_DECISION",
    ]
    route_id: str = Field(pattern=UUID_PATTERN)
    route_version: Annotated[int, Field(strict=True, ge=1)]
    route_version_hash: str = Field(pattern=HASH_PATTERN)
    model_id: str = Field(pattern=UUID_PATTERN)
    model_version: Annotated[int, Field(strict=True, ge=1)]
    fallback_model_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    fallback_model_version: Annotated[int, Field(strict=True, ge=1)] | None
    prompt_profile_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    prompt_version: str = Field(min_length=1)
    prompt_content_hash: str | None = Field(default=None, pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_provenance(self) -> RouteSnapshot:
        if (self.fallback_model_id is None) != (self.fallback_model_version is None):
            raise ValueError("fallback model provenance must be both present or null")
        if (self.prompt_profile_id is None) != (self.prompt_content_hash is None):
            raise ValueError("prompt profile provenance must be both present or null")
        if self.role in DECISION_ROLES and (
            self.prompt_profile_id is None or self.prompt_content_hash is None
        ):
            raise ValueError("Decision routes require complete prompt provenance")
        return self


class VersionSnapshot(ActivationStrictModel):
    dag_version: Literal["agent-dag-v7"]
    decision_context_schema_version: Literal["decision-context-v1"]
    decision_agent_result_schema_version: Literal["decision-agent-result-v1"]
    arbiter_result_schema_version: Literal["entry-consensus-v1"]
    consensus_policy_version: Literal["consensus-policy-v1"]
    policy_profiles: list[PolicyProfileSnapshot]
    routes: list[RouteSnapshot]

    @field_validator("policy_profiles", mode="before")
    @classmethod
    def normalize_policies(cls, value: object) -> object:
        if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
            rank = {category: index for index, category in enumerate(POLICY_ORDER)}
            return sorted(value, key=lambda item: rank.get(str(item.get("category")), 99))
        return value

    @field_validator("routes", mode="before")
    @classmethod
    def normalize_routes(cls, value: object) -> object:
        if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
            rank = {role: index for index, role in enumerate(ROUTE_ORDER)}
            return sorted(value, key=lambda item: rank.get(str(item.get("role")), 99))
        return value

    @model_validator(mode="after")
    def validate_sets(self) -> VersionSnapshot:
        if tuple(item.category for item in self.policy_profiles) != POLICY_ORDER:
            raise ValueError("policy profiles must contain the exact canonical set")
        if tuple(item.role for item in self.routes) != ROUTE_ORDER:
            raise ValueError("routes must contain the exact canonical set")
        return self


class SafetyEvidence(ActivationStrictModel):
    test_id: str = Field(min_length=1)
    requirement_ids: list[str]
    result: Literal["PASSED"]
    code_revision: str = Field(min_length=1)
    test_plan_version: str = Field(min_length=1)
    spec_version: str = Field(min_length=1)
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
    def validate_evidence(self) -> SafetyEvidence:
        if not self.requirement_ids or len(self.requirement_ids) != len(
            set(self.requirement_ids)
        ) or any(not item for item in self.requirement_ids):
            raise ValueError("requirement IDs must be non-empty and unique")
        if (self.valid_until is None) == (self.freshness_contract is None):
            raise ValueError("exactly one evidence validity mechanism is required")
        if self.valid_until is not None and self.executed_at >= self.valid_until:
            raise ValueError("evidence validity must follow execution")
        return self


class ActivationGatePayload(ActivationStrictModel):
    schema_version: Literal["activation-gate-v1"]
    gate_state: Literal["OPEN", "CLOSED"]
    target: Literal["MOCK"]
    version_snapshot: VersionSnapshot
    version_snapshot_hash: str = Field(pattern=HASH_PATTERN)
    safety_evidence: list[SafetyEvidence]
    validation_policy_version: Literal["activation-validation-policy-v1"]
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
    def validate_payload(self) -> ActivationGatePayload:
        if self.validated_at >= self.valid_until:
            raise ValueError("gate valid_until must follow validated_at")
        if len({item.test_id for item in self.safety_evidence}) != len(
            self.safety_evidence
        ):
            raise ValueError("safety evidence test IDs must be unique")
        return self


def build_actual_version_snapshot(
    *,
    policy_version_map: Mapping[str, Any],
    route_versions: Mapping[str, Any],
) -> VersionSnapshot:
    profiles = policy_version_map.get("profiles")
    if (
        policy_version_map.get("schema_version") != "policy-version-map-v1"
        or not isinstance(profiles, list)
        or set(route_versions) != set(ROUTE_ORDER)
    ):
        raise ActivationGateError("ACTIVATION_GATE_SNAPSHOT_MISMATCH")
    routes: list[dict[str, Any]] = []
    try:
        for role in ROUTE_ORDER:
            source = route_versions[role]
            if not isinstance(source, Mapping):
                raise TypeError("route snapshot must be an object")
            routes.append(
                {
                    "role": role,
                    "route_id": source["route_id"],
                    "route_version": source["route_version"],
                    "route_version_hash": source["route_version_hash"],
                    "model_id": source["model_id"],
                    "model_version": source["model_version"],
                    "fallback_model_id": source.get("fallback_model_id"),
                    "fallback_model_version": source.get("fallback_model_version"),
                    "prompt_profile_id": source.get("prompt_profile_id"),
                    "prompt_version": source["prompt_version"],
                    "prompt_content_hash": source.get("prompt_content_hash"),
                }
            )
        return VersionSnapshot.model_validate(
            {
                "dag_version": "agent-dag-v7",
                "decision_context_schema_version": "decision-context-v1",
                "decision_agent_result_schema_version": "decision-agent-result-v1",
                "arbiter_result_schema_version": "entry-consensus-v1",
                "consensus_policy_version": "consensus-policy-v1",
                "policy_profiles": profiles,
                "routes": routes,
            }
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ActivationGateError("ACTIVATION_GATE_SNAPSHOT_MISMATCH") from exc


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


def canonical_activation_json(value: BaseModel | Mapping[str, Any]) -> str:
    return json.dumps(
        _canonical_value(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def activation_digest(value: BaseModel | Mapping[str, Any] | str | bytes) -> str:
    if isinstance(value, bytes):
        material = value
    elif isinstance(value, str):
        material = value.encode("utf-8")
    else:
        material = canonical_activation_json(value).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def version_snapshot_hash(snapshot: VersionSnapshot | Mapping[str, Any]) -> str:
    parsed = snapshot if isinstance(snapshot, VersionSnapshot) else VersionSnapshot.model_validate(snapshot)
    return activation_digest(parsed)


def activation_payload_hash(payload: ActivationGatePayload | Mapping[str, Any]) -> str:
    parsed = (
        payload
        if isinstance(payload, ActivationGatePayload)
        else ActivationGatePayload.model_validate(payload)
    )
    return activation_digest(parsed)


@dataclass(frozen=True)
class ActivationValidationPolicy:
    required_test_ids: tuple[str, ...] = REQUIRED_ACTIVATION_TEST_IDS
    freshness_contracts: Mapping[str, timedelta] | None = None
    code_revision: str | None = None
    test_plan_version: str | None = None
    spec_version: str | None = None
    migration_revision: str | None = None
    environment: str | None = None
    required_acceptance_set_hash: str | None = None
    require_artifact_v1: bool = False


class ActivationGateError(Exception):
    def __init__(
        self, code: str, status_code: int = 422, *, retryable: bool = False
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


EvidenceLoader = Callable[[str], bytes]
VersionSnapshotVerifier = Callable[[VersionSnapshot], None]


def validate_activation_payload(
    payload: ActivationGatePayload | Mapping[str, Any] | str,
    *,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    policy: ActivationValidationPolicy | None = None,
) -> ActivationGatePayload:
    try:
        if isinstance(payload, str):
            gate = ActivationGatePayload.model_validate_json(payload)
        elif isinstance(payload, ActivationGatePayload):
            gate = payload
        else:
            gate = ActivationGatePayload.model_validate(payload)
        observed = _utc(now)
        if gate.version_snapshot_hash != version_snapshot_hash(gate.version_snapshot):
            raise ValueError("version snapshot hash mismatch")
        if gate.gate_state == "CLOSED":
            return gate
        if observed >= gate.valid_until:
            raise ValueError("OPEN gate is expired")
        validation_policy = policy or ActivationValidationPolicy()
        if validation_policy.require_artifact_v1 and any(
            value is None
            for value in (
                validation_policy.code_revision,
                validation_policy.test_plan_version,
                validation_policy.spec_version,
                validation_policy.migration_revision,
                validation_policy.environment,
                validation_policy.required_acceptance_set_hash,
            )
        ):
            raise ActivationGateError(
                "ACTIVATION_GATE_EVIDENCE_UNAVAILABLE",
                503,
                retryable=True,
            )
        expected_ids = tuple(sorted(validation_policy.required_test_ids))
        if tuple(item.test_id for item in gate.safety_evidence) != expected_ids:
            raise ValueError("activation acceptance evidence set is incomplete")
        for attribute in ("code_revision", "test_plan_version", "spec_version"):
            if len({getattr(item, attribute) for item in gate.safety_evidence}) != 1:
                raise ValueError("evidence target versions must be consistent")
        if evidence_loader is None:
            raise ValueError("evidence artifact resolver is required")
        freshness_contracts = validation_policy.freshness_contracts or {}
        for evidence in gate.safety_evidence:
            if validation_policy.code_revision and (
                evidence.code_revision != validation_policy.code_revision
            ):
                raise ValueError("evidence code revision mismatch")
            if validation_policy.test_plan_version and (
                evidence.test_plan_version != validation_policy.test_plan_version
            ):
                raise ValueError("evidence TEST_PLAN version mismatch")
            if validation_policy.spec_version and (
                evidence.spec_version != validation_policy.spec_version
            ):
                raise ValueError("evidence spec version mismatch")
            try:
                artifact = evidence_loader(evidence.evidence_ref)
            except ActivationEvidenceStoreError as exc:
                if exc.category in {
                    EvidenceStoreFailureCategory.UNREADABLE,
                    EvidenceStoreFailureCategory.STORE_UNAVAILABLE,
                }:
                    raise ActivationGateError(
                        "ACTIVATION_GATE_EVIDENCE_UNAVAILABLE",
                        503,
                        retryable=True,
                    ) from exc
                raise ValueError("activation evidence is invalid") from exc
            if activation_digest(artifact) != evidence.evidence_hash:
                raise ValueError("evidence artifact hash mismatch")
            if validation_policy.require_artifact_v1:
                try:
                    reference = EvidenceReference.parse(evidence.evidence_ref)
                    body = parse_canonical_artifact(artifact)
                except ActivationEvidenceError as exc:
                    raise ValueError("activation artifact is invalid") from exc
                if (
                    reference.digest != evidence.evidence_hash
                    or body.test_id != evidence.test_id
                    or body.requirement_ids != evidence.requirement_ids
                    or body.result != evidence.result
                    or body.code_revision != evidence.code_revision
                    or body.test_plan_version != evidence.test_plan_version
                    or body.spec_version != evidence.spec_version
                    or body.executed_at != evidence.executed_at
                    or body.freshness_contract != evidence.freshness_contract
                    or body.migration_revision != validation_policy.migration_revision
                    or body.environment != validation_policy.environment
                    or body.required_acceptance_set_hash
                    != validation_policy.required_acceptance_set_hash
                    or evidence.valid_until is not None
                    or evidence.freshness_contract != EXACT_REVISION_FRESHNESS
                ):
                    raise ValueError("activation artifact descriptor mismatch")
            if evidence.valid_until is not None:
                if observed >= evidence.valid_until:
                    raise ValueError("evidence is stale")
            elif not validation_policy.require_artifact_v1:
                freshness = freshness_contracts.get(evidence.freshness_contract or "")
                if freshness is None or observed >= evidence.executed_at + freshness:
                    raise ValueError("evidence freshness contract is invalid or stale")
        return gate
    except ActivationGateError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ActivationGateError("ACTIVATION_GATE_INVALID") from exc


def validate_version_snapshot_against_db(
    db: Session, snapshot: VersionSnapshot
) -> None:
    try:
        for item in snapshot.policy_profiles:
            version = db.get(ConfigurationVersion, item.configuration_version_id)
            if (
                version is None
                or version.scope != ACTIVATION_SCOPE
                or version.target_id != ACTIVATION_TARGET
                or version.category != item.category
                or version.sequence != item.sequence
                or version.payload_hash != item.payload_hash
                or version.state != "ACTIVE"
            ):
                raise ValueError("PolicyProfile target version mismatch")

        from app.agents.runtime import RouteBinding, _route_version_snapshot

        bindings: dict[str, RouteBinding] = {}
        owners: set[str] = set()
        for item in snapshot.routes:
            route = db.get(LlmRoleRoute, item.route_id)
            model = db.get(LlmModelProfile, item.model_id)
            if route is None or model is None:
                raise ValueError("route/model target missing")
            mismatches = {
                "role": route.role != item.role,
                "route_version": route.version != item.route_version,
                "model_id": route.primary_model_profile_id != item.model_id,
                "model_version": model.version != item.model_version,
                "route_state": route.state != "ACTIVE",
                "model_state": model.state != "VALIDATED",
                "prompt_version": route.prompt_version != item.prompt_version,
                "prompt_profile_id": route.prompt_profile_id != item.prompt_profile_id,
            }
            if any(mismatches.values()):
                fields = ",".join(key for key, mismatch in mismatches.items() if mismatch)
                raise ValueError(f"route/model target version mismatch: {fields}")
            owners.add(route.owner_id)
            fallback_ids = json.loads(route.fallback_model_profile_ids_json)
            if not isinstance(fallback_ids, list) or len(fallback_ids) > 1:
                raise ValueError("route fallback provenance is invalid")
            fallback = db.get(LlmModelProfile, fallback_ids[0]) if fallback_ids else None
            if (
                (fallback.id if fallback else None) != item.fallback_model_id
                or (fallback.version if fallback else None) != item.fallback_model_version
            ):
                raise ValueError("fallback model target version mismatch")
            prompt = (
                db.get(LlmPromptProfile, route.prompt_profile_id)
                if route.prompt_profile_id
                else None
            )
            if (
                (prompt.content_hash if prompt else None) != item.prompt_content_hash
                or (prompt is not None and prompt.state != "VALIDATED")
            ):
                raise ValueError("prompt target version mismatch")
            bindings[item.role] = RouteBinding(
                route=route,
                model=model,
                provider=None,  # type: ignore[arg-type]
                fallback_model=fallback,
                fallback_provider=None,
            )
        if len(owners) != 1:
            raise ValueError("route owner identity is ambiguous")
        resolved = _route_version_snapshot(db, bindings)
        for item in snapshot.routes:
            if resolved[item.role]["route_version_hash"] != item.route_version_hash:
                raise ValueError("route version hash mismatch")
    except ActivationGateError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ActivationGateError("ACTIVATION_GATE_INVALID") from exc


class GateOutcome(StrEnum):
    PASS = "PASS"
    CLOSED = "CLOSED"
    SUPERSEDED = "SUPERSEDED"
    INVALID = "INVALID"
    DB_RETRYABLE_FAILURE = "DB_RETRYABLE_FAILURE"


@dataclass(frozen=True)
class GateResolution:
    outcome: GateOutcome
    version: ConfigurationVersion | None = None
    payload: ActivationGatePayload | None = None


def select_current_v7_entry_activation_gate(
    db: Session,
    *,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    policy: ActivationValidationPolicy | None = None,
    snapshot_verifier: VersionSnapshotVerifier | None = None,
    lock: bool = False,
) -> GateResolution:
    try:
        statement = select(ConfigurationVersion).where(
            ConfigurationVersion.scope == ACTIVATION_SCOPE,
            ConfigurationVersion.target_id == ACTIVATION_TARGET,
            ConfigurationVersion.category == ACTIVATION_CATEGORY,
            ConfigurationVersion.state == "ACTIVE",
        )
        if lock:
            statement = statement.with_for_update()
        versions = list(db.scalars(statement))
    except SQLAlchemyError:
        return GateResolution(GateOutcome.DB_RETRYABLE_FAILURE)
    if not versions:
        return GateResolution(GateOutcome.CLOSED)
    if len(versions) != 1:
        return GateResolution(GateOutcome.INVALID)
    version = versions[0]
    try:
        payload = validate_activation_payload(
            version.payload_json,
            now=now,
            evidence_loader=evidence_loader,
            policy=policy,
        )
        if canonical_activation_json(payload) != version.payload_json:
            raise ActivationGateError("ACTIVATION_GATE_INVALID")
        if activation_payload_hash(payload) != version.payload_hash:
            raise ActivationGateError("ACTIVATION_GATE_INVALID")
        if payload.gate_state == "OPEN":
            (snapshot_verifier or (lambda value: validate_version_snapshot_against_db(db, value)))(
                payload.version_snapshot
            )
    except ActivationGateError:
        return GateResolution(GateOutcome.INVALID, version=version)
    except SQLAlchemyError:
        return GateResolution(GateOutcome.DB_RETRYABLE_FAILURE)
    if payload.gate_state == "CLOSED":
        return GateResolution(GateOutcome.CLOSED, version=version, payload=payload)
    return GateResolution(GateOutcome.PASS, version=version, payload=payload)


def verify_frozen_v7_entry_activation_gate(
    db: Session,
    *,
    frozen_version_id: str,
    frozen_payload_hash: str,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    policy: ActivationValidationPolicy | None = None,
    snapshot_verifier: VersionSnapshotVerifier | None = None,
    lock: bool = False,
) -> GateResolution:
    current = select_current_v7_entry_activation_gate(
        db,
        now=now,
        evidence_loader=evidence_loader,
        policy=policy,
        snapshot_verifier=snapshot_verifier,
        lock=lock,
    )
    if current.outcome != GateOutcome.PASS:
        return current
    assert current.version is not None
    if (
        current.version.id != frozen_version_id
        or current.version.payload_hash != frozen_payload_hash
    ):
        return GateResolution(
            GateOutcome.SUPERSEDED,
            version=current.version,
            payload=current.payload,
        )
    return current


def validate_frozen_activation_provenance(
    db: Session, *, run: object
) -> ActivationGatePayload | None:
    purpose = getattr(run, "purpose", None)
    version_id = getattr(run, "activation_gate_version_id", None)
    frozen_hash = getattr(run, "activation_gate_version_hash", None)
    if purpose == "DIAGNOSTIC":
        if version_id is not None or frozen_hash is not None:
            raise ActivationGateError("ACTIVATION_GATE_PROVENANCE_INVALID")
        return None
    if purpose != "TRADING" or not isinstance(version_id, str) or not isinstance(
        frozen_hash, str
    ):
        raise ActivationGateError("ACTIVATION_GATE_PROVENANCE_INVALID")
    version = db.get(ConfigurationVersion, version_id)
    try:
        if (
            version is None
            or version.scope != ACTIVATION_SCOPE
            or version.target_id != ACTIVATION_TARGET
            or version.category != ACTIVATION_CATEGORY
            or version.payload_hash != frozen_hash
        ):
            raise ValueError("frozen Gate identity/hash mismatch")
        payload = ActivationGatePayload.model_validate_json(version.payload_json)
        if (
            payload.gate_state != "OPEN"
            or canonical_activation_json(payload) != version.payload_json
            or version_snapshot_hash(payload.version_snapshot)
            != payload.version_snapshot_hash
            or activation_payload_hash(payload) != frozen_hash
        ):
            raise ValueError("frozen Gate payload is not immutable OPEN provenance")
        return payload
    except (TypeError, ValueError) as exc:
        raise ActivationGateError("ACTIVATION_GATE_PROVENANCE_INVALID") from exc


def _normalize_reason(reason: str) -> str:
    normalized = reason.strip()
    if not normalized:
        raise ActivationGateError("CONFIGURATION_REASON_REQUIRED", 400)
    return normalized


def _gate_version(db: Session, version_id: str, *, lock: bool = False) -> ConfigurationVersion:
    statement = select(ConfigurationVersion).where(ConfigurationVersion.id == version_id)
    if lock:
        statement = statement.with_for_update()
    version = db.scalar(statement)
    if (
        version is None
        or version.scope != ACTIVATION_SCOPE
        or version.target_id != ACTIVATION_TARGET
        or version.category != ACTIVATION_CATEGORY
    ):
        raise ActivationGateError("CONFIGURATION_VERSION_NOT_FOUND", 404)
    return version


def create_activation_gate_draft(
    db: Session,
    *,
    user: User,
    payload: ActivationGatePayload | Mapping[str, Any],
    reason: str,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    policy: ActivationValidationPolicy | None = None,
    snapshot_verifier: VersionSnapshotVerifier | None = None,
) -> ConfigurationVersion:
    gate = validate_activation_payload(
        payload, now=now, evidence_loader=evidence_loader, policy=policy
    )
    if gate.gate_state == "OPEN":
        (snapshot_verifier or (lambda value: validate_version_snapshot_against_db(db, value)))(
            gate.version_snapshot
        )
    payload_json = canonical_activation_json(gate)
    current = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.scope == ACTIVATION_SCOPE,
            ConfigurationVersion.target_id == ACTIVATION_TARGET,
            ConfigurationVersion.category == ACTIVATION_CATEGORY,
            ConfigurationVersion.state == "ACTIVE",
        )
    )
    sequence = int(
        db.scalar(
            select(func.max(ConfigurationVersion.sequence)).where(
                ConfigurationVersion.scope == ACTIVATION_SCOPE,
                ConfigurationVersion.target_id == ACTIVATION_TARGET,
                ConfigurationVersion.category == ACTIVATION_CATEGORY,
            )
        )
        or 0
    ) + 1
    version = ConfigurationVersion(
        scope=ACTIVATION_SCOPE,
        target_id=ACTIVATION_TARGET,
        category=ACTIVATION_CATEGORY,
        sequence=sequence,
        state="DRAFT",
        payload_json=payload_json,
        payload_hash=activation_digest(payload_json),
        reason=_normalize_reason(reason),
        created_by=user.id,
        base_active_version_id=current.id if current else None,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def validate_activation_gate_draft(
    db: Session,
    *,
    version_id: str,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    policy: ActivationValidationPolicy | None = None,
    snapshot_verifier: VersionSnapshotVerifier | None = None,
) -> ConfigurationVersion:
    version = _gate_version(db, version_id)
    if version.state == "VALIDATED":
        return version
    if version.state != "DRAFT":
        raise ActivationGateError("CONFIGURATION_STATE_INVALID", 409)
    gate = validate_activation_payload(
        version.payload_json, now=now, evidence_loader=evidence_loader, policy=policy
    )
    if gate.gate_state == "OPEN":
        (snapshot_verifier or (lambda value: validate_version_snapshot_against_db(db, value)))(
            gate.version_snapshot
        )
    if (
        canonical_activation_json(gate) != version.payload_json
        or activation_payload_hash(gate) != version.payload_hash
    ):
        raise ActivationGateError("ACTIVATION_GATE_INVALID")
    version.state = "VALIDATED"
    version.validated_at = _utc(now)
    db.commit()
    db.refresh(version)
    return version


def activate_activation_gate(
    db: Session,
    *,
    user: User,
    version_id: str,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    correlation_id: str,
    request_ip: str,
    user_agent: str,
    policy: ActivationValidationPolicy | None = None,
    snapshot_verifier: VersionSnapshotVerifier | None = None,
) -> ConfigurationVersion:
    version = _gate_version(db, version_id, lock=True)
    if version.state == "ACTIVE":
        return version
    if version.state != "VALIDATED":
        raise ActivationGateError("CONFIGURATION_NOT_VALIDATED", 409)
    gate = validate_activation_payload(
        version.payload_json, now=now, evidence_loader=evidence_loader, policy=policy
    )
    if gate.gate_state == "OPEN":
        (snapshot_verifier or (lambda value: validate_version_snapshot_against_db(db, value)))(
            gate.version_snapshot
        )
    if (
        canonical_activation_json(gate) != version.payload_json
        or activation_payload_hash(gate) != version.payload_hash
    ):
        raise ActivationGateError("ACTIVATION_GATE_INVALID")
    current = db.scalar(
        select(ConfigurationVersion)
        .where(
            ConfigurationVersion.scope == ACTIVATION_SCOPE,
            ConfigurationVersion.target_id == ACTIVATION_TARGET,
            ConfigurationVersion.category == ACTIVATION_CATEGORY,
            ConfigurationVersion.state == "ACTIVE",
        )
        .with_for_update()
    )
    if version.base_active_version_id != (current.id if current else None):
        raise ActivationGateError("CONFIGURATION_VERSION_CONFLICT", 409)
    if current is not None:
        current.state = "SUPERSEDED"
        db.flush()
    version.state = "ACTIVE"
    version.activated_at = _utc(now)
    db.add(
        AuditLog(
            actor_type="SYSTEM",
            actor_id=user.id,
            action="V7_ENTRY_ACTIVATION_ACTIVATED",
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


def activation_gate_history(db: Session) -> list[ConfigurationVersion]:
    return list(
        db.scalars(
            select(ConfigurationVersion)
            .where(
                ConfigurationVersion.scope == ACTIVATION_SCOPE,
                ConfigurationVersion.target_id == ACTIVATION_TARGET,
                ConfigurationVersion.category == ACTIVATION_CATEGORY,
            )
            .order_by(ConfigurationVersion.sequence.desc())
            .limit(50)
        )
    )
