from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from itertools import product

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.contracts import ArbiterResult, DecisionAgentResult, EntryArbiterInput
from app.agents.decision_agents import DECISION_AGENT_ROLES
from app.agents.decision_context import canonical_context_json, context_digest
from app.agents.entry_arbiter import (
    ENTRY_ARBITER_ROLE,
    ENTRY_ARBITER_ROLE_ORDER,
    EntryArbiterError,
    arbiter_result_hash,
    build_arbiter_result,
    build_entry_arbiter_input,
    entry_arbiter_input_hash,
    evaluate_consensus,
    materialize_entry_arbiter_stage,
)
from app.agents.worker import (
    process_agent_work_once,
    reconcile_v7_arbiter_stages,
    reconcile_v7_decision_stages,
)
from app.llm.registry import provider_registry
from app.models import (
    AgentStageRun,
    Approval,
    ConfigurationVersion,
    Decision,
    LlmInvocation,
    OrderIntent,
    TradingOrder,
    User,
)
from tests.test_policy_profile_admission import _all_profiles
from tests.test_v7_decision_agent_execution import _valid_output
from tests.test_v7_decision_agent_foundation import _complete_upstream
from tests.test_v7_entry_arbiter import _arbiter_stage, _complete_decisions, _items
from tests.test_v7_upstream_runtime import _admit


def _execute_arbiter(db: Session, *, prefix: str) -> AgentStageRun:
    assert process_agent_work_once(
        db,
        worker_id=f"{prefix}-reconcile",
        lease_seconds=60,
        now=datetime.now(UTC),
    )
    assert process_agent_work_once(
        db,
        worker_id=f"{prefix}-execute",
        lease_seconds=60,
        now=datetime.now(UTC),
    )
    stage = db.scalar(
        select(AgentStageRun).where(AgentStageRun.role == ENTRY_ARBITER_ROLE)
    )
    assert stage is not None
    return stage


def _assert_no_downstream_authority(db: Session) -> None:
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


@pytest.mark.parametrize(
    ("balanced_output", "expected_action", "expected_pattern"),
    [
        (None, "BUY", "ALL_BUY"),
        ("TIMEOUT", "UNKNOWN", "MANDATORY_UNKNOWN"),
    ],
    ids=("FULL-E2E-BUY", "FULL-E2E-UNKNOWN"),
)
def test_phase8d_full_production_e2e_stops_at_arbiter(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    balanced_output,
    expected_action: str,
    expected_pattern: str,
) -> None:
    outputs = (
        None
        if balanced_output is None
        else {"BALANCED_DECISION": balanced_output}
    )
    run, context, adapter = _complete_decisions(
        client, db, admin, monkeypatch, outputs=outputs
    )
    invocation_count = db.scalar(select(func.count()).select_from(LlmInvocation))

    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("ENTRY_ARBITER must not resolve a Provider")

    monkeypatch.setattr(provider_registry, "resolve", forbidden_provider)
    stage = _execute_arbiter(db, prefix=expected_action.lower())
    result = ArbiterResult.model_validate_json(stage.output_json)

    assert len(adapter.requests) == 3
    assert stage.run_id == run.id and stage.state == "SUCCEEDED"
    assert stage.route_id is None and stage.invocation_id is None
    assert result.decision_context_id == context.id
    assert (result.action, result.decision_pattern) == (
        expected_action,
        expected_pattern,
    )
    assert len(result.reason_codes) == 1
    assert db.scalar(select(func.count()).select_from(LlmInvocation)) == invocation_count
    _assert_no_downstream_authority(db)


def _non_success_output(status: str) -> dict[str, object] | str:
    if status == "TIMED_OUT":
        return "TIMEOUT"
    if status == "FAILED":
        return "ERROR"
    if status == "INVALID_OUTPUT":
        return {**_valid_output(), "action": "UNKNOWN"}
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


