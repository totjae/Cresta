from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.decision_execution import route_trading_decision
from app.emergency_stop import active_pause_entry
from app.models import AuditLog, EmergencyStop, GuardEvaluation, User
from tests.test_decision_execution_shadow import _decision
from tests.test_venue_selection import _login


def _headers(csrf: str, key: str) -> dict[str, str]:
    return {
        "Origin": "https://testserver",
        "X-CSRF-Token": csrf,
        "Idempotency-Key": key,
    }


def test_pause_entry_api_lifecycle_is_idempotent_and_persistent(
    client: TestClient, db: Session
) -> None:
    csrf = _login(client)
    payload = {
        "schema_version": "1.0",
        "level": "PAUSE_ENTRY",
        "reason": "모의 신규매수 경로를 즉시 중지합니다.",
    }
    headers = _headers(csrf, "pause-entry-activate-0001")

    assert client.post("/api/v1/risk/emergency-stop", json=payload).status_code == 403
    activated = client.post(
        "/api/v1/risk/emergency-stop", json=payload, headers=headers
    )
    assert activated.status_code == 200
    assert activated.json()["state"] == "ACTIVE"
    assert activated.json()["version"] == 1

    repeated = client.post(
        "/api/v1/risk/emergency-stop", json=payload, headers=headers
    )
    assert repeated.status_code == 200
    assert repeated.json()["stop_id"] == activated.json()["stop_id"]
    assert db.scalar(select(func.count()).select_from(EmergencyStop)) == 1

    health = client.get("/api/v1/system/health")
    assert health.status_code == 200
    assert health.json()["pause_entry_active"] is True
    assert health.json()["buy_execution_block_reason"] == "EMERGENCY_STOP_ACTIVE"

    release_payload = {**payload, "reason": "모의 신규매수 중지를 안전하게 해제합니다."}
    released = client.post(
        "/api/v1/risk/emergency-stop/release",
        json=release_payload,
        headers=_headers(csrf, "pause-entry-release-0001"),
    )
    assert released.status_code == 200
    assert released.json()["state"] == "RELEASED"
    assert released.json()["version"] == 2
    assert active_pause_entry(db) is None
    assert db.scalar(select(func.count()).select_from(AuditLog)) >= 2


def test_active_pause_entry_is_a_deterministic_buy_guard_block(
    client: TestClient, db: Session, admin: User, settings: Settings
) -> None:
    csrf = _login(client)
    response = client.post(
        "/api/v1/risk/emergency-stop",
        json={
            "schema_version": "1.0",
            "level": "PAUSE_ENTRY",
            "reason": "Guard 차단 동작을 검증하기 위한 중지입니다.",
        },
        headers=_headers(csrf, "pause-entry-guard-test-01"),
    )
    assert response.status_code == 200

    decision = _decision(db)
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="pause-entry-guard-correlation",
        settings=settings,
    )
    assert execution is not None
    guard = db.get(GuardEvaluation, execution.guard_evaluation_id)
    assert guard is not None
    blocked = {
        item["code"]
        for item in json.loads(guard.rule_results_json)
        if item["result"] == "BLOCKED"
    }
    assert "EMERGENCY_STOP_ACTIVE" in blocked
