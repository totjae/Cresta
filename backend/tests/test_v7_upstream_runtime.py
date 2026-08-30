from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.agents.runtime as runtime_module
import app.agents.worker as worker_module
from app.agents.decision_agents import DECISION_AGENT_ROLES, V7_LLM_ROUTE_ROLES
from app.agents.decision_context import SCOUT_ROLES, context_digest
from app.agents.krx import KrxCollection, KrxDailyMarket
from app.agents.runtime import (
    V7_DAG_VERSION,
    create_v7_upstream_diagnostic_run,
)
from app.agents.worker import (
    _v7_scout_role_input_hash,
    process_agent_work_once,
    reconcile_v7_upstream_contexts,
)
from app.config import Settings
from app.decision_inputs import build_v7_scout_input
from app.market_context import MarketContextInput, ingest_market_context
from app.models import (
    AgentRun,
    AgentStageRun,
    DecisionContext,
    DecisionInputSnapshot,
    EvidenceBundle,
    LlmInvocation,
    LlmRoleRoute,
    MarketStreamState,
    User,
)
from tests.test_agent_runtime import _login, _market_fixture, _routes
from tests.test_policy_profile_admission import _all_profiles

UPSTREAM_ROLES = {
    "INTEL_COLLECTOR",
    "EVIDENCE_VERIFIER",
    *SCOUT_ROLES,
    "EVIDENCE_CANDIDATE_AUDITOR",
}


def _admit(client, db: Session, admin: User, monkeypatch):
    _market_fixture(db)
    _all_profiles(db, admin)
    db.commit()
    csrf = _login(client)
    routes = _routes(client, {"Origin": "https://testserver", "X-CSRF-Token": csrf})
    _extend_v7_decision_routes(client, db, routes, csrf)
    settings = Settings(quote_stale_seconds=30)
    monkeypatch.setattr(runtime_module, "get_settings", lambda: settings)
    run, created = create_v7_upstream_diagnostic_run(
        db,
        user=admin,
        market="KRX",
        symbol="005930",
        route_ids={role: routes[role] for role in V7_LLM_ROUTE_ROLES},
        now=datetime.now(UTC),
    )
    return run, created, settings


def _extend_v7_decision_routes(
    client,
    db: Session,
    routes: dict[str, str],
    csrf: str,
) -> None:
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    model_id = next(iter(db.scalars(select(LlmRoleRoute)))).primary_model_profile_id
    for role in DECISION_AGENT_ROLES:
        prompt_response = client.post(
            "/api/v1/ai/prompts",
            headers=headers,
            json={
                "schema_version": "1.0",
                "role": role,
                "system_prompt": "Evaluate only the frozen Decision Agent input and policy.",
                "reason": "Phase 7C fixture",
            },
        )
        assert prompt_response.status_code == 201, prompt_response.text
        prompt = prompt_response.json()
        assert (
            client.post(
                f"/api/v1/ai/prompts/{prompt['id']}/validate",
                headers=headers,
            ).status_code
            == 200
        )
        route_response = client.post(
            "/api/v1/ai/routes",
            headers=headers,
            json={
                "schema_version": "1.0",
                "role": role,
                "primary_model_profile_id": model_id,
                "timeout_ms": 10000,
                "daily_call_limit": 100,
                "daily_cost_limit_krw": "0",
                "prompt_profile_id": prompt["id"],
                "output_schema_version": "decision-agent-model-output-v1",
                "reason": "Phase 7C deterministic fixture",
            },
        )
        assert route_response.status_code == 201, route_response.text
        route = route_response.json()
        assert (
            client.post(
                f"/api/v1/ai/routes/{route['id']}/validate",
                headers=headers,
            ).status_code
            == 200
        )
        routes[role] = route["id"]


