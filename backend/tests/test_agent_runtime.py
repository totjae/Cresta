from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.worker import process_agent_work_once
from app.market_context import MarketContextInput, ingest_market_context
from app.models import (
    AgentRun,
    AgentStageRun,
    Approval,
    ConfigurationVersion,
    Decision,
    EvidenceBundle,
    IndicatorSnapshot,
    LlmInvocation,
    LlmModelProfile,
    LlmRoleRoute,
    MarketSnapshot,
    MarketStreamState,
    OrderIntent,
    Position,
    TradingOrder,
    User,
)
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET

ROLES = (
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
    "CORE",
)


def _login(client: TestClient) -> str:
    now = datetime.now(UTC)
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    response = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(now),
        },
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _market_fixture(db: Session) -> MarketSnapshot:
    now = datetime.now(UTC)
    snapshot = MarketSnapshot(
        symbol="005930",
        market="KRX",
        source="TEST",
        sequence_or_hash="agent-runtime-1",
        payload_hash="a" * 64,
        last_price=Decimal(70000),
        open_price=Decimal(69000),
        high_price=Decimal(70500),
        low_price=Decimal(68800),
        cumulative_volume=100000,
        best_bid_price=Decimal(69900),
        best_ask_price=Decimal(70100),
        trading_status="TRADING",
        quality="NORMAL",
        recovery_snapshot=False,
        event_at=now,
        received_at=now,
    )
    db.add(snapshot)
    db.flush()
    db.add(
        MarketStreamState(
            market="KRX",
            symbol="005930",
            source="TEST",
            current_snapshot_id=snapshot.id,
            last_event_at=now,
            last_received_at=now,
            cumulative_volume=100000,
            quality="NORMAL",
        )
    )
    db.add(
        IndicatorSnapshot(
            market_snapshot_id=snapshot.id,
            market="KRX",
            symbol="005930",
            calculator_version="watch-indicators-v2",
            vwap=Decimal(69900),
            sma5=Decimal(69800),
            session_high=Decimal(70500),
            drawdown_from_high_pct=Decimal("-0.709220"),
            spread_pct=Decimal("0.286123"),
            price_vs_vwap_pct=Decimal("0.143062"),
            sma5_slope_pct=Decimal("0.1"),
            relative_volume_5=Decimal("1.2"),
            realized_volatility_pct=Decimal("0.5"),
            minute_bar_count=5,
            input_start_at=now - timedelta(minutes=5),
            input_end_at=now,
        )
    )
    db.commit()
    return snapshot


def _market_context_fixture(db: Session, *, source_ref: str = "context-1") -> str:
    now = datetime.now(UTC)
    context, _ = ingest_market_context(
        db,
        MarketContextInput(
            market="KRX",
            symbol="005930",
            source="TEST_CONTRACTED_FEED",
            source_ref=source_ref,
            source_tier="CONTRACTED",
            quality="NORMAL",
            index_code="KOSPI",
            index_change_pct=Decimal("0.5"),
            sector_code="G25",
            sector_change_pct=Decimal("0.4"),
            advancers=600,
            decliners=300,
            unchanged=100,
            observed_at=now - timedelta(seconds=1),
            received_at=now,
            valid_until=now + timedelta(minutes=5),
        ),
    )
    db.commit()
    return context.id


