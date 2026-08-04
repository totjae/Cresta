from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    IndicatorSnapshot,
    MarketSnapshot,
    MarketStreamState,
    WatchlistItem,
)
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET


def _login(client: TestClient) -> str:
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    completed = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(datetime.now(UTC) - timedelta(seconds=30)),
        },
    )
    assert completed.status_code == 200
    return completed.json()["csrf_token"]


def test_watchlist_crud_limit_duplicate_and_snapshot_summary(
    client: TestClient, db: Session
) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}

    missing_csrf = client.post(
        "/api/v1/watchlist",
        json={"schema_version": "1.0", "symbol": "005930", "market": "KRX"},
    )
    assert missing_csrf.status_code == 403

    created = client.post(
        "/api/v1/watchlist",
        headers=headers,
        json={"schema_version": "1.0", "symbol": "005930", "market": "KRX"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["remaining_slots"] == 2
    assert body["items"][0]["data_status"] == "WAITING_FOR_DATA"
    item_id = body["items"][0]["id"]

    duplicate = client.post(
        "/api/v1/watchlist",
        headers=headers,
        json={"schema_version": "1.0", "symbol": "005930", "market": "KRX"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "WATCHLIST_ITEM_EXISTS"

    for symbol in ("000660", "035420"):
        assert client.post(
            "/api/v1/watchlist",
            headers=headers,
            json={"schema_version": "1.0", "symbol": symbol, "market": "KRX"},
        ).status_code == 201
    full = client.post(
        "/api/v1/watchlist",
        headers=headers,
        json={"schema_version": "1.0", "symbol": "051910", "market": "KRX"},
    )
    assert full.status_code == 422
    assert full.json()["error"]["code"] == "WATCHLIST_LIMIT_REACHED"

    now = datetime.now(UTC)
    snapshot = MarketSnapshot(
        symbol="005930", market="KRX", source="TEST", sequence_or_hash="watch-api-1",
        payload_hash="a" * 64, last_price=Decimal(70000), open_price=Decimal(69000),
        high_price=Decimal(70500), low_price=Decimal(68800), cumulative_volume=12345,
        trading_status="TRADING", quality="NORMAL", recovery_snapshot=False,
        event_at=now, received_at=now,
    )
    db.add(snapshot)
    db.flush()
    db.add(MarketStreamState(
        market="KRX", symbol="005930", source="TEST", current_snapshot_id=snapshot.id,
        last_event_at=now, last_received_at=now, cumulative_volume=12345, quality="NORMAL",
    ))
    db.add(IndicatorSnapshot(
        market_snapshot_id=snapshot.id, market="KRX", symbol="005930",
        calculator_version="watch-indicators-v1", vwap=Decimal(69900),
        sma5=Decimal(69800), session_high=Decimal(70500),
        drawdown_from_high_pct=Decimal("-0.709220"), spread_pct=Decimal("0.142857"),
        minute_bar_count=5, input_start_at=now - timedelta(hours=1), input_end_at=now,
    ))
    db.commit()

    listed = client.get("/api/v1/watchlist")
    samsung = next(item for item in listed.json()["items"] if item["symbol"] == "005930")
    assert samsung["data_status"] == "AVAILABLE"
    assert samsung["quote"]["last_price"] == "70000.0000"
    assert samsung["indicators"]["vwap"] == "69900.0000"
    assert samsung["indicators"]["minute_bar_count"] == 5

    deleted = client.delete(f"/api/v1/watchlist/{item_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "DELETED"
    assert db.scalar(select(func.count()).select_from(WatchlistItem)) == 2
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.action.like("WATCHLIST_%"))))
    assert actions == {"WATCHLIST_ITEM_CREATED", "WATCHLIST_ITEM_DELETED"}


def test_watchlist_rejects_mock_nxt(client: TestClient) -> None:
    csrf = _login(client)
    response = client.post(
        "/api/v1/watchlist",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json={"schema_version": "1.0", "symbol": "005930", "market": "NXT"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WATCHLIST_MARKET_UNSUPPORTED_IN_MOCK"
