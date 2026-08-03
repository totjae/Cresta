from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.kiwoom import (
    AccountVerification,
    BrokerAccountSnapshot,
    BrokerOpenOrder,
    BrokerPosition,
    KiwoomAdapterError,
)
from app.models import (
    Position,
    ReconciliationMismatch,
    ReconciliationRun,
    TradingGate,
)
from app.reconciliation import ACCOUNT_ALIAS, run_kiwoom_reconciliation

NOW = datetime(2026, 8, 3, 5, 0, tzinfo=UTC)


class SnapshotClient:
    def __init__(self, snapshot: BrokerAccountSnapshot) -> None:
        self.snapshot = snapshot

    def verify_account(self) -> AccountVerification:
        return AccountVerification("ACCOUNT_VERIFIED", "********90")

    def get_account_snapshot(self) -> BrokerAccountSnapshot:
        return self.snapshot


class FailingClient:
    def verify_account(self) -> AccountVerification:
        raise KiwoomAdapterError("KIWOOM_TIMEOUT", "secret-detail", retryable=True)


def empty_snapshot() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(open_orders=(), fills=(), positions=(), observed_at=NOW)


def test_clean_snapshot_stays_reconciling_until_permanent_worker(db: Session) -> None:
    result = run_kiwoom_reconciliation(db, SnapshotClient(empty_snapshot()))

    assert result.state == "SUCCEEDED"
    assert result.gate_status == "RECONCILING"
    assert result.gate_reason == "PERMANENT_WORKER_REQUIRED"
    assert result.mismatch_count == 0
    run = db.get(ReconciliationRun, result.run_id)
    assert run is not None
    assert run.snapshot_at is not None
    assert run.snapshot_at.replace(tzinfo=UTC) == NOW
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    assert gate is not None
    assert gate.status == "RECONCILING"


def test_external_order_and_position_halt_without_automatic_adoption(db: Session) -> None:
    snapshot = BrokerAccountSnapshot(
        open_orders=(
            BrokerOpenOrder(
                broker_order_id="1234567",
                symbol="005930",
                side="BUY",
                requested_quantity=10,
                filled_quantity=0,
                remaining_quantity=10,
                limit_price=Decimal(70000),
                order_time="101500",
            ),
        ),
        fills=(),
        positions=(
            BrokerPosition(
                symbol="005930",
                quantity=10,
                available_quantity=10,
                average_price=Decimal(70000),
            ),
        ),
        observed_at=NOW,
    )

    result = run_kiwoom_reconciliation(db, SnapshotClient(snapshot))

    assert result.state == "MISMATCH"
    assert result.gate_status == "HALTED"
    assert result.critical_mismatch_count == 2
    codes = set(
        db.scalars(
            select(ReconciliationMismatch.code).where(
                ReconciliationMismatch.run_id == result.run_id
            )
        ).all()
    )
    assert codes == {"BROKER_ORDER_MISSING_INTERNAL", "UNKNOWN_EXTERNAL_POSITION"}
    assert db.scalar(select(Position).where(Position.account_alias == ACCOUNT_ALIAS)) is None


def test_position_quantity_and_average_price_are_compared_without_mutation(db: Session) -> None:
    internal = Position(
        account_alias=ACCOUNT_ALIAS,
        symbol="005930",
        quantity=5,
        average_price=Decimal(69000),
        state="OPEN",
    )
    db.add(internal)
    db.commit()
    snapshot = BrokerAccountSnapshot(
        open_orders=(),
        fills=(),
        positions=(BrokerPosition("005930", 7, 6, Decimal(70000)),),
        observed_at=NOW,
    )

    result = run_kiwoom_reconciliation(db, SnapshotClient(snapshot))

    assert result.gate_status == "HALTED"
    mismatch = db.scalar(
        select(ReconciliationMismatch).where(
            ReconciliationMismatch.run_id == result.run_id,
            ReconciliationMismatch.code == "POSITION_QUANTITY_MISMATCH",
        )
    )
    assert mismatch is not None
    db.refresh(internal)
    assert internal.quantity == 5
    assert internal.average_price == Decimal(69000)


def test_adapter_failure_persists_failed_run_and_degraded_gate(db: Session) -> None:
    with pytest.raises(KiwoomAdapterError) as failure:
        run_kiwoom_reconciliation(db, FailingClient())  # type: ignore[arg-type]
    assert failure.value.code == "KIWOOM_TIMEOUT"

    run = db.scalar(select(ReconciliationRun).order_by(ReconciliationRun.started_at.desc()))
    assert run is not None
    assert run.state == "FAILED"
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    assert gate is not None
    assert gate.status == "DEGRADED"
    assert gate.reason == "RECONCILIATION_FAILED"
