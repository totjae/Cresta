from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.contracts import DecisionAgentInput, DecisionAgentResult
from app.agents.decision_agents import DECISION_AGENT_ROLES, materialize_decision_agent_stages
from app.agents.worker import process_agent_work_once
from app.llm.contracts import LlmRequest, LlmResult
from app.llm.registry import provider_registry
from app.models import (
    AgentStageRun,
    Approval,
    ConfigurationVersion,
    Decision,
    LlmInvocation,
    LlmPromptProfile,
    LlmRoleRoute,
    TradingOrder,
    User,
)
from tests.test_v7_decision_agent_foundation import _complete_upstream
from tests.test_v7_upstream_runtime import _admit


def _valid_output(action: str = "BUY") -> dict[str, object]:
    return {
        "schema_version": "decision-agent-model-output-v1",
        "status": "SUCCEEDED",
        "action": action,
        "confidence": 0.8,
        "entry_score": 75,
        "risk_score": 25,
        "reason_codes": ["CONTEXT_SUPPORTS_ENTRY"],
        "positive_evidence_refs": [],
        "negative_evidence_refs": [],
    }


class DecisionFixtureAdapter:
    def __init__(
        self,
        db: Session,
        *,
        outputs: dict[str, dict[str, object] | str] | None = None,
        during_call=None,
    ) -> None:
        self.db = db
        self.outputs = outputs or {}
        self.during_call = during_call
        self.requests: list[LlmRequest] = []
        self.transaction_states: list[bool] = []

    def generate_structured(self, request: LlmRequest, model_id: str) -> LlmResult:
        self.transaction_states.append(self.db.in_transaction())
        self.requests.append(request)
        if self.during_call is not None:
            self.during_call(request)
        selected = self.outputs.get(
            f"{request.role}:{model_id}",
            self.outputs.get(request.role, _valid_output()),
        )
        if selected == "TIMEOUT":
            return LlmResult(
                invocation_id=request.invocation_id,
                status="TIMED_OUT",
                actual_provider="FIXTURE",
                actual_model=model_id,
                latency_ms=10,
                schema_validation="NOT_RUN",
            )
        if selected == "ERROR":
            return LlmResult(
                invocation_id=request.invocation_id,
                status="PROVIDER_ERROR",
                actual_provider="FIXTURE",
                actual_model=model_id,
                latency_ms=2,
                schema_validation="NOT_RUN",
            )
        assert isinstance(selected, dict)
        encoded = json.dumps(selected, sort_keys=True, separators=(",", ":"))
        return LlmResult(
            invocation_id=request.invocation_id,
            status="SUCCEEDED",
            actual_provider="FIXTURE",
            actual_model=model_id,
            output_json=selected,
            raw_response_hash=hashlib.sha256(encoded.encode()).hexdigest(),
            latency_ms=2,
            input_tokens=10,
            output_tokens=5,
            schema_validation="PASSED",
        )


def _ready(client, db: Session, admin: User, monkeypatch):
    run, _, settings = _admit(client, db, admin, monkeypatch)
    context = _complete_upstream(db, run, monkeypatch, settings)
    materialize_decision_agent_stages(db, run_id=run.id, now=datetime.now(UTC))
    return run, context


def _run_decisions(db: Session, count: int = 3) -> None:
    for index in range(count):
        assert process_agent_work_once(
            db,
            worker_id=f"phase7d-{index}",
            lease_seconds=60,
            now=datetime.now(UTC),
        )


def _results(db: Session, run_id: str) -> dict[str, DecisionAgentResult]:
    stages = list(
        db.scalars(
            select(AgentStageRun).where(
                AgentStageRun.run_id == run_id,
                AgentStageRun.role.in_(DECISION_AGENT_ROLES),
            )
        )
    )
    return {
        stage.role: DecisionAgentResult.model_validate_json(stage.output_json)
        for stage in stages
        if stage.output_json is not None
    }


def test_three_agents_execute_through_production_dispatch_without_authority(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, context = _ready(client, db, admin, monkeypatch)
    adapter = DecisionFixtureAdapter(db)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)

    _run_decisions(db)

    results = _results(db, run.id)
    assert set(results) == set(DECISION_AGENT_ROLES)
    assert all(item.status == "SUCCEEDED" and item.action == "BUY" for item in results.values())
    assert all(item.decision_context_id == context.id for item in results.values())
    assert len({item.agent_type for item in results.values()}) == 3
    assert all(state is False for state in adapter.transaction_states)
    assert [request.role for request in adapter.requests] == list(DECISION_AGENT_ROLES)
    for request in adapter.requests:
        payload = DecisionAgentInput.model_validate_json(request.messages[-1]["content"])
        assert payload.agent.role == request.role
        assert request.input_schema_version == "decision-agent-input-v1"
        assert request.tool_policy == "NONE" and request.allowed_tools == []
        assert request.input_hash == hashlib.sha256(
            request.messages[-1]["content"].encode()
        ).hexdigest()
    stages = list(
        db.scalars(
            select(AgentStageRun).where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role.in_(DECISION_AGENT_ROLES),
            )
        )
    )
    assert all(
        stage.output_hash == hashlib.sha256(stage.output_json.encode()).hexdigest()
        for stage in stages
        if stage.output_json
    )
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(AgentStageRun)
        .where(AgentStageRun.run_id == run.id, AgentStageRun.role == "ENTRY_ARBITER")
    ) == 0


