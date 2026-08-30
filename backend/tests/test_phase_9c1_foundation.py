from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.activation_gate import (
    ACTIVATION_CATEGORY,
    ACTIVATION_SCOPE,
    ACTIVATION_TARGET,
    REQUIRED_ACTIVATION_TEST_IDS,
    ActivationGateError,
    ActivationGatePayload,
    GateOutcome,
    activate_activation_gate,
    activation_digest,
    canonical_activation_json,
    create_activation_gate_draft,
    select_current_v7_entry_activation_gate,
    validate_activation_gate_draft,
    validate_activation_payload,
    verify_frozen_v7_entry_activation_gate,
    version_snapshot_hash,
)
from app.agents.contracts import ArbiterResult, EntryArbiterInputResult
from app.agents.decision_context import canonical_context_json, context_digest
from app.api.decisions import _response
from app.decision_contracts import (
    DecisionRepresentationError,
    validate_decision_representation,
)
from app.models import (
    AgentRun,
    AgentStageRun,
    ConfigurationVersion,
    Decision,
    DecisionInputSnapshot,
    MarketSnapshot,
    User,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
HASH = "a" * 64


def _snapshot_payload() -> dict[str, object]:
    policy_profiles = []
    for category, agent_type in (
        ("V7_ENTRY_POLICY_CONSERVATIVE", "CONSERVATIVE"),
        ("V7_ENTRY_POLICY_BALANCED", "BALANCED"),
        ("V7_ENTRY_POLICY_AGGRESSIVE", "AGGRESSIVE"),
    ):
        policy_profiles.append(
            {
                "configuration_version_id": str(uuid4()),
                "category": category,
                "sequence": 1,
                "agent_type": agent_type,
                "payload_hash": HASH,
            }
        )
    roles = (
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
        "CONSERVATIVE_DECISION",
        "BALANCED_DECISION",
        "AGGRESSIVE_DECISION",
    )
    routes = []
    for role in roles:
        is_decision = role.endswith("_DECISION")
        routes.append(
            {
                "role": role,
                "route_id": str(uuid4()),
                "route_version": 1,
                "route_version_hash": HASH,
                "model_id": str(uuid4()),
                "model_version": 1,
                "fallback_model_id": None,
                "fallback_model_version": None,
                "prompt_profile_id": str(uuid4()) if is_decision else None,
                "prompt_version": "v1",
                "prompt_content_hash": HASH if is_decision else None,
            }
        )
    return {
        "dag_version": "agent-dag-v7",
        "decision_context_schema_version": "decision-context-v1",
        "decision_agent_result_schema_version": "decision-agent-result-v1",
        "arbiter_result_schema_version": "entry-consensus-v1",
        "consensus_policy_version": "consensus-policy-v1",
        "policy_profiles": policy_profiles,
        "routes": routes,
    }


def _gate_payload(
    *, state: str = "OPEN"
) -> tuple[dict[str, object], dict[str, bytes]]:
    snapshot = _snapshot_payload()
    artifacts: dict[str, bytes] = {}
    evidence = []
    if state == "OPEN":
        for test_id in REQUIRED_ACTIVATION_TEST_IDS:
            reference = f"artifact:{test_id}"
            artifact = f"immutable report for {test_id}".encode()
            artifacts[reference] = artifact
            evidence.append(
                {
                    "test_id": test_id,
                    "requirement_ids": ["CFG-106"],
                    "result": "PASSED",
                    "code_revision": "revision-1",
                    "test_plan_version": "2026-08-27",
                    "spec_version": "phase-9b",
                    "executed_at": NOW - timedelta(minutes=10),
                    "valid_until": NOW + timedelta(hours=1),
                    "freshness_contract": None,
                    "evidence_ref": reference,
                    "evidence_hash": hashlib.sha256(artifact).hexdigest(),
                }
            )
    return (
        {
            "schema_version": "activation-gate-v1",
            "gate_state": state,
            "target": "MOCK",
            "version_snapshot": snapshot,
            "version_snapshot_hash": version_snapshot_hash(snapshot),
            "safety_evidence": evidence,
            "validation_policy_version": "activation-validation-policy-v1",
            "validated_at": NOW - timedelta(minutes=5),
            "valid_until": NOW + timedelta(hours=1),
        },
        artifacts,
    )


def test_activation_contract_hashes_and_strict_validation() -> None:
    payload, artifacts = _gate_payload()
    payload["version_snapshot"]["routes"].reverse()  # type: ignore[index,union-attr]
    payload["safety_evidence"].reverse()  # type: ignore[union-attr]
    gate = validate_activation_payload(
        payload, now=NOW, evidence_loader=artifacts.__getitem__
    )
    assert gate.gate_state == "OPEN"
    assert gate.version_snapshot_hash == version_snapshot_hash(gate.version_snapshot)
    assert activation_digest(canonical_activation_json(gate)) == activation_digest(gate)

    invalid = dict(payload)
    invalid["unknown"] = True
    with pytest.raises(ActivationGateError):
        validate_activation_payload(invalid, now=NOW, evidence_loader=artifacts.__getitem__)

    invalid_hash = dict(payload)
    invalid_hash["version_snapshot_hash"] = "b" * 64
    with pytest.raises(ActivationGateError):
        validate_activation_payload(
            invalid_hash, now=NOW, evidence_loader=artifacts.__getitem__
        )

    incomplete = dict(payload)
    incomplete["safety_evidence"] = list(payload["safety_evidence"])[1:]
    with pytest.raises(ActivationGateError):
        validate_activation_payload(
            incomplete, now=NOW, evidence_loader=artifacts.__getitem__
        )

    failed = dict(payload)
    failed_items = [dict(item) for item in payload["safety_evidence"]]
    failed_items[0]["result"] = "FAILED"
    failed["safety_evidence"] = failed_items
    with pytest.raises(ActivationGateError):
        validate_activation_payload(failed, now=NOW, evidence_loader=artifacts.__getitem__)

    expired = dict(payload)
    expired["valid_until"] = NOW
    with pytest.raises(ActivationGateError):
        validate_activation_payload(
            expired, now=NOW, evidence_loader=artifacts.__getitem__
        )

    artifacts[next(iter(artifacts))] = b"tampered"
    with pytest.raises(ActivationGateError):
        validate_activation_payload(payload, now=NOW, evidence_loader=artifacts.__getitem__)


def test_activation_contract_closed_and_invalid_shapes() -> None:
    payload, _ = _gate_payload(state="CLOSED")
    gate = validate_activation_payload(payload, now=NOW, evidence_loader=None)
    assert gate.gate_state == "CLOSED"

    with pytest.raises(ValidationError):
        ActivationGatePayload.model_validate({**payload, "target": "LIVE"})
    with pytest.raises(ValidationError):
        ActivationGatePayload.model_validate({key: value for key, value in payload.items() if key != "target"})
    with pytest.raises(ValidationError):
        ActivationGatePayload.model_validate({**payload, "safety_passed": True})
    with pytest.raises(ValidationError):
        ActivationGatePayload.model_validate(
            {**payload, "validated_at": NOW + timedelta(hours=2)}
        )


def test_activation_control_plane_selector_and_supersession(
    db: Session, admin: User
) -> None:
    payload, artifacts = _gate_payload()
    loader = artifacts.__getitem__
    version = create_activation_gate_draft(
        db,
        user=admin,
        payload=payload,
        reason="Phase 9C.1 acceptance",
        now=NOW,
        evidence_loader=loader,
        snapshot_verifier=lambda _snapshot: None,
    )
    assert (version.scope, version.target_id, version.category) == (
        ACTIVATION_SCOPE,
        ACTIVATION_TARGET,
        ACTIVATION_CATEGORY,
    )
    validate_activation_gate_draft(
        db,
        version_id=version.id,
        now=NOW,
        evidence_loader=loader,
        snapshot_verifier=lambda _snapshot: None,
    )
    activate_activation_gate(
        db,
        user=admin,
        version_id=version.id,
        now=NOW,
        evidence_loader=loader,
        correlation_id=str(uuid4()),
        request_ip="test",
        user_agent="pytest",
        snapshot_verifier=lambda _snapshot: None,
    )
    current = select_current_v7_entry_activation_gate(
        db,
        now=NOW,
        evidence_loader=loader,
        snapshot_verifier=lambda _snapshot: None,
    )
    assert current.outcome == GateOutcome.PASS
    assert current.version is not None
    assert verify_frozen_v7_entry_activation_gate(
        db,
        frozen_version_id=version.id,
        frozen_payload_hash=version.payload_hash,
        now=NOW,
        evidence_loader=loader,
        snapshot_verifier=lambda _snapshot: None,
    ).outcome == GateOutcome.PASS
    assert verify_frozen_v7_entry_activation_gate(
        db,
        frozen_version_id=str(uuid4()),
        frozen_payload_hash="b" * 64,
        now=NOW,
        evidence_loader=loader,
        snapshot_verifier=lambda _snapshot: None,
    ).outcome == GateOutcome.SUPERSEDED


def test_activation_control_plane_rejects_unknown_target_versions(
    db: Session, admin: User
) -> None:
    payload, artifacts = _gate_payload()
    with pytest.raises(ActivationGateError):
        create_activation_gate_draft(
            db,
            user=admin,
            payload=payload,
            reason="unknown target versions",
            now=NOW,
            evidence_loader=artifacts.__getitem__,
        )
    assert db.query(ConfigurationVersion).count() == 0


def test_gate_selector_closed_malformed_and_expired(db: Session, admin: User) -> None:
    assert select_current_v7_entry_activation_gate(
        db, now=NOW, evidence_loader=None
    ).outcome == GateOutcome.CLOSED

    payload, _ = _gate_payload(state="CLOSED")
    gate = ActivationGatePayload.model_validate(payload)
    version = ConfigurationVersion(
        scope=ACTIVATION_SCOPE,
        target_id=ACTIVATION_TARGET,
        category=ACTIVATION_CATEGORY,
        sequence=1,
        state="ACTIVE",
        payload_json=canonical_activation_json(gate),
        payload_hash=activation_digest(gate),
        reason="closed",
        created_by=admin.id,
        validated_at=NOW,
        activated_at=NOW,
    )
    db.add(version)
    db.commit()
    assert select_current_v7_entry_activation_gate(
        db, now=NOW, evidence_loader=None
    ).outcome == GateOutcome.CLOSED
    version.payload_hash = "b" * 64
    db.commit()
    assert select_current_v7_entry_activation_gate(
        db, now=NOW, evidence_loader=None
    ).outcome == GateOutcome.INVALID


def test_gate_selector_rejects_active_ambiguity_and_classifies_db_failure() -> None:
    ambiguous = Mock(spec=Session)
    ambiguous.scalars.return_value = [Mock(), Mock()]
    assert select_current_v7_entry_activation_gate(
        ambiguous, now=NOW, evidence_loader=None
    ).outcome == GateOutcome.INVALID

    unavailable = Mock(spec=Session)
    unavailable.scalars.side_effect = SQLAlchemyError("database unavailable")
    assert select_current_v7_entry_activation_gate(
        unavailable, now=NOW, evidence_loader=None
    ).outcome == GateOutcome.DB_RETRYABLE_FAILURE


def _market(db: Session) -> MarketSnapshot:
    value = MarketSnapshot(
        symbol="005930",
        market="KRX",
        source="TEST",
        sequence_or_hash=str(uuid4()),
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
    db.add(value)
    db.flush()
    return value


def test_sourced_decision_representation_and_incomplete_api_lineage_fails_closed(
    db: Session, admin: User
) -> None:
    market = _market(db)
    decision_input = DecisionInputSnapshot(
        user_id=admin.id,
        purpose="TRADING",
        schema_version="scout-input-v2",
        market="KRX",
        symbol="005930",
        market_snapshot_id=market.id,
        observed_at=NOW,
        data_quality="NORMAL",
        session_state="CONTINUOUS",
        input_json="{}",
        input_hash=HASH,
    )
    run = AgentRun(
        owner_id=admin.id,
        purpose="TRADING",
        execution_stage="SHADOW",
        market="KRX",
        symbol="005930",
        market_snapshot_id=market.id,
        input_hash=HASH,
        dag_version="agent-dag-v7",
        route_versions_json="{}",
        idempotency_key=str(uuid4()),
        state="RUNNING",
        analysis_context="ENTRY",
        valid_until=NOW + timedelta(hours=1),
    )
    db.add_all([decision_input, run])
    db.flush()
    roles = (
        ("CONSERVATIVE_DECISION", "CONSERVATIVE"),
        ("BALANCED_DECISION", "BALANCED"),
        ("AGGRESSIVE_DECISION", "AGGRESSIVE"),
    )
    input_results = [
        EntryArbiterInputResult(
            role=role,
            agent_type=agent_type,
            stage_run_id=str(uuid4()),
            output_hash=HASH,
            status="SUCCEEDED",
            action="WAIT",
        )
        for role, agent_type in roles
    ]
    arbiter = ArbiterResult(
        decision_context_id=str(uuid4()),
        decision_context_hash=HASH,
        action="WAIT",
        input_result_ids=[item.stage_run_id for item in input_results],
        input_results=input_results,
        decision_pattern="DEFAULT_WAIT",
        reason_codes=["ARBITER_DEFAULT_WAIT"],
        valid_until=(NOW + timedelta(hours=1)).isoformat(),
    )
    output_json = canonical_context_json(arbiter.model_dump(mode="json"))
    stage = AgentStageRun(
        run_id=run.id,
        role="ENTRY_ARBITER",
        sequence=80,
        dependency_roles_json="[]",
        state="SUCCEEDED",
        input_hash=HASH,
        output_json=output_json,
        output_hash=context_digest(output_json),
    )
    db.add(stage)
    db.flush()
    decision = Decision(
        decision_input_id=decision_input.id,
        purpose="TRADING",
        evaluation_request_id=f"v7fin-{uuid4().hex}",
        input_snapshot_id=market.id,
        symbol="005930",
        market="KRX",
        decision_kind="ENTRY",
        model_provider=None,
        model_id=None,
        prompt_version=None,
        schema_version="sourced-entry-decision-v1",
        scout_output_json=None,
        core_output_json=None,
        action="WAIT",
        confidence=None,
        risk_level=None,
        reason_codes_json='["ARBITER_DEFAULT_WAIT"]',
        valid_until=NOW + timedelta(hours=1),
        configuration_version_id=None,
        execution_mode=None,
        execution_outcome=None,
        validation_status="VALID",
        latency_ms=None,
        source_agent_run_id=run.id,
        source_stage_run_id=stage.id,
        source_stage_output_hash=stage.output_hash,
    )
    assert validate_decision_representation(decision) == "SOURCED_V7"
    db.add(decision)
    db.commit()
    with pytest.raises(
        DecisionRepresentationError, match="SOURCED_DECISION_LINEAGE_INVALID"
    ):
        _response(str(uuid4()), decision, db)


def test_decision_discriminator_and_legacy_contract_fail_closed() -> None:
    base = {
        "schema_version": "sourced-entry-decision-v1",
        "source_agent_run_id": None,
        "source_stage_run_id": None,
        "source_stage_output_hash": None,
        "model_provider": "SERVER",
        "model_id": "legacy",
        "prompt_version": "v1",
        "scout_output_json": "{}",
        "core_output_json": "{}",
        "confidence": Decimal("0.5"),
        "risk_level": "LOW",
        "latency_ms": 1,
        "execution_outcome": "NO_ACTION",
    }
    with pytest.raises(DecisionRepresentationError):
        validate_decision_representation(base)

    base["schema_version"] = "1.0"
    base["confidence"] = None
    with pytest.raises(DecisionRepresentationError):
        validate_decision_representation(base)
