from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.crypto import token_hash
from app.models import AuditLog, AuthRateLimit, UserSession
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET


def begin_login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["challenge_id"]


def complete_login(client: TestClient, challenge_id: str) -> dict:
    code = pyotp.TOTP(TEST_TOTP_SECRET).at(datetime.now(UTC))
    response = client.post(
        "/api/v1/auth/login/totp",
        json={"schema_version": "1.0", "challenge_id": challenge_id, "totp_code": code},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_password_alone_does_not_create_session(client: TestClient, db: Session) -> None:
    begin_login(client)
    assert db.scalar(select(func.count()).select_from(UserSession)) == 0
    protected = client.get("/api/v1/auth/session")
    assert protected.status_code == 401


def test_full_login_session_and_logout(client: TestClient, db: Session) -> None:
    login = complete_login(client, begin_login(client))
    assert login["login_id"] == "admin"
    assert login["csrf_token"]
    current = client.get("/api/v1/auth/session")
    assert current.status_code == 200
    refreshed_csrf = current.json()["csrf_token"]

    missing_csrf = client.post("/api/v1/auth/logout", headers={"Origin": "https://testserver"})
    assert missing_csrf.status_code == 403
    logout = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "https://testserver", "X-CSRF-Token": refreshed_csrf},
    )
    assert logout.status_code == 200
    assert client.get("/api/v1/auth/session").status_code == 401
    assert db.scalar(select(func.count()).select_from(AuditLog)) >= 3


def test_session_cookie_is_hashed_in_database(client: TestClient, db: Session) -> None:
    complete_login(client, begin_login(client))
    raw_cookie = client.cookies.get("cresta_session")
    assert raw_cookie
    stored = db.scalar(select(UserSession))
    assert stored is not None
    assert stored.token_hash == token_hash(raw_cookie)
    assert raw_cookie not in stored.token_hash


def test_invalid_account_and_password_have_same_public_error(client: TestClient) -> None:
    payloads = [
        {"schema_version": "1.0", "login_id": "missing", "password": TEST_PASSWORD},
        {"schema_version": "1.0", "login_id": "admin", "password": "wrong-password"},
    ]
    results = [client.post("/api/v1/auth/login/password", json=payload) for payload in payloads]
    assert [result.status_code for result in results] == [401, 401]
    assert [result.json()["error"]["code"] for result in results] == [
        "AUTHENTICATION_FAILED",
        "AUTHENTICATION_FAILED",
    ]


def test_totp_code_cannot_be_reused(client: TestClient) -> None:
    first_challenge = begin_login(client)
    code = pyotp.TOTP(TEST_TOTP_SECRET).at(datetime.now(UTC))
    first = client.post(
        "/api/v1/auth/login/totp",
        json={"schema_version": "1.0", "challenge_id": first_challenge, "totp_code": code},
    )
    assert first.status_code == 200
    second_challenge = begin_login(client)
    replay = client.post(
        "/api/v1/auth/login/totp",
        json={"schema_version": "1.0", "challenge_id": second_challenge, "totp_code": code},
    )
    assert replay.status_code == 401


def test_unknown_request_field_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login/password",
        json={
            "schema_version": "1.0",
            "login_id": "admin",
            "password": TEST_PASSWORD,
            "unexpected": True,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_inbound_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-Id": "not-a-valid-id"})
    assert response.status_code == 200
    uuid.UUID(response.headers["X-Request-Id"])
    assert response.headers["Cache-Control"] == "no-store"


def test_five_failures_lock_login_and_ip_subjects(client: TestClient, db: Session) -> None:
    payload = {"schema_version": "1.0", "login_id": "admin", "password": "wrong-password"}
    for _ in range(5):
        assert client.post("/api/v1/auth/login/password", json=payload).status_code == 401
    correct = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    assert correct.status_code == 401
    db.expire_all()
    locked = db.scalars(select(AuthRateLimit)).all()
    assert len(locked) == 2
    assert all(item.locked_until is not None for item in locked)


def test_logout_rejects_untrusted_origin(client: TestClient) -> None:
    login = complete_login(client, begin_login(client))
    response = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "https://attacker.invalid", "X-CSRF-Token": login["csrf_token"]},
    )
    assert response.status_code == 403
    assert client.get("/api/v1/auth/session").status_code == 200
