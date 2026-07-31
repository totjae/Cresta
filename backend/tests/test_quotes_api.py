from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.watch import QuoteEvent, ingest_quote
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET


def login(client: TestClient) -> None:
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    code = pyotp.TOTP(TEST_TOTP_SECRET).at(datetime.now(UTC))
    response = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": code,
        },
    )
    assert response.status_code == 200


def fixture_quote(*, market: str = "KRX", age_seconds: int = 0) -> QuoteEvent:
    received_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return QuoteEvent(
        symbol="005930",
        market=market,
        source="KIWOOM_FIXTURE",
        sequence_or_hash=f"{market}-1",
        source_sequence=1,
        event_at=received_at - timedelta(milliseconds=20),
        received_at=received_at,
        last_price=Decimal(70100),
        open_price=Decimal(70000),
        high_price=Decimal(70200),
        low_price=Decimal(69900),
        cumulative_volume=100,
        best_bid_price=Decimal(70000),
        best_bid_quantity=50,
        best_ask_price=Decimal(70100),
        best_ask_quantity=40,
        trading_status="TRADING",
    )


def test_quote_api_requires_auth_and_has_no_mutation_endpoint(client: TestClient) -> None:
    assert client.get("/api/v1/quotes/005930").status_code == 401
    assert client.post("/api/v1/quotes/005930", json={}).status_code == 405


def test_quote_api_reports_market_quality_and_freshness(
    client: TestClient,
    db: Session,
) -> None:
    ingest_quote(db, fixture_quote())
    ingest_quote(db, fixture_quote(market="NXT"))
    login(client)

    response = client.get("/api/v1/quotes/005930?market=KRX")
    assert response.status_code == 200
    body = response.json()
    assert body["market"] == "KRX"
    assert body["last_price"] == "70100.0000"
    assert body["quality"] == "NORMAL"
    assert body["is_fresh"] is True
    assert float(body["age_seconds"]) >= 0

    nxt = client.get("/api/v1/quotes/005930?market=NXT")
    assert nxt.status_code == 200
    assert nxt.json()["market"] == "NXT"
    assert client.get("/api/v1/quotes/005930?market=SOR").status_code == 400
    assert client.get("/api/v1/quotes/000000").status_code == 404

    health = client.get("/api/v1/system/health")
    assert health.status_code == 200
    assert health.json()["market_data_status"] == "AVAILABLE"


def test_stale_quote_is_explicit(client: TestClient, db: Session) -> None:
    ingest_quote(db, fixture_quote(age_seconds=10))
    login(client)
    quote_response = client.get("/api/v1/quotes/005930")
    assert quote_response.status_code == 200
    assert quote_response.json()["quality"] == "NORMAL"
    assert quote_response.json()["is_fresh"] is False
    assert client.get("/api/v1/system/health").json()["market_data_status"] == "STALE"


def test_gap_quality_is_degraded_and_keeps_previous_snapshot(
    client: TestClient,
    db: Session,
) -> None:
    first = fixture_quote()
    ingest_quote(db, first)
    ingest_quote(
        db,
        replace(
            first,
            sequence_or_hash="KRX-3",
            source_sequence=3,
            event_at=first.event_at + timedelta(milliseconds=100),
            received_at=first.received_at + timedelta(milliseconds=100),
            cumulative_volume=120,
        ),
    )
    login(client)

    response = client.get("/api/v1/quotes/005930")
    assert response.status_code == 200
    assert response.json()["sequence_or_hash"] == "KRX-1"
    assert response.json()["quality"] == "GAP_DETECTED"
    assert response.json()["is_fresh"] is False
    assert client.get("/api/v1/system/health").json()["market_data_status"] == "DEGRADED"
