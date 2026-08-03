from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import worker as worker_module
from app.broker.kiwoom import (
    AccountVerification,
    BrokerAccountSnapshot,
    KiwoomOrderAcknowledgement,
    KiwoomOrderOutcomeUnknownError,
    KiwoomOrderRequest,
)
from app.broker.worker_state import get_broker_status
from app.config import Settings
from app.models import OrderIntent, ReconciliationRun, TradingOrder
from app.reconciliation import ACCOUNT_ALIAS


class SnapshotClient:
    def __init__(self, order_outcome: object | None = None) -> None:
        self.order_outcome = order_outcome or KiwoomOrderAcknowledgement("1234567", "KRX")
        self.order_requests: list[KiwoomOrderRequest] = []

    def get_access_token(self) -> str:
        return "memory-only-token"

    def verify_account(self) -> AccountVerification:
        return AccountVerification("ACCOUNT_VERIFIED", "********11")

    def get_account_snapshot(self) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            open_orders=(),
            fills=(),
            positions=(),
            observed_at=datetime.now(UTC),
        )

    def place_order(self, request: KiwoomOrderRequest) -> KiwoomOrderAcknowledgement:
        self.order_requests.append(request)
        if isinstance(self.order_outcome, Exception):
            raise self.order_outcome
        assert isinstance(self.order_outcome, KiwoomOrderAcknowledgement)
        return self.order_outcome


class ControlledWebSocket:
    def __init__(self) -> None:
        self.opened_with: str | None = None
        self.closed = False
        self.on_receive = None

    async def open(self, token: str) -> None:
        self.opened_with = token

    async def receive(self) -> str:
        assert self.on_receive is not None
        self.on_receive()
        return "OTHER"

    async def close(self) -> None:
        self.closed = True


class AccountEventWebSocket:
    def __init__(self) -> None:
        self.opened_with: str | None = None
        self.closed = False
        self.on_first_event = None
        self.on_pending = None
        self._event_sent = False
        self._pending_inspected = False

    async def open(self, token: str) -> None:
        self.opened_with = token

    async def receive(self) -> str:
        if not self._event_sent:
            self._event_sent = True
            assert self.on_first_event is not None
            self.on_first_event()
            return "ACCOUNT_EVENT"
        if not self._pending_inspected:
            self._pending_inspected = True
            assert self.on_pending is not None
            self.on_pending()
        await asyncio.sleep(0.05)
        return "OTHER"

    async def close(self) -> None:
        self.closed = True


def _configured_settings(tmp_path: Path) -> Settings:
    files = []
    for name, value in (("key", "k"), ("secret", "s"), ("account", "1234567811")):
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        files.append(str(path))
    return Settings(
        database_url="sqlite://",
        cookie_secure=False,
        allowed_origins="https://testserver",
        kiwoom_enabled=True,
        kiwoom_app_key_file=files[0],
        kiwoom_app_secret_file=files[1],
        kiwoom_account_id_file=files[2],
    )


def test_worker_reaches_ready_only_after_websocket_and_clean_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(worker_module, "SessionLocal", session_factory)
    socket = ControlledWebSocket()
    worker = worker_module.KiwoomBrokerWorker(
        _configured_settings(tmp_path),
        client=SnapshotClient(),
        websocket=socket,
        owner_id="worker-owner",
    )
    observed: dict[str, object] = {}

    def inspect_ready_then_stop() -> None:
        with session_factory() as db:
            status = get_broker_status(db)
            run = db.scalar(select(ReconciliationRun).order_by(ReconciliationRun.started_at.desc()))
            assert run is not None
            observed.update(
                state=status.state,
                gate_status=status.gate_status,
                lease_valid=status.lease_valid,
                websocket_connected=status.websocket_connected,
                subscriptions_ready=status.subscriptions_ready,
                reconciliation_run_id=status.last_reconciliation_run_id,
                reconciliation_trigger=run.trigger,
            )
        worker.stop()

    socket.on_receive = inspect_ready_then_stop
    assert asyncio.run(worker.run()) == 0

    assert socket.opened_with == "memory-only-token"
    assert observed == {
        "state": "READY",
        "gate_status": "READY",
        "lease_valid": True,
        "websocket_connected": True,
        "subscriptions_ready": True,
        "reconciliation_run_id": observed["reconciliation_run_id"],
        "reconciliation_trigger": "WORKER_STARTUP",
    }
    assert observed["reconciliation_run_id"] is not None
    assert socket.closed is True


