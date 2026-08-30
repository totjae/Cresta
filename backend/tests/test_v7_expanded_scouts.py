from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.agents.runtime as runtime_module
import app.agents.worker as worker_module
from app.agents.decision_agents import V7_LLM_ROUTE_ROLES
from app.agents.decision_context import (
    SCOUT_ROLES,
    DecisionContextFreezeError,
    freeze_decision_context,
)
from app.agents.runtime import AgentRuntimeError, _hash, create_v7_upstream_diagnostic_run
from app.agents.worker import (
    _v7_scout_role_input_hash,
    _v7_scout_role_input_material,
    process_agent_work_once,
)
from app.config import Settings
from app.llm.contracts import EvidenceSourceCandidate, LlmResult
from app.models import (
    AgentRun,
    AgentStageRun,
    Approval,
    Decision,
    DecisionContext,
    EvidenceBundle,
    EvidenceItem,
    IndicatorSnapshot,
    LlmInvocation,
    LlmRoleRoute,
    MarketContextSnapshot,
    Position,
    TradingOrder,
    User,
)
from tests.test_agent_external_output import (
    ExternalFixtureAdapter,
    _external_routes,
    _run_until_terminal,
)
from tests.test_agent_runtime import _market_context_fixture, _market_fixture
from tests.test_policy_profile_admission import _all_profiles
from tests.test_v7_technical_scout import _krx_fixture
from tests.test_v7_upstream_runtime import _extend_v7_decision_routes


def _prepare_v7(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    *,
    with_market_context: bool = False,
    search_role: str | None = None,
    source_candidates: list[EvidenceSourceCandidate] | None = None,
    **adapter_options: str,
) -> tuple[AgentRun, ExternalFixtureAdapter]:
    _market_fixture(db)
    if with_market_context:
        _market_context_fixture(db, source_ref="phase6-context")
    _all_profiles(db, admin)
    db.commit()
    route_ids, adapter, csrf = _external_routes(
        client,
        db,
        monkeypatch,
        source_candidates=source_candidates,
        **adapter_options,
    )
    _extend_v7_decision_routes(client, db, route_ids, csrf)
    if search_role is not None:
        route = db.get(LlmRoleRoute, route_ids[search_role])
        assert route is not None
        route.web_search_enabled = True
        db.commit()
    settings = Settings(quote_stale_seconds=30)
    monkeypatch.setattr(runtime_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        worker_module,
        "get_settings",
        lambda: settings.model_copy(update={"krx_enabled": True}),
    )
    monkeypatch.setattr(
        worker_module,
        "collect_krx_daily_market",
        lambda *args, **kwargs: _krx_fixture(),
    )
    run, created = create_v7_upstream_diagnostic_run(
        db,
        user=admin,
        market="KRX",
        symbol="005930",
        route_ids={role: route_ids[role] for role in V7_LLM_ROUTE_ROLES},
        now=datetime.now(UTC),
    )
    assert created is True
    return run, adapter


def _work(db: Session, count: int) -> None:
    for index in range(count):
        process_agent_work_once(
            db,
            worker_id=f"phase6-worker-{index}",
            lease_seconds=30,
            now=datetime.now(UTC),
        )


def _stage(db: Session, run: AgentRun, role: str) -> AgentStageRun:
    stage = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == role,
        )
    )
    assert stage is not None
    return stage


def _trading_resource_count(db: Session) -> int:
    return sum(
        db.scalar(select(func.count()).select_from(model)) or 0
        for model in (Decision, Approval, TradingOrder)
    )


