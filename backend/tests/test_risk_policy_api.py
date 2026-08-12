from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditLog, ConfigurationVersion
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET


def _login(client: TestClient) -> str:
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    response = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(
                datetime.now(UTC) - timedelta(seconds=30)
            ),
        },
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _policy(**overrides: object) -> dict[str, object]:
    return {
        "entry_order_amount": 500_000,
        "max_single_order_amount": 1_000_000,
        "max_position_amount_per_symbol": 1_000_000,
        "max_total_position_amount": 3_000_000,
        "max_open_positions": 3,
        "max_daily_entries": 5,
        "fixed_stop_loss_pct": "-2.0",
        "quote_stale_seconds": 2,
        "max_spread_pct": "0.30",
        "max_price_deviation_pct": "0.50",
        "daily_loss_limit_pct": "5.0",
        "daily_loss_basis": "REALIZED_PLUS_UNREALIZED",
        "max_consecutive_losses": 3,
    } | overrides


def test_risk_policy_safe_default_and_version_lifecycle(
    client: TestClient, db: Session
) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    initial = client.get("/api/v1/settings/risk-policy")
    assert initial.status_code == 200
    assert initial.json()["source"] == "SAFE_DEFAULT"
    assert initial.json()["active_version_id"] is None
    assert initial.json()["policy"]["entry_order_amount"] is None

    draft = client.post(
        "/api/v1/settings/risk-policy/drafts",
        headers=headers,
        json={"schema_version": "1.0", "policy": _policy(), "reason": "모의 위험 설정"},
    )
    assert draft.status_code == 200, draft.text
    version_id = draft.json()["version_id"]
    validated = client.post(
        f"/api/v1/settings/risk-policy/{version_id}/validate", headers=headers
    )
    assert validated.status_code == 200
    activated = client.post(
        f"/api/v1/settings/risk-policy/{version_id}/activate",
        headers=headers,
        json={"schema_version": "1.0"},
    )
    assert activated.status_code == 200, activated.text
    current = client.get("/api/v1/settings/risk-policy").json()
    assert current["active_version_id"] == version_id
    assert current["policy"]["entry_order_amount"] == 500_000
    assert db.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "RISK_POLICY_ACTIVATED"
        )
    ) == 1
    history = client.get("/api/v1/settings/risk-policy/history")
    assert [item["state"] for item in history.json()["items"]] == ["ACTIVE"]


def test_risk_policy_rejects_invalid_ranges_and_stale_activation(
    client: TestClient, db: Session
) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    invalid = client.post(
        "/api/v1/settings/risk-policy/drafts",
        headers=headers,
        json={
            "schema_version": "1.0",
            "policy": _policy(entry_order_amount=2_000_000),
            "reason": "잘못된 한도",
        },
    )
    assert invalid.status_code == 400
    assert db.scalar(
        select(func.count()).select_from(ConfigurationVersion).where(
            ConfigurationVersion.category == "RISK_POLICY"
        )
    ) == 0

    first = client.post(
        "/api/v1/settings/risk-policy/drafts",
        headers=headers,
        json={"schema_version": "1.0", "policy": _policy(), "reason": "첫 후보"},
    ).json()["version_id"]
    stale = client.post(
        "/api/v1/settings/risk-policy/drafts",
        headers=headers,
        json={"schema_version": "1.0", "policy": _policy(), "reason": "동시 후보"},
    ).json()["version_id"]
    for version_id in (first, stale):
        assert client.post(
            f"/api/v1/settings/risk-policy/{version_id}/validate", headers=headers
        ).status_code == 200
    assert client.post(
        f"/api/v1/settings/risk-policy/{first}/activate",
        headers=headers,
        json={"schema_version": "1.0"},
    ).status_code == 200
    conflict = client.post(
        f"/api/v1/settings/risk-policy/{stale}/activate",
        headers=headers,
        json={"schema_version": "1.0"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CONFIGURATION_VERSION_CONFLICT"


def test_risk_policy_daily_loss_fields_validated(client: TestClient, db: Session) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    # daily_loss_limit_pct out of range (too high) -> rejected.
    bad = client.post(
        "/api/v1/settings/risk-policy/drafts",
        headers=headers,
        json={"schema_version": "1.0", "policy": _policy(daily_loss_limit_pct="25.0"), "reason": "bad daily loss"},
    )
    assert bad.status_code == 400
    # max_consecutive_losses out of range -> rejected.
    bad2 = client.post(
        "/api/v1/settings/risk-policy/drafts",
        headers=headers,
        json={"schema_version": "1.0", "policy": _policy(max_consecutive_losses=20), "reason": "bad consec"},
    )
    assert bad2.status_code == 400
    # invalid basis -> rejected.
    bad3 = client.post(
        "/api/v1/settings/risk-policy/drafts",
        headers=headers,
        json={"schema_version": "1.0", "policy": _policy(daily_loss_basis="BOGUS"), "reason": "bad basis"},
    )
    assert bad3.status_code == 400
    # valid new fields accepted and persisted.
    good = client.post(
        "/api/v1/settings/risk-policy/drafts",
        headers=headers,
        json={"schema_version": "1.0", "policy": _policy(daily_loss_limit_pct="3.0", daily_loss_basis="REALIZED_ONLY", max_consecutive_losses=2), "reason": "valid loss config"},
    )
    assert good.status_code == 200
    assert good.json()["policy"]["daily_loss_limit_pct"] == "3.0"
    assert good.json()["policy"]["daily_loss_basis"] == "REALIZED_ONLY"
    assert good.json()["policy"]["max_consecutive_losses"] == 2
