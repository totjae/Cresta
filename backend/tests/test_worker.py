from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import worker as worker_module
from app.broker.kiwoom import AccountVerification, BrokerAccountSnapshot
from app.broker.worker_state import get_broker_status
from app.config import Settings
from app.models import ReconciliationRun


class SnapshotClient:
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
