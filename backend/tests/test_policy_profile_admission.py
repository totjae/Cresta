from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.agents.decision_context import canonical_context_json, context_digest
from app.agents.policy_profiles import (
    AGENT_TYPE_ORDER,
    CATEGORY_AGENT_TYPES,
    POLICY_CATEGORIES,
    POLICY_SCHEMA_VERSION,
    POLICY_SCOPE,
    POLICY_TARGET_ID,
    POLICY_VERSION_MAP_SCHEMA_VERSION,
    PolicyProfileError,
    admit_v7_diagnostic_run,
    resolve_run_policy_profiles,
    select_active_policy_profiles,
)
from app.models import (
    AgentRun,
    AgentStageRun,
    ConfigurationVersion,
    DecisionContext,
    DecisionInputSnapshot,
    MarketSnapshot,
    User,
)

NOW = datetime(2026, 8, 25, 3, 0, tzinfo=UTC)


def _input(db: Session, owner: User, suffix: str) -> DecisionInputSnapshot:
    snapshot = MarketSnapshot(
        symbol="005930",
        market="KRX",
        source="PHASE3C",
        sequence_or_hash=suffix,
        payload_hash=context_digest(suffix),
        last_price=Decimal(70000),
        open_price=Decimal(70000),
        high_price=Decimal(70000),
        low_price=Decimal(70000),
        cumulative_volume=1,
        trading_status="OPEN",
        quality="NORMAL",
        event_at=NOW,
        received_at=NOW,
    )
    db.add(snapshot)
    db.flush()
    payload = {
        "schema_version": "scout-input-v2",
        "purpose": "DIAGNOSTIC",
        "snapshot_id": snapshot.id,
        "market": snapshot.market,
        "symbol": snapshot.symbol,
        "observed_at": NOW.isoformat(),
        "valid_until": (NOW + timedelta(minutes=5)).isoformat(),
    }
    encoded = canonical_context_json(payload)
    decision_input = DecisionInputSnapshot(
        user_id=owner.id,
        purpose="DIAGNOSTIC",
        schema_version="scout-input-v2",
        market=snapshot.market,
        symbol=snapshot.symbol,
        market_snapshot_id=snapshot.id,
        observed_at=NOW,
        data_quality="NORMAL",
        session_state="OPEN",
        input_json=encoded,
        input_hash=context_digest(encoded),
    )
    db.add(decision_input)
    db.flush()
    return decision_input


def _payload(
    agent_type: str,
    *,
    schema_version: str = POLICY_SCHEMA_VERSION,
    parameters: object | None = None,
    metadata: object | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "agent_type": agent_type,
        "policy_parameters": {} if parameters is None else parameters,
        "validation_metadata": (
            {"validation_policy_version": "policy-profile-validation-v1"}
            if metadata is None
            else metadata
        ),
    }
    if extra:
        payload.update(extra)
    return payload


def _profile(
    db: Session,
    owner: User,
    category: str,
    *,
    sequence: int = 1,
    state: str = "ACTIVE",
    agent_type: str | None = None,
    payload: dict[str, object] | None = None,
    payload_hash: str | None = None,
    target_id: str = POLICY_TARGET_ID,
) -> ConfigurationVersion:
    encoded = canonical_context_json(
        payload or _payload(agent_type or CATEGORY_AGENT_TYPES[category])
    )
    version = ConfigurationVersion(
        scope=POLICY_SCOPE,
        target_id=target_id,
        category=category,
        sequence=sequence,
        state=state,
        payload_json=encoded,
        payload_hash=payload_hash or context_digest(encoded),
        reason="Phase 3C fixture",
        created_by=owner.id,
        validated_at=NOW,
        activated_at=NOW,
    )
    db.add(version)
    db.flush()
    return version


def _all_profiles(db: Session, owner: User, *, sequence: int = 1) -> list[ConfigurationVersion]:
    return [
        _profile(
            db,
            owner,
            category,
            sequence=sequence,
            payload=_payload(
                CATEGORY_AGENT_TYPES[category],
                parameters={
                    "minimum_confidence": "0.5",
                    "minimum_entry_score": 50,
                    "risk_tolerance_score": 50,
                    "uncertainty_tolerance_ratio": "0.5",
                    "momentum_deterioration_tolerance_pct": "10",
                    "drawdown_tolerance_pct": "10",
                },
            ),
        )
        for category in reversed(POLICY_CATEGORIES)
    ]


