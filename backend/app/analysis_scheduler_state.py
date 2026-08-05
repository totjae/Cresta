from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AnalysisSchedulerLease, AnalysisSchedulerState

SCHEDULER_NAME = "PERIODIC_AI"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class SchedulerIdentity:
    owner_id: str
    fencing_token: int


@dataclass(frozen=True)
class SchedulerStatus:
    state: str
    lease_valid: bool
    last_heartbeat_at: datetime | None
    last_tick_at: datetime | None
    last_completed_at: datetime | None
    next_due_at: datetime | None
    processed_count: int
    decision_count: int
    skipped_count: int
    failed_count: int
    last_error_code: str | None


def get_scheduler_status(db: Session, *, now: datetime | None = None) -> SchedulerStatus:
    observed_at = now or datetime.now(UTC)
    lease = db.get(AnalysisSchedulerLease, SCHEDULER_NAME)
    state = db.get(AnalysisSchedulerState, SCHEDULER_NAME)
    lease_valid = bool(lease and _utc(lease.expires_at) > observed_at)
    state_name = "NOT_STARTED" if state is None else state.state
    if state is not None and not lease_valid and state.state != "STOPPED":
        state_name = "STALE"
    return SchedulerStatus(
        state=state_name,
        lease_valid=lease_valid,
        last_heartbeat_at=state.last_heartbeat_at if state else None,
        last_tick_at=state.last_tick_at if state else None,
        last_completed_at=state.last_completed_at if state else None,
        next_due_at=state.next_due_at if state else None,
        processed_count=state.processed_count if state else 0,
        decision_count=state.decision_count if state else 0,
        skipped_count=state.skipped_count if state else 0,
        failed_count=state.failed_count if state else 0,
        last_error_code=state.last_error_code if state else None,
    )


def acquire_scheduler_lease(
    db: Session, owner_id: str, *, lease_seconds: int, now: datetime | None = None
) -> SchedulerIdentity | None:
    observed_at = now or datetime.now(UTC)
    lease = db.scalar(
        select(AnalysisSchedulerLease)
        .where(AnalysisSchedulerLease.scheduler_name == SCHEDULER_NAME)
        .with_for_update()
    )
    if lease is not None and _utc(lease.expires_at) > observed_at and lease.owner_id != owner_id:
        db.rollback()
        return None
    if lease is None:
        lease = AnalysisSchedulerLease(
            scheduler_name=SCHEDULER_NAME,
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
    return SchedulerIdentity(owner_id=owner_id, fencing_token=lease.fencing_token)


def renew_scheduler_lease(
    db: Session,
    identity: SchedulerIdentity,
    *,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    observed_at = now or datetime.now(UTC)
    lease = db.scalar(
        select(AnalysisSchedulerLease)
        .where(AnalysisSchedulerLease.scheduler_name == SCHEDULER_NAME)
        .with_for_update()
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
    state = db.get(AnalysisSchedulerState, SCHEDULER_NAME)
    if state is not None and state.fencing_token == identity.fencing_token:
        state.last_heartbeat_at = observed_at
        state.updated_at = observed_at
    db.commit()
    return True


def update_scheduler_state(
    db: Session,
    identity: SchedulerIdentity,
    state_name: str,
    *,
    now: datetime | None = None,
    slot_key: str | None = None,
    next_due_at: datetime | None = None,
    completed: bool = False,
    processed_count: int = 0,
    decision_count: int = 0,
    skipped_count: int = 0,
    failed_count: int = 0,
    error_code: str | None = None,
) -> bool:
    observed_at = now or datetime.now(UTC)
    lease = db.scalar(
        select(AnalysisSchedulerLease)
        .where(AnalysisSchedulerLease.scheduler_name == SCHEDULER_NAME)
        .with_for_update()
    )
    if (
        lease is None
        or lease.owner_id != identity.owner_id
        or lease.fencing_token != identity.fencing_token
        or _utc(lease.expires_at) <= observed_at
    ):
        db.rollback()
        return False
    state = db.get(AnalysisSchedulerState, SCHEDULER_NAME)
    if state is None:
        state = AnalysisSchedulerState(
            scheduler_name=SCHEDULER_NAME,
            state=state_name,
            fencing_token=identity.fencing_token,
            last_heartbeat_at=observed_at,
            started_at=observed_at,
            updated_at=observed_at,
        )
        db.add(state)
    state.state = state_name
    state.fencing_token = identity.fencing_token
    state.last_heartbeat_at = observed_at
    state.next_due_at = next_due_at
    state.last_error_code = error_code
    state.updated_at = observed_at
    if slot_key is not None:
        state.last_slot_key = slot_key
        state.last_tick_at = observed_at
    if completed:
        state.last_completed_at = observed_at
        state.processed_count = processed_count
        state.decision_count = decision_count
        state.skipped_count = skipped_count
        state.failed_count = failed_count
    db.commit()
    return True


def release_scheduler_lease(
    db: Session, identity: SchedulerIdentity, *, now: datetime | None = None
) -> bool:
    observed_at = now or datetime.now(UTC)
    lease = db.scalar(
        select(AnalysisSchedulerLease)
        .where(AnalysisSchedulerLease.scheduler_name == SCHEDULER_NAME)
        .with_for_update()
    )
    if (
        lease is None
        or lease.owner_id != identity.owner_id
        or lease.fencing_token != identity.fencing_token
    ):
        db.rollback()
        return False
    state = db.get(AnalysisSchedulerState, SCHEDULER_NAME)
    if state is not None and state.fencing_token == identity.fencing_token:
        state.state = "STOPPED"
        state.last_heartbeat_at = observed_at
        state.updated_at = observed_at
    lease.expires_at = observed_at
    lease.version += 1
    lease.updated_at = observed_at
    db.commit()
    return True