def test_v7_admission_materializes_exact_upstream_slice_and_v2_input(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, created, _ = _admit(client, db, admin, monkeypatch)
    stages = list(db.scalars(select(AgentStageRun).where(AgentStageRun.run_id == run.id)))
    decision_input = db.scalar(
        select(DecisionInputSnapshot).where(DecisionInputSnapshot.input_hash == run.input_hash)
    )

    assert created is True
    assert run.dag_version == V7_DAG_VERSION
    assert {stage.role for stage in stages} == UPSTREAM_ROLES
    assert len(stages) == 7
    assert {stage.role for stage in stages if stage.route_id} == set(SCOUT_ROLES)
    assert decision_input is not None
    payload = json.loads(decision_input.input_json)
    assert set(payload) == {
        "schema_version",
        "user_id",
        "purpose",
        "analysis_context",
        "snapshot_id",
        "market",
        "symbol",
        "observed_at",
        "valid_until",
        "data_quality",
        "session_state",
        "quote",
        "indicators",
        "position",
        "open_orders",
        "account_risk_summary",
        "market_context",
        "strategy",
        "configuration_version",
        "prior_decision_summary",
        "server_input_policy_version",
        "market_snapshot_provenance",
        "indicator_provenance",
        "market_context_provenance",
    }
    assert decision_input.schema_version == "scout-input-v2"
    assert context_digest(decision_input.input_json) == decision_input.input_hash
    assert "profiles" not in decision_input.input_json.lower()


def test_v7_admission_is_idempotent_and_policy_change_conflicts(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, settings = _admit(client, db, admin, monkeypatch)
    routes = {
        role: json.loads(run.route_versions_json)[role]["route_id"]
        for role in V7_LLM_ROUTE_ROLES
    }
    again, created = create_v7_upstream_diagnostic_run(
        db,
        user=admin,
        market="KRX",
        symbol="005930",
        route_ids=routes,
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
    assert created is False
    assert again.id == run.id
    assert settings.quote_stale_seconds == 30


def test_v7_production_e2e_freezes_one_context_and_keeps_running(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, settings = _admit(client, db, admin, monkeypatch)
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
        lambda *args, **kwargs: KrxCollection(item=item, dates_queried=(today,), requests_made=1),
    )

    for index in range(7):
        assert process_agent_work_once(
            db, worker_id=f"v7-worker-{index}", lease_seconds=30, now=datetime.now(UTC)
        )

    db.expire_all()
    persisted = db.get(AgentRun, run.id)
    stages = {
        stage.role: stage
        for stage in db.scalars(select(AgentStageRun).where(AgentStageRun.run_id == run.id))
    }
    verifier = json.loads(stages["EVIDENCE_VERIFIER"].output_json or "{}")
    auditor = json.loads(stages["EVIDENCE_CANDIDATE_AUDITOR"].output_json or "{}")
    position = json.loads(stages["POSITION_RISK_SCOUT"].output_json or "{}")

    assert persisted is not None and persisted.state == "RUNNING"
    assert persisted.completed_at is None
    assert verifier["schema_version"] == "evidence-verifier-v2"
    assert verifier["stage_run_id"] == stages["EVIDENCE_VERIFIER"].id
    assert verifier["evidence_bundle_id"] == db.scalar(
        select(EvidenceBundle.id).where(EvidenceBundle.run_id == run.id)
    )
    assert verifier["verified_item_validity"]
    assert auditor["schema_version"] == "evidence-candidate-audit-v2"
    assert auditor["stage_run_id"] == stages["EVIDENCE_CANDIDATE_AUDITOR"].id
    assert position["status"] == "NOT_APPLICABLE"
    assert position["stance"] == "UNKNOWN"
    assert position["entry_score"] is None and position["exit_risk_score"] is None
    assert position["reason_codes"] == ["OPEN_POSITION_NOT_FOUND"]
    assert (
        db.scalar(
            select(func.count())
            .select_from(LlmInvocation)
            .where(LlmInvocation.stage_run_id == stages["POSITION_RISK_SCOUT"].id)
        )
        == 0
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(DecisionContext)
            .where(DecisionContext.run_id == run.id)
        )
        == 1
    )
    assert reconcile_v7_upstream_contexts(db, now=datetime.now(UTC), run_id=run.id) == 0
    assert (
        db.scalar(
            select(func.count())
            .select_from(DecisionContext)
            .where(DecisionContext.run_id == run.id)
        )
        == 1
    )


def test_v7_scout_hash_uses_dependencies_not_other_scout_outputs(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, _ = _admit(client, db, admin, monkeypatch)
    bundle = EvidenceBundle(
        owner_id=admin.id,
        run_id=run.id,
        market=run.market,
        symbol=run.symbol,
        as_of=datetime.now(UTC),
        policy_version="official-primary-secondary-v3",
        state="PARTIAL",
        evidence_ids_json="[]",
        contradiction_groups_json="[]",
        stale_evidence_ids_json="[]",
        reason_codes_json="[]",
        bundle_hash="a" * 64,
    )
    db.add(bundle)
    db.flush()
    technical = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id, AgentStageRun.role == "TECHNICAL_SCOUT"
        )
    )
    news = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id, AgentStageRun.role == "NEWS_DISCLOSURE_SCOUT"
        )
    )
    assert technical is not None and news is not None
    first = _v7_scout_role_input_hash(db, run=run, stage=technical)
    news.output_json = '{"irrelevant":true}'
    news.output_hash = "b" * 64
    assert _v7_scout_role_input_hash(db, run=run, stage=technical) == first
    bundle.bundle_hash = "c" * 64
    assert _v7_scout_role_input_hash(db, run=run, stage=technical) != first


