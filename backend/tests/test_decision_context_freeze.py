from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.decision_context import (
    CANDIDATE_AUDIT_ROLE,
    DECISION_AGENT_ROLES,
    SCOUT_ROLES,
    DecisionContextFreezeError,
    canonical_context_json,
    context_digest,
    freeze_decision_context,
)
from app.agents.worker import claim_next_stage
from app.ids import uuid7
from app.models import (
    AgentRun,
    AgentStageRun,
    DecisionContext,
    DecisionInputSnapshot,
    EvidenceBundle,
    LlmInvocation,
    MarketContextSnapshot,
    MarketSnapshot,
    User,
)

NOW = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
HASH = "a" * 64
POLICY_MARKER = "POLICY_PROFILE_MUST_NOT_ENTER_CONTEXT"


def _stage(
    db: Session,
    run: AgentRun,
    *,
    role: str,
    sequence: int,
    state: str,
    schema_version: str,
    valid_until: datetime | None = None,
    extra: dict[str, object] | None = None,
) -> AgentStageRun:
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "stage_run_id": "pending",
        "role": role,
        "status": state,
    }
    if valid_until is not None:
        payload.update(
            {
                "observed_at": NOW.isoformat(),
                "valid_until": valid_until.isoformat(),
            }
        )
    if extra:
        payload.update(extra)
    stage = AgentStageRun(
        run_id=run.id,
        role=role,
        sequence=sequence,
        dependency_roles_json="[]",
        state=state,
        input_hash=HASH,
        output_json="{}",
        output_hash=HASH,
        completed_at=NOW,
    )
    db.add(stage)
    db.flush()
    payload["stage_run_id"] = stage.id
    encoded = canonical_context_json(payload)
    stage.output_json = encoded
    stage.output_hash = context_digest(encoded)
    db.flush()
    return stage


