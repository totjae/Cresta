from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.analysis_scheduler_state import acquire_scheduler_lease, update_scheduler_state
from app.config import Settings
from app.trading.paper import (
    PaperOrderRequest,
    apply_paper_fill,
    create_paper_order,
    set_paper_gate,
)
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET


def login(client: TestClient) -> None:
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    code = pyotp.TOTP(TEST_TOTP_SECRET).at(datetime.now(UTC))
    completed = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": code,
        },
    )
    assert completed.status_code == 200


def test_paper_read_models_require_auth_and_report_empty_state(client: TestClient) -> None:
    assert client.get("/api/v1/system/health").status_code == 401
    assert client.get("/api/v1/positions").status_code == 401
    login(client)

    health = client.get("/api/v1/system/health")
    assert health.status_code == 200
    assert health.json()["environment"] == "MOCK"
    assert health.json()["live_trading_enabled"] is False
    assert health.json()["paper_broker_status"] == "NOT_INITIALIZED"
    assert health.json()["analysis_scheduler"] == {
        "state": "NOT_STARTED",
        "lease_valid": False,
        "last_heartbeat_at": None,
        "last_tick_at": None,
        "last_completed_at": None,
        "next_due_at": None,
        "processed_count": 0,
        "decision_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "last_error_code": None,
    }
    assert health.json()["counts"] == {"orders": 0, "active_orders": 0, "open_positions": 0}

    positions = client.get("/api/v1/positions")
    assert positions.status_code == 200
    assert positions.json()["items"] == []


def test_system_health_caps_kiwoom_at_configured_without_runtime_worker(
    client: TestClient,
    settings: Settings,
    tmp_path: Path,
) -> None:
    secret_paths = []
    for name, value in (
        ("app_key", "test-key"),
        ("app_secret", "test-secret"),
        ("account", "1234567890"),
    ):
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        secret_paths.append(str(path))
    settings.kiwoom_enabled = True
    settings.kiwoom_app_key_file = secret_paths[0]
    settings.kiwoom_app_secret_file = secret_paths[1]
    settings.kiwoom_account_id_file = secret_paths[2]

    login(client)
    health = client.get("/api/v1/system/health")

    assert health.status_code == 200
    assert health.json()["kiwoom_broker_status"] == "CONFIGURED"
    assert health.json()["kiwoom_broker_status"] not in {"AUTHENTICATED", "READY"}


def test_system_health_reports_scheduler_without_internal_lease_identity(
    client: TestClient,
    db: Session,
) -> None:
    identity = acquire_scheduler_lease(db, "private-owner-id", lease_seconds=30)
    assert identity is not None
    assert update_scheduler_state(
        db,
        identity,
        "IDLE",
        next_due_at=datetime(2026, 8, 6, 23, 0, tzinfo=UTC),
    )
    login(client)

    scheduler = client.get("/api/v1/system/health").json()["analysis_scheduler"]

    assert scheduler["state"] == "IDLE"
    assert scheduler["lease_valid"] is True
    assert scheduler["next_due_at"].startswith("2026-08-06T23:00:00")
    assert "owner_id" not in scheduler
    assert "fencing_token" not in scheduler


def test_paper_health_and_position_use_persisted_fill_data(
    client: TestClient,
    db: Session,
    settings: Settings,
) -> None:
    set_paper_gate(db, "READY", "TEST_RECONCILED")
    order = create_paper_order(
        db,
        PaperOrderRequest(
            symbol="005930",
            side="BUY",
            quantity=3,
            order_type="LIMIT",
            limit_price=Decimal(70000),
            idempotency_key="paper-read-position",
            correlation_id="019fb7f6-0000-7000-8000-000000000201",
        ),
        settings,
    )
    apply_paper_fill(
        db,
        order.id,
        source_key="paper-read-position-fill",
        quantity=2,
        price=Decimal(70100),
    )
    login(client)

    health = client.get("/api/v1/system/health").json()
    assert health["paper_broker_status"] == "AVAILABLE"
    assert health["trading_gate"]["status"] == "READY"
    assert health["counts"] == {"orders": 1, "active_orders": 1, "open_positions": 1}

    listed = client.get("/api/v1/positions")
    assert listed.status_code == 200
    assert listed.json()["items"][0] == {
        **listed.json()["items"][0],
        "account_alias": "PAPER",
        "environment": "MOCK",
        "market": "KRX",
        "symbol": "005930",
        "quantity": 2,
        "average_price": "70100.0000",
        "state": "OPEN",
    }
    detail = client.get("/api/v1/positions/005930")
    assert detail.status_code == 200
    assert detail.json()["quantity"] == 2
    assert client.post("/api/v1/positions", json={}).status_code == 405
    assert client.post("/api/v1/system/health", json={}).status_code == 405


def test_missing_position_uses_standard_error(client: TestClient) -> None:
    login(client)
    response = client.get("/api/v1/positions/000000")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "POSITION_NOT_FOUND"