def test_scout_input_v2_is_deterministic_policy_independent_and_context_sensitive(
    db: Session, admin: User
) -> None:
    snapshot = _market_fixture(db)
    state = db.get(MarketStreamState, ("KRX", "005930"))
    assert state is not None
    observed = snapshot.received_at
    first, first_payload = build_v7_scout_input(
        db,
        user_id=admin.id,
        snapshot=snapshot,
        state=state,
        observed_at=observed,
        quote_stale_seconds=30,
        dart_lookback_days=3,
        krx_lookback_days=7,
        naver_news_lookback_hours=72,
    )
    _all_profiles(db, admin)
    second, second_payload = build_v7_scout_input(
        db,
        user_id=admin.id,
        snapshot=snapshot,
        state=state,
        observed_at=observed,
        quote_stale_seconds=30,
        dart_lookback_days=3,
        krx_lookback_days=7,
        naver_news_lookback_hours=72,
    )
    assert second.id == first.id
    assert second.input_hash == first.input_hash
    assert second_payload == first_payload

    context, _ = ingest_market_context(
        db,
        MarketContextInput(
            market="KRX",
            symbol="005930",
            source="TEST",
            source_ref="v7-input",
            source_tier="PRIMARY",
            quality="NORMAL",
            observed_at=observed,
            received_at=observed,
            valid_until=observed + timedelta(seconds=20),
        ),
    )
    contextual, _ = build_v7_scout_input(
        db,
        user_id=admin.id,
        snapshot=snapshot,
        state=state,
        observed_at=observed,
        quote_stale_seconds=30,
        dart_lookback_days=3,
        krx_lookback_days=7,
        naver_news_lookback_hours=72,
        market_context=context,
    )
    assert contextual.input_hash != first.input_hash


def test_expired_required_input_rejects_without_partial_admission(
    db: Session, admin: User, monkeypatch
) -> None:
    snapshot = _market_fixture(db)
    monkeypatch.setattr(runtime_module, "get_settings", lambda: Settings(quote_stale_seconds=2))
    try:
        create_v7_upstream_diagnostic_run(
            db,
            user=admin,
            market="KRX",
            symbol="005930",
            route_ids={},
            now=snapshot.received_at + timedelta(seconds=3),
        )
    except ValueError as exc:
        assert str(exc) == "V7_SCOUT_INPUT_EXPIRED"
    else:
        raise AssertionError("expired v7 admission unexpectedly succeeded")
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert db.scalar(select(func.count()).select_from(AgentStageRun)) == 0
    assert db.scalar(select(func.count()).select_from(DecisionInputSnapshot)) == 0


def test_v7_unknown_or_stale_freshness_fails_closed_without_context(
    client, db: Session, admin: User, monkeypatch
) -> None:
    run, _, settings = _admit(client, db, admin, monkeypatch)
    worker_settings = settings.model_copy(update={"krx_enabled": True})
    monkeypatch.setattr(worker_module, "get_settings", lambda: worker_settings)
    old_day = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y%m%d")
    item = KrxDailyMarket(
        base_date=old_day,
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
        lambda *args, **kwargs: KrxCollection(item=item, dates_queried=(old_day,), requests_made=1),
    )
    for index in range(9):
        process_agent_work_once(
            db,
            worker_id=f"stale-worker-{index}",
            lease_seconds=30,
            now=datetime.now(UTC),
        )
    verifier = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id, AgentStageRun.role == "EVIDENCE_VERIFIER"
        )
    )
    assert verifier is not None and verifier.state == "FAILED"
    output = json.loads(verifier.output_json or "{}")
    assert "EVIDENCE_USABLE_ITEMS_EMPTY" in output["reason_codes"]
    assert output["valid_until"] <= output["observed_at"]
    assert db.scalar(select(func.count()).select_from(DecisionContext)) == 0