def _routes(client: TestClient, headers: dict[str, str]) -> dict[str, str]:
    provider = client.post(
        "/api/v1/ai/providers",
        headers=headers,
        json={
            "schema_version": "1.0",
            "name": "agent-runtime-mock",
            "adapter_type": "MOCK",
            "endpoint": None,
            "credential_secret_ref": None,
            "data_policy": "NONE",
        },
    ).json()
    assert (
        client.post(f"/api/v1/ai/providers/{provider['id']}/test", headers=headers).status_code
        == 200
    )
    model = client.post(
        "/api/v1/ai/models",
        headers=headers,
        json={
            "schema_version": "1.0",
            "provider_profile_id": provider["id"],
            "alias": "agent-runtime-v1",
            "provider_model_id": "deterministic-mock-v2",
            "capabilities": {
                "structured_output": True,
                "tool_calling": False,
                "web_search": False,
                "streaming": False,
                "reasoning": False,
                "seed": True,
                "usage_reporting": True,
                "local_execution": True,
            },
            "max_context_tokens": 4096,
            "max_output_tokens": 1024,
            "temperature": "0",
        },
    ).json()
    assert (
        client.post(f"/api/v1/ai/models/{model['id']}/validate", headers=headers).status_code == 200
    )
    route_ids: dict[str, str] = {}
    for role in ROLES:
        response = client.post(
            "/api/v1/ai/routes",
            headers=headers,
            json={
                "schema_version": "1.0",
                "role": role,
                "primary_model_profile_id": model["id"],
                "timeout_ms": 10000,
                "daily_call_limit": 100,
                "daily_cost_limit_krw": "0",
                "prompt_version": f"{role.lower()}-fixture-v1",
                "output_schema_version": "agent-core-v1"
                if role == "CORE"
                else "agent-assessment-v1",
                "reason": "Agent Runtime deterministic fixture",
            },
        )
        assert response.status_code == 201, response.text
        route = response.json()
        assert (
            client.post(f"/api/v1/ai/routes/{route['id']}/validate", headers=headers).status_code
            == 200
        )
        route_ids[role] = route["id"]
    return route_ids


