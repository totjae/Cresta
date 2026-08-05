from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AgentRun,
    AgentStageRun,
    Approval,
    Decision,
    EvidenceBundle,
    IndicatorSnapshot,
    LlmInvocation,
    MarketSnapshot,
    MarketStreamState,
    TradingOrder,
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
    assert body["state"] == "PARTIAL"
    assert body["core_action"] == "WAIT"
    assert body["evidence_bundle"]["state"] == "PARTIAL"
    assert len(body["stages"]) == 7
    assert sum(stage["invocation"] is not None for stage in body["stages"]) == 5
    news = next(stage for stage in body["stages"] if stage["role"] == "NEWS_DISCLOSURE_SCOUT")
    assert news["state"] == "INSUFFICIENT_DATA"
    assert news["invocation"]["actual_provider"] == "CRESTA_MOCK"

    second = client.post("/api/v1/ai/agent-runs/diagnostic", headers=headers, json=request)
    assert second.status_code == 201
    assert second.json()["created"] is False
    assert second.json()["run_id"] == body["run_id"]
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 1
    assert db.scalar(select(func.count()).select_from(AgentStageRun)) == 7
    assert db.scalar(select(func.count()).select_from(EvidenceBundle)) == 1
    assert db.scalar(select(func.count()).select_from(LlmInvocation)) == 5
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0

    listed = client.get("/api/v1/ai/agent-runs")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["run_id"] == body["run_id"]


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
