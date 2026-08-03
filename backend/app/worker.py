from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from datetime import UTC, datetime, timedelta

from websockets.exceptions import WebSocketException

from app.broker.kiwoom import KiwoomAdapterError, KiwoomMockClient
from app.broker.kiwoom_ws import KiwoomAccountWebSocket, KiwoomWebSocketError
from app.broker.order_sender import KiwoomSendResult, send_next_created_order
from app.broker.worker_state import (
    LeaseIdentity,
    acquire_lease,
    release_lease,
    renew_lease,
    update_worker_state,
)
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.ids import uuid7
from app.reconciliation import ReconciliationResult, run_kiwoom_reconciliation

logger = logging.getLogger("cresta.kiwoom_worker")
RECONNECT_BACKOFF = (1, 2, 5, 10, 30)


class WorkerReconciliationError(RuntimeError):
    def __init__(self, source_code: str) -> None:
        super().__init__("WORKER_RECONCILIATION_FAILED")
        self.code = "WORKER_RECONCILIATION_FAILED"
        self.source_code = source_code


class KiwoomBrokerWorker:
    def __init__(
        self,
        settings: Settings,
        *,
        client: KiwoomMockClient | None = None,
        websocket: KiwoomAccountWebSocket | None = None,
        owner_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or KiwoomMockClient(settings)
        self.websocket = websocket or KiwoomAccountWebSocket(settings)
        self.owner_id = owner_id or uuid7()
        self.stop_event = asyncio.Event()
        self.lease_lost = asyncio.Event()
        self.identity: LeaseIdentity | None = None

    def stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> int:
        if self.settings.kiwoom_configuration_status() != "CONFIGURED":
            logger.error("Kiwoom worker configuration unavailable code=KIWOOM_NOT_CONFIGURED")
            return 2
        while not self.stop_event.is_set() and self.identity is None:
            with SessionLocal() as db:
                self.identity = acquire_lease(
                    db,
                    self.owner_id,
                    lease_seconds=self.settings.kiwoom_worker_lease_seconds,
                )
            if self.identity is None:
                await self._wait_or_stop(self.settings.kiwoom_worker_heartbeat_seconds)
        if self.identity is None:
            return 0

        self._update_state(
            "STARTING",
            websocket_connected=False,
            subscriptions_ready=False,
            gate_status="RECONCILING",
            gate_reason="WORKER_STARTING",
        )
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            return await self._connection_loop()
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            await self.websocket.close()
            if self.identity is not None:
                self._update_state(
                    "STOPPED",
                    websocket_connected=False,
                    subscriptions_ready=False,
                    gate_status="DEGRADED",
                    gate_reason="WORKER_STOPPED",
                )
                with SessionLocal() as db:
                    release_lease(db, self.identity)

    async def _connection_loop(self) -> int:
        failures = 0
        connected_once = False
        while not self.stop_event.is_set():
            if self.lease_lost.is_set():
                await self.websocket.close()
                logger.error("Kiwoom worker lease lost code=WORKER_LEASE_LOST")
                return 3
            try:
                self._require_state(
                    "AUTHENTICATING",
                    websocket_connected=False,
                    subscriptions_ready=False,
                    gate_status="RECONCILING",
                    gate_reason="WORKER_AUTHENTICATING",
                )
                access_token = await asyncio.to_thread(self.client.get_access_token)
                await asyncio.to_thread(self.client.verify_account)
                self._require_state(
                    "CONNECTING",
                    websocket_connected=False,
                    subscriptions_ready=False,
                    gate_status="RECONCILING",
                    gate_reason="WEBSOCKET_CONNECTING",
                )
                await self.websocket.open(access_token)
                self._require_state(
                    "SUBSCRIBING",
                    websocket_connected=True,
                    subscriptions_ready=True,
                    gate_status="RECONCILING",
                    gate_reason="WORKER_RECONCILING",
                )
                trigger = "WEBSOCKET_RECONNECTED" if connected_once else "WORKER_STARTUP"
                connected_once = True
                result = await self._reconcile(trigger)
                self._apply_reconciliation_result(result)
                failures = 0
                token_used = access_token
                await self._receive_loop(token_used)
            except asyncio.CancelledError:
                raise
            except (
                KiwoomAdapterError,
                KiwoomWebSocketError,
                WorkerReconciliationError,
                WebSocketException,
                OSError,
                TimeoutError,
                RuntimeError,
            ) as exc:
                await self.websocket.close()
                if self.lease_lost.is_set() or str(exc) == "WORKER_LEASE_LOST":
                    logger.error("Kiwoom worker lease lost code=WORKER_LEASE_LOST")
                    return 3
                failures += 1
                code = _safe_error_code(exc)
                reconciliation_failed = isinstance(exc, WorkerReconciliationError)
                degraded = reconciliation_failed or failures >= len(RECONNECT_BACKOFF)
                self._update_state(
                    "DEGRADED" if degraded else "RECONCILING",
                    websocket_connected=False,
                    subscriptions_ready=False,
                    gate_status="DEGRADED" if degraded else "RECONCILING",
                    gate_reason=(
                        "RECONCILIATION_FAILED"
                        if reconciliation_failed
                        else "WEBSOCKET_UNAVAILABLE"
                        if degraded
                        else "WEBSOCKET_RECONNECTING"
                    ),
                    error_code=code,
                )
                logger.warning(
                    "Kiwoom worker reconnect scheduled code=%s attempt=%d", code, failures
                )
                await self._wait_or_stop(
                    RECONNECT_BACKOFF[min(failures - 1, len(RECONNECT_BACKOFF) - 1)]
                )
        return 0

    async def _receive_loop(self, token_used: str) -> None:
        now = datetime.now(UTC)
        next_periodic = now + timedelta(seconds=self.settings.kiwoom_reconcile_interval_seconds)
        next_token_check = now + timedelta(seconds=self.settings.kiwoom_worker_heartbeat_seconds)
        event_due: datetime | None = None
        order_dispatch_enabled = True
        while not self.stop_event.is_set() and not self.lease_lost.is_set():
            observed_at = datetime.now(UTC)
            if order_dispatch_enabled:
                dispatch_result = await self._dispatch_next_order()
                if dispatch_result is not None and dispatch_result.status == "UNKNOWN":
                    result = await self._reconcile("ORDER_OUTCOME_UNKNOWN")
                    self._apply_reconciliation_result(result)
                    order_dispatch_enabled = result.critical_mismatch_count == 0
                    next_periodic = datetime.now(UTC) + timedelta(
                        seconds=self.settings.kiwoom_reconcile_interval_seconds
                    )
                    continue
            if observed_at >= next_token_check:
                current_token = await asyncio.to_thread(self.client.get_access_token)
                if current_token != token_used:
                    self._update_state(
                        "RECONCILING",
                        websocket_connected=True,
                        subscriptions_ready=True,
                        gate_status="RECONCILING",
                        gate_reason="TOKEN_ROTATION_RECONNECT",
                    )
                    await self.websocket.close()
                    return
                next_token_check = observed_at + timedelta(
                    seconds=self.settings.kiwoom_worker_heartbeat_seconds
                )
            trigger: str | None = None
            if event_due is not None and observed_at >= event_due:
                trigger = "BROKER_EVENT"
                event_due = None
            elif observed_at >= next_periodic:
                trigger = "PERIODIC"
            if trigger is not None:
                result = await self._reconcile(trigger)
                self._apply_reconciliation_result(result)
                order_dispatch_enabled = result.critical_mismatch_count == 0
                next_periodic = datetime.now(UTC) + timedelta(
                    seconds=self.settings.kiwoom_reconcile_interval_seconds
                )
            try:
                event = await asyncio.wait_for(self.websocket.receive(), timeout=1.0)
            except TimeoutError:
                continue
            if event == "ACCOUNT_EVENT" and event_due is None:
                self._require_state(
                    "RECONCILING",
                    websocket_connected=True,
                    subscriptions_ready=True,
                    gate_status="RECONCILING",
                    gate_reason="BROKER_EVENT_PENDING",
                )
                order_dispatch_enabled = False
                event_due = datetime.now(UTC) + timedelta(
                    seconds=self.settings.kiwoom_event_debounce_seconds
                )
        if self.lease_lost.is_set():
            raise RuntimeError("WORKER_LEASE_LOST")

    async def _dispatch_next_order(self) -> KiwoomSendResult | None:
        if self.identity is None:
            raise RuntimeError("WORKER_LEASE_LOST")

        def execute() -> KiwoomSendResult | None:
            with SessionLocal() as db:
                return send_next_created_order(db, self.client, self.identity)

        return await asyncio.to_thread(execute)

    async def _reconcile(self, trigger: str) -> ReconciliationResult:
        self._require_state(
            "RECONCILING",
            websocket_connected=True,
            subscriptions_ready=True,
            gate_status="RECONCILING",
            gate_reason="ACCOUNT_RECONCILING",
        )

        def execute() -> ReconciliationResult:
            with SessionLocal() as db:
                return run_kiwoom_reconciliation(
                    db,
                    self.client,
                    trigger=trigger,
                    clean_gate_reason="WORKER_VALIDATION_PENDING",
                )

        try:
            return await asyncio.to_thread(execute)
        except KiwoomAdapterError as exc:
            raise WorkerReconciliationError(exc.code) from exc

    def _apply_reconciliation_result(self, result: ReconciliationResult) -> None:
        if result.critical_mismatch_count:
            self._require_state(
                "DEGRADED",
                websocket_connected=True,
                subscriptions_ready=True,
                reconciliation_run_id=result.run_id,
            )
            return
        self._require_state(
            "READY",
            websocket_connected=True,
            subscriptions_ready=True,
            gate_status="READY",
            gate_reason="WORKER_HEALTHY",
            reconciliation_run_id=result.run_id,
        )

    async def _heartbeat_loop(self) -> None:
        assert self.identity is not None
        while not self.stop_event.is_set():
            await self._wait_or_stop(self.settings.kiwoom_worker_heartbeat_seconds)
            if self.stop_event.is_set():
                return
            with SessionLocal() as db:
                renewed = renew_lease(
                    db,
                    self.identity,
                    lease_seconds=self.settings.kiwoom_worker_lease_seconds,
                )
            if not renewed:
                self.lease_lost.set()
                return

    def _require_state(self, state_name: str, **kwargs: object) -> None:
        if not self._update_state(state_name, **kwargs):
            self.lease_lost.set()
            raise RuntimeError("WORKER_LEASE_LOST")

    def _update_state(self, state_name: str, **kwargs: object) -> bool:
        if self.identity is None:
            return False
        with SessionLocal() as db:
            return update_worker_state(db, self.identity, state_name, **kwargs)

    async def _wait_or_stop(self, seconds: int) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, (KiwoomAdapterError, KiwoomWebSocketError, WorkerReconciliationError)):
        return exc.code
    if str(exc) == "WORKER_LEASE_LOST":
        return "WORKER_LEASE_LOST"
    return "KIWOOM_WORKER_ERROR"


async def _run_worker() -> int:
    worker = KiwoomBrokerWorker(get_settings())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.stop)
    return await worker.run()


def main() -> None:
    parser = argparse.ArgumentParser(prog="cresta-worker")
    parser.add_argument("worker", choices=["kiwoom"])
    parser.parse_args()
    raise SystemExit(asyncio.run(_run_worker()))


if __name__ == "__main__":
    main()