def test_diagnostic_agent_runtime_is_idempotent_and_never_trades(
    client: TestClient, db: Session
) -> None:
    _market_fixture(db)
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    route_ids = _routes(client, headers)
    request = {
        "schema_version": "1.0",
        "market": "KRX",
        "symbol": "005930",
        "route_ids": route_ids,
    }

    first = client.post("/api/v1/ai/agent-runs/diagnostic", headers=headers, json=request)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["created"] is True
    assert body["purpose"] == "DIAGNOSTIC"
    assert body["execution_stage"] == "SHADOW"
    assert body["dag_version"] == "agent-dag-v6"
    assert body["analysis_context"] == "ENTRY"
    assert len(body["position_snapshot_hash"]) == 64
    assert body["server_input_policy_version"] == "agent-server-input-v1"
    assert body["market_context_snapshot_id"] is None
    assert body["market_context_snapshot_hash"] is None
    assert body["assessment_schema_version"] == "agent-assessment-v2"
    assert body["core_schema_version"] == "agent-core-v2"
    assert body["score_policy_version"] == "score-policy-v1"
    assert body["state"] == "CREATED"
    assert body["core_action"] is None
    assert body["evidence_bundle"] is None
    assert len(body["stages"]) == 8
    assert all(stage["state"] == "PENDING" for stage in body["stages"])
    assert all(stage["attempt_count"] == 0 for stage in body["stages"])
    stored_run = db.get(AgentRun, body["run_id"])
    assert stored_run is not None
    stored_routes = json.loads(stored_run.route_versions_json)
    assert stored_routes["CORE"]["declared_output_schema_version"] == "agent-core-v1"
    assert stored_routes["CORE"]["effective_output_schema_version"] == "agent-core-v2"
    assert (
        stored_routes["TECHNICAL_SCOUT"]["declared_output_schema_version"] == "agent-assessment-v1"
    )
    assert (
        stored_routes["TECHNICAL_SCOUT"]["effective_output_schema_version"] == "agent-assessment-v2"
    )
    pending_auditor = next(
        stage for stage in body["stages"] if stage["role"] == "EVIDENCE_CANDIDATE_AUDITOR"
    )
    pending_core = next(stage for stage in body["stages"] if stage["role"] == "CORE")
    assert set(pending_auditor["dependencies"]) == {
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
    }
    assert "EVIDENCE_CANDIDATE_AUDITOR" in pending_core["dependencies"]

    second = client.post("/api/v1/ai/agent-runs/diagnostic", headers=headers, json=request)
    assert second.status_code == 201
    assert second.json()["created"] is False
    assert second.json()["run_id"] == body["run_id"]
    for _ in range(8):
        assert process_agent_work_once(
            db,
            worker_id="agent-test-worker",
            lease_seconds=30,
        )

    completed = client.get(f"/api/v1/ai/agent-runs/{body['run_id']}")
    assert completed.status_code == 200
    completed_body = completed.json()
    assert completed_body["state"] == "PARTIAL"
    assert completed_body["core_action"] == "WAIT"
    assert completed_body["shadow_assessment"] == "UNKNOWN"
    assert completed_body["evidence_bundle"]["state"] == "PARTIAL"
    auditor = next(
        stage for stage in completed_body["stages"] if stage["role"] == "EVIDENCE_CANDIDATE_AUDITOR"
    )
    assert auditor["state"] == "SUCCEEDED"
    assert auditor["invocation"] is None
    assert auditor["output"]["candidate_count"] == 0
    assert auditor["output"]["reason_codes"] == ["NO_PROVIDER_SOURCE_CANDIDATES"]
    assert auditor["output"]["bundle_mutated"] is False
    assert (
        auditor["output"]["evidence_bundle_hash"]
        == completed_body["evidence_bundle"]["bundle_hash"]
    )
    assert sum(stage["invocation"] is not None for stage in completed_body["stages"]) == 2
    news = next(
        stage for stage in completed_body["stages"] if stage["role"] == "NEWS_DISCLOSURE_SCOUT"
    )
    assert news["state"] == "INSUFFICIENT_DATA"
    assert news["invocation"]["actual_provider"] == "CRESTA_MOCK"
    position_risk = next(
        stage for stage in completed_body["stages"] if stage["role"] == "POSITION_RISK_SCOUT"
    )
    assert position_risk["state"] == "NOT_APPLICABLE"
    assert position_risk["output"]["stance"] == "UNKNOWN"
    assert position_risk["output"]["entry_score"] is None
    assert position_risk["output"]["exit_risk_score"] is None
    assert position_risk["invocation"] is None
    market_sector = next(
        stage for stage in completed_body["stages"] if stage["role"] == "MARKET_SECTOR_SCOUT"
    )
    assert market_sector["state"] == "INSUFFICIENT_DATA"
    assert market_sector["invocation"] is None
    assert "MARKET_DATA_INSUFFICIENT" in market_sector["output"]["reason_codes"]
    core = next(stage for stage in completed_body["stages"] if stage["role"] == "CORE")
    assert core["state"] == "SUCCEEDED"
    assert core["invocation"] is None
    assert core["output"]["action"] == "WAIT"
    assert core["output"]["shadow_assessment"] == "UNKNOWN"
    assert core["output"]["confidence"] == 0
    assert core["output"]["risk_level"] == "HIGH"
    assert core["output"]["incomplete_roles"] == [
        "MARKET_SECTOR_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
    ]
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 1
    assert db.scalar(select(func.count()).select_from(AgentStageRun)) == 8
    assert db.scalar(select(func.count()).select_from(EvidenceBundle)) == 1
    assert db.scalar(select(func.count()).select_from(LlmInvocation)) == 2
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0

    listed = client.get("/api/v1/ai/agent-runs")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["run_id"] == body["run_id"]


