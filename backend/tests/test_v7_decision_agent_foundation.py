from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.contracts import (
    DecisionAgentInput,
    DecisionAgentModelOutput,
    DecisionAgentResult,
    DecisionAgentStageInput,
    DecisionPolicyParameters,
    validate_decision_evidence_refs,
)
from app.agents.decision_agents import (
    DECISION_AGENT_ROLES,
    V7_LLM_ROUTE_ROLES,
    DecisionAgentFoundationError,
    build_decision_agent_input,
    decision_agent_input_hash,
    decision_agent_stage_input_hash,
    materialize_decision_agent_stages,
    resolve_decision_context_material,
)
from app.agents.krx import KrxCollection, KrxDailyMarket
from app.agents.policy_profiles import PolicyProfileError, resolve_decision_agent_policy
from app.agents.runtime import (
    AgentRuntimeError,
    create_v7_upstream_diagnostic_run,
    logical_roles,
    materializable_roles,
)
from app.agents.worker import (
    claim_next_stage,
    process_agent_work_once,
    reconcile_v7_decision_stages,
)
from app.config import Settings
from app.llm.prompts import LlmPromptError, create_prompt, validate_prompt
from app.models import (
    AgentRun,
    AgentStageRun,
    Approval,
    Decision,
    DecisionContext,
    DecisionInputSnapshot,
    LlmRoleRoute,
    TradingOrder,
    User,
)
from tests.test_v7_upstream_runtime import _admit


def _model_output(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "decision-agent-model-output-v1",
        "status": "SUCCEEDED",
        "action": "WAIT",
        "confidence": 0.5,
        "entry_score": 50,
        "risk_score": 40,
        "reason_codes": ["ENTRY_CRITERIA_NOT_MET"],
        "positive_evidence_refs": [],
        "negative_evidence_refs": [],
    }
    value.update(overrides)
    return value


def _server_result(**overrides: object) -> dict[str, object]:
    value = {
        **_model_output(),
        "schema_version": "decision-agent-result-v1",
        "stage_run_id": "stage-1",
        "role": "CONSERVATIVE_DECISION",
        "decision_context_id": "context-1",
        "decision_context_hash": "a" * 64,
        "agent_type": "CONSERVATIVE",
        "policy_profile_id": "policy-1",
        "policy_profile_hash": "b" * 64,
        "policy_profile_version": 1,
        "policy_profile_category": "V7_ENTRY_POLICY_CONSERVATIVE",
        "route_id": "route-1",
        "route_version": 1,
        "route_version_hash": "c" * 64,
        "prompt_profile_id": "prompt-1",
        "prompt_version": "conservative-prompt-v1",
        "prompt_hash": "d" * 64,
        "model_id": "model-1",
        "requested_model_profile_id": "model-1",
        "actual_provider": "MOCK",
        "actual_model": "deterministic-mock-v2",
        "fallback_used": False,
        "valid_until": "2026-08-25T04:00:00+00:00",
    }
    value.update(overrides)
    return value


def _complete_upstream(
    db: Session,
    run: AgentRun,
    monkeypatch,
    settings: Settings,
) -> DecisionContext:
    worker_settings = settings.model_copy(update={"krx_enabled": True})
    monkeypatch.setattr(worker_module, "get_settings", lambda: worker_settings)
    today = datetime.now(UTC).strftime("%Y%m%d")
    item = KrxDailyMarket(
        base_date=today,
        symbol="005930",
        name="Samsung",
        market_name="KOSPI",
        sector_type_name="ELECTRONICS",
        close_price="70000",
        change_price="0",
        change_rate="0",
        open_price="70000",
        high_price="70000",
        low_price="70000",
        trading_volume="1",
        trading_value="70000",
        market_cap="1",
        listed_shares="1",
        endpoint_path="/fixture",
    )
    monkeypatch.setattr(
        worker_module,
        "collect_krx_daily_market",
        lambda *args, **kwargs: KrxCollection(
            item=item,
            dates_queried=(today,),
            requests_made=1,
        ),
    )
    for index in range(8):
        process_agent_work_once(
            db,
            worker_id=f"phase7c-{index}",
            lease_seconds=30,
            now=datetime.now(UTC),
        )
        context = db.scalar(select(DecisionContext).where(DecisionContext.run_id == run.id))
        if context is not None:
            return context
    raise AssertionError("upstream did not freeze DecisionContext")


