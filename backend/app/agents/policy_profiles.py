from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.activation_gate import ActivationGateError, validate_frozen_activation_provenance
from app.agents.contracts import DecisionAgentPolicyProfile, DecisionPolicyParameters
from app.agents.decision_context import canonical_context_json, context_digest
from app.models import AgentRun, ConfigurationVersion, DecisionInputSnapshot

POLICY_SCHEMA_VERSION = "policy-schema-v1"
POLICY_VERSION_MAP_SCHEMA_VERSION = "policy-version-map-v1"
V7_DAG_VERSION = "agent-dag-v7"
POLICY_SCOPE = "SYSTEM"
POLICY_TARGET_ID = "MOCK"

POLICY_CATEGORIES = (
    "V7_ENTRY_POLICY_CONSERVATIVE",
    "V7_ENTRY_POLICY_BALANCED",
    "V7_ENTRY_POLICY_AGGRESSIVE",
)
CATEGORY_AGENT_TYPES = {
    "V7_ENTRY_POLICY_CONSERVATIVE": "CONSERVATIVE",
    "V7_ENTRY_POLICY_BALANCED": "BALANCED",
    "V7_ENTRY_POLICY_AGGRESSIVE": "AGGRESSIVE",
}
AGENT_TYPE_ORDER = ("CONSERVATIVE", "BALANCED", "AGGRESSIVE")
ROLE_AGENT_TYPES = {
    "CONSERVATIVE_DECISION": "CONSERVATIVE",
    "BALANCED_DECISION": "BALANCED",
    "AGGRESSIVE_DECISION": "AGGRESSIVE",
}
AGENT_TYPE_CATEGORIES = {value: key for key, value in CATEGORY_AGENT_TYPES.items()}