def test_agent_context_and_position_snapshot_change_idempotency_key(
    client: TestClient, db: Session
) -> None:
    _market_fixture(db)
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    route_ids = _routes(client, headers)
    request = {
        "schema_version": "1.0",
        "market": "KRX",
        "symbol": "005930",
        "route_ids": route_ids,
    }

    entry = client.post("/api/v1/ai/agent-runs/diagnostic", headers=headers, json=request)
    assert entry.status_code == 201
    assert entry.json()["analysis_context"] == "ENTRY"
    for _ in range(8):
        assert process_agent_work_once(db, worker_id="entry-worker", lease_seconds=30)

    position = Position(
        account_alias="KIWOOM_MOCK_PRIMARY",
        symbol="005930",
        quantity=3,
        available_quantity=3,
        average_price=Decimal(69500),
        managed_quantity=3,
        managed_average_price=Decimal(69500),
        state="OPEN",
    )
    db.add(position)
    db.commit()

    holding = client.post("/api/v1/ai/agent-runs/diagnostic", headers=headers, json=request)
    assert holding.status_code == 201
    assert holding.json()["created"] is True
    assert holding.json()["analysis_context"] == "POSITION"
    assert holding.json()["run_id"] != entry.json()["run_id"]
    assert holding.json()["position_snapshot_hash"] != entry.json()["position_snapshot_hash"]
    stored = db.get(AgentRun, holding.json()["run_id"])
    assert stored is not None
    position_input = json.loads(stored.position_snapshot_json or "{}")
    assert position_input["calculation_version"] == "position-risk-input-v1"
    assert position_input["cost_basis_amount"] == "208500.0000"
    assert position_input["market_value_amount"] == "210000.0000"
    assert position_input["unrealized_pnl_amount"] == "1500.0000"
    assert position_input["unrealized_return_pct"] == "0.719424"
    assert position_input["drawdown_from_session_high_pct"] == "-0.709220"
    assert position_input["fixed_stop_price"] == "68110.0000"
    assert position_input["distance_to_fixed_stop_pct"] == "2.700000"
    assert position_input["freshness"]["status"] == "FRESH"
    assert position_input["risk_policy"]["source"] == "SAFE_DEFAULT"
    assert len(position_input["risk_policy"]["payload_hash"]) == 64

    for _ in range(8):
        assert process_agent_work_once(db, worker_id="position-worker", lease_seconds=30)
    completed = client.get(f"/api/v1/ai/agent-runs/{holding.json()['run_id']}").json()
    position_stage = next(
        stage for stage in completed["stages"] if stage["role"] == "POSITION_RISK_SCOUT"
    )
    assert position_stage["state"] == "SUCCEEDED"
    assert position_stage["invocation"] is not None


def test_agent_run_freezes_valid_market_context_and_invokes_market_scout(
    client: TestClient, db: Session
) -> None:
    _market_fixture(db)
    context_id = _market_context_fixture(db)
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    route_ids = _routes(client, headers)

    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": route_ids,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["market_context_snapshot_id"] == context_id
    assert len(body["market_context_snapshot_hash"]) == 64
    for _ in range(8):
        assert process_agent_work_once(db, worker_id="context-worker", lease_seconds=30)
    completed = client.get(f"/api/v1/ai/agent-runs/{body['run_id']}").json()
    market_stage = next(
        stage for stage in completed["stages"] if stage["role"] == "MARKET_SECTOR_SCOUT"
    )
    assert market_stage["state"] == "SUCCEEDED"
    assert market_stage["invocation"] is not None
    assert market_stage["output"]["stance"] == "SUPPORTIVE"
    assert "MARKET_TREND_SUPPORTIVE" in market_stage["output"]["reason_codes"]


def test_stale_position_input_is_insufficient_without_provider_call(
    client: TestClient, db: Session
) -> None:
    snapshot = _market_fixture(db)
    stale_at = snapshot.event_at - timedelta(minutes=5)
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
            created_at=stale_at,
            updated_at=stale_at,
        )
    )
    db.commit()
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    route_ids = _routes(client, headers)
    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": route_ids,
        },
    )
    assert response.status_code == 201

    for _ in range(8):
        assert process_agent_work_once(db, worker_id="stale-position-worker", lease_seconds=30)
    completed = client.get(f"/api/v1/ai/agent-runs/{response.json()['run_id']}").json()
    position_stage = next(
        stage for stage in completed["stages"] if stage["role"] == "POSITION_RISK_SCOUT"
    )
    assert position_stage["state"] == "INSUFFICIENT_DATA"
    assert position_stage["invocation"] is None
    assert "POSITION_DATA_STALE" in position_stage["output"]["reason_codes"]


