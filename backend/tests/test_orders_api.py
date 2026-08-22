from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import OrderEvent
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
        json={"schema_version": "1.0", "challenge_id": challenge.json()["challenge_id"], "totp_code": code},
    )
    assert completed.status_code == 200


def test_order_read_api_requires_auth_and_has_no_creation_route(
    client: TestClient,
    db: Session,
    settings: Settings,
) -> None:
    assert client.get("/api/v1/orders").status_code == 401
    set_paper_gate(db, "READY", "TEST_RECONCILED")
    order = create_paper_order(
        db,
        PaperOrderRequest(
            symbol="005930",
            side="BUY",
            quantity=2,
            order_type="LIMIT",
            limit_price=Decimal(70000),
            idempotency_key="api-read-order",
            correlation_id="019fb7f6-0000-7000-8000-000000000101",
        ),
        settings,
    )
    apply_paper_fill(
        db,
        order.id,
        source_key="api-read-fill",
        quantity=1,
        price=Decimal(70000),
    )
    db.add(
        OrderEvent(
            order_id=order.id,
            event_type="ORDER_REJECTED",
            source="KIWOOM",
            source_key="api-order-rejection",
            payload_hash="a" * 64,
            payload_json=json.dumps(
                {
                    "broker_result_code": "8030",
                    "broker_result_message": "투자구분 불일치 계좌 1234567890 token=top-secret-token",
                    "raw_response": "MUST_NOT_BE_EXPOSED",
                },
                ensure_ascii=False,
            ),
            correlation_id=order.correlation_id,
            occurred_at=datetime.now(UTC),
        )
    )
    db.commit()
    login(client)
    listed = client.get("/api/v1/orders")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == order.id
    detail = client.get(f"/api/v1/orders/{order.id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "PARTIALLY_FILLED"
    assert detail.json()["fills"][0]["quantity"] == 1
    assert len(detail.json()["events"]) >= 5
    rejected_event = next(
        event for event in detail.json()["events"] if event["event_type"] == "ORDER_REJECTED"
    )
    assert rejected_event["broker_result_code"] == "8030"
    assert "투자구분 불일치" in rejected_event["broker_result_message"]
    assert "1234567890" not in rejected_event["broker_result_message"]
    assert "top-secret-token" not in rejected_event["broker_result_message"]
    assert "payload_json" not in rejected_event
    assert "MUST_NOT_BE_EXPOSED" not in detail.text
    assert client.post("/api/v1/orders", json={}).status_code == 405


def test_missing_order_uses_standard_error_envelope(client: TestClient) -> None:
    login(client)
    response = client.get("/api/v1/orders/019fb7f6-0000-7000-8000-000000000999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORDER_NOT_FOUND"
    assert response.json()["error"]["correlation_id"]