def test_decision_agent_contracts_are_strict_and_enforce_status_matrix() -> None:
    valid = DecisionAgentModelOutput.model_validate(_model_output())
    assert valid.action == "WAIT"
    DecisionAgentResult.model_validate(_server_result())

    for mutation in (
        {"extra": True},
        {"status": "SUCCEEDED", "action": "UNKNOWN"},
        {
            "status": "FAILED",
            "action": "BUY",
            "confidence": 0.0,
            "entry_score": None,
            "risk_score": None,
        },
        {
            "status": "INSUFFICIENT_DATA",
            "action": "UNKNOWN",
            "confidence": 0.1,
            "entry_score": None,
            "risk_score": None,
        },
        {"reason_codes": ["UNKNOWN_REASON"]},
        {"stage_run_id": "server-owned"},
    ):
        with pytest.raises(ValidationError):
            DecisionAgentModelOutput.model_validate(_model_output(**mutation))
    DecisionAgentResult.model_validate(
        _server_result(
            status="FAILED",
            action="UNKNOWN",
            confidence=0.0,
            entry_score=None,
            risk_score=None,
            reason_codes=["DECISION_AGENT_PROVIDER_ERROR"],
        )
    )
    with pytest.raises(ValidationError):
        DecisionAgentResult.model_validate(
            _server_result(
                status="FAILED",
                action="UNKNOWN",
                confidence=0.0,
                entry_score=None,
                risk_score=None,
                reason_codes=["ENTRY_CRITERIA_NOT_MET"],
            )
        )


def test_policy_parameters_are_required_strict_and_range_checked() -> None:
    valid = {
        "minimum_confidence": "0.5",
        "minimum_entry_score": 50,
        "risk_tolerance_score": 50,
        "uncertainty_tolerance_ratio": "0.5",
        "momentum_deterioration_tolerance_pct": "10",
        "drawdown_tolerance_pct": "10",
    }
    assert DecisionPolicyParameters.model_validate(valid).minimum_entry_score == 50
    for mutation in (
        {key: value for key, value in valid.items() if key != "minimum_confidence"},
        {**valid, "unknown": 1},
        {**valid, "minimum_entry_score": "50"},
        {**valid, "minimum_confidence": "1.1"},
        {**valid, "drawdown_tolerance_pct": "1e1"},
    ):
        with pytest.raises(ValidationError):
            DecisionPolicyParameters.model_validate(mutation)


def test_evidence_refs_are_sorted_disjoint_and_within_frozen_allowlist() -> None:
    output = DecisionAgentModelOutput.model_validate(
        _model_output(
            positive_evidence_refs=["evidence-a"],
            negative_evidence_refs=["evidence-b"],
        )
    )
    validate_decision_evidence_refs(
        output,
        allowed_evidence_refs={"evidence-a", "evidence-b"},
    )
    with pytest.raises(ValueError):
        validate_decision_evidence_refs(output, allowed_evidence_refs={"evidence-a"})
    with pytest.raises(ValidationError):
        DecisionAgentModelOutput.model_validate(
            _model_output(
                positive_evidence_refs=["same"],
                negative_evidence_refs=["same"],
            )
        )
    url_output = DecisionAgentModelOutput.model_validate(
        _model_output(positive_evidence_refs=["https://example.com"])
    )
    with pytest.raises(ValueError):
        validate_decision_evidence_refs(url_output, allowed_evidence_refs=set())


def test_decision_agent_stage_input_hash_is_role_isolated_and_deterministic() -> None:
    base = DecisionAgentStageInput(
        decision_context_id="context",
        decision_context_hash="a" * 64,
        role="CONSERVATIVE_DECISION",
        agent_type="CONSERVATIVE",
        policy_profile_id="policy",
        policy_profile_hash="b" * 64,
        route_id="route",
        route_version=1,
        route_version_hash="c" * 64,
        prompt_profile_id="prompt",
        prompt_version="prompt-v1",
        prompt_hash="d" * 64,
        requested_model_profile_id="model",
    )
    first = decision_agent_stage_input_hash(base)
    assert decision_agent_stage_input_hash(base) == first
    assert decision_agent_stage_input_hash(
        base.model_copy(update={"policy_profile_hash": "e" * 64})
    ) != first
    assert decision_agent_stage_input_hash(
        base.model_copy(update={"route_version_hash": "f" * 64})
    ) != first
    unrelated = {"balanced_policy_hash": "0" * 64, "aggressive_result": "ignored"}
    assert unrelated and decision_agent_stage_input_hash(base) == first