def test_active_risk_policy_provenance_is_frozen_at_admission(
    client: TestClient, db: Session, admin: User
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
    policy = {
        "entry_order_amount": None,
        "max_single_order_amount": 1_000_000,
        "max_position_amount_per_symbol": 1_000_000,
        "max_total_position_amount": 3_000_000,
        "max_open_positions": 3,
        "max_daily_entries": 5,
        "fixed_stop_loss_pct": "-3.0",
        "quote_stale_seconds": 2,
        "max_spread_pct": "0.30",
        "max_price_deviation_pct": "0.50",
    }
    policy_json = json.dumps(policy, separators=(",", ":"), sort_keys=True)
    active = ConfigurationVersion(
        scope="USER_DEFAULT",
        target_id=admin.id,
        category="RISK_POLICY",
        sequence=1,
        state="ACTIVE",
        payload_json=policy_json,
        payload_hash=hashlib.sha256(policy_json.encode()).hexdigest(),
        reason="active fixture",
        created_by=admin.id,
        activated_at=datetime.now(UTC),
    )
    db.add(active)
    db.commit()
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    route_ids = _routes(client, headers)
    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": route_ids,
        },
    )
    assert response.status_code == 201
    run = db.get(AgentRun, response.json()["run_id"])
    assert run is not None
    frozen = json.loads(run.position_snapshot_json or "{}")
    assert frozen["fixed_stop_loss_pct"] == "-3.000000"
    assert frozen["fixed_stop_price"] == "67415.0000"
    assert frozen["risk_policy"] == {
        "source": "ACTIVE",
        "version_id": active.id,
        "payload_hash": active.payload_hash,
    }

    active.state = "SUPERSEDED"
    changed_policy = {**policy, "fixed_stop_loss_pct": "-5.0"}
    changed_json = json.dumps(changed_policy, separators=(",", ":"), sort_keys=True)
    db.add(
        ConfigurationVersion(
            scope="USER_DEFAULT",
            target_id=admin.id,
            category="RISK_POLICY",
            sequence=2,
            state="ACTIVE",
            payload_json=changed_json,
            payload_hash=hashlib.sha256(changed_json.encode()).hexdigest(),
            reason="replacement fixture",
            created_by=admin.id,
            base_active_version_id=active.id,
            activated_at=datetime.now(UTC),
        )
    )
    db.commit()
    db.refresh(run)
    assert json.loads(run.position_snapshot_json or "{}") == frozen


def test_legacy_agent_run_remains_readable_without_v4_contract_fields(
    client: TestClient, db: Session, admin: User
) -> None:
    snapshot = _market_fixture(db)
    legacy = AgentRun(
        owner_id=admin.id,
        market="KRX",
        symbol="005930",
        market_snapshot_id=snapshot.id,
        input_hash="b" * 64,
        dag_version="agent-dag-v3",
        route_versions_json="{}",
        idempotency_key="c" * 64,
        state="SUCCEEDED",
        core_action="WAIT",
        valid_until=datetime.now(UTC) + timedelta(minutes=5),
        completed_at=datetime.now(UTC),
    )
    db.add(legacy)
    db.commit()

    _login(client)
    response = client.get(f"/api/v1/ai/agent-runs/{legacy.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["dag_version"] == "agent-dag-v3"
    assert body["analysis_context"] is None
    assert body["position_snapshot_hash"] is None
    assert body["assessment_schema_version"] is None
    assert body["core_schema_version"] is None
    assert body["score_policy_version"] is None
    assert body["server_input_policy_version"] is None
    assert body["market_context_snapshot_id"] is None
    assert body["market_context_snapshot_hash"] is None
    assert body["shadow_assessment"] is None


def test_corrupted_position_snapshot_is_conflicted_and_core_is_unknown(
    client: TestClient, db: Session
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
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    route_ids = _routes(client, headers)
    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": route_ids,
        },
    )
    assert response.status_code == 201
    run = db.get(AgentRun, response.json()["run_id"])
    assert run is not None
    run.position_snapshot_json = '{"marker":"NO_OPEN_POSITION"}'
    db.commit()

    for _ in range(8):
        assert process_agent_work_once(db, worker_id="corrupt-position-worker", lease_seconds=30)

    completed = client.get(f"/api/v1/ai/agent-runs/{run.id}").json()
    assert completed["core_action"] == "WAIT"
    assert completed["shadow_assessment"] == "UNKNOWN"
    position_stage = next(
        stage for stage in completed["stages"] if stage["role"] == "POSITION_RISK_SCOUT"
    )
    assert position_stage["state"] == "CONFLICTED"
    assert position_stage["output"]["entry_score"] is None
    assert position_stage["output"]["exit_risk_score"] is None
    assert "POSITION_DATA_CONFLICTED" in position_stage["output"]["reason_codes"]


