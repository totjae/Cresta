from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.worker_state import get_broker_status
from app.config import Settings
from app.models import AuditLog, OrderIntent, TradingOrder, User
from app.reconciliation import ACCOUNT_ALIAS
from app.schemas import MockOrderTestRequest

KST = timezone(timedelta(hours=9))
ACTIVE_STATES = {
    "CREATED",
    "VALIDATING",
    "SUBMITTING",
    "ACKNOWLEDGED",
    "OPEN",
    "PARTIALLY_FILLED",
    "CANCEL_PENDING",
    "REPLACE_PENDING",
    "UNKNOWN",
    "RECONCILING",
}


class MockOrderTestError(RuntimeError):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def create_mock_order_test(
    db: Session,
    *,
    user: User,
    payload: MockOrderTestRequest,
    correlation_id: str,
    request_ip: str,
    user_agent: str,
    settings: Settings,
) -> TradingOrder:
    if settings.live_trading_enabled or settings.kiwoom_configuration_status() != "CONFIGURED":
        raise MockOrderTestError("KIWOOM_MOCK_TEST_UNAVAILABLE", 403)
    if payload.order_type == "MARKET" and payload.limit_price is not None:
        raise MockOrderTestError("MARKET_PRICE_NOT_ALLOWED", 400)
    if payload.order_type == "LIMIT" and (
        payload.limit_price is None or payload.limit_price != payload.limit_price.to_integral_value()
    ):
        raise MockOrderTestError("LIMIT_INTEGER_PRICE_REQUIRED", 400)

    status = get_broker_status(db)
    if not (
        status.state == "READY"
        and status.gate_status == "READY"
        and status.lease_valid
        and status.websocket_connected
        and status.subscriptions_ready
    ):
        raise MockOrderTestError("KIWOOM_BROKER_NOT_READY", 409)

    idempotency_key = f"kiwoom-mock-test:{payload.test_request_id}"
    if db.scalar(select(TradingOrder.id).where(TradingOrder.idempotency_key == idempotency_key)):
        raise MockOrderTestError("MOCK_TEST_REQUEST_ALREADY_USED", 409)
    if db.scalar(
        select(TradingOrder.id).where(
            TradingOrder.account_alias == ACCOUNT_ALIAS,
            TradingOrder.symbol == payload.symbol,
            TradingOrder.status.in_(ACTIVE_STATES),
        )
    ):
        raise MockOrderTestError("ACTIVE_SYMBOL_ORDER_EXISTS", 409)

    intent = OrderIntent(
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        symbol=payload.symbol,
        market="KRX",
        side="BUY",
        action="MOCK_CONNECTION_TEST",
        requested_quantity=1,
        correlation_id=correlation_id,
    )
    db.add(intent)
    db.flush()
    canonical = json.dumps(
        {
            "environment": "MOCK",
            "market": "KRX",
            "symbol": payload.symbol,
            "side": "BUY",
            "quantity": 1,
            "order_type": payload.order_type,
            "limit_price": str(payload.limit_price) if payload.limit_price is not None else None,
            "test_request_id": payload.test_request_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    order = TradingOrder(
        intent_id=intent.id,
        order_group_id=intent.order_group_id,
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        symbol=payload.symbol,
        market="KRX",
        side="BUY",
        order_type=payload.order_type,
        limit_price=payload.limit_price,
        requested_quantity=1,
        remaining_quantity=1,
        status="CREATED",
        idempotency_key=idempotency_key,
        request_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        trading_date=datetime.now(KST).date(),
        correlation_id=correlation_id,
    )
    db.add(order)
    db.flush()
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="KIWOOM_MOCK_ORDER_TEST_CREATED",
            target=order.id,
            result="PASSED",
            request_ip=request_ip,
            user_agent=user_agent[:256],
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {"symbol": payload.symbol, "order_type": payload.order_type, "quantity": 1},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    db.refresh(order)
    return order
