from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.crypto import token_hash
from app.models import AuditLog, ConfigurationVersion, ReauthProof, User
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
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(now - timedelta(seconds=30)),
        },
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_execution_policy_version_lifecycle_requires_bound_reauth(
    client: TestClient, db: Session
) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    initial = client.get("/api/v1/settings/execution-policy")
    assert initial.status_code == 200
    assert initial.json()["source"] == "SAFE_DEFAULT"
    assert initial.json()["active_version_id"] is None
    assert initial.json()["policy"]["buy"] == "MANUAL_APPROVAL"
    assert initial.json()["policy"]["fixed_stop_loss"] == "AUTOMATIC"

    policy = initial.json()["policy"] | {"buy": "AUTOMATIC"}
    draft = client.post(
        "/api/v1/settings/execution-policy/drafts",
        headers=headers,
        json={"schema_version": "1.0", "policy": policy, "reason": "모의 자동매수 검증"},
    )
    assert draft.status_code == 200, draft.text
    version_id = draft.json()["version_id"]
    assert draft.json()["state"] == "DRAFT"

    validated = client.post(
        f"/api/v1/settings/execution-policy/{version_id}/validate", headers=headers
    )
    assert validated.status_code == 200
    assert validated.json()["state"] == "VALIDATED"
    assert client.get("/api/v1/settings/execution-policy").json()["source"] == "SAFE_DEFAULT"

    reauth = client.post(
        "/api/v1/auth/reauth/totp",
        headers=headers,
        json={
            "schema_version": "1.0",
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(datetime.now(UTC)),
            "target_action": "EXECUTION_POLICY_ACTIVATE",
            "target_id": version_id,
        },
    )
    assert reauth.status_code == 200, reauth.text
    activated = client.post(
        f"/api/v1/settings/execution-policy/{version_id}/activate",
        headers=headers,
        json={"schema_version": "1.0", "reauth_proof": reauth.json()["reauth_proof"]},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["state"] == "ACTIVE"
    current = client.get("/api/v1/settings/execution-policy").json()
    assert current["active_version_id"] == version_id
    assert current["source"] == "USER_DEFAULT"
    assert current["policy"]["buy"] == "AUTOMATIC"
    assert db.scalar(select(func.count()).select_from(ConfigurationVersion)) == 1
    assert db.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "EXECUTION_POLICY_ACTIVATED"
        )
    ) == 1

    history = client.get("/api/v1/settings/execution-policy/history")
    assert history.status_code == 200
    assert [item["state"] for item in history.json()["items"]] == ["ACTIVE"]

    second = client.post(
        "/api/v1/settings/execution-policy/drafts",
        headers=headers,
        json={
            "schema_version": "1.0",
            "policy": policy | {"partial_sell": "AUTOMATIC"},
            "reason": "부분매도 자동화 추가",
        },
    )
    second_id = second.json()["version_id"]
    assert client.post(
        f"/api/v1/settings/execution-policy/{second_id}/validate", headers=headers
    ).status_code == 200
    raw_proof = "second-policy-proof-value-000000000000"
    user = db.scalar(select(User).where(User.login_id == "admin"))
    assert user is not None
    db.add(
        ReauthProof(
            proof_hash=token_hash(raw_proof),
            user_id=user.id,
            target_action="EXECUTION_POLICY_ACTIVATE",
            target_id=second_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    db.commit()
    replacement = client.post(
        f"/api/v1/settings/execution-policy/{second_id}/activate",
        headers=headers,
        json={"schema_version": "1.0", "reauth_proof": raw_proof},
    )
    assert replacement.status_code == 200, replacement.text
    assert db.get(ConfigurationVersion, version_id).state == "SUPERSEDED"
    assert db.scalar(
        select(func.count()).select_from(ConfigurationVersion).where(
            ConfigurationVersion.state == "ACTIVE"
        )
    ) == 1
