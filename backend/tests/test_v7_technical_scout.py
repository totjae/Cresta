from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.agents.runtime as runtime_module
import app.agents.worker as worker_module
from app.agents.decision_agents import V7_LLM_ROUTE_ROLES
from app.agents.krx import KrxCollection, KrxDailyMarket
from app.agents.runtime import _hash, create_v7_upstream_diagnostic_run
from app.agents.worker import (
    _v7_scout_role_input_hash,
    _v7_scout_role_input_material,
    process_agent_work_once,
)
from app.config import Settings
from app.llm.contracts import LlmResult
from app.models import (
    AgentRun,
    AgentStageRun,
    Approval,
    Decision,
    DecisionContext,
    EvidenceItem,
    IndicatorSnapshot,
    LlmInvocation,
    TradingOrder,
    User,
)
from tests.test_agent_external_output import ExternalFixtureAdapter, _external_routes
from tests.test_agent_runtime import _market_fixture
from tests.test_policy_profile_admission import _all_profiles
from tests.test_v7_upstream_runtime import _extend_v7_decision_routes


def _krx_fixture() -> KrxCollection:
    today = datetime.now(UTC).strftime("%Y%m%d")
    return KrxCollection(
        item=KrxDailyMarket(
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
        ),
        dates_queried=(today,),
        requests_made=1,
    )


def _prepare_v7(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    **adapter_options: str,
) -> tuple[AgentRun, ExternalFixtureAdapter]:
    _market_fixture(db)
    _all_profiles(db, admin)
    db.commit()
    route_ids, adapter, csrf = _external_routes(client, db, monkeypatch, **adapter_options)
    _extend_v7_decision_routes(client, db, route_ids, csrf)
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
            worker_id=f"phase5-worker-{index}",
            lease_seconds=30,
            now=datetime.now(UTC),
        )


def _stage(db: Session, run: AgentRun, role: str) -> AgentStageRun:
    stage = db.scalar(
        select(AgentStageRun).where(AgentStageRun.run_id == run.id, AgentStageRun.role == role)
    )
    assert stage is not None
    return stage