def _error(db: Session, expected: str) -> None:
    with pytest.raises(PolicyProfileError) as captured:
        select_active_policy_profiles(db)
    assert captured.value.code == expected


def test_three_active_profiles_freeze_deterministic_canonical_map(
    db: Session, admin: User
) -> None:
    profiles = _all_profiles(db, admin)
    db.commit()

    first = select_active_policy_profiles(db)
    second = select_active_policy_profiles(db)
    manifest = json.loads(first.manifest_json)

    assert first.manifest_json == second.manifest_json
    assert first.manifest_hash == second.manifest_hash
    assert context_digest(first.manifest_json) == first.manifest_hash
    assert manifest["schema_version"] == POLICY_VERSION_MAP_SCHEMA_VERSION
    assert [item["agent_type"] for item in manifest["profiles"]] == list(
        AGENT_TYPE_ORDER
    )
    assert [item["category"] for item in manifest["profiles"]] == list(
        POLICY_CATEGORIES
    )
    assert {item["configuration_version_id"] for item in manifest["profiles"]} == {
        profile.id for profile in profiles
    }


@pytest.mark.parametrize("missing_category", POLICY_CATEGORIES)
def test_missing_profile_rejects_admission_without_partial_run(
    db: Session, admin: User, missing_category: str
) -> None:
    decision_input = _input(db, admin, missing_category)
    for category in POLICY_CATEGORIES:
        if category != missing_category:
            _profile(db, admin, category)
    db.commit()

    with pytest.raises(PolicyProfileError) as captured:
        admit_v7_diagnostic_run(
            db,
            owner_id=admin.id,
            decision_input_snapshot_id=decision_input.id,
            now=NOW,
        )

    assert captured.value.code == "POLICY_PROFILE_MISSING"
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_category_agent_type_mismatch_is_rejected(db: Session, admin: User) -> None:
    _all_profiles(db, admin)
    conservative = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.category == POLICY_CATEGORIES[0]
        )
    )
    assert conservative is not None
    mismatch = _payload("BALANCED")
    conservative.payload_json = canonical_context_json(mismatch)
    conservative.payload_hash = context_digest(conservative.payload_json)
    db.commit()

    _error(db, "POLICY_PROFILE_TYPE_MISMATCH")


def test_only_inactive_profiles_are_treated_as_missing(db: Session, admin: User) -> None:
    for category in POLICY_CATEGORIES:
        _profile(db, admin, category, state="SUPERSEDED")
    db.commit()

    _error(db, "POLICY_PROFILE_MISSING")


def test_duplicate_active_profile_fails_closed(db: Session, admin: User) -> None:
    db.execute(text("DROP INDEX uq_configuration_active_target"))
    _all_profiles(db, admin)
    _profile(db, admin, POLICY_CATEGORIES[0], sequence=2)
    db.commit()

    _error(db, "POLICY_PROFILE_DUPLICATE")


@pytest.mark.parametrize(
    "invalid_payload",
    (
        _payload("CONSERVATIVE", schema_version="policy-schema-v999"),
        _payload("CONSERVATIVE", parameters=[]),
        _payload("CONSERVATIVE", metadata={}),
        _payload("CONSERVATIVE", extra={"unknown": True}),
    ),
)
def test_invalid_schema_or_payload_is_rejected(
    db: Session, admin: User, invalid_payload: dict[str, object]
) -> None:
    _all_profiles(db, admin)
    conservative = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.category == POLICY_CATEGORIES[0]
        )
    )
    assert conservative is not None
    conservative.payload_json = canonical_context_json(invalid_payload)
    conservative.payload_hash = context_digest(conservative.payload_json)
    db.commit()

    _error(db, "POLICY_PROFILE_INVALID")


def test_payload_hash_mismatch_is_rejected(db: Session, admin: User) -> None:
    _all_profiles(db, admin)
    conservative = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.category == POLICY_CATEGORIES[0]
        )
    )
    assert conservative is not None
    conservative.payload_hash = "f" * 64
    db.commit()

    _error(db, "POLICY_PROFILE_HASH_MISMATCH")