def _prepared_run(
    db: Session,
    *,
    position_state: str = "SUCCEEDED",
    include_market_context: bool = False,
    run_valid_seconds: int = 120,
    input_valid_seconds: int = 110,
    verifier_valid_seconds: int = 100,
    scout_valid_seconds: int = 90,
) -> dict[str, object]:
    suffix = uuid7()
    owner = User(login_id=f"v7-{suffix}", password_hash="unused")
    db.add(owner)
    db.flush()
    snapshot = MarketSnapshot(
        symbol="005930",
        market="KRX",
        source="TEST",
        sequence_or_hash=suffix,
        payload_hash=HASH,
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

    input_payload = {
        "configuration_version": {"id": "shared-config-v1", "payload_hash": HASH},
        "market_snapshot_id": snapshot.id,
        "schema_version": "scout-input-v2",
        "valid_until": (NOW + timedelta(seconds=input_valid_seconds)).isoformat(),
    }
    input_json = canonical_context_json(input_payload)
    input_hash = context_digest(input_json)
    decision_input = DecisionInputSnapshot(
        user_id=owner.id,
        purpose="DIAGNOSTIC",
        schema_version="scout-input-v2",
        market="KRX",
        symbol="005930",
        market_snapshot_id=snapshot.id,
        observed_at=NOW,
        data_quality="NORMAL",
        session_state="OPEN",
        input_json=input_json,
        input_hash=input_hash,
    )
    db.add(decision_input)
    db.flush()

    policy_json = canonical_context_json(
        {"CONSERVATIVE": {"marker": POLICY_MARKER}}
    )
    run = AgentRun(
        owner_id=owner.id,
        purpose="DIAGNOSTIC",
        execution_stage="SHADOW",
        market="KRX",
        symbol="005930",
        market_snapshot_id=snapshot.id,
        input_hash=input_hash,
        dag_version="agent-dag-v7",
        route_versions_json=canonical_context_json(
            {role: {"route_version": "v7-test"} for role in SCOUT_ROLES}
        ),
        policy_profile_version_map_json=policy_json,
        policy_profile_version_map_hash=context_digest(policy_json),
        idempotency_key=f"v7-{suffix}",
        state="RUNNING",
        analysis_context="ENTRY",
        server_input_policy_version="agent-server-input-v1",
        valid_until=NOW + timedelta(seconds=run_valid_seconds),
        started_at=NOW,
    )
    db.add(run)
    db.flush()

    bundle_record = {
        "schema_version": "evidence-bundle-v1",
        "market": run.market,
        "symbol": run.symbol,
        "market_snapshot_id": run.market_snapshot_id,
        "policy_version": "official-primary-secondary-v3",
        "state": "VERIFIED",
        "evidence_ids": [],
        "stale_evidence_ids": [],
        "reason_codes": [],
    }
    bundle = EvidenceBundle(
        owner_id=owner.id,
        run_id=run.id,
        market=run.market,
        symbol=run.symbol,
        as_of=NOW,
        policy_version="official-primary-secondary-v3",
        state="VERIFIED",
        evidence_ids_json="[]",
        contradiction_groups_json="[]",
        stale_evidence_ids_json="[]",
        reason_codes_json="[]",
        bundle_hash=context_digest(canonical_context_json(bundle_record)),
    )
    db.add(bundle)
    db.flush()

    verifier = _stage(
        db,
        run,
        role="EVIDENCE_VERIFIER",
        sequence=20,
        state="SUCCEEDED",
        schema_version="evidence-verifier-v1",
        valid_until=NOW + timedelta(seconds=verifier_valid_seconds),
        extra={"bundle_id": bundle.id, "bundle_hash": bundle.bundle_hash},
    )
    stages: dict[str, AgentStageRun] = {}
    for sequence, role in enumerate(SCOUT_ROLES, start=30):
        state = position_state if role == "POSITION_RISK_SCOUT" else "SUCCEEDED"
        stages[role] = _stage(
            db,
            run,
            role=role,
            sequence=sequence,
            state=state,
            schema_version="agent-assessment-v2",
            valid_until=NOW + timedelta(seconds=scout_valid_seconds),
        )
    stages[CANDIDATE_AUDIT_ROLE] = _stage(
        db,
        run,
        role=CANDIDATE_AUDIT_ROLE,
        sequence=65,
        state="SUCCEEDED",
        schema_version="evidence-candidate-audit-v1",
        extra={"candidate_count": 0},
    )

    market_context = None
    if include_market_context:
        market_payload = {
            "market": run.market,
            "schema_version": "market-context-v1",
            "symbol": run.symbol,
        }
        market_json = canonical_context_json(market_payload)
        market_context = MarketContextSnapshot(
            market=run.market,
            symbol=run.symbol,
            source="TEST",
            source_ref=suffix,
            source_tier="PRIMARY",
            quality="NORMAL",
            payload_json=market_json,
            payload_hash=context_digest(market_json),
            observed_at=NOW,
            received_at=NOW,
            valid_until=NOW + timedelta(seconds=80),
        )
        db.add(market_context)
        db.flush()
        run.market_context_snapshot_id = market_context.id
        run.market_context_snapshot_hash = market_context.payload_hash

    db.commit()
    return {
        "owner": owner,
        "snapshot": snapshot,
        "decision_input": decision_input,
        "run": run,
        "bundle": bundle,
        "verifier": verifier,
        "stages": stages,
        "market_context": market_context,
    }


def _expect_freeze_error(db: Session, run: AgentRun, code: str) -> None:
    with pytest.raises(DecisionContextFreezeError) as exc_info:
        freeze_decision_context(db, run_id=run.id, now=NOW)
    assert exc_info.value.code == code
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0


def test_freeze_is_canonical_idempotent_and_uses_earliest_validity(db: Session) -> None:
    fixture = _prepared_run(db, include_market_context=True)
    run = fixture["run"]
    assert isinstance(run, AgentRun)
    stage_count_before = db.scalar(select(func.count()).select_from(AgentStageRun))

    first = freeze_decision_context(db, run_id=run.id, now=NOW)
    first_frozen_at = first.frozen_at
    second = freeze_decision_context(db, run_id=run.id, now=NOW + timedelta(seconds=1))

    assert second.id == first.id
    assert second.context_hash == first.context_hash
    assert second.frozen_at == first_frozen_at
    assert second.valid_until.replace(tzinfo=UTC) == NOW + timedelta(seconds=80)
    assert context_digest(second.manifest_json) == second.context_hash
    assert canonical_context_json(json.loads(second.manifest_json)) == second.manifest_json
    assert POLICY_MARKER not in second.manifest_json
    assert POLICY_MARKER not in second.configuration_provenance_json
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 1
    assert db.scalar(select(func.count()).select_from(AgentStageRun)) == stage_count_before
    assert db.scalar(select(func.count()).select_from(LlmInvocation)) == 0
    manifest = json.loads(second.manifest_json)
    assert [item["role"] for item in manifest["scouts"]] == list(SCOUT_ROLES)
    assert manifest["market_context"]["id"] == fixture["market_context"].id


def test_freeze_conflict_does_not_update_existing_context(db: Session) -> None:
    fixture = _prepared_run(db)
    run = fixture["run"]
    assert isinstance(run, AgentRun)
    context = freeze_decision_context(db, run_id=run.id, now=NOW)
    immutable_before = (
        context.id,
        context.manifest_json,
        context.context_hash,
        context.frozen_at,
        context.valid_until,
    )
    technical = fixture["stages"]["TECHNICAL_SCOUT"]
    payload = json.loads(technical.output_json)
    payload["test_change"] = True
    technical.output_json = canonical_context_json(payload)
    technical.output_hash = context_digest(technical.output_json)
    db.commit()

    with pytest.raises(DecisionContextFreezeError) as exc_info:
        freeze_decision_context(db, run_id=run.id, now=NOW + timedelta(seconds=1))
    assert exc_info.value.code == "DECISION_CONTEXT_FREEZE_CONFLICT"
    db.refresh(context)
    assert (
        context.id,
        context.manifest_json,
        context.context_hash,
        context.frozen_at,
        context.valid_until,
    ) == immutable_before


@pytest.mark.parametrize("mixed_object", ["stage", "bundle"])
def test_freeze_rejects_cross_run_lineage(db: Session, mixed_object: str) -> None:
    fixture = _prepared_run(db)
    run = fixture["run"]
    assert isinstance(run, AgentRun)
    other = AgentRun(
        owner_id=run.owner_id,
        purpose="DIAGNOSTIC",
        execution_stage="SHADOW",
        market=run.market,
        symbol=run.symbol,
        market_snapshot_id=run.market_snapshot_id,
        input_hash=run.input_hash,
        dag_version="agent-dag-v7",
        route_versions_json="{}",
        idempotency_key=f"other-{uuid7()}",
        state="RUNNING",
        analysis_context="ENTRY",
        valid_until=run.valid_until,
    )
    db.add(other)
    db.flush()
    if mixed_object == "stage":
        fixture["stages"]["TECHNICAL_SCOUT"].run_id = other.id
        expected = "DECISION_CONTEXT_REQUIRED_STAGE_NOT_FOUND"
    else:
        fixture["bundle"].run_id = other.id
        expected = "DECISION_CONTEXT_EVIDENCE_BUNDLE_NOT_FOUND"
    db.commit()

    _expect_freeze_error(db, run, expected)


def test_position_risk_not_applicable_is_explicitly_allowed(db: Session) -> None:
    fixture = _prepared_run(db, position_state="NOT_APPLICABLE")
    run = fixture["run"]
    assert isinstance(run, AgentRun)

    context = freeze_decision_context(db, run_id=run.id, now=NOW)
    manifest = json.loads(context.manifest_json)
    position = next(
        item for item in manifest["scouts"] if item["role"] == "POSITION_RISK_SCOUT"
    )
    assert position["state"] == "NOT_APPLICABLE"
    assert position["status"] == "NOT_APPLICABLE"


def test_diagnostic_run_cannot_be_promoted_to_trading_without_gate_provenance(
    db: Session,
) -> None:
    fixture = _prepared_run(db)
    run = fixture["run"]
    decision_input = fixture["decision_input"]
    assert isinstance(run, AgentRun)
    assert isinstance(decision_input, DecisionInputSnapshot)
    run.purpose = "TRADING"
    decision_input.purpose = "TRADING"
    db.commit()

    _expect_freeze_error(
        db,
        run,
        "DECISION_CONTEXT_ACTIVATION_PROVENANCE_INVALID",
    )
    db.refresh(run)
    assert run.purpose == "TRADING"
    assert db.scalar(
        select(func.count()).select_from(DecisionContext).where(DecisionContext.run_id == run.id)
    ) == 0


def test_missing_position_risk_stage_is_not_not_applicable(db: Session) -> None:
    fixture = _prepared_run(db)
    run = fixture["run"]
    assert isinstance(run, AgentRun)
    db.delete(fixture["stages"]["POSITION_RISK_SCOUT"])
    db.commit()

    _expect_freeze_error(db, run, "DECISION_CONTEXT_REQUIRED_STAGE_NOT_FOUND")


def test_legacy_and_expired_v7_runs_are_rejected(db: Session) -> None:
    fixture = _prepared_run(db)
    run = fixture["run"]
    assert isinstance(run, AgentRun)
    run.dag_version = "agent-dag-v6"
    db.commit()
    _expect_freeze_error(db, run, "DECISION_CONTEXT_RUN_NOT_ELIGIBLE")

    fixture = _prepared_run(db, run_valid_seconds=-1)
    expired = fixture["run"]
    assert isinstance(expired, AgentRun)
    _expect_freeze_error(db, expired, "DECISION_CONTEXT_RUN_EXPIRED")


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing_bundle", "DECISION_CONTEXT_EVIDENCE_BUNDLE_NOT_FOUND"),
        ("missing_scout", "DECISION_CONTEXT_REQUIRED_STAGE_NOT_FOUND"),
        ("missing_hash", "DECISION_CONTEXT_STAGE_OUTPUT_MISSING"),
        ("missing_audit", "DECISION_CONTEXT_REQUIRED_STAGE_NOT_FOUND"),
    ],
)
def test_freeze_rejects_missing_required_lineage(
    db: Session, mutation: str, expected: str
) -> None:
    fixture = _prepared_run(db)
    run = fixture["run"]
    assert isinstance(run, AgentRun)
    if mutation == "missing_bundle":
        db.delete(fixture["bundle"])
    elif mutation == "missing_scout":
        db.delete(fixture["stages"]["NEWS_DISCLOSURE_SCOUT"])
    elif mutation == "missing_hash":
        fixture["stages"]["MARKET_SECTOR_SCOUT"].output_hash = None
    else:
        db.delete(fixture["stages"][CANDIDATE_AUDIT_ROLE])
    db.commit()

    _expect_freeze_error(db, run, expected)