class PolicyProfileError(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class FrozenPolicyVersionMap:
    profiles: tuple[ConfigurationVersion, ...]
    manifest_json: str
    manifest_hash: str


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _object(encoded: str, error_code: str = "POLICY_PROFILE_INVALID") -> dict[str, object]:
    try:
        value = json.loads(encoded)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PolicyProfileError(error_code) from exc
    if not isinstance(value, dict):
        raise PolicyProfileError(error_code)
    return value


def _validate_profile(
    version: ConfigurationVersion,
    *,
    expected_category: str,
    require_active: bool,
) -> dict[str, object]:
    if (
        expected_category not in CATEGORY_AGENT_TYPES
        or version.scope != POLICY_SCOPE
        or version.target_id != POLICY_TARGET_ID
        or version.category != expected_category
    ):
        raise PolicyProfileError("POLICY_PROFILE_INVALID")
    allowed_states = {"ACTIVE"} if require_active else {"ACTIVE", "SUPERSEDED"}
    if version.state not in allowed_states:
        raise PolicyProfileError("POLICY_PROFILE_INVALID")
    if version.validated_at is None or version.activated_at is None:
        raise PolicyProfileError("POLICY_PROFILE_INVALID")

    payload = _object(version.payload_json)
    if set(payload) != {
        "schema_version",
        "agent_type",
        "policy_parameters",
        "validation_metadata",
    }:
        raise PolicyProfileError("POLICY_PROFILE_INVALID")
    if payload.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise PolicyProfileError("POLICY_PROFILE_INVALID")
    expected_agent_type = CATEGORY_AGENT_TYPES[expected_category]
    if payload.get("agent_type") != expected_agent_type:
        raise PolicyProfileError("POLICY_PROFILE_TYPE_MISMATCH")
    if not isinstance(payload.get("policy_parameters"), dict):
        raise PolicyProfileError("POLICY_PROFILE_INVALID")
    metadata = payload.get("validation_metadata")
    if not isinstance(metadata, dict) or not metadata:
        raise PolicyProfileError("POLICY_PROFILE_INVALID")

    canonical = canonical_context_json(payload)
    if canonical != version.payload_json:
        raise PolicyProfileError("POLICY_PROFILE_INVALID")
    if context_digest(canonical) != version.payload_hash:
        raise PolicyProfileError("POLICY_PROFILE_HASH_MISMATCH")
    return payload


def _build_version_map(
    profiles_by_category: dict[str, ConfigurationVersion],
) -> FrozenPolicyVersionMap:
    ordered_profiles: list[ConfigurationVersion] = []
    entries: list[dict[str, object]] = []
    for category in POLICY_CATEGORIES:
        version = profiles_by_category[category]
        payload = _validate_profile(
            version, expected_category=category, require_active=True
        )
        agent_type = payload["agent_type"]
        entries.append(
            {
                "configuration_version_id": version.id,
                "category": category,
                "sequence": version.sequence,
                "agent_type": agent_type,
                "payload_hash": version.payload_hash,
            }
        )
        ordered_profiles.append(version)
    manifest = {
        "schema_version": POLICY_VERSION_MAP_SCHEMA_VERSION,
        "profiles": entries,
    }
    encoded = canonical_context_json(manifest)
    return FrozenPolicyVersionMap(
        profiles=tuple(ordered_profiles),
        manifest_json=encoded,
        manifest_hash=context_digest(encoded),
    )


def select_active_policy_profiles(
    db: Session,
    *,
    scope: str = POLICY_SCOPE,
    target_id: str = POLICY_TARGET_ID,
) -> FrozenPolicyVersionMap:
    if scope != POLICY_SCOPE or target_id != POLICY_TARGET_ID:
        raise PolicyProfileError("POLICY_PROFILE_INVALID")
    candidates = list(
        db.scalars(
            select(ConfigurationVersion)
            .where(
                ConfigurationVersion.scope == scope,
                ConfigurationVersion.target_id == target_id,
                ConfigurationVersion.category.in_(POLICY_CATEGORIES),
                ConfigurationVersion.state == "ACTIVE",
            )
            .order_by(ConfigurationVersion.category, ConfigurationVersion.sequence)
            .with_for_update()
        )
    )
    grouped: dict[str, list[ConfigurationVersion]] = {
        category: [] for category in POLICY_CATEGORIES
    }
    for candidate in candidates:
        grouped[candidate.category].append(candidate)
    if any(not grouped[category] for category in POLICY_CATEGORIES):
        raise PolicyProfileError("POLICY_PROFILE_MISSING")
    if any(len(grouped[category]) != 1 for category in POLICY_CATEGORIES):
        raise PolicyProfileError("POLICY_PROFILE_DUPLICATE", 409)
    return _build_version_map(
        {category: grouped[category][0] for category in POLICY_CATEGORIES}
    )


def _validate_stored_map(
    db: Session, run: AgentRun
) -> tuple[ConfigurationVersion, ...]:
    if (
        run.dag_version != V7_DAG_VERSION
        or run.analysis_context != "ENTRY"
        or run.purpose not in {"DIAGNOSTIC", "TRADING"}
        or not run.policy_profile_version_map_json
        or not run.policy_profile_version_map_hash
    ):
        raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)
    try:
        validate_frozen_activation_provenance(db, run=run)
    except ActivationGateError as exc:
        raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409) from exc
    manifest = _object(
        run.policy_profile_version_map_json,
        "POLICY_PROFILE_VERSION_MAP_CONFLICT",
    )
    canonical = canonical_context_json(manifest)
    if (
        canonical != run.policy_profile_version_map_json
        or context_digest(canonical) != run.policy_profile_version_map_hash
        or set(manifest) != {"schema_version", "profiles"}
        or manifest.get("schema_version") != POLICY_VERSION_MAP_SCHEMA_VERSION
    ):
        raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)
    entries = manifest.get("profiles")
    if not isinstance(entries, list) or len(entries) != len(POLICY_CATEGORIES):
        raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)

    resolved: list[ConfigurationVersion] = []
    for category, expected_agent_type, entry in zip(
        POLICY_CATEGORIES, AGENT_TYPE_ORDER, entries, strict=True
    ):
        if not isinstance(entry, dict) or set(entry) != {
            "configuration_version_id",
            "category",
            "sequence",
            "agent_type",
            "payload_hash",
        }:
            raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)
        if entry.get("category") != category or entry.get("agent_type") != expected_agent_type:
            raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)
        version_id = entry.get("configuration_version_id")
        if not isinstance(version_id, str):
            raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)
        version = db.get(ConfigurationVersion, version_id)
        if version is None:
            raise PolicyProfileError("POLICY_PROFILE_INVALID")
        _validate_profile(version, expected_category=category, require_active=False)
        if (
            entry.get("sequence") != version.sequence
            or entry.get("payload_hash") != version.payload_hash
        ):
            raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)
        resolved.append(version)
    return tuple(resolved)


def resolve_run_policy_profiles(
    db: Session, *, run_id: str
) -> tuple[ConfigurationVersion, ...]:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)
    return _validate_stored_map(db, run)


def validate_policy_profile_semantics(
    version: ConfigurationVersion, *, expected_agent_type: str
) -> DecisionAgentPolicyProfile:
    expected_category = AGENT_TYPE_CATEGORIES.get(expected_agent_type)
    if expected_category is None:
        raise PolicyProfileError("POLICY_PROFILE_TYPE_MISMATCH")
    payload = _validate_profile(
        version,
        expected_category=expected_category,
        require_active=False,
    )
    try:
        parameters = DecisionPolicyParameters.model_validate(payload["policy_parameters"])
        return DecisionAgentPolicyProfile(
            configuration_version_id=version.id,
            category=version.category,
            sequence=version.sequence,
            agent_type=expected_agent_type,
            payload_hash=version.payload_hash,
            schema_version=payload["schema_version"],
            policy_parameters=parameters,
        )
    except (TypeError, ValueError) as exc:
        raise PolicyProfileError("POLICY_PROFILE_SEMANTIC_INVALID") from exc