def test_v7_news_and_market_provider_payloads_are_role_scoped_and_context_compatible(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, adapter = _prepare_v7(
        client, db, admin, monkeypatch, with_market_context=True
    )
    _work(db, 7)

    requests = {request.role: request for request in adapter.requests}
    news_input = json.loads(requests["NEWS_DISCLOSURE_SCOUT"].messages[-1]["content"])
    market_input = json.loads(requests["MARKET_SECTOR_SCOUT"].messages[-1]["content"])
    news = _stage(db, run, "NEWS_DISCLOSURE_SCOUT")
    market = _stage(db, run, "MARKET_SECTOR_SCOUT")

    assert json.loads(news.output_json or "{}")["schema_version"] == "agent-assessment-v2"
    assert json.loads(market.output_json or "{}")["schema_version"] == "agent-assessment-v2"
    assert news.input_hash == _hash(news_input["scout_role_input"])
    assert market.input_hash == _hash(market_input["scout_role_input"])
    assert news_input["scout_role_input"]["role"] == "NEWS_DISCLOSURE_SCOUT"
    assert market_input["scout_role_input"]["role"] == "MARKET_SECTOR_SCOUT"
    assert "indicator_snapshot" not in news_input
    assert "position" not in news_input
    assert "indicator_snapshot" not in market_input
    assert "position" not in market_input
    assert market_input["market_context_snapshot"]["quality"] == "NORMAL"
    assert requests["NEWS_DISCLOSURE_SCOUT"].tool_policy == "NONE"
    assert requests["NEWS_DISCLOSURE_SCOUT"].allowed_tools == []
    assert db.scalar(
        select(func.count()).select_from(DecisionContext).where(DecisionContext.run_id == run.id)
    ) == 1
    assert _trading_resource_count(db) == 0


@pytest.mark.parametrize("role", ("NEWS_DISCLOSURE_SCOUT", "MARKET_SECTOR_SCOUT"))
def test_v7_allowed_search_persists_unrated_candidate_without_bundle_promotion(
    client, db: Session, admin: User, monkeypatch, role: str
) -> None:
    candidate = EvidenceSourceCandidate(
        url=f"https://example.com/{role.lower()}",
        title="Phase 6 external candidate",
        published_at=datetime.now(UTC).isoformat(),
    )
    run, adapter = _prepare_v7(
        client,
        db,
        admin,
        monkeypatch,
        with_market_context=True,
        search_role=role,
        source_candidates=[candidate],
    )
    _work(db, 2)
    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run.id))
    assert bundle is not None
    original_ids = bundle.evidence_ids_json
    original_hash = bundle.bundle_hash
    _work(db, 5)

    request = next(item for item in adapter.requests if item.role == role)
    candidates = list(
        db.scalars(
            select(EvidenceItem).where(
                EvidenceItem.run_id == run.id,
                EvidenceItem.source_tier == "UNRATED",
            )
        )
    )
    auditor = json.loads(_stage(db, run, "EVIDENCE_CANDIDATE_AUDITOR").output_json or "{}")
    db.refresh(bundle)

    assert request.tool_policy == "ALLOWLIST"
    assert request.allowed_tools == ["WEB_SEARCH"]
    assert len(candidates) == 1
    assert candidates[0].source_url == candidate.url
    assert bundle.evidence_ids_json == original_ids
    assert bundle.bundle_hash == original_hash
    assert candidates[0].id not in json.loads(bundle.evidence_ids_json)
    assert auditor["candidate_ids"] == [candidates[0].id]
    context = db.scalar(select(DecisionContext).where(DecisionContext.run_id == run.id))
    assert context is not None
    assert candidates[0].id not in context.manifest_json


