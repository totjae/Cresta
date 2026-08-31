from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import worker as worker_module
from app.broker.kiwoom import (
    AccountFundsSnapshotData,
    AccountVerification,
    BrokerAccountSnapshot,
    KiwoomOrderAcknowledgement,
    KiwoomOrderOutcomeUnknownError,
    KiwoomOrderRequest,
)
from app.broker.worker_state import get_broker_status
from app.config import Settings
from app.models import (
    MarketSnapshot,
    MarketStreamState,
    OrderIntent,
    ReconciliationRun,
    TradingOrder,
    User,
    WatchlistItem,
)
from app.reconciliation import ACCOUNT_ALIAS
from app.watch import QuoteEvent


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

    def get_account_funds(self, *, query_type: str) -> AccountFundsSnapshotData:
        return AccountFundsSnapshotData(
            "KIWOOM",
            "KIWOOM_MOCK_PRIMARY",
            "MOCK",
            "kt00001",
            query_type,
            1_000_000,
            900_000,
            800_000,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            datetime.now(UTC),
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

    async def sync_quotes(self, symbols: tuple[str, ...]) -> None:
        assert symbols == ()

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

    async def sync_quotes(self, symbols: tuple[str, ...]) -> None:
        assert symbols == ()

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


class QuoteWebSocket:
    def __init__(self, quote: QuoteEvent) -> None:
        self.quote = quote
        self.worker: worker_module.KiwoomBrokerWorker | None = None
        self.synced: list[tuple[str, ...]] = []
        self.sent = False

    async def open(self, _: str) -> None:
        return None

    async def sync_quotes(self, symbols: tuple[str, ...]) -> None:
        self.synced.append(symbols)

    async def receive(self) -> str | QuoteEvent:
        if not self.sent:
            self.sent = True
            return self.quote
        assert self.worker is not None
        self.worker.stop()
        return "OTHER"

    async def close(self) -> None:
        return None


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


def test_fixed_stop_worker_uses_distinct_persistence_safe_correlation_ids(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(worker_module, "SessionLocal", session_factory)
    captured: list[str] = []

    def capture_correlation_id(*_args, **kwargs) -> int:
        captured.append(kwargs["correlation_id"])
        return 0

    monkeypatch.setattr(worker_module, "run_fixed_stop_triggers", capture_correlation_id)
    monkeypatch.setattr(worker_module, "recover_exit_pending", lambda *_args, **_kwargs: 0)
    worker = worker_module.KiwoomBrokerWorker(
        _configured_settings(tmp_path),
        client=SnapshotClient(),
        websocket=ControlledWebSocket(),
    )
    worker.identity = object()  # type: ignore[assignment]

    evaluated_at = datetime(2026, 8, 31, 6, 1, 22, 948000, tzinfo=UTC)
    asyncio.run(worker._run_stop_triggers(evaluated_at))
    asyncio.run(worker._run_stop_triggers(evaluated_at))

    assert len(captured) == 2
    assert captured[0] != captured[1]
    for correlation_id in captured:
        assert len(correlation_id) == 36
        assert str(UUID(correlation_id)) == correlation_id


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


def test_worker_syncs_watchlist_and_persists_realtime_quote(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    admin: User,
) -> None:
    monkeypatch.setattr(worker_module, "SessionLocal", session_factory)
    with session_factory() as db:
        db.add(WatchlistItem(user_id=admin.id, symbol="005930", market="KRX"))
        db.commit()
    observed_at = datetime.now(UTC)
    quote = QuoteEvent(
        symbol="005930", market="KRX", source="KIWOOM_WS", sequence_or_hash="worker-quote-1",
        event_at=observed_at, received_at=observed_at, last_price=Decimal(70000),
        open_price=Decimal(69000), high_price=Decimal(70500), low_price=Decimal(68800),
        cumulative_volume=12345, trading_status="TRADING",
    )
    socket = QuoteWebSocket(quote)
    worker = worker_module.KiwoomBrokerWorker(
        _configured_settings(tmp_path),
        client=SnapshotClient(),
        websocket=socket,
        owner_id="worker-owner-quote",
    )
    socket.worker = worker

    assert asyncio.run(worker.run()) == 0
    assert socket.synced[0] == ("005930",)
    with session_factory() as db:
        state = db.get(MarketStreamState, ("KRX", "005930"))
        assert state is not None
        snapshot = db.get(MarketSnapshot, state.current_snapshot_id)
        assert snapshot is not None
        assert snapshot.last_price == Decimal(70000)


def _queue_order(session_factory: sessionmaker[Session]) -> str:
    with session_factory() as db:
        intent = OrderIntent(
            account_alias=ACCOUNT_ALIAS,
            environment="MOCK",
            symbol="005930",
            market="KRX",
            side="BUY",
            action="MOCK_CONNECTION_TEST",
            requested_quantity=1,
            source_type="BROKER_DIAGNOSTIC",
            source_id="worker-poll-order",
            authority_key="diagnostic:worker-poll-order",
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