@pytest.mark.parametrize("role", DECISION_AGENT_ROLES)
@pytest.mark.parametrize(
    "status",
    ("INSUFFICIENT_DATA", "CONFLICTED", "TIMED_OUT", "FAILED", "INVALID_OUTPUT"),
)
def test_phase8d_each_role_non_success_is_normal_unknown_consensus(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    role: str,
    status: str,
) -> None:
    run, _, _ = _complete_decisions(
        client,
        db,
        admin,
        monkeypatch,
        outputs={role: _non_success_output(status)},
    )
    stage = _execute_arbiter(db, prefix=f"{role}-{status}")
    result = ArbiterResult.model_validate_json(stage.output_json)
    source = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == role,
        )
    )

    assert source is not None and source.state == status
    assert source.output_json is not None and source.output_hash is not None
    assert (result.action, result.decision_pattern, result.reason_codes) == (
        "UNKNOWN",
        "MANDATORY_UNKNOWN",
        ["ARBITER_MANDATORY_UNKNOWN"],
    )
    assert stage.state == "SUCCEEDED"


def _expected_consensus(actions: tuple[str, str, str]) -> tuple[str, str]:
    rejects = actions.count("REJECT")
    if rejects >= 2:
        return "MULTIPLE_REJECT", "REJECT"
    if rejects == 1:
        return "SINGLE_REJECT", "WAIT"
    if actions == ("BUY", "BUY", "BUY"):
        return "ALL_BUY", "BUY"
    if actions[1] == "BUY" and sorted((actions[0], actions[2])) == ["BUY", "WAIT"]:
        return "BALANCED_PLUS_ONE_BUY", "BUY"
    return "DEFAULT_WAIT", "WAIT"


def test_phase8d_pure_truth_table_is_deterministic_for_all_success_actions() -> None:
    for actions in product(("BUY", "WAIT", "REJECT"), repeat=3):
        expected_pattern, expected_action = _expected_consensus(actions)
        first = evaluate_consensus(_items(actions))
        second = evaluate_consensus(_items(actions))
        assert first == second
        assert (first.decision_pattern, first.action) == (
            expected_pattern,
            expected_action,
        )
        assert first.reason_code == {
            "MULTIPLE_REJECT": "ARBITER_MULTIPLE_REJECT",
            "SINGLE_REJECT": "ARBITER_SINGLE_REJECT",
            "ALL_BUY": "ARBITER_ALL_BUY",
            "BALANCED_PLUS_ONE_BUY": "ARBITER_BALANCED_PLUS_ONE_BUY",
            "DEFAULT_WAIT": "ARBITER_DEFAULT_WAIT",
        }[expected_pattern]


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_c",
        "missing_b",
        "missing_a",
        "output_missing",
        "hash_missing",
        "hash_mismatch",
        "malformed",
        "stage_identity",
        "context_identity",
        "context_hash",
        "role_identity",
        "policy_provenance",
        "validity",
    ),
)
def test_phase8d_structural_corruption_never_materializes_unknown(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    mutation: str,
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    role_for_missing = {
        "missing_c": "CONSERVATIVE_DECISION",
        "missing_b": "BALANCED_DECISION",
        "missing_a": "AGGRESSIVE_DECISION",
    }
    role = role_for_missing.get(mutation, "CONSERVATIVE_DECISION")
    stage = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == role,
        )
    )
    assert stage is not None
    if mutation.startswith("missing_"):
        db.delete(stage)
    elif mutation == "output_missing":
        stage.output_json = None
    elif mutation == "hash_missing":
        stage.output_hash = None
    elif mutation == "hash_mismatch":
        stage.output_hash = "b" * 64
    else:
        payload = json.loads(stage.output_json)
        if mutation == "malformed":
            payload = {"schema_version": "decision-agent-result-v1"}
        elif mutation == "stage_identity":
            payload["stage_run_id"] = "foreign-stage"
        elif mutation == "context_identity":
            payload["decision_context_id"] = "foreign-context"
        elif mutation == "context_hash":
            payload["decision_context_hash"] = "b" * 64
        elif mutation == "role_identity":
            payload["role"] = "BALANCED_DECISION"
        elif mutation == "policy_provenance":
            payload["policy_profile_hash"] = "b" * 64
        else:
            payload["valid_until"] = (
                datetime.fromisoformat(payload["valid_until"]) + timedelta(seconds=1)
            ).isoformat()
        stage.output_json = canonical_context_json(payload)
        stage.output_hash = context_digest(stage.output_json)
    db.commit()

    with pytest.raises(EntryArbiterError):
        materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))
    assert _arbiter_stage(db, run.id) is None


