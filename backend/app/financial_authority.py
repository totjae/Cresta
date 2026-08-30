from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.account_authority import (
    get_latest_account_funds,
    get_latest_exact_order_capacity,
    query_and_persist_account_funds,
    query_and_persist_order_capacity,
)
from app.broker.kiwoom import KiwoomAdapterError, KiwoomMockClient, OrderCapacityRequest
from app.config import Settings
from app.models import AccountFundsSnapshot, OrderCapacitySnapshot
from app.schemas import RiskPolicyPayload

BROKER = "KIWOOM"
ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"
ENVIRONMENT = "MOCK"


def configured_financial_client(settings: Settings) -> KiwoomMockClient | None:
    try:
        return KiwoomMockClient(settings)
    except KiwoomAdapterError:
        return None


@dataclass(frozen=True)
class BuyFinancialContext:
    request: OrderCapacityRequest
    requested_notional: int
    funds_ttl: int
    capacity_ttl: int


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _age(received_at: datetime, now: datetime) -> float | None:
    age = (_utc(now) - _utc(received_at)).total_seconds()
    return age if age >= 0 else None


def build_buy_financial_context(
    *,
    symbol: str,
    price: Decimal,
    quantity: int,
    frozen_policy: RiskPolicyPayload,
    current_policy: RiskPolicyPayload,
) -> BuyFinancialContext:
    integral_price = int(price)
    if price != Decimal(integral_price) or integral_price <= 0 or quantity <= 0:
        raise ValueError("exact BUY price and quantity must be positive integers")
    return BuyFinancialContext(
        request=OrderCapacityRequest(
            symbol=symbol,
            side="BUY",
            requested_price=integral_price,
            requested_quantity=quantity,
            expected_buy_price=integral_price,
        ),
        requested_notional=integral_price * quantity,
        funds_ttl=min(
            frozen_policy.account_funds_stale_seconds,
            current_policy.account_funds_stale_seconds,
        ),
        capacity_ttl=min(
            frozen_policy.order_capacity_stale_seconds,
            current_policy.order_capacity_stale_seconds,
        ),
    )


def _fresh(received_at: datetime, *, now: datetime, ttl: int) -> tuple[bool, float | None]:
    age = _age(received_at, now)
    return age is not None and age <= ttl, age


def refresh_financial_evidence_if_needed(
    db: Session,
    *,
    client: KiwoomMockClient | None,
    context: BuyFinancialContext,
    now: datetime,
) -> None:
    funds = get_latest_account_funds(
        db, broker=BROKER, account_alias=ACCOUNT_ALIAS, environment=ENVIRONMENT
    )
    funds_fresh = bool(
        funds and _fresh(funds.received_at, now=now, ttl=context.funds_ttl)[0]
    )
    capacity = get_latest_exact_order_capacity(
        db,
        broker=BROKER,
        account_alias=ACCOUNT_ALIAS,
        environment=ENVIRONMENT,
        request=context.request,
    )
    capacity_fresh = bool(
        capacity and _fresh(capacity.received_at, now=now, ttl=context.capacity_ttl)[0]
    )
    if client is None or (funds_fresh and capacity_fresh):
        return
    # This helper is called before the Guard/Approval authority transaction.
    db.rollback()
    if not funds_fresh:
        try:
            query_and_persist_account_funds(db, client, query_type="3")
        except (KiwoomAdapterError, SQLAlchemyError):
            db.rollback()
    if not capacity_fresh:
        try:
            query_and_persist_order_capacity(db, client, context.request)
        except (KiwoomAdapterError, SQLAlchemyError):
            db.rollback()


def financial_guard_rules(
    db: Session,
    *,
    context: BuyFinancialContext,
    now: datetime,
    frozen_risk_policy_id: str | None,
    current_risk_policy_id: str | None,
    frozen_policy: RiskPolicyPayload,
    current_policy: RiskPolicyPayload,
) -> list[dict[str, object]]:
    funds: AccountFundsSnapshot | None = get_latest_account_funds(
        db, broker=BROKER, account_alias=ACCOUNT_ALIAS, environment=ENVIRONMENT
    )
    capacity: OrderCapacitySnapshot | None = get_latest_exact_order_capacity(
        db,
        broker=BROKER,
        account_alias=ACCOUNT_ALIAS,
        environment=ENVIRONMENT,
        request=context.request,
    )
    funds_fresh, funds_age = (
        _fresh(funds.received_at, now=now, ttl=context.funds_ttl)
        if funds is not None
        else (False, None)
    )
    capacity_fresh, capacity_age = (
        _fresh(capacity.received_at, now=now, ttl=context.capacity_ttl)
        if capacity is not None
        else (False, None)
    )
    provenance = {
        "frozen_risk_policy_id": frozen_risk_policy_id,
        "current_risk_policy_id": current_risk_policy_id,
        "frozen_funds_ttl": frozen_policy.account_funds_stale_seconds,
        "current_funds_ttl": current_policy.account_funds_stale_seconds,
        "effective_funds_ttl": context.funds_ttl,
        "frozen_capacity_ttl": frozen_policy.order_capacity_stale_seconds,
        "current_capacity_ttl": current_policy.order_capacity_stale_seconds,
        "effective_capacity_ttl": context.capacity_ttl,
        "funds_snapshot_id": funds.id if funds else None,
        "funds_received_at": _utc(funds.received_at).isoformat() if funds else None,
        "funds_age_seconds": funds_age,
        "capacity_snapshot_id": capacity.id if capacity else None,
        "capacity_received_at": _utc(capacity.received_at).isoformat() if capacity else None,
        "capacity_age_seconds": capacity_age,
        "evaluation_time": _utc(now).isoformat(),
        "requested_notional": context.requested_notional,
        "requested_quantity": context.request.requested_quantity,
        "requested_price": context.request.requested_price,
    }

    def result(code: str, passed: bool, **values: object) -> dict[str, object]:
        return {
            "code": code,
            "result": "PASSED" if passed else "BLOCKED",
            "evidence": {**provenance, **values},
        }

    generic = funds.generic_orderable_amount if funds else None
    cash = capacity.orderable_cash if capacity else None
    amount100 = capacity.margin_100_orderable_amount if capacity else None
    quantity100 = capacity.margin_100_orderable_quantity if capacity else None
    notional = context.requested_notional
    quantity = int(context.request.requested_quantity or 0)
    return [
        result("ACCOUNT_FUNDS_FRESH", funds_fresh),
        result("ORDER_CAPACITY_FRESH", capacity_fresh),
        result(
            "GENERIC_ORDERABLE_AMOUNT_SUFFICIENT",
            funds_fresh and generic is not None and notional <= generic,
            generic_orderable_amount=generic,
        ),
        result(
            "ORDERABLE_CASH_SUFFICIENT",
            capacity_fresh and cash is not None and notional <= cash,
            orderable_cash=cash,
        ),
        result(
            "MARGIN_100_AMOUNT_SUFFICIENT",
            capacity_fresh and amount100 is not None and notional <= amount100,
            margin_100_orderable_amount=amount100,
        ),
        result(
            "MARGIN_100_QUANTITY_SUFFICIENT",
            capacity_fresh and quantity100 is not None and quantity <= quantity100,
            margin_100_orderable_quantity=quantity100,
        ),
    ]