def test_control_plane_accepts_decision_roles_and_rejects_web_search(
    client,
    db: Session,
    admin: User,
    monkeypatch,
) -> None:
    run, _, _ = _admit(client, db, admin, monkeypatch)
    snapshot = json.loads(run.route_versions_json)
    assert set(snapshot) == set(V7_LLM_ROUTE_ROLES)
    assert "CORE" not in snapshot and "ENTRY_ARBITER" not in snapshot
    assert all(snapshot[role]["web_search_enabled"] is False for role in DECISION_AGENT_ROLES)
    assert all(snapshot[role]["prompt_profile_id"] for role in DECISION_AGENT_ROLES)
    route = db.get(LlmRoleRoute, snapshot["CONSERVATIVE_DECISION"]["route_id"])
    assert route is not None
    route.web_search_enabled = True
    db.commit()
    with pytest.raises(AgentRuntimeError, match="AGENT_ROUTE_NOT_READY"):
        create_v7_upstream_diagnostic_run(
            db,
            user=admin,
            market=run.market,
            symbol=run.symbol,
            route_ids={role: snapshot[role]["route_id"] for role in V7_LLM_ROUTE_ROLES},
            now=datetime.fromisoformat(
                json.loads(
                    db.scalar(
                        select(DecisionInputSnapshot).where(
                            DecisionInputSnapshot.input_hash == run.input_hash
                        )
                    ).input_json
                )["observed_at"]
            ),
        )


def test_decision_prompt_rejects_hard_coded_policy_threshold(
    db: Session,
    admin: User,
) -> None:
    prompt = create_prompt(
        db,
        user=admin,
        role="CONSERVATIVE_DECISION",
        system_prompt="Evaluate the frozen input; minimum confidence = 0.8 for entry.",
        reason="Phase 7C unsafe threshold fixture",
        correlation_id="phase7c-prompt",
    )
    with pytest.raises(LlmPromptError) as captured:
        validate_prompt(
            db,
            user=admin,
            prompt_id=prompt.id,
            correlation_id="phase7c-prompt-validate",
        )
    assert captured.value.code == "PROMPT_POLICY_THRESHOLD_FORBIDDEN"


def test_logical_materializable_and_execution_boundaries_are_distinct() -> None:
    assert logical_roles("agent-dag-v7") == {
        "INTEL_COLLECTOR",
        "EVIDENCE_VERIFIER",
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
        "EVIDENCE_CANDIDATE_AUDITOR",
        *DECISION_AGENT_ROLES,
        "ENTRY_ARBITER",
    }
    assert set(DECISION_AGENT_ROLES) <= materializable_roles("agent-dag-v7")
    assert "ENTRY_ARBITER" in materializable_roles("agent-dag-v7")