def test_v7_admission_freezes_map_and_is_idempotent(db: Session, admin: User) -> None:
    decision_input = _input(db, admin, "idempotent")
    _all_profiles(db, admin)
    db.commit()

    first, created = admit_v7_diagnostic_run(
        db,
        owner_id=admin.id,
        decision_input_snapshot_id=decision_input.id,
        now=NOW,
    )
    second, created_again = admit_v7_diagnostic_run(
        db,
        owner_id=admin.id,
        decision_input_snapshot_id=decision_input.id,
        now=NOW,
    )

    assert created is True
    assert created_again is False
    assert second.id == first.id
    assert first.dag_version == "agent-dag-v7"
    assert first.purpose == "DIAGNOSTIC"
    assert first.analysis_context == "ENTRY"
    assert first.policy_profile_version_map_json is not None
    assert context_digest(first.policy_profile_version_map_json) == (
        first.policy_profile_version_map_hash
    )
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 1


def test_active_replacement_cannot_reinterpret_existing_run_and_new_input_uses_it(
    db: Session, admin: User
) -> None:
    first_input = _input(db, admin, "first")
    active = _all_profiles(db, admin)
    db.commit()
    first_run, _ = admit_v7_diagnostic_run(
        db,
        owner_id=admin.id,
        decision_input_snapshot_id=first_input.id,
        now=NOW,
    )
    frozen_json = first_run.policy_profile_version_map_json
    frozen_hash = first_run.policy_profile_version_map_hash

    for old in active:
        old.state = "SUPERSEDED"
    db.flush()
    replacements = _all_profiles(db, admin, sequence=2)
    second_input = _input(db, admin, "second")
    db.commit()

    with pytest.raises(PolicyProfileError) as captured:
        admit_v7_diagnostic_run(
            db,
            owner_id=admin.id,
            decision_input_snapshot_id=first_input.id,
            now=NOW,
        )
    assert captured.value.code == "POLICY_PROFILE_VERSION_MAP_CONFLICT"
    db.refresh(first_run)
    assert first_run.policy_profile_version_map_json == frozen_json
    assert first_run.policy_profile_version_map_hash == frozen_hash

    second_run, created = admit_v7_diagnostic_run(
        db,
        owner_id=admin.id,
        decision_input_snapshot_id=second_input.id,
        now=NOW,
    )
    assert created is True
    second_ids = {
        item["configuration_version_id"]
        for item in json.loads(second_run.policy_profile_version_map_json or "{}")[
            "profiles"
        ]
    }
    assert second_ids == {profile.id for profile in replacements}


def test_historical_superseded_profiles_resolve_by_frozen_identity(
    db: Session, admin: User
) -> None:
    decision_input = _input(db, admin, "history")
    originals = _all_profiles(db, admin)
    db.commit()
    run, _ = admit_v7_diagnostic_run(
        db,
        owner_id=admin.id,
        decision_input_snapshot_id=decision_input.id,
        now=NOW,
    )
    for old in originals:
        old.state = "SUPERSEDED"
    db.flush()
    _all_profiles(db, admin, sequence=2)
    db.commit()

    resolved = resolve_run_policy_profiles(db, run_id=run.id)

    assert [profile.id for profile in resolved] == [
        next(item.id for item in originals if item.category == category)
        for category in POLICY_CATEGORIES
    ]
    assert all(profile.state == "SUPERSEDED" for profile in resolved)


@pytest.mark.parametrize("corruption", ("json", "hash"))
def test_stored_policy_map_mismatch_fails_closed(
    db: Session, admin: User, corruption: str
) -> None:
    decision_input = _input(db, admin, corruption)
    _all_profiles(db, admin)
    db.commit()
    run, _ = admit_v7_diagnostic_run(
        db,
        owner_id=admin.id,
        decision_input_snapshot_id=decision_input.id,
        now=NOW,
    )
    if corruption == "json":
        manifest = json.loads(run.policy_profile_version_map_json or "{}")
        manifest["profiles"][0]["sequence"] = 999
        run.policy_profile_version_map_json = canonical_context_json(manifest)
    else:
        run.policy_profile_version_map_hash = "0" * 64
    db.commit()

    with pytest.raises(PolicyProfileError) as captured:
        resolve_run_policy_profiles(db, run_id=run.id)
    assert captured.value.code == "POLICY_PROFILE_VERSION_MAP_CONFLICT"


def test_policy_admission_does_not_create_context_or_stages(
    db: Session, admin: User
) -> None:
    decision_input = _input(db, admin, "separation")
    _all_profiles(db, admin)
    db.commit()

    run, _ = admit_v7_diagnostic_run(
        db,
        owner_id=admin.id,
        decision_input_snapshot_id=decision_input.id,
        now=NOW,
    )

    assert run.decision_context is None
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(AgentStageRun)
        .where(AgentStageRun.run_id == run.id)
    ) == 0
