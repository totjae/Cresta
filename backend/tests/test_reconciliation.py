from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.kiwoom import (
    AccountVerification,
    BrokerAccountSnapshot,
    BrokerFillSummary,
    BrokerOpenOrder,
    BrokerPosition,
    KiwoomAdapterError,
)
from app.models import (
    Fill,
    OrderEvent,
    OrderIntent,
    Position,
    PositionEvent,
    ReconciliationMismatch,
    ReconciliationRun,
    TradingGate,
    TradingOrder,
)
from app.reconciliation import (
    ACCOUNT_ALIAS,
    ReconciliationProjectionError,
    run_kiwoom_reconciliation,
)

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


def test_external_order_and_position_are_projected_from_broker(db: Session) -> None:
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

    assert result.state == "SUCCEEDED"
    assert result.gate_status == "RECONCILING"
    assert result.critical_mismatch_count == 0
    order = db.scalar(select(TradingOrder).where(TradingOrder.broker_order_id == "1234567"))
    assert order is not None
    assert order.status == "OPEN"
    assert order.remaining_quantity == 10
    intent = db.get(OrderIntent, order.intent_id)
    assert intent is not None
    assert intent.action == "BROKER_IMPORTED"
    position = db.scalar(select(Position).where(Position.account_alias == ACCOUNT_ALIAS))
    assert position is not None
    assert position.quantity == 10
    assert position.origin == "EXTERNAL"


def test_position_quantity_and_average_price_follow_broker_projection(db: Session) -> None:
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

    assert result.gate_status == "RECONCILING"
    assert result.mismatch_count == 0
    db.refresh(internal)
    assert internal.quantity == 7
    assert internal.average_price == Decimal(70000)
    assert internal.origin == "CRESTA_MANAGED"
    event = db.scalar(select(PositionEvent).where(PositionEvent.position_id == internal.id))
    assert event is not None
    assert event.cause_id == result.run_id


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


def test_projection_failure_rolls_back_and_degrades_gate(db: Session) -> None:
    invalid_snapshot = BrokerAccountSnapshot(
        open_orders=(
            BrokerOpenOrder(
                "invalid-order",
                "005930",
                "BUY",
                1,
                1,
                1,
                Decimal(70000),
                "101500",
            ),
        ),
        fills=(),
        positions=(),
        observed_at=NOW,
    )

    with pytest.raises(ReconciliationProjectionError):
        run_kiwoom_reconciliation(db, SnapshotClient(invalid_snapshot))

    run = db.scalar(select(ReconciliationRun).order_by(ReconciliationRun.started_at.desc()))
    assert run is not None
    assert run.state == "FAILED"
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    assert gate is not None
    assert gate.status == "DEGRADED"
    assert db.scalar(select(TradingOrder).where(TradingOrder.broker_order_id == "invalid-order")) is None


def _internal_order(
    db: Session,
    status: str,
    key: str,
    *,
    quantity: int = 1,
    broker_order_id: str | None = None,
) -> TradingOrder:
    intent = OrderIntent(
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        symbol="005930",
        market="KRX",
        side="BUY",
        action="USER_APPROVED",
        requested_quantity=quantity,
        correlation_id=f"corr-{key}",
    )
    db.add(intent)
    db.flush()
    order = TradingOrder(
        intent_id=intent.id,
        order_group_id=intent.order_group_id,
        account_alias=ACCOUNT_ALIAS,
        environment="MOCK",
        symbol="005930",
        market="KRX",
        side="BUY",
        order_type="LIMIT",
        limit_price=Decimal(70000),
        requested_quantity=quantity,
        remaining_quantity=quantity,
        status=status,
        idempotency_key=key,
        request_hash="b" * 64,
        broker_order_id=broker_order_id,
        trading_date=date(2026, 8, 4),
        correlation_id=f"corr-{key}",
    )
    db.add(order)
    db.commit()
    return order


@pytest.mark.parametrize("status", ["CREATED", "VALIDATING"])
def test_unsent_order_is_not_expected_in_broker_snapshot(
    db: Session, status: str
) -> None:
    _internal_order(db, status, f"{status.lower()}-not-visible")

    result = run_kiwoom_reconciliation(
        db,
        SnapshotClient(empty_snapshot()),
        trigger="WORKER_STARTUP",
        clean_gate_reason="WORKER_VALIDATION_PENDING",
    )

    assert result.state == "SUCCEEDED"
    assert result.mismatch_count == 0


@pytest.mark.parametrize("status", ["SUBMITTING", "UNKNOWN"])
def test_uncertain_sent_order_halts_without_automatic_resend(
    db: Session, status: str
) -> None:
    _internal_order(db, status, f"uncertain-{status.lower()}")

    result = run_kiwoom_reconciliation(
        db,
        SnapshotClient(empty_snapshot()),
        trigger="ORDER_OUTCOME_UNKNOWN",
    )

    assert result.gate_status == "HALTED"
    assert result.critical_mismatch_count == 1
    mismatch = db.scalar(
        select(ReconciliationMismatch).where(
            ReconciliationMismatch.run_id == result.run_id
        )
    )
    assert mismatch is not None
    assert mismatch.code == "INTERNAL_ORDER_MISSING_BROKER"