def test_failed_scout_output_is_reduced_to_unknown_core_assessment(
    client: TestClient, db: Session
) -> None:
    _market_fixture(db)
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    route_ids = _routes(client, headers)
    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": route_ids,
        },
    )
    assert response.status_code == 201
    run_id = response.json()["run_id"]
    failed_scout = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run_id,
            AgentStageRun.role == "TECHNICAL_SCOUT",
        )
    )
    assert failed_scout is not None
    failed_scout.state = "INVALID_OUTPUT"
    failed_scout.error_code = "LLM_INVALID_OUTPUT"
    failed_scout.completed_at = datetime.now(UTC)
    db.commit()

    while process_agent_work_once(db, worker_id="invalid-output-worker", lease_seconds=30):
        pass

    completed = client.get(f"/api/v1/ai/agent-runs/{run_id}").json()
    core = next(stage for stage in completed["stages"] if stage["role"] == "CORE")
    assert core["state"] == "SUCCEEDED"
    assert core["invocation"] is None
    assert core["output"]["shadow_assessment"] == "UNKNOWN"
    assert "TECHNICAL_SCOUT" in core["output"]["incomplete_roles"]
    assert completed["core_action"] == "WAIT"
    assert completed["shadow_assessment"] == "UNKNOWN"
    assert completed["state"] == "FAILED"


def test_agent_runtime_rejects_incomplete_route_set_without_creating_run(
    client: TestClient, db: Session
) -> None:
    _market_fixture(db)
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": {},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AGENT_ROUTE_SET_INCOMPLETE"
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_agent_runtime_enforces_route_daily_call_limit_before_provider_call(
    client: TestClient, db: Session
) -> None:
    _market_fixture(db)
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    route_ids = _routes(client, headers)
    technical_route = db.get(LlmRoleRoute, route_ids["TECHNICAL_SCOUT"])
    assert technical_route is not None
    technical_route.daily_call_limit = 1
    db.commit()

    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": route_ids,
        },
    )
    assert response.status_code == 201
    run_id = response.json()["run_id"]
    technical_stage = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run_id,
            AgentStageRun.role == "TECHNICAL_SCOUT",
        )
    )
    assert technical_stage is not None
    model = db.get(LlmModelProfile, technical_route.primary_model_profile_id)
    assert model is not None
    db.add(
        LlmInvocation(
            stage_run_id=technical_stage.id,
            requested_provider_profile_id=model.provider_profile_id,
            requested_model_profile_id=model.id,
            state="SUCCEEDED",
            input_hash=technical_stage.input_hash,
            validation_status="PASSED",
            completed_at=datetime.now(UTC),
        )
    )
    db.commit()

    for _ in range(3):
        assert process_agent_work_once(db, worker_id="daily-limit-worker", lease_seconds=30)

    db.refresh(technical_stage)
    assert technical_stage.state == "FAILED"
    assert technical_stage.error_code == "AGENT_DAILY_CALL_LIMIT"
    limited = db.scalar(
        select(LlmInvocation).where(
            LlmInvocation.stage_run_id == technical_stage.id,
            LlmInvocation.error_code == "LOCAL_DAILY_CALL_LIMIT",
        )
    )
    assert limited is not None
    assert limited.state == "RATE_LIMITED"
    assert limited.actual_provider is None
