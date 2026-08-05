from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.decision_inputs import input_digest
from app.models import (
    Approval,
    Decision,
    DecisionExecution,
    DecisionInputSnapshot,
    GuardEvaluation,
    IndicatorSnapshot,
    MarketSnapshot,
    MarketStreamState,
    TradingOrder,
    User,
)
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET


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


def _snapshot(db: Session, *, stale: bool = False) -> MarketSnapshot:
    observed = datetime.now(UTC) - (timedelta(minutes=1) if stale else timedelta(0))
    snapshot = MarketSnapshot(
        symbol="005930", market="KRX", source="TEST", sequence_or_hash="ai-1",
        source_sequence=1, payload_hash="a" * 64,
        last_price=Decimal(101), open_price=Decimal(100), high_price=Decimal(102),
        low_price=Decimal(99), cumulative_volume=10000,
        best_bid_price=Decimal("100.9"), best_bid_quantity=100,
        best_ask_price=Decimal("101.1"), best_ask_quantity=100,
        trading_status="TRADING", quality="NORMAL", recovery_snapshot=False,
        event_at=observed, received_at=observed,
    )
    db.add(snapshot); db.flush()
    db.add(MarketStreamState(
        market="KRX", symbol="005930", source="TEST", current_snapshot_id=snapshot.id,
        last_sequence=1, last_event_at=observed, last_received_at=observed,
        cumulative_volume=10000, quality="NORMAL",
    ))
    db.add(
        IndicatorSnapshot(
            market_snapshot_id=snapshot.id,
            market="KRX",
            symbol="005930",
            calculator_version="watch-indicators-v2",
            vwap=Decimal(100),
            sma5=Decimal("100.5"),
            session_high=Decimal(102),
            drawdown_from_high_pct=Decimal("-0.5"),
            spread_pct=Decimal("0.2"),
            price_vs_vwap_pct=Decimal("1.0"),
            sma5_slope_pct=Decimal("0.2"),
            relative_volume_5=Decimal("1.5"),
            realized_volatility_pct=Decimal("0.5"),
            minute_bar_count=10,
            input_start_at=observed - timedelta(minutes=10),
            input_end_at=observed,
        )
    )
    db.commit()
    return snapshot


def test_public_mock_ai_is_diagnostic_without_creating_execution_or_orders(
    client: TestClient, db: Session, admin: User
) -> None:
    _snapshot(db)
    csrf = _login(client)
    payload = {
        "schema_version": "1.0", "evaluation_request_id": "ai-evaluation-diagnostic-0001",
        "symbol": "005930", "market": "KRX",
    }
    response = client.post(
        "/api/v1/decisions/mock-evaluate",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json=payload,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_id"] == "deterministic-mock-v2"
    assert body["core"]["action"] == "BUY"
    assert body["purpose"] == "DIAGNOSTIC"
    assert body["execution"] is None
    assert body["execution_mode"] is None
    assert body["execution_outcome"] == "NO_ACTION"
    assert body["configuration_version_id"] is None
    assert body["input_schema_version"] == "scout-input-v1"
    assert body["indicator_calculator_version"] == "watch-indicators-v2"
    assert len(body["input_hash"]) == 64
    repeated = client.post(
        "/api/v1/decisions/mock-evaluate",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json=payload,
    )
    assert repeated.json()["decision_id"] == body["decision_id"]
    assert db.scalar(select(func.count()).select_from(Decision)) == 1
    decision_input = db.scalar(select(DecisionInputSnapshot))
    assert decision_input is not None
    assert decision_input.input_hash == body["input_hash"]
    assert input_digest(decision_input.input_json) == decision_input.input_hash
    assert admin.id not in decision_input.input_json
    assert "app_secret" not in decision_input.input_json
    assert db.scalar(select(func.count()).select_from(DecisionInputSnapshot)) == 1
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 0
    assert db.scalar(select(func.count()).select_from(GuardEvaluation)) == 0
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_missing_v2_indicators_fail_closed(client: TestClient, db: Session) -> None:
    snapshot = _snapshot(db)
    indicator = db.scalar(
        select(IndicatorSnapshot).where(IndicatorSnapshot.market_snapshot_id == snapshot.id)
    )
    assert indicator is not None
    db.delete(indicator)
    db.commit()
    csrf = _login(client)

    response = client.post(
        "/api/v1/decisions/mock-evaluate",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json={
            "schema_version": "1.0",
            "evaluation_request_id": "ai-evaluation-no-indicators-0001",
            "symbol": "005930",
            "market": "KRX",
        },
    )

    assert response.status_code == 200
    assert response.json()["core"]["action"] == "RISK_BLOCK"
    assert response.json()["core"]["reason_codes"] == ["DATA_INSUFFICIENT"]


def test_mismatched_indicator_version_fails_closed(client: TestClient, db: Session) -> None:
    snapshot = _snapshot(db)
    indicator = db.scalar(
        select(IndicatorSnapshot).where(IndicatorSnapshot.market_snapshot_id == snapshot.id)
    )
    assert indicator is not None
    indicator.calculator_version = "watch-indicators-v1"
    db.commit()
    csrf = _login(client)

    response = client.post(
        "/api/v1/decisions/mock-evaluate",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json={
            "schema_version": "1.0",
            "evaluation_request_id": "ai-evaluation-old-indicators-0001",
            "symbol": "005930",
            "market": "KRX",
        },
    )

    assert response.status_code == 200
    assert response.json()["core"]["action"] == "RISK_BLOCK"
    decision_input = db.scalar(select(DecisionInputSnapshot))
    assert decision_input is not None
    assert '"status":"VERSION_MISMATCH"' in decision_input.input_json


def test_stale_snapshot_is_persisted_as_risk_block(client: TestClient, db: Session) -> None:
    _snapshot(db, stale=True)
    csrf = _login(client)
    response = client.post(
        "/api/v1/decisions/mock-evaluate",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json={
            "schema_version": "1.0", "evaluation_request_id": "ai-evaluation-stale-0001",
            "symbol": "005930", "market": "KRX",
        },
    )
    assert response.status_code == 200
    assert response.json()["core"]["action"] == "RISK_BLOCK"
    assert response.json()["core"]["reason_codes"] == ["DATA_INSUFFICIENT"]
    assert response.json()["execution_outcome"] == "NO_ACTION"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
