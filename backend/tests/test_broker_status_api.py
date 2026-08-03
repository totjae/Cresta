from __future__ import annotations

import json
from datetime import UTC, datetime

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app import cli
from app.broker.worker_state import acquire_lease, update_worker_state
from app.models import BrokerLease
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET


def _login(client: TestClient) -> None:
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    completed = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(datetime.now(UTC)),
        },
    )
    assert completed.status_code == 200


def _ready_worker(db: Session) -> None:
    identity = acquire_lease(db, "private-owner-id", lease_seconds=60)
    assert identity is not None
    assert update_worker_state(
        db,
        identity,
        "READY",
        websocket_connected=True,
        subscriptions_ready=True,
        gate_status="READY",
        gate_reason="WORKER_HEALTHY",
        reconciliation_run_id="safe-run-id",
    )


def test_broker_status_api_requires_auth_and_omits_owner_and_secrets(
    client: TestClient,
    db: Session,
) -> None:
    _ready_worker(db)
    assert client.get("/api/v1/system/broker").status_code == 401
    _login(client)

    response = client.get("/api/v1/system/broker")
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "READY"
    assert payload["gate_status"] == "READY"
    assert payload["lease_valid"] is True
    assert payload["websocket_connected"] is True
    encoded = json.dumps(payload)
    assert "private-owner-id" not in encoded
    assert "access_token" not in encoded.casefold()
    assert "account_id" not in encoded.casefold()


def test_worker_status_cli_is_safe_and_uses_nonready_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
    capsys,
) -> None:
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    with session_factory() as db:
        _ready_worker(db)

    assert cli.kiwoom_worker_status() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "READY"
    assert payload["lease_valid"] is True
    assert "owner" not in payload

    with session_factory() as db:
        lease = db.get(BrokerLease, "KIWOOM_MOCK_PRIMARY")
        assert lease is not None
        lease.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
        db.commit()
    assert cli.kiwoom_worker_status() == 5