def test_v7_news_rejects_unrated_candidate_as_verified_evidence_ref(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, adapter = _prepare_v7(
        client, db, admin, monkeypatch, search_role="NEWS_DISCLOSURE_SCOUT"
    )
    _work(db, 2)
    candidate = EvidenceItem(
        run_id=run.id,
        market=run.market,
        symbol=run.symbol,
        source_type="WEB",
        source_tier="UNRATED",
        source_name="EXTERNAL_FIXTURE",
        source_url="https://example.com/unrated",
        title="unrated",
        facts_json="[]",
        content_hash="7" * 64,
        extraction_method="RULE",
        received_at=datetime.now(UTC),
    )
    db.add(candidate)
    db.commit()
    original = adapter.generate_structured

    def use_candidate_as_evidence(request, model_id):
        result = original(request, model_id)
        if request.role != "NEWS_DISCLOSURE_SCOUT" or result.output_json is None:
            return result
        return result.model_copy(
            update={"output_json": {**result.output_json, "evidence_refs": [candidate.id]}}
        )

    monkeypatch.setattr(adapter, "generate_structured", use_candidate_as_evidence)
    _work(db, 5)
    news = _stage(db, run, "NEWS_DISCLOSURE_SCOUT")
    invocation = db.get(LlmInvocation, news.invocation_id)

    assert news.state == "FAILED"
    assert invocation is not None
    assert invocation.error_code == "LLM_EVIDENCE_REF_NOT_ALLOWED"
    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run.id))
    assert bundle is not None and candidate.id not in json.loads(bundle.evidence_ids_json)
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0
    assert _trading_resource_count(db) == 0


@pytest.mark.parametrize(
    ("adapter_option", "provider_status", "error_code"),
    (
        ("invalid_role", None, "LLM_SCHEMA_VALIDATION_FAILED"),
        ("invalid_reason_role", None, "LLM_REASON_CODE_NOT_ALLOWED"),
        ("invalid_evidence_role", None, "LLM_EVIDENCE_REF_NOT_ALLOWED"),
        (None, "TIMED_OUT", "LLM_TIMED_OUT"),
        (None, "PROVIDER_ERROR", "LLM_PROVIDER_ERROR"),
    ),
)
def test_v7_news_failures_are_isolated_and_fail_closed(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    adapter_option: str | None,
    provider_status: str | None,
    error_code: str,
) -> None:
    options = {adapter_option: "NEWS_DISCLOSURE_SCOUT"} if adapter_option else {}
    run, adapter = _prepare_v7(client, db, admin, monkeypatch, **options)
    if provider_status is not None:
        original = adapter.generate_structured

        def fail_news(request, model_id):
            if request.role != "NEWS_DISCLOSURE_SCOUT":
                return original(request, model_id)
            adapter.requests.append(request)
            return LlmResult(
                invocation_id=request.invocation_id,
                status=provider_status,
                actual_provider="EXTERNAL_FIXTURE",
                actual_model=model_id,
                output_json=None,
                latency_ms=2,
                schema_validation="FAILED",
            )

        monkeypatch.setattr(adapter, "generate_structured", fail_news)
    _work(db, 7)

    news = _stage(db, run, "NEWS_DISCLOSURE_SCOUT")
    invocation = db.get(LlmInvocation, news.invocation_id)
    technical = _stage(db, run, "TECHNICAL_SCOUT")
    market = _stage(db, run, "MARKET_SECTOR_SCOUT")
    position = _stage(db, run, "POSITION_RISK_SCOUT")

    assert news.state == "FAILED"
    assert invocation is not None and invocation.error_code == error_code
    assert technical.state == "SUCCEEDED"
    assert market.state == "INSUFFICIENT_DATA"
    assert position.state == "NOT_APPLICABLE"
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0
    assert _trading_resource_count(db) == 0


@pytest.mark.parametrize("mutation", ("HASH", "QUALITY", "EXPIRY"))
def test_v7_market_context_mutation_fails_before_provider(
    client, db: Session, admin: User, monkeypatch, mutation: str
) -> None:
    run, adapter = _prepare_v7(
        client, db, admin, monkeypatch, with_market_context=True
    )
    context = db.get(MarketContextSnapshot, run.market_context_snapshot_id)
    assert context is not None
    if mutation == "HASH":
        context.payload_hash = "f" * 64
    elif mutation == "QUALITY":
        context.quality = "INCOMPLETE"
    else:
        context.valid_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    _work(db, 7)

    market = _stage(db, run, "MARKET_SECTOR_SCOUT")
    output = json.loads(market.output_json or "{}")
    assert market.state == "CONFLICTED"
    assert market.invocation_id is None
    assert output["stance"] == "UNKNOWN"
    assert output["entry_score"] is None and output["exit_risk_score"] is None
    assert output["reason_codes"] == ["INPUT_DATA_CONFLICTED"]
    assert not any(request.role == "MARKET_SECTOR_SCOUT" for request in adapter.requests)
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0
    assert _trading_resource_count(db) == 0


