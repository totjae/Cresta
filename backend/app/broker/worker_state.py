from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import BrokerLease, BrokerWorkerState, TradingGate
from app.reconciliation import ACCOUNT_ALIAS


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class LeaseIdentity:
    owner_id: str
    fencing_token: int


@dataclass(frozen=True)
class BrokerStatus:
    state: str
    gate_status: str | None
    gate_reason: str | None
    fencing_token: int | None
    lease_valid: bool
    websocket_connected: bool
    subscriptions_ready: bool
    last_heartbeat_at: datetime | None
    last_reconciliation_at: datetime | None
    last_reconciliation_run_id: str | None
    last_error_code: str | None


def get_broker_status(db: Session, *, now: datetime | None = None) -> BrokerStatus:
    observed_at = now or datetime.now(UTC)
    lease = db.get(BrokerLease, ACCOUNT_ALIAS)
    state = db.get(BrokerWorkerState, ACCOUNT_ALIAS)
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    lease_valid = lease is not None and _utc(lease.expires_at) > observed_at
    state_name = "NOT_STARTED" if state is None else state.state
    if state is not None and not lease_valid and state.state != "STOPPED":
        state_name = "STALE"
    return BrokerStatus(
        state=state_name,
        gate_status=gate.status if gate else None,
        gate_reason=gate.reason if gate else None,
        fencing_token=state.fencing_token if state else None,
        lease_valid=lease_valid,
        websocket_connected=bool(state and state.websocket_connected and lease_valid),
        subscriptions_ready=bool(state and state.subscriptions_ready and lease_valid),
        last_heartbeat_at=state.last_heartbeat_at if state else None,
        last_reconciliation_at=state.last_reconciliation_at if state else None,
        last_reconciliation_run_id=state.last_reconciliation_run_id if state else None,
        last_error_code=state.last_error_code if state else None,
    )


def acquire_lease(
    db: Session,
    owner_id: str,
    *,
    lease_seconds: int,
    now: datetime | None = None,
) -> LeaseIdentity | None:
    observed_at = now or datetime.now(UTC)
    lease = db.scalar(
        select(BrokerLease).where(BrokerLease.account_alias == ACCOUNT_ALIAS).with_for_update()
    )
    if lease is not None and _utc(lease.expires_at) > observed_at and lease.owner_id != owner_id:
        db.rollback()
        return None
    if lease is None:
        lease = BrokerLease(
            account_alias=ACCOUNT_ALIAS,
            owner_id=owner_id,
            fencing_token=1,
            expires_at=observed_at + timedelta(seconds=lease_seconds),
            version=1,
            updated_at=observed_at,
        )
        db.add(lease)
    else:
        lease.owner_id = owner_id
        lease.fencing_token += 1
        lease.expires_at = observed_at + timedelta(seconds=lease_seconds)
        lease.version += 1
        lease.updated_at = observed_at
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return LeaseIdentity(owner_id=owner_id, fencing_token=lease.fencing_token)


def renew_lease(
    db: Session,
    identity: LeaseIdentity,
    *,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    observed_at = now or datetime.now(UTC)
    lease = db.scalar(
        select(BrokerLease).where(BrokerLease.account_alias == ACCOUNT_ALIAS).with_for_update()
    )
    if (
        lease is None
        or lease.owner_id != identity.owner_id
        or lease.fencing_token != identity.fencing_token
        or _utc(lease.expires_at) <= observed_at
    ):
        db.rollback()
        return False
    lease.expires_at = observed_at + timedelta(seconds=lease_seconds)
    lease.version += 1
    lease.updated_at = observed_at
    state = db.get(BrokerWorkerState, ACCOUNT_ALIAS)
    if state is not None and state.fencing_token == identity.fencing_token:
        state.last_heartbeat_at = observed_at
    db.commit()
    return True


def lease_is_current(
    db: Session,
    identity: LeaseIdentity,
    *,
    now: datetime | None = None,
) -> bool:
    observed_at = now or datetime.now(UTC)
    lease = db.get(BrokerLease, ACCOUNT_ALIAS)
    return bool(
        lease is not None
        and lease.owner_id == identity.owner_id
        and lease.fencing_token == identity.fencing_token
        and _utc(lease.expires_at) > observed_at
    )


def update_worker_state(
    db: Session,
    identity: LeaseIdentity,
    state_name: str,
    *,
    websocket_connected: bool,
    subscriptions_ready: bool,
    gate_status: str | None = None,
    gate_reason: str | None = None,
    error_code: str | None = None,
    reconciliation_run_id: str | None = None,
    now: datetime | None = None,
) -> bool:
    observed_at = now or datetime.now(UTC)
    lease = db.scalar(
        select(BrokerLease).where(BrokerLease.account_alias == ACCOUNT_ALIAS).with_for_update()
    )
    if (
        lease is None
        or lease.owner_id != identity.owner_id
        or lease.fencing_token != identity.fencing_token
        or _utc(lease.expires_at) <= observed_at
    ):
        db.rollback()
        return False
    state = db.get(BrokerWorkerState, ACCOUNT_ALIAS)
    if state is None:
        state = BrokerWorkerState(
            account_alias=ACCOUNT_ALIAS,
            environment="MOCK",
            state=state_name,
            fencing_token=identity.fencing_token,
            websocket_connected=websocket_connected,
            subscriptions_ready=subscriptions_ready,
            last_heartbeat_at=observed_at,
            started_at=observed_at,
            updated_at=observed_at,
        )
        db.add(state)
    else:
        state.state = state_name
        state.fencing_token = identity.fencing_token
        state.websocket_connected = websocket_connected
        state.subscriptions_ready = subscriptions_ready
        state.last_heartbeat_at = observed_at
        state.updated_at = observed_at
    state.last_error_code = error_code
    if reconciliation_run_id is not None:
        state.last_reconciliation_run_id = reconciliation_run_id
        state.last_reconciliation_at = observed_at
    if gate_status is not None:
        gate = db.get(TradingGate, ACCOUNT_ALIAS)
        if gate is None:
            gate = TradingGate(
                account_alias=ACCOUNT_ALIAS,
                environment="MOCK",
                status=gate_status,
                reason=gate_reason,
            )
            db.add(gate)
        else:
            gate.status = gate_status
            gate.reason = gate_reason
            gate.version += 1
    db.commit()
    return True


def release_lease(
    db: Session,
    identity: LeaseIdentity,
    *,
    now: datetime | None = None,
) -> bool:
    observed_at = now or datetime.now(UTC)
    lease = db.scalar(
        select(BrokerLease).where(BrokerLease.account_alias == ACCOUNT_ALIAS).with_for_update()
    )
    if (
        lease is None
        or lease.owner_id != identity.owner_id
        or lease.fencing_token != identity.fencing_token
    ):
        db.rollback()
        return False
    state = db.get(BrokerWorkerState, ACCOUNT_ALIAS)
    if state is not None and state.fencing_token == identity.fencing_token:
        state.state = "STOPPED"
        state.websocket_connected = False
        state.subscriptions_ready = False
        state.last_heartbeat_at = observed_at
        state.updated_at = observed_at
    lease.expires_at = observed_at
    lease.version += 1
    lease.updated_at = observed_at
    db.commit()
    return True