def test_exact_broker_fill_closes_order_and_is_idempotent(db: Session) -> None:
    order = _internal_order(
        db,
        "ACKNOWLEDGED",
        "exact-fill",
        quantity=2,
        broker_order_id="broker-exact",
    )
    snapshot = BrokerAccountSnapshot(
        open_orders=(),
        fills=(
            BrokerFillSummary(
                broker_order_id="broker-exact",
                symbol="005930",
                side="BUY",
                quantity=2,
                price=Decimal(70100),
                fee=Decimal(10),
                tax=Decimal(0),
                order_time="101530",
            ),
        ),
        positions=(BrokerPosition("005930", 2, 2, Decimal(70100)),),
        observed_at=NOW,
    )

    first = run_kiwoom_reconciliation(db, SnapshotClient(snapshot))
    second = run_kiwoom_reconciliation(db, SnapshotClient(snapshot))

    assert first.state == second.state == "SUCCEEDED"
    db.refresh(order)
    assert order.status == "FILLED"
    assert order.filled_quantity == 2
    assert order.remaining_quantity == 0
    assert len(db.scalars(select(Fill).where(Fill.order_id == order.id)).all()) == 1
    assert len(db.scalars(select(OrderEvent).where(OrderEvent.order_id == order.id)).all()) == 1
    position = db.scalar(select(Position).where(Position.symbol == "005930"))
    assert position is not None
    assert len(
        db.scalars(select(PositionEvent).where(PositionEvent.position_id == position.id)).all()
    ) == 1


def test_partial_fill_without_open_order_remains_halted(db: Session) -> None:
    order = _internal_order(
        db,
        "ACKNOWLEDGED",
        "partial-fill",
        quantity=3,
        broker_order_id="broker-partial",
    )
    snapshot = BrokerAccountSnapshot(
        open_orders=(),
        fills=(
            BrokerFillSummary(
                "broker-partial",
                "005930",
                "BUY",
                1,
                Decimal(70000),
                Decimal(0),
                Decimal(0),
                "101500",
            ),
        ),
        positions=(BrokerPosition("005930", 1, 1, Decimal(70000)),),
        observed_at=NOW,
    )

    result = run_kiwoom_reconciliation(db, SnapshotClient(snapshot))

    assert result.gate_status == "HALTED"
    assert result.critical_mismatch_count == 1
    db.refresh(order)
    assert order.status == "RECONCILING"
    assert order.filled_quantity == 1
    assert order.remaining_quantity == 2
    mismatch = db.scalar(
        select(ReconciliationMismatch).where(ReconciliationMismatch.run_id == result.run_id)
    )
    assert mismatch is not None
    assert mismatch.code == "INTERNAL_ORDER_MISSING_BROKER"


def test_broker_missing_position_is_closed_without_changing_origin(db: Session) -> None:
    position = Position(
        account_alias=ACCOUNT_ALIAS,
        symbol="005930",
        quantity=4,
        average_price=Decimal(69000),
        state="OPEN",
        origin="CRESTA_MANAGED",
    )
    db.add(position)
    db.commit()

    result = run_kiwoom_reconciliation(db, SnapshotClient(empty_snapshot()))

    assert result.state == "SUCCEEDED"
    db.refresh(position)
    assert position.state == "CLOSED"
    assert position.quantity == 0
    assert position.average_price == Decimal(0)
    assert position.origin == "CRESTA_MANAGED"
    event = db.scalar(select(PositionEvent).where(PositionEvent.position_id == position.id))
    assert event is not None
    assert event.cause_id == result.run_id


def test_overfill_halts_without_writing_invalid_projection(db: Session) -> None:
    order = _internal_order(
        db,
        "ACKNOWLEDGED",
        "overfill",
        broker_order_id="broker-overfill",
    )
    snapshot = BrokerAccountSnapshot(
        open_orders=(),
        fills=(
            BrokerFillSummary(
                "broker-overfill",
                "005930",
                "BUY",
                2,
                Decimal(70000),
                Decimal(0),
                Decimal(0),
                "101500",
            ),
        ),
        positions=(),
        observed_at=NOW,
    )

    result = run_kiwoom_reconciliation(db, SnapshotClient(snapshot))

    assert result.gate_status == "HALTED"
    assert result.critical_mismatch_count == 2
    db.refresh(order)
    assert order.filled_quantity == 0
    assert order.remaining_quantity == 1
    assert db.scalar(select(Fill).where(Fill.order_id == order.id)) is None
    codes = set(
        db.scalars(
            select(ReconciliationMismatch.code).where(
                ReconciliationMismatch.run_id == result.run_id
            )
        ).all()
    )
    assert codes == {"INTERNAL_ORDER_MISSING_BROKER", "FILL_QUANTITY_MISMATCH"}


def test_resolved_mismatch_is_marked_after_broker_fact_becomes_clear(db: Session) -> None:
    order = _internal_order(
        db,
        "UNKNOWN",
        "later-resolved",
        broker_order_id="broker-later",
    )
    first = run_kiwoom_reconciliation(db, SnapshotClient(empty_snapshot()))
    first_mismatch = db.scalar(
        select(ReconciliationMismatch).where(ReconciliationMismatch.run_id == first.run_id)
    )
    assert first_mismatch is not None
    snapshot = BrokerAccountSnapshot(
        open_orders=(
            BrokerOpenOrder(
                "broker-later",
                "005930",
                "BUY",
                1,
                0,
                1,
                Decimal(70000),
                "101500",
            ),
        ),
        fills=(),
        positions=(),
        observed_at=NOW,
    )

    second = run_kiwoom_reconciliation(db, SnapshotClient(snapshot))

    assert second.state == "SUCCEEDED"
    db.refresh(order)
    db.refresh(first_mismatch)
    assert order.status == "OPEN"
    assert first_mismatch.state == "RESOLVED"
    assert first_mismatch.resolved_at is not None