def test_timeout_provider_error_and_invalid_output_are_structured(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    invalid = _valid_output()
    invalid["action"] = "UNKNOWN"
    adapter = DecisionFixtureAdapter(
        db,
        outputs={
            "CONSERVATIVE_DECISION": "TIMEOUT",
            "BALANCED_DECISION": "ERROR",
            "AGGRESSIVE_DECISION": invalid,
        },
    )
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)

    _run_decisions(db)

    results = _results(db, run.id)
    assert results["CONSERVATIVE_DECISION"].status == "TIMED_OUT"
    assert results["CONSERVATIVE_DECISION"].reason_codes == [
        "DECISION_AGENT_PROVIDER_TIMEOUT"
    ]
    assert results["BALANCED_DECISION"].status == "FAILED"
    assert results["BALANCED_DECISION"].reason_codes == [
        "DECISION_AGENT_PROVIDER_ERROR"
    ]
    assert results["AGGRESSIVE_DECISION"].status == "INVALID_OUTPUT"
    assert all(
        item.action == "UNKNOWN"
        and item.confidence == 0
        and item.entry_score is None
        and item.risk_score is None
        for item in results.values()
    )
    assert db.scalar(
        select(func.count())
        .select_from(LlmInvocation)
        .join(AgentStageRun, AgentStageRun.id == LlmInvocation.stage_run_id)
        .where(AgentStageRun.role.in_(DECISION_AGENT_ROLES))
    ) == 3


def test_valid_insufficient_data_result_remains_non_success_unknown(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    insufficient = {
        "schema_version": "decision-agent-model-output-v1",
        "status": "INSUFFICIENT_DATA",
        "action": "UNKNOWN",
        "confidence": 0.0,
        "entry_score": None,
        "risk_score": None,
        "reason_codes": ["EVIDENCE_INSUFFICIENT"],
        "positive_evidence_refs": [],
        "negative_evidence_refs": [],
    }
    adapter = DecisionFixtureAdapter(
        db, outputs={"CONSERVATIVE_DECISION": insufficient}
    )
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)

    _run_decisions(db, 1)

    result = _results(db, run.id)["CONSERVATIVE_DECISION"]
    assert result.status == "INSUFFICIENT_DATA"
    assert result.action == "UNKNOWN" and result.confidence == 0
    assert result.entry_score is None and result.risk_score is None


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"reason_codes": ["NOT_ALLOWED"]}, "DECISION_AGENT_REASON_NOT_ALLOWED"),
        (
            {"reason_codes": ["DECISION_AGENT_PROVIDER_ERROR"]},
            "DECISION_AGENT_REASON_NOT_ALLOWED",
        ),
        (
            {"reason_codes": ["PRICE_ABOVE_VWAP"]},
            "DECISION_AGENT_REASON_NOT_ALLOWED",
        ),
        (
            {"positive_evidence_refs": ["not-frozen"]},
            "DECISION_AGENT_EVIDENCE_NOT_ALLOWED",
        ),
        (
            {
                "positive_evidence_refs": ["same"],
                "negative_evidence_refs": ["same"],
            },
            "DECISION_AGENT_EVIDENCE_NOT_ALLOWED",
        ),
        ({"server_owned": "forbidden"}, "DECISION_AGENT_OUTPUT_SCHEMA_INVALID"),
    ],
)
def test_invalid_reason_evidence_overlap_and_extra_field_are_rejected(
    client, db: Session, admin: User, monkeypatch, mutation, reason
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    output = {**_valid_output(), **mutation}
    adapter = DecisionFixtureAdapter(
        db, outputs={"CONSERVATIVE_DECISION": output}
    )
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)

    _run_decisions(db, 1)

    result = _results(db, run.id)["CONSERVATIVE_DECISION"]
    assert result.status == "INVALID_OUTPUT"
    assert result.reason_codes == [reason]


