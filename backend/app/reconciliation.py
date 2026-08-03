from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.kiwoom import BrokerAccountSnapshot, KiwoomAdapterError, KiwoomMockClient
from app.ids import uuid7
from app.models import (
    Position,
    ReconciliationMismatch,
    ReconciliationRun,
    TradingGate,
    TradingOrder,
)

ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"
ACTIVE_ORDER_STATES = {
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
BROKER_REQUEST_IDS = ["ka00001", "ka10075", "ka10076", "kt00018"]


@dataclass(frozen=True)
class Mismatch:
    code: str
    symbol: str | None
    broker_value: dict[str, object]
    internal_value: dict[str, object]
    severity: str = "CRITICAL"


@dataclass(frozen=True)
class ReconciliationResult:
    run_id: str
    state: str
    gate_status: str
    gate_reason: str
    open_order_count: int
    fill_count: int
    position_count: int
    mismatch_count: int
    critical_mismatch_count: int


def run_kiwoom_reconciliation(
    db: Session,
    client: KiwoomMockClient,
    *,
    correlation_id: str | None = None,
) -> ReconciliationResult:
    now = datetime.now(UTC)
    run = ReconciliationRun(
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        trigger="MANUAL_BOOTSTRAP",
        scope="ACCOUNT",
        state="RUNNING",
        started_at=now,
        mismatch_count=0,
        critical_mismatch_count=0,
        broker_request_ids_json=_json(BROKER_REQUEST_IDS),
        correlation_id=correlation_id or uuid7(),
        result_summary_json="{}",
    )
    db.add(run)
    _set_gate(db, "RECONCILING", "BOOTSTRAP_RECONCILIATION_RUNNING")
    db.commit()
    db.refresh(run)

    try:
        client.verify_account()
        snapshot = client.get_account_snapshot()
        mismatches = compare_snapshot(db, snapshot)
    except KiwoomAdapterError:
        run.state = "FAILED"
        run.completed_at = datetime.now(UTC)
        run.result_summary_json = _json({"result": "FAILED"})
        _set_gate(db, "DEGRADED", "RECONCILIATION_FAILED")
        db.commit()
        raise

    for mismatch in mismatches:
        db.add(
            ReconciliationMismatch(
                run_id=run.id,
                code=mismatch.code,
                symbol=mismatch.symbol,
                severity=mismatch.severity,
                state="OPEN",
                broker_value_json=_json(mismatch.broker_value),
                internal_value_json=_json(mismatch.internal_value),
            )
        )

    critical_count = sum(item.severity == "CRITICAL" for item in mismatches)
    run.state = "MISMATCH" if mismatches else "SUCCEEDED"
    run.completed_at = datetime.now(UTC)
    run.snapshot_at = snapshot.observed_at
    run.mismatch_count = len(mismatches)
    run.critical_mismatch_count = critical_count
    run.result_summary_json = _json(
        {
            "open_order_count": len(snapshot.open_orders),
            "fill_count": len(snapshot.fills),
            "position_count": len(snapshot.positions),
        }
    )
    if critical_count:
        gate_status, gate_reason = "HALTED", "RECONCILIATION_MISMATCH"
    else:
        gate_status, gate_reason = "RECONCILING", "PERMANENT_WORKER_REQUIRED"
    _set_gate(db, gate_status, gate_reason)
    db.commit()

    return ReconciliationResult(
        run_id=run.id,
        state=run.state,
        gate_status=gate_status,
        gate_reason=gate_reason,
        open_order_count=len(snapshot.open_orders),
        fill_count=len(snapshot.fills),
        position_count=len(snapshot.positions),
        mismatch_count=len(mismatches),
        critical_mismatch_count=critical_count,
    )


def compare_snapshot(db: Session, snapshot: BrokerAccountSnapshot) -> list[Mismatch]:
    mismatches: list[Mismatch] = []
    internal_orders = db.scalars(
        select(TradingOrder).where(
            TradingOrder.account_alias == ACCOUNT_ALIAS,
            TradingOrder.status.in_(ACTIVE_ORDER_STATES),
        )
    ).all()
    internal_by_broker = {
        order.broker_order_id: order for order in internal_orders if order.broker_order_id
    }
    broker_by_id = {order.broker_order_id: order for order in snapshot.open_orders}

    for broker_id, broker in broker_by_id.items():
        internal = internal_by_broker.get(broker_id)
        if internal is None:
            mismatches.append(
                Mismatch(
                    "BROKER_ORDER_MISSING_INTERNAL",
                    broker.symbol,
                    _broker_order_value(broker),
                    {},
                )
            )
            continue
        internal_value = _internal_order_value(internal)
        broker_value = _broker_order_value(broker)
        if broker_value != internal_value:
            mismatches.append(
                Mismatch(
                    "ORDER_STATE_MISMATCH",
                    broker.symbol,
                    broker_value,
                    internal_value,
                )
            )

    for internal in internal_orders:
        if not internal.broker_order_id or internal.broker_order_id not in broker_by_id:
            mismatches.append(
                Mismatch(
                    "INTERNAL_ORDER_MISSING_BROKER",
                    internal.symbol,
                    {},
                    _internal_order_value(internal),
                )
            )

    broker_fill_totals: dict[str, int] = defaultdict(int)
    for fill in snapshot.fills:
        broker_fill_totals[fill.broker_order_id] += fill.quantity
    for broker_id, quantity in broker_fill_totals.items():
        internal = internal_by_broker.get(broker_id)
        if internal is not None and quantity != internal.filled_quantity:
            mismatches.append(
                Mismatch(
                    "FILL_QUANTITY_MISMATCH",
                    internal.symbol,
                    {"broker_order_id": broker_id, "filled_quantity": quantity},
                    {
                        "broker_order_id": broker_id,
                        "filled_quantity": internal.filled_quantity,
                    },
                )
            )

    internal_positions = db.scalars(
        select(Position).where(Position.account_alias == ACCOUNT_ALIAS, Position.state == "OPEN")
    ).all()
    internal_positions_by_symbol = {position.symbol: position for position in internal_positions}
    broker_positions_by_symbol = {position.symbol: position for position in snapshot.positions}

    for symbol, broker in broker_positions_by_symbol.items():
        internal = internal_positions_by_symbol.get(symbol)
        if internal is None:
            mismatches.append(
                Mismatch(
                    "UNKNOWN_EXTERNAL_POSITION",
                    symbol,
                    _broker_position_value(broker),
                    {},
                )
            )
            continue
        if broker.quantity != internal.quantity:
            mismatches.append(
                Mismatch(
                    "POSITION_QUANTITY_MISMATCH",
                    symbol,
                    _broker_position_value(broker),
                    _internal_position_value(internal),
                )
            )
        elif broker.average_price != internal.average_price:
            mismatches.append(
                Mismatch(
                    "AVERAGE_PRICE_MISMATCH",
                    symbol,
                    _broker_position_value(broker),
                    _internal_position_value(internal),
                )
            )

    for symbol, internal in internal_positions_by_symbol.items():
        if symbol not in broker_positions_by_symbol:
            mismatches.append(
                Mismatch(
                    "POSITION_QUANTITY_MISMATCH",
                    symbol,
                    {},
                    _internal_position_value(internal),
                )
            )
    return mismatches


def _set_gate(db: Session, status: str, reason: str) -> TradingGate:
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    if gate is None:
        gate = TradingGate(
            account_alias=ACCOUNT_ALIAS,
            environment="MOCK",
            status=status,
            reason=reason,
        )
        db.add(gate)
    else:
        gate.status = status
        gate.reason = reason
        gate.version += 1
    return gate


def _broker_order_value(order) -> dict[str, object]:
    return {
        "broker_order_id": order.broker_order_id,
        "symbol": order.symbol,
        "side": order.side,
        "requested_quantity": order.requested_quantity,
        "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity,
    }


def _internal_order_value(order: TradingOrder) -> dict[str, object]:
    return {
        "broker_order_id": order.broker_order_id,
        "symbol": order.symbol,
        "side": order.side,
        "requested_quantity": order.requested_quantity,
        "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity,
    }


def _broker_position_value(position) -> dict[str, object]:
    return {
        "symbol": position.symbol,
        "quantity": position.quantity,
        "available_quantity": position.available_quantity,
        "average_price": _decimal_text(position.average_price),
    }


def _internal_position_value(position: Position) -> dict[str, object]:
    return {
        "symbol": position.symbol,
        "quantity": position.quantity,
        "average_price": _decimal_text(position.average_price),
    }


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