def resolve_decision_agent_policy(
    db: Session, *, run_id: str, role: str
) -> tuple[ConfigurationVersion, DecisionAgentPolicyProfile]:
    agent_type = ROLE_AGENT_TYPES.get(role)
    if agent_type is None:
        raise PolicyProfileError("POLICY_PROFILE_TYPE_MISMATCH")
    profiles = resolve_run_policy_profiles(db, run_id=run_id)
    matches = [
        version
        for version in profiles
        if CATEGORY_AGENT_TYPES.get(version.category) == agent_type
    ]
    if len(matches) != 1:
        raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)
    version = matches[0]
    return version, validate_policy_profile_semantics(
        version,
        expected_agent_type=agent_type,
    )


def _assert_existing_run(
    db: Session,
    run: AgentRun,
    *,
    owner_id: str,
    decision_input: DecisionInputSnapshot,
    route_versions_json: str,
    frozen: FrozenPolicyVersionMap,
) -> None:
    if (
        run.owner_id != owner_id
        or run.purpose != "DIAGNOSTIC"
        or run.market != decision_input.market
        or run.symbol != decision_input.symbol
        or run.market_snapshot_id != decision_input.market_snapshot_id
        or run.input_hash != decision_input.input_hash
        or run.dag_version != V7_DAG_VERSION
        or run.analysis_context != "ENTRY"
        or run.route_versions_json != route_versions_json
        or run.policy_profile_version_map_json != frozen.manifest_json
        or run.policy_profile_version_map_hash != frozen.manifest_hash
    ):
        raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)
    _validate_stored_map(db, run)


def admit_v7_diagnostic_run(
    db: Session,
    *,
    owner_id: str,
    decision_input_snapshot_id: str,
    route_versions: dict[str, object] | None = None,
    now: datetime | None = None,
) -> tuple[AgentRun, bool]:
    observed = now or datetime.now(UTC)
    decision_input = db.scalar(
        select(DecisionInputSnapshot)
        .where(DecisionInputSnapshot.id == decision_input_snapshot_id)
        .with_for_update()
    )
    if (
        decision_input is None
        or decision_input.user_id != owner_id
        or decision_input.purpose != "DIAGNOSTIC"
        or decision_input.schema_version != "scout-input-v2"
    ):
        raise PolicyProfileError("POLICY_PROFILE_INVALID")
    input_payload = _object(decision_input.input_json, "POLICY_PROFILE_INVALID")
    if (
        canonical_context_json(input_payload) != decision_input.input_json
        or context_digest(decision_input.input_json) != decision_input.input_hash
    ):
        raise PolicyProfileError("POLICY_PROFILE_INVALID")
    valid_until_value = input_payload.get("valid_until")
    if not isinstance(valid_until_value, str):
        raise PolicyProfileError("POLICY_PROFILE_INVALID")
    try:
        valid_until = _aware(datetime.fromisoformat(valid_until_value))
    except ValueError as exc:
        raise PolicyProfileError("POLICY_PROFILE_INVALID") from exc
    if valid_until <= _aware(observed):
        raise PolicyProfileError("POLICY_PROFILE_INVALID")

    frozen = select_active_policy_profiles(db)
    route_versions_json = canonical_context_json(route_versions or {})
    idempotency_material = {
        "owner_id": owner_id,
        "purpose": "DIAGNOSTIC",
        "input_hash": decision_input.input_hash,
        "dag_version": V7_DAG_VERSION,
        "analysis_context": "ENTRY",
    }
    idempotency_key = context_digest(canonical_context_json(idempotency_material))
    existing = db.scalar(
        select(AgentRun).where(AgentRun.idempotency_key == idempotency_key)
    )
    if existing is not None:
        _assert_existing_run(
            db,
            existing,
            owner_id=owner_id,
            decision_input=decision_input,
            route_versions_json=route_versions_json,
            frozen=frozen,
        )
        db.commit()
        return existing, False

    run = AgentRun(
        owner_id=owner_id,
        purpose="DIAGNOSTIC",
        execution_stage="SHADOW",
        market=decision_input.market,
        symbol=decision_input.symbol,
        market_snapshot_id=decision_input.market_snapshot_id,
        input_hash=decision_input.input_hash,
        dag_version=V7_DAG_VERSION,
        route_versions_json=route_versions_json,
        policy_profile_version_map_json=frozen.manifest_json,
        policy_profile_version_map_hash=frozen.manifest_hash,
        idempotency_key=idempotency_key,
        state="CREATED",
        analysis_context="ENTRY",
        valid_until=valid_until,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(AgentRun).where(AgentRun.idempotency_key == idempotency_key)
        )
        if existing is None:
            raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)
        expected_input = db.get(DecisionInputSnapshot, decision_input_snapshot_id)
        if expected_input is None:
            raise PolicyProfileError("POLICY_PROFILE_VERSION_MAP_CONFLICT", 409)
        _assert_existing_run(
            db,
            existing,
            owner_id=owner_id,
            decision_input=expected_input,
            route_versions_json=route_versions_json,
            frozen=frozen,
        )
        db.commit()
        return existing, False
    db.commit()
    db.refresh(run)
    return run, True