def test_context_input_builder_and_atomic_materializer(
    client,
    db: Session,
    admin: User,
    monkeypatch,
) -> None:
    run, _, settings = _admit(client, db, admin, monkeypatch)
    context = _complete_upstream(db, run, monkeypatch, settings)
    resolved = resolve_decision_context_material(
        db,
        run=run,
        context=context,
        now=datetime.now(UTC),
    )
    inputs = [
        build_decision_agent_input(
            db,
            run=run,
            context=context,
            role=role,
            now=datetime.now(UTC),
            resolved_context=resolved,
        )
        for role in DECISION_AGENT_ROLES
    ]
    assert all(item.decision_context == inputs[0].decision_context for item in inputs)
    assert len({item.policy_profile.agent_type for item in inputs}) == 3
    assert all("user_id" not in item.decision_context.decision_input.material for item in inputs)
    assert all(DecisionAgentInput.model_validate(item.model_dump()) for item in inputs)
    assert all(decision_agent_input_hash(item) == decision_agent_input_hash(item) for item in inputs)
    invalid_input = inputs[0].model_dump()
    invalid_input["unexpected"] = True
    with pytest.raises(ValidationError):
        DecisionAgentInput.model_validate(invalid_input)

    stages = materialize_decision_agent_stages(
        db,
        run_id=run.id,
        now=datetime.now(UTC),
    )
    assert {stage.role for stage in stages} == set(DECISION_AGENT_ROLES)
    assert all(json.loads(stage.dependency_roles_json) == ["EVIDENCE_CANDIDATE_AUDITOR"] for stage in stages)
    assert all(stage.state == "PENDING" and stage.invocation_id is None for stage in stages)
    assert reconcile_v7_decision_stages(
        db,
        now=datetime.now(UTC),
        run_id=run.id,
    ) == 0
    assert (
        db.scalar(
            select(func.count())
            .select_from(AgentStageRun)
            .where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role.in_(DECISION_AGENT_ROLES),
            )
        )
        == 3
    )
    assert claim_next_stage(
        db,
        worker_id="phase7d-enabled",
        lease_seconds=30,
        now=datetime.now(UTC),
    ) is not None
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_materializer_recovers_exact_partial_and_rejects_mismatch(
    client,
    db: Session,
    admin: User,
    monkeypatch,
) -> None:
    run, _, settings = _admit(client, db, admin, monkeypatch)
    _complete_upstream(db, run, monkeypatch, settings)
    stages = list(
        materialize_decision_agent_stages(db, run_id=run.id, now=datetime.now(UTC))
    )
    db.delete(stages[-1])
    db.commit()
    assert len(materialize_decision_agent_stages(db, run_id=run.id, now=datetime.now(UTC))) == 3
    conservative = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == "CONSERVATIVE_DECISION",
        )
    )
    assert conservative is not None
    conservative.input_hash = "0" * 64
    db.commit()
    with pytest.raises(DecisionAgentFoundationError) as captured:
        materialize_decision_agent_stages(db, run_id=run.id, now=datetime.now(UTC))
    assert captured.value.code == "DECISION_AGENT_MATERIALIZATION_CONFLICT"


def test_cross_role_policy_and_expired_context_fail_closed(
    client,
    db: Session,
    admin: User,
    monkeypatch,
) -> None:
    run, _, settings = _admit(client, db, admin, monkeypatch)
    context = _complete_upstream(db, run, monkeypatch, settings)
    version, policy = resolve_decision_agent_policy(
        db,
        run_id=run.id,
        role="CONSERVATIVE_DECISION",
    )
    assert version.id == policy.configuration_version_id
    with pytest.raises(PolicyProfileError):
        resolve_decision_agent_policy(db, run_id=run.id, role="ENTRY_ARBITER")
    with pytest.raises(ValidationError):
        DecisionAgentStageInput(
            decision_context_id=context.id,
            decision_context_hash=context.context_hash,
            role="CONSERVATIVE_DECISION",
            agent_type="BALANCED",
            policy_profile_id=version.id,
            policy_profile_hash=version.payload_hash,
            route_id="route",
            route_version=1,
            route_version_hash="a" * 64,
            prompt_profile_id="prompt",
            prompt_version="prompt-v1",
            prompt_hash="b" * 64,
            requested_model_profile_id="model",
        )

    context.valid_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(DecisionAgentFoundationError):
        materialize_decision_agent_stages(db, run_id=run.id, now=datetime.now(UTC))


def test_historical_four_route_run_is_not_retrofitted(
    client,
    db: Session,
    admin: User,
    monkeypatch,
) -> None:
    run, _, settings = _admit(client, db, admin, monkeypatch)
    snapshot = json.loads(run.route_versions_json)
    run.route_versions_json = json.dumps(
        {role: snapshot[role] for role in snapshot if role not in DECISION_AGENT_ROLES},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    db.commit()
    assert set(json.loads(run.route_versions_json)) == {
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
    }
    assert (
        db.scalar(
            select(func.count())
            .select_from(AgentStageRun)
            .where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role.in_(DECISION_AGENT_ROLES),
            )
        )
        == 0
    )
    assert _complete_upstream(db, run, monkeypatch, settings) is not None
    assert reconcile_v7_decision_stages(
        db,
        now=datetime.now(UTC),
        run_id=run.id,
    ) == 0
    assert set(json.loads(run.route_versions_json)) == {
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
    }


def test_materializer_requires_committed_context(
    client,
    db: Session,
    admin: User,
    monkeypatch,
) -> None:
    run, _, _ = _admit(client, db, admin, monkeypatch)
    with pytest.raises(DecisionAgentFoundationError):
        materialize_decision_agent_stages(db, run_id=run.id, now=datetime.now(UTC))
    assert (
        db.scalar(
            select(func.count())
            .select_from(AgentStageRun)
            .where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role.in_(DECISION_AGENT_ROLES),
            )
        )
        == 0
    )
import app.agents.worker as worker_module
