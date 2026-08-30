from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.contracts import (
    DecisionAgentInput,
    DecisionAgentModelOutput,
    DecisionAgentResult,
    DecisionPolicyParameters,
    validate_decision_evidence_refs,
)
from app.agents.decision_agents import (
    DECISION_AGENT_ROLES,
    build_decision_agent_input,
    decision_agent_input_hash,
    materialize_decision_agent_stages,
)
from app.agents.decision_context import canonical_context_json, context_digest
from app.agents.worker import (
    claim_next_stage,
    process_agent_work_once,
    reconcile_v7_decision_stages,
    recover_expired_stages,
)
from app.llm.registry import provider_registry
from app.models import (
    AgentStageRun,
    Approval,
    Decision,
    LlmInvocation,
    LlmModelProfile,
    LlmPromptProfile,
    LlmRoleRoute,
    OrderIntent,
    TradingOrder,
    User,
)
from tests.test_v7_decision_agent_execution import (
    DecisionFixtureAdapter,
    _results,
    _run_decisions,
    _valid_output,
)
from tests.test_v7_decision_agent_foundation import _complete_upstream
from tests.test_v7_upstream_runtime import _admit


def _ready(client, db: Session, admin: User, monkeypatch):
    run, _, settings = _admit(client, db, admin, monkeypatch)
    context = _complete_upstream(db, run, monkeypatch, settings)
    materialize_decision_agent_stages(db, run_id=run.id, now=datetime.now(UTC))
    return run, context


def _non_success(status: str) -> dict[str, object]:
    return {
        "schema_version": "decision-agent-model-output-v1",
        "status": status,
        "action": "UNKNOWN",
        "confidence": 0.0,
        "entry_score": None,
        "risk_score": None,
        "reason_codes": [
            "EVIDENCE_INSUFFICIENT"
            if status == "INSUFFICIENT_DATA"
            else "SCOUT_SIGNALS_CONFLICTED"
        ],
        "positive_evidence_refs": [],
        "negative_evidence_refs": [],
    }


def _assert_no_authority(db: Session, run_id: str) -> None:
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(AgentStageRun)
        .where(AgentStageRun.run_id == run_id, AgentStageRun.role == "ENTRY_ARBITER")
    ) == 0


def test_full_e2e_has_shared_context_and_arbiter_ready_lineage(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, context = _ready(client, db, admin, monkeypatch)
    adapter = DecisionFixtureAdapter(db)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)

    _run_decisions(db)

    results = _results(db, run.id)
    payloads = [
        DecisionAgentInput.model_validate_json(request.messages[-1]["content"])
        for request in adapter.requests
    ]
    context_materials = {
        canonical_context_json(payload.decision_context.model_dump(mode="json"))
        for payload in payloads
    }
    assert len(context_materials) == 1
    assert {item.decision_context_id for item in results.values()} == {context.id}
    assert {item.decision_context_hash for item in results.values()} == {
        context.context_hash
    }
    assert {item.agent_type for item in results.values()} == {
        "CONSERVATIVE",
        "BALANCED",
        "AGGRESSIVE",
    }
    stages = list(
        db.scalars(
            select(AgentStageRun).where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role.in_(DECISION_AGENT_ROLES),
            )
        )
    )
    assert len(stages) == 3
    for stage in stages:
        result = results[stage.role]
        assert result.stage_run_id == stage.id
        assert stage.output_hash == hashlib.sha256(stage.output_json.encode()).hexdigest()
        assert all(
            getattr(result, field)
            for field in (
                "policy_profile_id",
                "policy_profile_hash",
                "route_id",
                "route_version_hash",
                "prompt_profile_id",
                "prompt_hash",
                "requested_model_profile_id",
                "actual_provider",
                "actual_model",
                "valid_until",
            )
        )
    assert len({payload.policy_profile.configuration_version_id for payload in payloads}) == 3
    assert all(request.tool_policy == "NONE" and not request.allowed_tools for request in adapter.requests)
    _assert_no_authority(db, run.id)


