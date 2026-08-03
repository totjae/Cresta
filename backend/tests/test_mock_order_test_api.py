from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.broker.worker_state import acquire_lease, update_worker_state
from app.config import Settings
from app.models import AuditLog, OrderIntent, TradingOrder
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET


def _configure_kiwoom(settings: Settings, tmp_path: Path) -> None:
    paths = []
    for name, value in (("key", "k"), ("secret", "s"), ("account", "1234567811")):
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        paths.append(str(path))
    settings.kiwoom_enabled = True
    settings.kiwoom_app_key_file = paths[0]
    settings.kiwoom_app_secret_file = paths[1]
    settings.kiwoom_account_id_file = paths[2]


def _login(client: TestClient) -> str:
    now = datetime.now(UTC)
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    completed = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(now - timedelta(seconds=30)),
        },
    )
    assert completed.status_code == 200
    return completed.json()["csrf_token"]


def _ready_worker(db: Session) -> None:
    identity = acquire_lease(db, "mock-ui-test-worker", lease_seconds=60)
    assert identity is not None
    assert update_worker_state(
        db,
        identity,
        "READY",
        websocket_connected=True,
        subscriptions_ready=True,
        gate_status="READY",
        gate_reason="WORKER_HEALTHY",
    )


def test_mock_order_test_requires_bound_reauth_and_queues_one_share(
    client: TestClient,
    db: Session,
    settings: Settings,
    tmp_path: Path,
) -> None:
    _configure_kiwoom(settings, tmp_path)
    _ready_worker(db)
    csrf = _login(client)
    test_request_id = "mock-browser-request-0001"
    reauth = client.post(
        "/api/v1/auth/reauth/totp",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json={
            "schema_version": "1.0",
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(datetime.now(UTC)),
            "target_action": "KIWOOM_MOCK_ORDER_TEST",
            "target_id": test_request_id,
        },
    )
    assert reauth.status_code == 200, reauth.text

    response = client.post(
        "/api/v1/system/broker/mock-order-test",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json={
            "schema_version": "1.0",
            "test_request_id": test_request_id,
            "symbol": "005930",
            "order_type": "MARKET",
            "limit_price": None,
            "reauth_proof": reauth.json()["reauth_proof"],
            "confirmation": "KIWOOM_MOCK_ONE_SHARE",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "schema_version": "1.0",
        "request_id": response.json()["request_id"],
        "result_type": "ORDER_QUEUED",
        "order_id": response.json()["order_id"],
        "status": "CREATED",
        "environment": "MOCK",
        "account_alias": "KIWOOM_MOCK_PRIMARY",
        "symbol": "005930",
        "side": "BUY",
        "requested_quantity": 1,
    }
    order = db.get(TradingOrder, response.json()["order_id"])
    assert order is not None
    assert (order.side, order.requested_quantity, order.order_type) == ("BUY", 1, "MARKET")
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 1
    assert db.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "KIWOOM_MOCK_ORDER_TEST_CREATED"
        )
    ) == 1

    repeated = client.post(
        "/api/v1/system/broker/mock-order-test",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json={
            "schema_version": "1.0",
            "test_request_id": "different-target-id",
            "symbol": "000660",
            "order_type": "MARKET",
            "limit_price": None,
            "reauth_proof": reauth.json()["reauth_proof"],
            "confirmation": "KIWOOM_MOCK_ONE_SHARE",
        },
    )
    assert repeated.status_code == 403
    assert repeated.json()["error"]["code"] == "REAUTH_PROOF_INVALID"


def test_mock_order_test_fails_closed_when_worker_is_not_ready(
    client: TestClient,
    settings: Settings,
    tmp_path: Path,
) -> None:
    _configure_kiwoom(settings, tmp_path)
    csrf = _login(client)
    response = client.post(
        "/api/v1/system/broker/mock-order-test",
        headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        json={
            "schema_version": "1.0",
            "test_request_id": "mock-browser-request-0002",
            "symbol": "005930",
            "order_type": "MARKET",
            "limit_price": None,
            "reauth_proof": "x" * 32,
            "confirmation": "KIWOOM_MOCK_ONE_SHARE",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "KIWOOM_BROKER_NOT_READY"