def test_expired_required_source_is_rejected(db: Session) -> None:
    fixture = _prepared_run(db, input_valid_seconds=-1)
    run = fixture["run"]
    assert isinstance(run, AgentRun)

    _expect_freeze_error(db, run, "DECISION_CONTEXT_SOURCE_EXPIRED")


@pytest.mark.parametrize("role", sorted(DECISION_AGENT_ROLES))
def test_decision_agent_claim_requires_committed_valid_context(
    db: Session, role: str
) -> None:
    fixture = _prepared_run(db)
    run = fixture["run"]
    assert isinstance(run, AgentRun)
    decision_stage = AgentStageRun(
        run_id=run.id,
        role=role,
        sequence=70,
        dependency_roles_json=canonical_context_json(list(SCOUT_ROLES)),
        state="PENDING",
        input_hash=HASH,
        available_at=NOW,
    )
    db.add(decision_stage)
    db.commit()

    assert claim_next_stage(
        db, worker_id="worker-before", lease_seconds=30, now=NOW
    ) is None
    db.refresh(decision_stage)
    assert decision_stage.state == "PENDING"

    context = freeze_decision_context(db, run_id=run.id, now=NOW)
    assert context.id is not None
    claim = claim_next_stage(
        db, worker_id="worker-after", lease_seconds=30, now=NOW
    )
    assert claim is not None
    assert claim.stage_id == decision_stage.id
    assert db.scalar(select(func.count()).select_from(LlmInvocation)) == 0