@pytest.mark.parametrize(
    "target",
    ["context", "scout", "bundle", "candidate", "policy", "prompt", "stage_input"],
)
def test_pre_call_provenance_conflict_skips_provider(
    client, db: Session, admin: User, monkeypatch, target: str
) -> None:
    run, context = _ready(client, db, admin, monkeypatch)
    stage = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == "CONSERVATIVE_DECISION",
        )
    )
    assert stage is not None
    if target == "context":
        context.manifest_json = context.manifest_json + " "
    elif target == "scout":
        scout = db.get(AgentStageRun, context.technical_scout_stage_id)
        assert scout is not None
        scout.output_hash = "0" * 64
    elif target == "bundle":
        from app.models import EvidenceBundle

        bundle = db.get(EvidenceBundle, context.evidence_bundle_id)
        assert bundle is not None
        bundle.bundle_hash = "0" * 64
    elif target == "candidate":
        candidate = db.get(AgentStageRun, context.candidate_audit_stage_id)
        assert candidate is not None
        candidate.output_hash = "0" * 64
    elif target == "policy":
        policy_id = next(
            item["configuration_version_id"]
            for item in json.loads(run.policy_profile_version_map_json)["profiles"]
            if item["agent_type"] == "CONSERVATIVE"
        )
        policy = db.get(ConfigurationVersion, policy_id)
        assert policy is not None
        policy.payload_hash = "0" * 64
    elif target == "prompt":
        prompt_id = json.loads(run.route_versions_json)[stage.role]["prompt_profile_id"]
        prompt = db.get(LlmPromptProfile, prompt_id)
        assert prompt is not None
        prompt.system_prompt += " tampered"
    else:
        stage.input_hash = "0" * 64
    db.commit()
    adapter = DecisionFixtureAdapter(db)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)

    _run_decisions(db, 1)

    result = _results(db, run.id)["CONSERVATIVE_DECISION"]
    assert result.status == "CONFLICTED" and result.action == "UNKNOWN"
    assert adapter.requests == []


def test_completion_expiry_discards_provider_success(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, context = _ready(client, db, admin, monkeypatch)

    def expire(_request: LlmRequest) -> None:
        context.valid_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    adapter = DecisionFixtureAdapter(db, during_call=expire)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)

    _run_decisions(db, 1)

    result = _results(db, run.id)["CONSERVATIVE_DECISION"]
    assert result.status == "TIMED_OUT"
    assert result.action == "UNKNOWN"
    assert result.reason_codes == ["DECISION_AGENT_CONTEXT_EXPIRED"]


def test_policy_supersession_during_call_preserves_frozen_execution(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    policy_id = next(
        item["configuration_version_id"]
        for item in json.loads(run.policy_profile_version_map_json)["profiles"]
        if item["agent_type"] == "CONSERVATIVE"
    )

    def supersede(_request: LlmRequest) -> None:
        policy = db.get(ConfigurationVersion, policy_id)
        assert policy is not None
        policy.state = "SUPERSEDED"
        db.commit()

    adapter = DecisionFixtureAdapter(db, during_call=supersede)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)

    _run_decisions(db, 1)

    assert _results(db, run.id)["CONSERVATIVE_DECISION"].status == "SUCCEEDED"


def test_route_supersession_during_call_preserves_frozen_execution(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    route_id = json.loads(run.route_versions_json)["CONSERVATIVE_DECISION"]["route_id"]

    def supersede(_request: LlmRequest) -> None:
        route = db.get(LlmRoleRoute, route_id)
        assert route is not None
        route.state = "SUPERSEDED"
        route.version += 1
        db.commit()

    adapter = DecisionFixtureAdapter(db, during_call=supersede)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)

    _run_decisions(db, 1)

    result = _results(db, run.id)["CONSERVATIVE_DECISION"]
    assert result.status == "SUCCEEDED"
    assert result.route_id == route_id
    assert result.route_version == json.loads(run.route_versions_json)[
        "CONSERVATIVE_DECISION"
    ]["route_version"]


def test_one_timeout_isolated_between_two_successful_agents(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)
    adapter = DecisionFixtureAdapter(
        db, outputs={"BALANCED_DECISION": "TIMEOUT"}
    )
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)

    _run_decisions(db)

    results = _results(db, run.id)
    assert results["CONSERVATIVE_DECISION"].status == "SUCCEEDED"
    assert results["CONSERVATIVE_DECISION"].action == "BUY"
    assert results["BALANCED_DECISION"].status == "TIMED_OUT"
    assert results["BALANCED_DECISION"].action == "UNKNOWN"
    assert results["AGGRESSIVE_DECISION"].status == "SUCCEEDED"
    assert results["AGGRESSIVE_DECISION"].action == "BUY"
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(AgentStageRun)
        .where(AgentStageRun.run_id == run.id, AgentStageRun.role == "ENTRY_ARBITER")
    ) == 0


def test_stale_fencing_completion_cannot_overwrite_stage(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _ready(client, db, admin, monkeypatch)

    def replace_fence(request: LlmRequest) -> None:
        invocation = db.get(LlmInvocation, request.invocation_id)
        assert invocation is not None
        stage = db.get(AgentStageRun, invocation.stage_run_id)
        assert stage is not None
        stage.fencing_token += 1
        stage.lease_owner_id = "replacement-worker"
        db.commit()

    adapter = DecisionFixtureAdapter(db, during_call=replace_fence)
    monkeypatch.setattr(provider_registry, "resolve", lambda *args, **kwargs: adapter)

    assert not process_agent_work_once(
        db,
        worker_id="stale-worker",
        lease_seconds=60,
        now=datetime.now(UTC),
    )

    stage = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == "CONSERVATIVE_DECISION",
        )
    )
    assert stage is not None
    assert stage.output_json is None and stage.output_hash is None
    assert stage.lease_owner_id == "replacement-worker"