def test_phase8d_hash_sensitivity_and_result_determinism() -> None:
    original = EntryArbiterInput(
        decision_context_id="context-a",
        decision_context_hash="a" * 64,
        input_results=_items(("BUY", "BUY", "BUY")),
        valid_until="2026-08-26T12:00:00+00:00",
    )
    variants: list[EntryArbiterInput] = []
    variants.append(original.model_copy(update={"decision_context_id": "context-b"}))
    variants.append(original.model_copy(update={"decision_context_hash": "b" * 64}))
    variants.append(
        original.model_copy(update={"valid_until": "2026-08-26T12:01:00+00:00"})
    )
    for index in range(3):
        changed_hash = original.model_copy(deep=True)
        changed_hash.input_results[index].output_hash = "b" * 64
        variants.append(changed_hash)
        changed_semantics = original.model_copy(deep=True)
        changed_semantics.input_results[index].action = "WAIT"
        variants.append(changed_semantics)

    baseline_hash = entry_arbiter_input_hash(original)
    assert all(entry_arbiter_input_hash(item) != baseline_hash for item in variants)
    first = build_arbiter_result(original)
    second = build_arbiter_result(original.model_copy(deep=True))
    assert first == second
    assert arbiter_result_hash(first) == arbiter_result_hash(second)
    assert "confidence" not in first.model_dump()
    assert "entry_score" not in first.model_dump()
    assert "risk_score" not in first.model_dump()


def test_phase8d_db_query_order_is_canonicalized(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    normal = build_entry_arbiter_input(db, run_id=run.id, now=datetime.now(UTC))
    original_scalars = db.scalars

    def reverse_decision_stage_query(statement, *args, **kwargs):
        result = original_scalars(statement, *args, **kwargs)
        sql = str(statement)
        if "agent_stage_runs.role IN" not in sql:
            return result
        values = list(result)
        if {getattr(item, "role", None) for item in values} == set(
            ENTRY_ARBITER_ROLE_ORDER
        ):
            return iter(reversed(values))
        return iter(values)

    monkeypatch.setattr(db, "scalars", reverse_decision_stage_query)
    reversed_query = build_entry_arbiter_input(
        db, run_id=run.id, now=datetime.now(UTC)
    )

    assert reversed_query == normal
    assert [item.role for item in reversed_query.input_results] == list(
        ENTRY_ARBITER_ROLE_ORDER
    )
    assert entry_arbiter_input_hash(reversed_query) == entry_arbiter_input_hash(normal)
    assert arbiter_result_hash(build_arbiter_result(reversed_query)) == arbiter_result_hash(
        build_arbiter_result(normal)
    )


def test_phase8d_worker_recovers_before_and_after_materialization(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    assert _arbiter_stage(db, run.id) is None

    assert process_agent_work_once(
        db,
        worker_id="recover-before-materialization",
        lease_seconds=60,
        now=datetime.now(UTC),
    )
    pending = _arbiter_stage(db, run.id)
    assert pending is not None and pending.state == "PENDING"
    pending_id = pending.id

    assert process_agent_work_once(
        db,
        worker_id="recover-after-materialization",
        lease_seconds=60,
        now=datetime.now(UTC),
    )
    recovered = _arbiter_stage(db, run.id)
    assert recovered is not None and recovered.id == pending_id
    assert recovered.state == "SUCCEEDED"
    assert db.scalar(
        select(func.count())
        .select_from(AgentStageRun)
        .where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == ENTRY_ARBITER_ROLE,
        )
    ) == 1


def test_phase8d_stored_input_hash_tamper_conflicts_without_repair(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    stage = materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))
    stage.input_hash = "b" * 64
    db.commit()

    assert process_agent_work_once(
        db,
        worker_id="input-hash-conflict",
        lease_seconds=60,
        now=datetime.now(UTC),
    ) is True
    db.refresh(stage)
    assert stage.state == "CONFLICTED"
    assert stage.input_hash == "b" * 64
    assert stage.output_json is None and stage.output_hash is None
    db.refresh(run)
    assert run.state == "FAILED"
    assert run.error_code == "ENTRY_ARBITER_CONFLICTED"


