from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import entry_arbiter
from app.agents.contracts import (
    ArbiterResult,
    EntryArbiterInput,
    EntryArbiterInputResult,
)
from app.agents.decision_agents import DECISION_AGENT_ROLES
from app.agents.entry_arbiter import (
    ENTRY_ARBITER_ROLE,
    EntryArbiterError,
    arbiter_result_hash,
    build_arbiter_result,
    build_entry_arbiter_input,
    complete_entry_arbiter_execution,
    entry_arbiter_input_hash,
    evaluate_consensus,
    materialize_entry_arbiter_stage,
    prepare_entry_arbiter_execution,
)
from app.agents.runtime import executable_roles, materializable_roles
from app.agents.worker import claim_next_stage, process_agent_work_once
from app.llm.registry import provider_registry
from app.models import (
    AgentStageRun,
    Approval,
    Decision,
    LlmInvocation,
    OrderIntent,
    TradingOrder,
    User,
)
from tests.test_v7_decision_agent_acceptance import _ready
from tests.test_v7_decision_agent_execution import (
    DecisionFixtureAdapter,
    _run_decisions,
    _valid_output,
)

HASH = "a" * 64
ROLES = tuple(DECISION_AGENT_ROLES)
TYPES = ("CONSERVATIVE", "BALANCED", "AGGRESSIVE")


def _items(
    actions: tuple[str, str, str],
    statuses: tuple[str, str, str] = ("SUCCEEDED", "SUCCEEDED", "SUCCEEDED"),
) -> list[EntryArbiterInputResult]:
    return [
        EntryArbiterInputResult(
            role=role,
            agent_type=agent_type,
            stage_run_id=f"stage-{index}",
            output_hash=HASH,
            status=status,
            action=action,
        )
        for index, (role, agent_type, status, action) in enumerate(
            zip(ROLES, TYPES, statuses, actions, strict=True)
        )
    ]


@pytest.mark.parametrize(
    ("actions", "statuses", "pattern", "action", "reason"),
    [
        (
            ("BUY", "BUY", "BUY"),
            ("SUCCEEDED",) * 3,
            "ALL_BUY",
            "BUY",
            "ARBITER_ALL_BUY",
        ),
        (
            ("WAIT", "BUY", "BUY"),
            ("SUCCEEDED",) * 3,
            "BALANCED_PLUS_ONE_BUY",
            "BUY",
            "ARBITER_BALANCED_PLUS_ONE_BUY",
        ),
        (
            ("BUY", "BUY", "WAIT"),
            ("SUCCEEDED",) * 3,
            "BALANCED_PLUS_ONE_BUY",
            "BUY",
            "ARBITER_BALANCED_PLUS_ONE_BUY",
        ),
        (
            ("WAIT", "BUY", "WAIT"),
            ("SUCCEEDED",) * 3,
            "DEFAULT_WAIT",
            "WAIT",
            "ARBITER_DEFAULT_WAIT",
        ),
        (
            ("REJECT", "BUY", "BUY"),
            ("SUCCEEDED",) * 3,
            "SINGLE_REJECT",
            "WAIT",
            "ARBITER_SINGLE_REJECT",
        ),
        (
            ("REJECT", "REJECT", "BUY"),
            ("SUCCEEDED",) * 3,
            "MULTIPLE_REJECT",
            "REJECT",
            "ARBITER_MULTIPLE_REJECT",
        ),
        (
            ("BUY", "WAIT", "REJECT"),
            ("SUCCEEDED",) * 3,
            "SINGLE_REJECT",
            "WAIT",
            "ARBITER_SINGLE_REJECT",
        ),
        (
            ("BUY", "UNKNOWN", "BUY"),
            ("SUCCEEDED", "TIMED_OUT", "SUCCEEDED"),
            "MANDATORY_UNKNOWN",
            "UNKNOWN",
            "ARBITER_MANDATORY_UNKNOWN",
        ),
        (
            ("UNKNOWN", "WAIT", "BUY"),
            ("INVALID_OUTPUT", "SUCCEEDED", "SUCCEEDED"),
            "MANDATORY_UNKNOWN",
            "UNKNOWN",
            "ARBITER_MANDATORY_UNKNOWN",
        ),
    ],
)
def test_consensus_policy_truth_table(
    actions, statuses, pattern: str, action: str, reason: str
) -> None:
    outcome = evaluate_consensus(_items(actions, statuses))
    assert (outcome.decision_pattern, outcome.action, outcome.reason_code) == (
        pattern,
        action,
        reason,
    )


