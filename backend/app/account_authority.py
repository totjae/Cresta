from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.broker.kiwoom import (
    AccountFundsSnapshotData,
    KiwoomAdapterError,
    KiwoomMockClient,
    OrderCapacityRequest,
    OrderCapacitySnapshotData,
)
from app.models import AccountFundsSnapshot, OrderCapacitySnapshot


def append_account_funds_snapshot(
    db: Session, data: AccountFundsSnapshotData
) -> AccountFundsSnapshot:
    _validate_received_at(data.received_at)
    row = AccountFundsSnapshot(**asdict(data))
    db.add(row)
    db.flush()
    return row


def append_order_capacity_snapshot(
    db: Session, data: OrderCapacitySnapshotData
) -> OrderCapacitySnapshot:
    _validate_received_at(data.received_at)
    row = OrderCapacitySnapshot(**asdict(data))
    db.add(row)
    db.flush()
    return row


def get_latest_account_funds(
    db: Session, *, broker: str, account_alias: str, environment: str
) -> AccountFundsSnapshot | None:
    return db.scalar(
        select(AccountFundsSnapshot)
        .where(
            AccountFundsSnapshot.broker == broker,
            AccountFundsSnapshot.account_alias == account_alias,
            AccountFundsSnapshot.environment == environment,
        )
        .order_by(AccountFundsSnapshot.received_at.desc(), AccountFundsSnapshot.id.desc())
        .limit(1)
    )


def get_latest_exact_order_capacity(
    db: Session,
    *,
    broker: str,
    account_alias: str,
    environment: str,
    request: OrderCapacityRequest,
) -> OrderCapacitySnapshot | None:
    statement: Select[tuple[OrderCapacitySnapshot]] = select(OrderCapacitySnapshot).where(
        OrderCapacitySnapshot.broker == broker,
        OrderCapacitySnapshot.account_alias == account_alias,
        OrderCapacitySnapshot.environment == environment,
        OrderCapacitySnapshot.symbol == request.symbol,
        OrderCapacitySnapshot.side == request.side,
        OrderCapacitySnapshot.requested_price == request.requested_price,
        _nullable_exact(OrderCapacitySnapshot.io_amount, request.io_amount),
        _nullable_exact(
            OrderCapacitySnapshot.requested_quantity, request.requested_quantity
        ),
        _nullable_exact(
            OrderCapacitySnapshot.expected_buy_price, request.expected_buy_price
        ),
    )
    return db.scalar(
        statement.order_by(
            OrderCapacitySnapshot.received_at.desc(), OrderCapacitySnapshot.id.desc()
        ).limit(1)
    )


def query_and_persist_order_capacity(
    db: Session, client: KiwoomMockClient, request: OrderCapacityRequest
) -> OrderCapacitySnapshot:
    """Perform the broker read before opening this function's short write boundary."""
    data = client.query_order_capacity(request)
    try:
        row = append_order_capacity_snapshot(db, data)
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


def query_and_persist_account_funds(
    db: Session, client: KiwoomMockClient, *, query_type: str = "3"
) -> AccountFundsSnapshot:
    """Read kt00001 outside the short append transaction and persist one observation."""
    data = client.get_account_funds(query_type=query_type)
    try:
        row = append_account_funds_snapshot(db, data)
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


def _nullable_exact(column: object, value: int | None) -> object:
    if value is None:
        return column.is_(None)  # type: ignore[attr-defined, no-any-return]
    return column == value


def _validate_received_at(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise KiwoomAdapterError(
            "KIWOOM_TIMEZONE_REQUIRED", "Financial received_at must include timezone"
        )