@pytest.mark.parametrize(
    "actions",
    [
        ("BUY", "BUY", "BUY"),
        ("WAIT", "BUY", "BUY"),
        ("REJECT", "WAIT", "BUY"),
        ("BUY", "WAIT", "REJECT"),
    ],
)
def test_production_success_matrix(
    client, db: Session, admin: User, monkeypatch, actions: tuple[str, str, str]
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    adapter = DecisionFixtureAdapter(
        db,
        outputs={role: _valid_output(action) for role, action in zip(DECISION_AGENT_ROLES, actions)},
    )
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    _run_decisions(db)
    results = _results(db, run.id)
    assert tuple(results[role].action for role in DECISION_AGENT_ROLES) == actions
    assert all(results[role].status == "SUCCEEDED" for role in DECISION_AGENT_ROLES)
    _assert_no_authority(db, run.id)


@pytest.mark.parametrize(
    "outputs",
    [
        {"BALANCED_DECISION": "TIMEOUT"},
        {"CONSERVATIVE_DECISION": _valid_output("REJECT")},
        {
            "CONSERVATIVE_DECISION": {
                **_valid_output(),
                "action": "UNKNOWN",
            },
            "BALANCED_DECISION": _valid_output("WAIT"),
        },
    ],
)
def test_mixed_results_preserve_all_three_outputs_without_consensus(
    client, db: Session, admin: User, monkeypatch, outputs
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    adapter = DecisionFixtureAdapter(db, outputs=outputs)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    _run_decisions(db)
    results = _results(db, run.id)
    assert set(results) == set(DECISION_AGENT_ROLES)
    stages = list(
        db.scalars(
            select(AgentStageRun).where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role.in_(DECISION_AGENT_ROLES),
            )
        )
    )
    assert all(stage.output_json and stage.output_hash for stage in stages)
    _assert_no_authority(db, run.id)


@pytest.mark.parametrize(
    ("provider_output", "expected_status"),
    [
        (_non_success("INSUFFICIENT_DATA"), "INSUFFICIENT_DATA"),
        ("TIMEOUT", "TIMED_OUT"),
        ("ERROR", "FAILED"),
        ({**_valid_output(), "action": "UNKNOWN"}, "INVALID_OUTPUT"),
        (_non_success("CONFLICTED"), "CONFLICTED"),
    ],
)
def test_each_role_failure_matrix_is_structured(
    client, db: Session, admin: User, monkeypatch, provider_output, expected_status
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    adapter = DecisionFixtureAdapter(
        db, outputs={role: provider_output for role in DECISION_AGENT_ROLES}
    )
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    _run_decisions(db)
    results = _results(db, run.id)
    assert {item.status for item in results.values()} == {expected_status}
    assert all(
        item.action == "UNKNOWN"
        and item.confidence == 0
        and item.entry_score is None
        and item.risk_score is None
        for item in results.values()
    )
    _assert_no_authority(db, run.id)


def test_parallel_readiness_order_independence_and_result_immutability(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, context = _ready(client, db, admin, monkeypatch)
    stages = {
        stage.role: stage
        for stage in db.scalars(
            select(AgentStageRun).where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role.in_(DECISION_AGENT_ROLES),
            )
        )
    }
    assert all(json.loads(stage.dependency_roles_json) == ["EVIDENCE_CANDIDATE_AUDITOR"] for stage in stages.values())
    before_hashes = {
        role: decision_agent_input_hash(
            build_decision_agent_input(
                db, run=run, context=context, role=role, now=datetime.now(UTC)
            )
        )
        for role in DECISION_AGENT_ROLES
    }
    future = datetime.now(UTC) + timedelta(minutes=5)
    stages["CONSERVATIVE_DECISION"].available_at = future
    stages["BALANCED_DECISION"].available_at = future
    db.commit()
    adapter = DecisionFixtureAdapter(db)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    assert process_agent_work_once(db, worker_id="order-a", lease_seconds=60, now=datetime.now(UTC))
    stages["CONSERVATIVE_DECISION"].available_at = datetime.now(UTC)
    db.commit()
    assert process_agent_work_once(db, worker_id="order-c", lease_seconds=60, now=datetime.now(UTC))
    stages["BALANCED_DECISION"].available_at = datetime.now(UTC)
    db.commit()
    assert process_agent_work_once(db, worker_id="order-b", lease_seconds=60, now=datetime.now(UTC))
    assert [request.role for request in adapter.requests] == [
        "AGGRESSIVE_DECISION",
        "CONSERVATIVE_DECISION",
        "BALANCED_DECISION",
    ]
    after_hashes = {
        role: decision_agent_input_hash(
            build_decision_agent_input(
                db, run=run, context=context, role=role, now=datetime.now(UTC)
            )
        )
        for role in DECISION_AGENT_ROLES
    }
    assert before_hashes == after_hashes
    stored = {role: (stage.output_json, stage.output_hash) for role, stage in stages.items()}
    assert reconcile_v7_decision_stages(db, now=datetime.now(UTC), run_id=run.id) == 0
    assert materialize_decision_agent_stages(db, run_id=run.id, now=datetime.now(UTC))
    db.expire_all()
    assert stored == {
        role: (db.get(AgentStageRun, stage.id).output_json, db.get(AgentStageRun, stage.id).output_hash)
        for role, stage in stages.items()
    }


def test_expired_ambiguous_claim_recovery_stores_one_structured_result(
    client, db: Session, admin: User, monkeypatch
) -> None:
    _run, _ = _ready(client, db, admin, monkeypatch)
    claimed_at = datetime.now(UTC)
    claim = claim_next_stage(
        db, worker_id="lost-worker", lease_seconds=1, now=claimed_at
    )
    assert claim is not None
    stage = db.get(AgentStageRun, claim.stage_id)
    assert stage is not None
    route = db.get(LlmRoleRoute, stage.route_id)
    model = db.get(LlmModelProfile, route.primary_model_profile_id)
    assert route is not None and model is not None
    invocation = LlmInvocation(
        stage_run_id=stage.id,
        requested_provider_profile_id=model.provider_profile_id,
        requested_model_profile_id=model.id,
        state="RUNNING",
        input_hash=stage.input_hash,
        runtime_context_at=claimed_at,
        web_search_enabled=False,
    )
    db.add(invocation)
    db.flush()
    stage.invocation_id = invocation.id
    db.commit()
    assert recover_expired_stages(db, now=claimed_at + timedelta(seconds=2)) == 1
    db.refresh(stage)
    result = DecisionAgentResult.model_validate_json(stage.output_json)
    assert result.status == "TIMED_OUT" and result.action == "UNKNOWN"
    assert result.reason_codes == ["DECISION_AGENT_CLAIM_OUTCOME_UNKNOWN"]
    assert stage.output_hash == hashlib.sha256(stage.output_json.encode()).hexdigest()
    recovered_output = (stage.output_json, stage.output_hash)
    adapter = DecisionFixtureAdapter(db)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    process_agent_work_once(db, worker_id="next-worker", lease_seconds=60, now=datetime.now(UTC))
    db.refresh(stage)
    assert (stage.output_json, stage.output_hash) == recovered_output
    assert db.scalar(
        select(func.count())
        .select_from(LlmInvocation)
        .where(LlmInvocation.stage_run_id == stage.id)
    ) == 1


def test_model_fallback_preserves_requested_and_actual_provenance(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, settings = _admit(client, db, admin, monkeypatch)
    snapshot = json.loads(run.route_versions_json)
    role_snapshot = snapshot["CONSERVATIVE_DECISION"]
    primary = db.get(LlmModelProfile, role_snapshot["model_id"])
    route = db.get(LlmRoleRoute, role_snapshot["route_id"])
    assert primary is not None and route is not None
    fallback = LlmModelProfile(
        provider_profile_id=primary.provider_profile_id,
        alias="phase7e-fallback",
        provider_model_id="phase7e-fallback-model",
        capabilities_json=primary.capabilities_json,
        max_context_tokens=primary.max_context_tokens,
        max_output_tokens=primary.max_output_tokens,
        temperature=primary.temperature,
        top_p=primary.top_p,
        reasoning_effort=primary.reasoning_effort,
        seed=primary.seed,
        state="VALIDATED",
        validated_at=datetime.now(UTC),
    )
    db.add(fallback)
    db.flush()
    route.fallback_policy = "FAILOVER"
    route.fallback_model_profile_ids_json = canonical_context_json([fallback.id])
    role_snapshot["failure_policy"] = "FAILOVER"
    role_snapshot["fallback_model_id"] = fallback.id
    role_snapshot["fallback_model_version"] = fallback.version
    role_snapshot["route_version_hash"] = context_digest(
        canonical_context_json(
            {key: value for key, value in role_snapshot.items() if key != "route_version_hash"}
        )
    )
    run.route_versions_json = canonical_context_json(snapshot)
    db.commit()
    _complete_upstream(db, run, monkeypatch, settings)
    materialize_decision_agent_stages(db, run_id=run.id, now=datetime.now(UTC))
    adapter = DecisionFixtureAdapter(
        db,
        outputs={
            f"CONSERVATIVE_DECISION:{primary.provider_model_id}": "ERROR",
            f"CONSERVATIVE_DECISION:{fallback.provider_model_id}": _valid_output(),
        },
    )
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    _run_decisions(db, 1)
    result = _results(db, run.id)["CONSERVATIVE_DECISION"]
    assert result.status == "SUCCEEDED"
    assert result.requested_model_profile_id == primary.id
    assert result.actual_model == fallback.provider_model_id
    assert result.fallback_used is True
    invocations = list(
        db.scalars(
            select(LlmInvocation)
            .where(LlmInvocation.stage_run_id == result.stage_run_id)
            .order_by(LlmInvocation.created_at)
        )
    )
    assert len(invocations) == 2
    assert invocations[-1].requested_model_profile_id == fallback.id
    assert json.loads(invocations[-1].fallback_path_json) == [primary.id, fallback.id]
    changed = result.model_copy(update={"actual_model": primary.provider_model_id, "fallback_used": False})
    assert context_digest(canonical_context_json(result.model_dump(mode="json"))) != context_digest(
        canonical_context_json(changed.model_dump(mode="json"))
    )


@pytest.mark.parametrize(
    "parameters",
    [
        {
            "minimum_confidence": "0",
            "minimum_entry_score": 0,
            "risk_tolerance_score": 0,
            "uncertainty_tolerance_ratio": "0",
            "momentum_deterioration_tolerance_pct": "0",
            "drawdown_tolerance_pct": "0",
        },
        {
            "minimum_confidence": "1",
            "minimum_entry_score": 100,
            "risk_tolerance_score": 100,
            "uncertainty_tolerance_ratio": "1",
            "momentum_deterioration_tolerance_pct": "100",
            "drawdown_tolerance_pct": "100",
        },
    ],
)
def test_policy_semantic_boundaries_accept_fixture_minimum_and_maximum(parameters) -> None:
    assert DecisionPolicyParameters.model_validate(parameters)


def test_completion_policy_corruption_discards_success(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    policy_id = next(
        item["configuration_version_id"]
        for item in json.loads(run.policy_profile_version_map_json)["profiles"]
        if item["agent_type"] == "CONSERVATIVE"
    )

    def corrupt(_request) -> None:
        from app.models import ConfigurationVersion

        policy = db.get(ConfigurationVersion, policy_id)
        assert policy is not None
        policy.payload_hash = "0" * 64
        db.commit()

    adapter = DecisionFixtureAdapter(db, during_call=corrupt)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    _run_decisions(db, 1)
    result = _results(db, run.id)["CONSERVATIVE_DECISION"]
    assert result.status == "CONFLICTED" and result.action == "UNKNOWN"
    assert result.reason_codes == ["DECISION_AGENT_POLICY_PROVENANCE_INVALID"]


def test_new_prompt_version_during_call_does_not_replace_frozen_prompt(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    frozen = json.loads(run.route_versions_json)["CONSERVATIVE_DECISION"]

    def add_prompt(_request) -> None:
        old = db.get(LlmPromptProfile, frozen["prompt_profile_id"])
        assert old is not None
        replacement_text = "A newer validated prompt that must not affect the frozen run."
        db.add(
            LlmPromptProfile(
                owner_id=old.owner_id,
                role=old.role,
                version_number=old.version_number + 1,
                version_label="phase7e-newer-prompt",
                system_prompt=replacement_text,
                content_hash=hashlib.sha256(replacement_text.encode()).hexdigest(),
                state="VALIDATED",
                reason="Phase 7E supersession fixture",
                validated_at=datetime.now(UTC),
            )
        )
        db.commit()

    adapter = DecisionFixtureAdapter(db, during_call=add_prompt)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    _run_decisions(db, 1)
    result = _results(db, run.id)["CONSERVATIVE_DECISION"]
    assert result.status == "SUCCEEDED"
    assert result.prompt_profile_id == frozen["prompt_profile_id"]
    assert result.prompt_hash == frozen["prompt_content_hash"]


def test_evidence_namespace_rejects_every_non_frozen_reference_class() -> None:
    invalid_refs = (
        "unrated-id",
        "rejected-or-stale-id",
        "different-run-id",
        "https://example.com/raw",
        "evidence title",
        "scout-stage-id",
        "a" * 64,
        "candidate-audit-id",
        "nonexistent-id",
    )
    for ref in invalid_refs:
        output = DecisionAgentModelOutput(
            status="SUCCEEDED",
            action="WAIT",
            confidence=0.5,
            entry_score=50,
            risk_score=50,
            reason_codes=["EVIDENCE_MIXED"],
            positive_evidence_refs=[ref],
            negative_evidence_refs=[],
        )
        with pytest.raises(ValueError):
            validate_decision_evidence_refs(output, allowed_evidence_refs={"verified-id"})
        mirrored = output.model_copy(
            update={"positive_evidence_refs": [], "negative_evidence_refs": [ref]}
        )
        with pytest.raises(ValueError):
            validate_decision_evidence_refs(mirrored, allowed_evidence_refs={"verified-id"})
    allowed = output.model_copy(
        update={"positive_evidence_refs": ["verified-id"], "negative_evidence_refs": []}
    )
    validate_decision_evidence_refs(allowed, allowed_evidence_refs={"verified-id"})