def test_contracts_are_strict_ordered_and_pattern_bound() -> None:
    value = EntryArbiterInput(
        decision_context_id="context",
        decision_context_hash=HASH,
        input_results=_items(("BUY", "BUY", "BUY")),
        valid_until="2026-08-26T12:00:00+00:00",
    )
    result = build_arbiter_result(value)
    assert result.input_result_ids == [item.stage_run_id for item in value.input_results]
    assert set(result.model_dump()) == {
        "schema_version",
        "decision_context_id",
        "decision_context_hash",
        "action",
        "policy_version",
        "input_result_ids",
        "input_results",
        "decision_pattern",
        "reason_codes",
        "valid_until",
    }
    with pytest.raises(ValidationError):
        EntryArbiterInput.model_validate({**value.model_dump(), "confidence": 0.9})
    with pytest.raises(ValidationError):
        EntryArbiterInput.model_validate(
            {**value.model_dump(), "input_results": list(reversed(value.input_results))}
        )
    with pytest.raises(ValidationError):
        ArbiterResult.model_validate({**result.model_dump(), "reason_codes": ["OTHER"]})
    changed = value.model_copy(deep=True)
    changed.input_results[0].output_hash = "b" * 64
    assert entry_arbiter_input_hash(changed) != entry_arbiter_input_hash(value)


def _complete_decisions(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    *,
    outputs: dict[str, dict[str, object] | str] | None = None,
):
    run, context = _ready(client, db, admin, monkeypatch)
    adapter = DecisionFixtureAdapter(db, outputs=outputs)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)
    _run_decisions(db)
    return run, context, adapter


def _arbiter_stage(db: Session, run_id: str) -> AgentStageRun | None:
    return db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run_id,
            AgentStageRun.role == ENTRY_ARBITER_ROLE,
        )
    )


def _run_arbiter(db: Session) -> None:
    assert process_agent_work_once(
        db,
        worker_id="arbiter-reconcile",
        lease_seconds=60,
        now=datetime.now(UTC),
    )
    assert process_agent_work_once(
        db,
        worker_id="arbiter-execute",
        lease_seconds=60,
        now=datetime.now(UTC),
    )


