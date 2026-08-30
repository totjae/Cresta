from __future__ import annotations

import asyncio
import contextlib
import logging

from app.config import Settings
from app.db import SessionLocal
from app.sourced_execution import ReconciliationResult, reconcile_sourced_entry_executions

logger = logging.getLogger("cresta.sourced_handoff")


class SourcedHandoffWorker:
    """Opt-in runtime bridge from committed sourced Decisions to execution.

    The persisted reconciliation helper owns eligibility and execution semantics.
    This worker only owns polling and lifecycle isolation; it never calls a broker.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stop_event = asyncio.Event()

    def stop(self) -> None:
        self.stop_event.set()

    @staticmethod
    def _correlation_id(decision) -> str:
        # Persisted correlation columns are UUID-width; the immutable Decision ID
        # is already a stable, non-secret handoff correlation identity.
        return decision.id

    def _run_sweep(self) -> ReconciliationResult:
        with SessionLocal() as db:
            return reconcile_sourced_entry_executions(
                db,
                settings=self.settings,
                correlation_id_factory=self._correlation_id,
            )

    async def run(self) -> int:
        if not self.settings.v7_sourced_handoff_enabled:
            logger.info("Sourced handoff worker inactive enabled=false")
            await self.stop_event.wait()
            return 0

        logger.info("Sourced handoff worker started")
        try:
            while not self.stop_event.is_set():
                try:
                    result = await asyncio.to_thread(self._run_sweep)
                except Exception:
                    logger.exception("Sourced handoff sweep failed unexpectedly")
                else:
                    log = logger.warning if result.deferred or result.failed else logger.info
                    log(
                        "Sourced handoff sweep attempted candidates=%d processed=%d "
                        "retryable_failures=%d terminal_failures=%d",
                        result.scanned,
                        result.completed,
                        result.deferred,
                        result.failed,
                    )
                if self.stop_event.is_set():
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self.stop_event.wait(),
                        timeout=self.settings.agent_worker_poll_seconds,
                    )
            return 0
        finally:
            logger.info("Sourced handoff worker stopped")
