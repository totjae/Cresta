from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.broker.worker_state import (
    acquire_lease,
    get_broker_status,
    release_lease,
    renew_lease,
    update_worker_state,
)

NOW = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)


def test_lease_excludes_second_owner_and_fences_expired_owner(db: Session) -> None:
    first = acquire_lease(db, "owner-one", lease_seconds=60, now=NOW)
    assert first is not None
    assert acquire_lease(db, "owner-two", lease_seconds=60, now=NOW + timedelta(seconds=10)) is None
    assert renew_lease(db, first, lease_seconds=60, now=NOW + timedelta(seconds=20)) is True

    second = acquire_lease(db, "owner-two", lease_seconds=60, now=NOW + timedelta(seconds=81))
    assert second is not None
    assert second.fencing_token == first.fencing_token + 1
    assert (
        update_worker_state(
            db,
            first,
            "READY",
            websocket_connected=True,
            subscriptions_ready=True,
            gate_status="READY",
            gate_reason="UNSAFE",
            now=NOW + timedelta(seconds=82),
        )
        is False
    )


def test_ready_status_requires_current_lease_and_release_is_owner_scoped(db: Session) -> None:
    identity = acquire_lease(db, "owner", lease_seconds=60, now=NOW)
    assert identity is not None
    assert update_worker_state(
        db,
        identity,
        "READY",
        websocket_connected=True,
        subscriptions_ready=True,
        gate_status="READY",
        gate_reason="WORKER_HEALTHY",
        reconciliation_run_id="run-safe",
        now=NOW,
    )
    status = get_broker_status(db, now=NOW + timedelta(seconds=1))
    assert status.state == "READY"
    assert status.lease_valid is True
    assert status.websocket_connected is True
    assert status.subscriptions_ready is True
    assert status.last_reconciliation_run_id == "run-safe"

    assert release_lease(db, identity, now=NOW + timedelta(seconds=2)) is True
    stopped = get_broker_status(db, now=NOW + timedelta(seconds=2))
    assert stopped.state == "STOPPED"
    assert stopped.lease_valid is False
    assert stopped.websocket_connected is False
    successor = acquire_lease(db, "successor", lease_seconds=60, now=NOW + timedelta(seconds=3))
    assert successor is not None
    assert successor.fencing_token == identity.fencing_token + 1