def test_all_buy_production_path_is_providerless_and_has_no_authority(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, context, adapter = _complete_decisions(client, db, admin, monkeypatch)
    invocation_count = db.scalar(select(func.count()).select_from(LlmInvocation))

    _run_arbiter(db)

    stage = _arbiter_stage(db, run.id)
    assert stage is not None and stage.state == "SUCCEEDED"
    assert stage.route_id is None and stage.invocation_id is None
    result = ArbiterResult.model_validate_json(stage.output_json)
    assert result.action == "BUY" and result.decision_pattern == "ALL_BUY"
    assert result.decision_context_id == context.id
    assert stage.output_hash == hashlib.sha256(stage.output_json.encode()).hexdigest()
    assert len(adapter.requests) == 3
    assert db.scalar(select(func.count()).select_from(LlmInvocation)) == invocation_count
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


@pytest.mark.parametrize(
    "outcome",
    [
        {
            "schema_version": "decision-agent-model-output-v1",
            "status": "INSUFFICIENT_DATA",
            "action": "UNKNOWN",
            "confidence": 0.0,
            "entry_score": None,
            "risk_score": None,
            "reason_codes": ["EVIDENCE_INSUFFICIENT"],
            "positive_evidence_refs": [],
            "negative_evidence_refs": [],
        },
        {
            "schema_version": "decision-agent-model-output-v1",
            "status": "CONFLICTED",
            "action": "UNKNOWN",
            "confidence": 0.0,
            "entry_score": None,
            "risk_score": None,
            "reason_codes": ["SCOUT_SIGNALS_CONFLICTED"],
            "positive_evidence_refs": [],
            "negative_evidence_refs": [],
        },
        "TIMEOUT",
        "ERROR",
        {**_valid_output(), "action": "UNKNOWN"},
    ],
)
def test_structured_non_success_is_a_successful_unknown_consensus(
    client, db: Session, admin: User, monkeypatch, outcome
) -> None:
    run, _, _ = _complete_decisions(
        client,
        db,
        admin,
        monkeypatch,
        outputs={"BALANCED_DECISION": outcome},
    )
    _run_arbiter(db)
    stage = _arbiter_stage(db, run.id)
    assert stage is not None and stage.state == "SUCCEEDED"
    result = ArbiterResult.model_validate_json(stage.output_json)
    assert (result.action, result.decision_pattern, result.reason_codes) == (
        "UNKNOWN",
        "MANDATORY_UNKNOWN",
        ["ARBITER_MANDATORY_UNKNOWN"],
    )


def test_input_and_result_hashes_are_canonical_and_order_independent(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    first = build_entry_arbiter_input(db, run_id=run.id, now=datetime.now(UTC))
    db.expire_all()
    second = build_entry_arbiter_input(db, run_id=run.id, now=datetime.now(UTC))
    assert [item.role for item in first.input_results] == list(ROLES)
    assert first == second
    assert entry_arbiter_input_hash(first) == entry_arbiter_input_hash(second)
    assert arbiter_result_hash(build_arbiter_result(first)) == arbiter_result_hash(
        build_arbiter_result(second)
    )


def test_materialization_is_exactly_once_and_mismatch_does_not_repair(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    first = materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))
    second = materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))
    assert first.id == second.id
    assert (
        db.scalar(
            select(func.count())
            .select_from(AgentStageRun)
            .where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role == ENTRY_ARBITER_ROLE,
            )
        )
        == 1
    )
    first.input_hash = "b" * 64
    db.commit()
    with pytest.raises(EntryArbiterError) as captured:
        materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))
    assert captured.value.code == "ENTRY_ARBITER_MATERIALIZATION_CONFLICT"
    assert db.get(AgentStageRun, first.id).input_hash == "b" * 64


@pytest.mark.parametrize(
    "mutation", ["output", "output_hash", "hash", "context", "stage_id", "type", "validity"]
)
def test_structural_corruption_prevents_materialization(
    client, db: Session, admin: User, monkeypatch, mutation: str
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    stage = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == "CONSERVATIVE_DECISION",
        )
    )
    assert stage is not None
    if mutation == "output":
        stage.output_json = None
    elif mutation == "output_hash":
        stage.output_hash = None
    elif mutation == "hash":
        stage.output_hash = "b" * 64
    else:
        payload = json.loads(stage.output_json)
        if mutation == "context":
            payload["decision_context_id"] = "cross-context"
        elif mutation == "stage_id":
            payload["stage_run_id"] = "cross-run-stage"
        elif mutation == "validity":
            payload["valid_until"] = (
                datetime.fromisoformat(payload["valid_until"]) + timedelta(seconds=1)
            ).isoformat()
        else:
            payload["agent_type"] = "AGGRESSIVE"
        stage.output_json = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        stage.output_hash = hashlib.sha256(stage.output_json.encode()).hexdigest()
    db.commit()
    with pytest.raises(EntryArbiterError):
        materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))
    assert _arbiter_stage(db, run.id) is None