def test_entry_arbiter_requires_phase8_integrity_gate(
    db: Session,
) -> None:
    fixture = _prepared_run(db)
    run = fixture["run"]
    assert isinstance(run, AgentRun)
    arbiter_stage = AgentStageRun(
        run_id=run.id,
        role="ENTRY_ARBITER",
        sequence=80,
        dependency_roles_json="[]",
        state="PENDING",
        input_hash=HASH,
        available_at=NOW,
    )
    db.add(arbiter_stage)
    db.commit()

    claim = claim_next_stage(
        db, worker_id="arbiter-worker", lease_seconds=30, now=NOW
    )

    assert claim is None
    db.refresh(arbiter_stage)
    assert arbiter_stage.state == "CONFLICTED"
    assert arbiter_stage.error_code == "ENTRY_ARBITER_STAGE_INVALID"
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0


@pytest.mark.parametrize("role", ("FINALIZER", "DECISION_FINALIZER"))
def test_finalizer_is_not_an_agent_stage_role(db: Session, role: str) -> None:
    fixture = _prepared_run(db)
    run = fixture["run"]
    assert isinstance(run, AgentRun)
    db.add(
        AgentStageRun(
            run_id=run.id,
            role=role,
            sequence=90,
            dependency_roles_json="[]",
            state="PENDING",
            input_hash=HASH,
            available_at=NOW,
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