def test_v7_technical_provider_path_produces_context_compatible_v2_assessment(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, adapter = _prepare_v7(client, db, admin, monkeypatch)
    _work(db, 7)
    technical = _stage(db, run, "TECHNICAL_SCOUT")
    output = json.loads(technical.output_json or "{}")
    invocation = db.get(LlmInvocation, technical.invocation_id)
    request = next(item for item in adapter.requests if item.role == "TECHNICAL_SCOUT")
    provider_input = json.loads(request.messages[-1]["content"])
    role_material = provider_input["scout_role_input"]

    assert output["schema_version"] == "agent-assessment-v2"
    assert output["stage_run_id"] == technical.id
    assert output["role"] == "TECHNICAL_SCOUT"
    assert output["status"] == "SUCCEEDED"
    assert output["entry_score"] == 50
    assert output["valid_until"] == run.valid_until.isoformat()
    assert technical.input_hash == _hash(role_material)
    assert role_material["role"] == "TECHNICAL_SCOUT"
    assert role_material["scout_input_hash"] == run.input_hash
    assert role_material["indicator_provenance"]["snapshot_id"] in output["input_refs"]
    assert role_material["market_context_provenance"] is None
    assert provider_input["indicator_snapshot"]["vwap"] == "69900.0000"
    assert provider_input["position"] is None
    assert "policy_profile" not in json.dumps(provider_input).lower()
    assert request.tool_policy == "NONE"
    assert request.allowed_tools == []
    assert invocation is not None
    assert invocation.web_search_enabled is False
    assert invocation.validation_status == "PASSED"
    assert invocation.input_hash == technical.input_hash
    assert invocation.model_output_hash is not None
    assert technical.output_hash is not None
    assert (
        db.scalar(
            select(func.count())
            .select_from(DecisionContext)
            .where(DecisionContext.run_id == run.id)
        )
        == 1
    )
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_v7_technical_hash_tracks_indicator_and_ignores_policy_and_other_scouts(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _prepare_v7(client, db, admin, monkeypatch)
    _work(db, 2)
    technical = _stage(db, run, "TECHNICAL_SCOUT")
    other = _stage(db, run, "NEWS_DISCLOSURE_SCOUT")
    material = _v7_scout_role_input_material(db, run=run, stage=technical)
    original = _v7_scout_role_input_hash(db, run=run, stage=technical)

    changed = json.loads(json.dumps(material))
    changed["indicator_provenance"]["payload_hash"] = "f" * 64
    assert _hash(changed) != original

    run.policy_profile_version_map_json = '{"changed":"irrelevant"}'
    run.policy_profile_version_map_hash = "e" * 64
    other.output_json = '{"changed":"irrelevant"}'
    other.output_hash = "d" * 64
    assert _v7_scout_role_input_hash(db, run=run, stage=technical) == original


@pytest.mark.parametrize(
    ("option", "error_code"),
    (
        ("invalid_evidence_role", "LLM_EVIDENCE_REF_NOT_ALLOWED"),
        ("invalid_reason_role", "LLM_REASON_CODE_NOT_ALLOWED"),
    ),
)
def test_v7_technical_rejects_invalid_evidence_and_reason_codes(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    option: str,
    error_code: str,
) -> None:
    run, _ = _prepare_v7(client, db, admin, monkeypatch, **{option: "TECHNICAL_SCOUT"})
    _work(db, 3)
    technical = _stage(db, run, "TECHNICAL_SCOUT")
    invocation = db.get(LlmInvocation, technical.invocation_id)

    assert technical.state == "FAILED"
    assert technical.error_code == "AGENT_LLM_FAIL_STOP"
    assert invocation is not None
    assert invocation.state == "INVALID_OUTPUT"
    assert invocation.validation_status == "FAILED"
    assert invocation.error_code == error_code
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_v7_technical_rejects_evidence_from_another_run(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, adapter = _prepare_v7(client, db, admin, monkeypatch)
    other_run = AgentRun(
        owner_id=admin.id,
        purpose="DIAGNOSTIC",
        execution_stage="SHADOW",
        market=run.market,
        symbol=run.symbol,
        market_snapshot_id=run.market_snapshot_id,
        input_hash="9" * 64,
        dag_version="agent-dag-v6",
        route_versions_json="{}",
        idempotency_key="8" * 64,
        state="RUNNING",
        analysis_context="ENTRY",
        valid_until=run.valid_until,
    )
    db.add(other_run)
    db.flush()
    foreign_evidence = EvidenceItem(
        run_id=other_run.id,
        market=run.market,
        symbol=run.symbol,
        source_type="KRX_DAILY_MARKET",
        source_tier="PRIMARY",
        source_name="FOREIGN_RUN_FIXTURE",
        title="foreign run evidence",
        facts_json="[]",
        content_hash="7" * 64,
        extraction_method="RULE",
        event_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
    )
    db.add(foreign_evidence)
    db.commit()
    original = adapter.generate_structured

    def inject_foreign_ref(request, model_id):
        result = original(request, model_id)
        if request.role != "TECHNICAL_SCOUT" or result.output_json is None:
            return result
        return result.model_copy(
            update={
                "output_json": {
                    **result.output_json,
                    "evidence_refs": [foreign_evidence.id],
                }
            }
        )

    monkeypatch.setattr(adapter, "generate_structured", inject_foreign_ref)
    _work(db, 3)
    technical = _stage(db, run, "TECHNICAL_SCOUT")
    invocation = db.get(LlmInvocation, technical.invocation_id)

    assert technical.state == "FAILED"
    assert invocation is not None
    assert invocation.error_code == "LLM_EVIDENCE_REF_NOT_ALLOWED"
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0


def test_v7_technical_indicator_conflict_is_server_normalized_without_provider(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, adapter = _prepare_v7(client, db, admin, monkeypatch)
    indicator = db.scalar(
        select(IndicatorSnapshot).where(
            IndicatorSnapshot.market_snapshot_id == run.market_snapshot_id
        )
    )
    assert indicator is not None
    indicator.calculator_version = "corrupted-after-admission"
    db.commit()
    _work(db, 3)
    technical = _stage(db, run, "TECHNICAL_SCOUT")
    output = json.loads(technical.output_json or "{}")

    assert technical.state == "CONFLICTED"
    assert technical.invocation_id is None
    assert output["stance"] == "UNKNOWN"
    assert output["entry_score"] is None
    assert output["exit_risk_score"] is None
    assert output["reason_codes"] == ["INPUT_DATA_CONFLICTED"]
    assert not any(request.role == "TECHNICAL_SCOUT" for request in adapter.requests)
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_v7_technical_missing_indicator_rejects_admission_without_partial_run(
    db: Session, admin: User, monkeypatch
) -> None:
    snapshot = _market_fixture(db)
    indicator = db.scalar(
        select(IndicatorSnapshot).where(IndicatorSnapshot.market_snapshot_id == snapshot.id)
    )
    assert indicator is not None
    db.delete(indicator)
    db.commit()
    monkeypatch.setattr(runtime_module, "get_settings", lambda: Settings(quote_stale_seconds=30))

    with pytest.raises(ValueError, match="V7_SCOUT_INPUT_INDICATOR_INVALID"):
        create_v7_upstream_diagnostic_run(
            db,
            user=admin,
            market="KRX",
            symbol="005930",
            route_ids={},
            now=datetime.now(UTC),
        )

    assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert db.scalar(select(func.count()).select_from(AgentStageRun)) == 0


def test_v7_technical_schema_failure_creates_no_trading_resources(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _ = _prepare_v7(client, db, admin, monkeypatch, invalid_role="TECHNICAL_SCOUT")
    _work(db, 3)
    technical = _stage(db, run, "TECHNICAL_SCOUT")
    invocation = db.get(LlmInvocation, technical.invocation_id)

    assert technical.state == "FAILED"
    assert invocation is not None
    assert invocation.error_code == "LLM_SCHEMA_VALIDATION_FAILED"
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


@pytest.mark.parametrize("provider_status", ("TIMED_OUT", "PROVIDER_ERROR"))
def test_v7_technical_provider_failure_is_fail_closed(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    provider_status: str,
) -> None:
    run, adapter = _prepare_v7(client, db, admin, monkeypatch)
    original = adapter.generate_structured

    def fail_technical(request, model_id):
        if request.role != "TECHNICAL_SCOUT":
            return original(request, model_id)
        adapter.requests.append(request)
        return LlmResult(
            invocation_id=request.invocation_id,
            status=provider_status,
            actual_provider="EXTERNAL_FIXTURE",
            actual_model=model_id,
            output_json=None,
            raw_response_hash="c" * 64,
            latency_ms=2,
            schema_validation="FAILED",
        )

    monkeypatch.setattr(adapter, "generate_structured", fail_technical)
    _work(db, 3)
    technical = _stage(db, run, "TECHNICAL_SCOUT")
    invocation = db.get(LlmInvocation, technical.invocation_id)

    assert technical.state == "FAILED"
    assert technical.error_code == "AGENT_LLM_FAIL_STOP"
    assert invocation is not None
    assert invocation.state == provider_status
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