def test_missing_decision_stage_prevents_arbiter_materialization(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    missing = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == "CONSERVATIVE_DECISION",
        )
    )
    assert missing is not None
    db.delete(missing)
    db.commit()
    with pytest.raises(EntryArbiterError):
        materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))
    assert _arbiter_stage(db, run.id) is None


def test_pre_materialization_expiry_creates_no_stage(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, context, _ = _complete_decisions(client, db, admin, monkeypatch)
    context.valid_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(EntryArbiterError) as captured:
        materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))
    assert captured.value.failure_state == "TIMED_OUT"
    assert _arbiter_stage(db, run.id) is None


def test_post_materialization_tamper_is_conflicted_without_output(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    arbiter = materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))
    source = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == "CONSERVATIVE_DECISION",
        )
    )
    source.output_hash = "b" * 64
    db.commit()
    assert claim_next_stage(
        db,
        worker_id="conflict-worker",
        lease_seconds=60,
        now=datetime.now(UTC),
    ) is None
    db.refresh(arbiter)
    assert arbiter.state == "CONFLICTED"
    assert arbiter.output_json is None and arbiter.output_hash is None


def test_post_materialization_expiry_is_timed_out_without_output(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, context, _ = _complete_decisions(client, db, admin, monkeypatch)
    arbiter = materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))
    context.valid_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert claim_next_stage(
        db,
        worker_id="expired-worker",
        lease_seconds=60,
        now=datetime.now(UTC),
    ) is None
    db.refresh(arbiter)
    assert arbiter.state == "TIMED_OUT"
    assert arbiter.output_json is None and arbiter.output_hash is None


def test_stale_fencing_completion_cannot_overwrite_arbiter(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    arbiter = materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))
    claim = claim_next_stage(
        db,
        worker_id="old-worker",
        lease_seconds=60,
        now=datetime.now(UTC),
    )
    assert claim is not None and claim.stage_id == arbiter.id
    db.refresh(arbiter)
    prepared = prepare_entry_arbiter_execution(
        db,
        stage=arbiter,
        run=run,
        fencing_token=claim.fencing_token,
        worker_id="old-worker",
        now=datetime.now(UTC),
    )
    candidate = build_arbiter_result(prepared.input)
    db.commit()
    arbiter.fencing_token += 1
    arbiter.lease_owner_id = "new-worker"
    db.commit()
    assert not complete_entry_arbiter_execution(
        db,
        prepared=prepared,
        result=candidate,
        worker_id="old-worker",
        now=datetime.now(UTC),
    )
    db.refresh(arbiter)
    assert arbiter.state == "RUNNING"
    assert arbiter.output_json is None and arbiter.output_hash is None


def test_unexpected_evaluator_failure_is_failed_without_unknown_result(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _ = _complete_decisions(client, db, admin, monkeypatch)
    materialize_entry_arbiter_stage(db, run_id=run.id, now=datetime.now(UTC))

    def fail_evaluator(_value):
        raise RuntimeError("injected evaluator failure")

    monkeypatch.setattr(entry_arbiter, "build_arbiter_result", fail_evaluator)
    assert process_agent_work_once(
        db,
        worker_id="failed-evaluator",
        lease_seconds=60,
        now=datetime.now(UTC),
    )
    stage = _arbiter_stage(db, run.id)
    assert stage is not None and stage.state == "FAILED"
    assert stage.error_code == "ENTRY_ARBITER_INTERNAL_ERROR"
    assert stage.output_json is None and stage.output_hash is None


def test_phase8_role_enablement_does_not_add_a_provider_role() -> None:
    assert ENTRY_ARBITER_ROLE in materializable_roles("agent-dag-v7")
    assert ENTRY_ARBITER_ROLE in executable_roles("agent-dag-v7")
    assert len(materializable_roles("agent-dag-v7")) == 11
    assert len(executable_roles("agent-dag-v7")) == 11