def test_phase8d_policy_supersession_does_not_reinterpret_or_mutate_terminal_result(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    stage = materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))

    active = list(
        db.scalars(select(ConfigurationVersion).where(ConfigurationVersion.state == "ACTIVE"))
    )
    assert len(active) >= 3
    for profile in active:
        profile.state = "SUPERSEDED"
    db.flush()
    _all_profiles(db, admin, sequence=2)
    db.commit()

    assert process_agent_work_once(
        db,
        worker_id="frozen-policy-arbiter",
        lease_seconds=60,
        now=datetime.now(UTC),
    )
    db.refresh(stage)
    assert stage.state == "SUCCEEDED"
    original_json, original_hash = stage.output_json, stage.output_hash
    assert original_json is not None and original_hash is not None

    assert reconcile_v7_arbiter_stages(
        db, run_id=run.id, now=datetime.now(UTC), limit=1
    ) == 0
    db.refresh(stage)
    assert (stage.output_json, stage.output_hash) == (original_json, original_hash)
    assert hashlib.sha256(original_json.encode()).hexdigest() == original_hash


def test_phase8d_exact_one_finalizer_ready_lineage(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, context, _ = _complete_decisions(client, db, admin, monkeypatch)
    stage = _execute_arbiter(db, prefix="lineage")
    result = ArbiterResult.model_validate_json(stage.output_json)
    decision_stages = list(
        db.scalars(
            select(AgentStageRun).where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role.in_(DECISION_AGENT_ROLES),
            )
        )
    )
    by_role = {item.role: item for item in decision_stages}

    assert len(decision_stages) == 3
    assert [item.role for item in result.input_results] == list(ENTRY_ARBITER_ROLE_ORDER)
    assert result.input_result_ids == [by_role[role].id for role in ENTRY_ARBITER_ROLE_ORDER]
    assert [item.output_hash for item in result.input_results] == [
        by_role[role].output_hash for role in ENTRY_ARBITER_ROLE_ORDER
    ]
    assert result.decision_context_id == context.id
    assert result.decision_context_hash == context.context_hash
    context_valid_until = context.valid_until.replace(tzinfo=UTC)
    assert result.valid_until == context_valid_until.isoformat()
    assert stage.output_hash == context_digest(stage.output_json)
    assert _arbiter_stage(db, run.id).id == stage.id
    _assert_no_downstream_authority(db)


def test_phase8d_agent_reason_and_score_are_not_arbiter_contract_inputs(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    value = build_entry_arbiter_input(db, run_id=run.id, now=datetime.now(UTC))
    decision_stages = list(
        db.scalars(
            select(AgentStageRun).where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role.in_(DECISION_AGENT_ROLES),
            )
        )
    )
    for stage in decision_stages:
        source = DecisionAgentResult.model_validate_json(stage.output_json)
        assert source.reason_codes
        assert source.confidence >= 0
        assert source.entry_score is not None
        assert source.risk_score is not None

    payload = value.model_dump()
    assert all(
        forbidden not in json.dumps(payload)
        for forbidden in ("confidence", "entry_score", "risk_score", "reason_codes")
    )
    result = build_arbiter_result(value)
    assert result.reason_codes == ["ARBITER_ALL_BUY"]


def test_phase8d_historical_four_route_v7_run_is_not_retroactively_upgraded(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, settings = _admit(client, db, admin, monkeypatch)
    frozen_routes = json.loads(run.route_versions_json or "{}")
    for role in DECISION_AGENT_ROLES:
        frozen_routes.pop(role, None)
    run.route_versions_json = canonical_context_json(frozen_routes)
    db.commit()
    _complete_upstream(db, run, monkeypatch, settings)

    assert reconcile_v7_decision_stages(
        db, run_id=run.id, now=datetime.now(UTC), limit=1
    ) == 0
    assert reconcile_v7_arbiter_stages(
        db, run_id=run.id, now=datetime.now(UTC), limit=1
    ) == 0
    roles = set(
        db.scalars(
            select(AgentStageRun.role).where(AgentStageRun.run_id == run.id)
        )
    )
    assert not roles.intersection(DECISION_AGENT_ROLES)
    assert ENTRY_ARBITER_ROLE not in roles