def test_v7_role_hashes_track_only_frozen_role_provenance_and_ignore_policy_outputs(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _prepare_v7(client, db, admin, monkeypatch, with_market_context=True)
    _work(db, 2)
    stages = {role: _stage(db, run, role) for role in SCOUT_ROLES}
    hashes = {
        role: _v7_scout_role_input_hash(db, run=run, stage=stage)
        for role, stage in stages.items()
    }
    materials = {
        role: _v7_scout_role_input_material(db, run=run, stage=stage)
        for role, stage in stages.items()
    }

    assert materials["NEWS_DISCLOSURE_SCOUT"]["indicator_provenance"] is None
    assert materials["MARKET_SECTOR_SCOUT"]["position_provenance"] is None
    assert materials["POSITION_RISK_SCOUT"]["market_context_provenance"] is None
    changed_market = json.loads(json.dumps(materials["MARKET_SECTOR_SCOUT"]))
    changed_market["market_context_provenance"]["payload_hash"] = "a" * 64
    changed_position = json.loads(json.dumps(materials["POSITION_RISK_SCOUT"]))
    changed_position["position_provenance"]["position_snapshot_hash"] = "b" * 64
    changed_news = json.loads(json.dumps(materials["NEWS_DISCLOSURE_SCOUT"]))
    changed_news["evidence_bundle_hash"] = "9" * 64
    assert _hash(changed_market) != hashes["MARKET_SECTOR_SCOUT"]
    assert _hash(changed_position) != hashes["POSITION_RISK_SCOUT"]
    assert _hash(changed_news) != hashes["NEWS_DISCLOSURE_SCOUT"]

    original_position_json = run.position_snapshot_json
    original_position_hash = run.position_snapshot_hash
    run.position_snapshot_json = '{"marker":"NO_OPEN_POSITION","version":2}'
    assert _v7_scout_role_input_hash(
        db, run=run, stage=stages["POSITION_RISK_SCOUT"]
    ) != hashes["POSITION_RISK_SCOUT"]
    assert _v7_scout_role_input_hash(
        db, run=run, stage=stages["MARKET_SECTOR_SCOUT"]
    ) == hashes["MARKET_SECTOR_SCOUT"]
    run.position_snapshot_json = original_position_json
    run.position_snapshot_hash = original_position_hash

    indicator = db.scalar(
        select(IndicatorSnapshot).where(
            IndicatorSnapshot.market_snapshot_id == run.market_snapshot_id
        )
    )
    assert indicator is not None
    indicator.calculator_version = "irrelevant-to-news"
    run.policy_profile_version_map_json = '{"changed":"irrelevant"}'
    run.policy_profile_version_map_hash = "c" * 64
    stages["TECHNICAL_SCOUT"].output_json = '{"changed":"irrelevant"}'
    stages["TECHNICAL_SCOUT"].output_hash = "d" * 64
    db.add(
        EvidenceItem(
            run_id=run.id,
            market=run.market,
            symbol=run.symbol,
            source_type="WEB",
            source_tier="UNRATED",
            source_name="IRRELEVANT_CANDIDATE",
            source_url="https://example.com/irrelevant-candidate",
            title="irrelevant candidate",
            facts_json="[]",
            content_hash="e" * 64,
            extraction_method="RULE",
            received_at=datetime.now(UTC),
        )
    )
    db.flush()

    assert _v7_scout_role_input_hash(
        db, run=run, stage=stages["NEWS_DISCLOSURE_SCOUT"]
    ) == hashes["NEWS_DISCLOSURE_SCOUT"]
    assert _v7_scout_role_input_hash(
        db, run=run, stage=stages["MARKET_SECTOR_SCOUT"]
    ) == hashes["MARKET_SECTOR_SCOUT"]
    assert _v7_scout_role_input_hash(
        db, run=run, stage=stages["POSITION_RISK_SCOUT"]
    ) == hashes["POSITION_RISK_SCOUT"]


def test_v7_entry_position_risk_is_explicit_not_applicable_without_tools(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, adapter = _prepare_v7(client, db, admin, monkeypatch)
    _work(db, 6)
    position = _stage(db, run, "POSITION_RISK_SCOUT")
    output = json.loads(position.output_json or "{}")

    assert position.state == "NOT_APPLICABLE"
    assert position.invocation_id is None
    assert output["status"] == "NOT_APPLICABLE"
    assert output["stance"] == "UNKNOWN"
    assert output["entry_score"] is None and output["exit_risk_score"] is None
    assert output["reason_codes"] == ["OPEN_POSITION_NOT_FOUND"]
    assert not any(request.role == "POSITION_RISK_SCOUT" for request in adapter.requests)


def test_v7_missing_position_stage_is_not_treated_as_not_applicable(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _prepare_v7(client, db, admin, monkeypatch)
    _work(db, 6)
    position = _stage(db, run, "POSITION_RISK_SCOUT")
    db.delete(position)
    db.commit()
    _work(db, 1)

    with pytest.raises(DecisionContextFreezeError):
        freeze_decision_context(db, run_id=run.id, now=datetime.now(UTC))

    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0


def test_v7_position_web_search_route_is_rejected_at_admission(
    client, db: Session, admin: User, monkeypatch
) -> None:
    _market_fixture(db)
    _all_profiles(db, admin)
    db.commit()
    route_ids, _, csrf = _external_routes(client, db, monkeypatch)
    _extend_v7_decision_routes(client, db, route_ids, csrf)
    route = db.get(LlmRoleRoute, route_ids["POSITION_RISK_SCOUT"])
    assert route is not None
    route.web_search_enabled = True
    db.commit()
    monkeypatch.setattr(
        runtime_module, "get_settings", lambda: Settings(quote_stale_seconds=30)
    )

    with pytest.raises(AgentRuntimeError, match="AGENT_ROUTE_NOT_READY"):
        create_v7_upstream_diagnostic_run(
            db,
            user=admin,
            market="KRX",
            symbol="005930",
            route_ids={role: route_ids[role] for role in V7_LLM_ROUTE_ROLES},
            now=datetime.now(UTC),
        )

    assert db.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_v6_position_provider_schema_failure_remains_fail_closed(
    client, db: Session, monkeypatch
) -> None:
    _market_fixture(db)
    db.add(
        Position(
            account_alias="KIWOOM_MOCK_PRIMARY",
            symbol="005930",
            quantity=3,
            available_quantity=3,
            average_price=Decimal(69500),
            managed_quantity=3,
            managed_average_price=Decimal(69500),
            state="OPEN",
        )
    )
    db.commit()
    route_ids, _, csrf = _external_routes(
        client, db, monkeypatch, invalid_role="POSITION_RISK_SCOUT"
    )
    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": route_ids,
        },
    )
    assert response.status_code == 201
    run = _run_until_terminal(db, response.json()["run_id"])
    position = _stage(db, run, "POSITION_RISK_SCOUT")
    invocation = db.get(LlmInvocation, position.invocation_id)

    assert position.state == "FAILED"
    assert invocation is not None
    assert invocation.error_code == "LLM_SCHEMA_VALIDATION_FAILED"
    assert _trading_resource_count(db) == 0
