from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.sourced_execution import ReconciliationResult
from app.sourced_handoff import SourcedHandoffWorker


def test_handoff_setting_defaults_off_and_malformed_env_fails(monkeypatch) -> None:
    assert Settings(_env_file=None).v7_sourced_handoff_enabled is False
    monkeypatch.setenv("CRESTA_V7_SOURCED_HANDOFF_ENABLED", "not-a-boolean")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_disabled_worker_waits_for_shutdown_without_sweeping(monkeypatch) -> None:
    worker = SourcedHandoffWorker(Settings(v7_sourced_handoff_enabled=False))
    calls = 0

    def forbidden_sweep() -> ReconciliationResult:
        nonlocal calls
        calls += 1
        raise AssertionError("disabled worker must not sweep")

    monkeypatch.setattr(worker, "_run_sweep", forbidden_sweep)

    async def scenario() -> int:
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0)
        assert not task.done()
        worker.stop()
        return await asyncio.wait_for(task, timeout=1)

    assert asyncio.run(scenario()) == 0
    assert calls == 0


def test_enabled_worker_isolates_failure_retries_and_stops_cleanly(monkeypatch) -> None:
    worker = SourcedHandoffWorker(
        Settings(v7_sourced_handoff_enabled=True, agent_worker_poll_seconds=1)
    )
    outcomes: list[str] = []

    def sweep() -> ReconciliationResult:
        outcomes.append("attempt")
        if len(outcomes) == 1:
            raise RuntimeError("temporary database outage")
        return ReconciliationResult(scanned=1, completed=1, deferred=0, failed=0)

    monkeypatch.setattr(worker, "_run_sweep", sweep)

    async def scenario() -> int:
        task = asyncio.create_task(worker.run())
        for _ in range(250):
            if len(outcomes) >= 2:
                break
            await asyncio.sleep(0.01)
        worker.stop()
        return await asyncio.wait_for(task, timeout=1)

    assert asyncio.run(scenario()) == 0
    assert outcomes == ["attempt", "attempt"]