def _queue_order(session_factory: sessionmaker[Session]) -> str:
    with session_factory() as db:
        intent = OrderIntent(
            account_alias=ACCOUNT_ALIAS,
            environment="MOCK",
            symbol="005930",
            market="KRX",
            side="BUY",
            action="USER_APPROVED",
            requested_quantity=1,
            correlation_id="corr-worker-order",
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
            requested_quantity=1,
            remaining_quantity=1,
            idempotency_key="worker-poll-order",
            request_hash="c" * 64,
            trading_date=date(2026, 8, 4),
            correlation_id="corr-worker-order",
        )
        db.add(order)
        db.commit()
        return order.id


def test_worker_unknown_order_immediately_reconciles_and_stops_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(worker_module, "SessionLocal", session_factory)
    order_id = _queue_order(session_factory)
    client = SnapshotClient(
        KiwoomOrderOutcomeUnknownError("KIWOOM_ORDER_OUTCOME_UNKNOWN", "unknown")
    )
    socket = ControlledWebSocket()
    worker = worker_module.KiwoomBrokerWorker(
        _configured_settings(tmp_path),
        client=client,
        websocket=socket,
        owner_id="worker-owner-unknown",
    )
    observed: dict[str, object] = {}

    def inspect_halted_then_stop() -> None:
        with session_factory() as db:
            status = get_broker_status(db)
            order = db.get(TradingOrder, order_id)
            latest_run = db.scalar(
                select(ReconciliationRun).order_by(ReconciliationRun.started_at.desc())
            )
            assert order is not None
            assert latest_run is not None
            observed.update(
                worker_state=status.state,
                gate_status=status.gate_status,
                order_status=order.status,
                reconciliation_trigger=latest_run.trigger,
            )
        worker.stop()

    socket.on_receive = inspect_halted_then_stop
    assert asyncio.run(worker.run()) == 0

    assert observed == {
        "worker_state": "DEGRADED",
        "gate_status": "HALTED",
        "order_status": "UNKNOWN",
        "reconciliation_trigger": "ORDER_OUTCOME_UNKNOWN",
    }
    assert len(client.order_requests) == 1


def test_account_event_closes_gate_until_broker_reconciliation_finishes(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(worker_module, "SessionLocal", session_factory)
    client = SnapshotClient()
    socket = AccountEventWebSocket()
    worker = worker_module.KiwoomBrokerWorker(
        _configured_settings(tmp_path),
        client=client,
        websocket=socket,
        owner_id="worker-owner-account-event",
    )
    observed: dict[str, object] = {}

    socket.on_first_event = lambda: _queue_order(session_factory)

    def inspect_pending() -> None:
        with session_factory() as db:
            status = get_broker_status(db)
            observed.update(
                pending_state=status.state,
                pending_gate=status.gate_status,
                pending_reason=status.gate_reason,
                requests_before_reconciliation=len(client.order_requests),
            )

    socket.on_pending = inspect_pending

    original_place_order = client.place_order

    def place_order_after_reconciliation(
        request: KiwoomOrderRequest,
    ) -> KiwoomOrderAcknowledgement:
        with session_factory() as db:
            latest_run = db.scalar(
                select(ReconciliationRun).order_by(ReconciliationRun.started_at.desc())
            )
            status = get_broker_status(db)
            assert latest_run is not None
            observed.update(
                trigger_before_send=latest_run.trigger,
                gate_before_send=status.gate_status,
            )
        result = original_place_order(request)
        worker.stop()
        return result

    monkeypatch.setattr(client, "place_order", place_order_after_reconciliation)

    assert asyncio.run(worker.run()) == 0

    assert observed == {
        "pending_state": "RECONCILING",
        "pending_gate": "RECONCILING",
        "pending_reason": "BROKER_EVENT_PENDING",
        "requests_before_reconciliation": 0,
        "trigger_before_send": "BROKER_EVENT",
        "gate_before_send": "READY",
    }
    assert len(client.order_requests) == 1
    assert socket.closed is True
