from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis_scheduler_state import (
    SCHEDULER_NAME,
    SchedulerIdentity,
    acquire_scheduler_lease,
    release_scheduler_lease,
    renew_scheduler_lease,
    update_scheduler_state,
)
from app.config import Settings
from app.db import SessionLocal
from app.decision_execution import route_trading_decision
from app.ids import uuid7
from app.mock_ai import MODEL_ID, PROMPT_VERSION, create_mock_trading_decision
from app.models import AnalysisSchedulerState, MarketStreamState, User, WatchlistItem

logger = logging.getLogger("cresta.analysis_scheduler")
KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class AnalysisSlot:
    key: str
    start_at: datetime
    next_due_at: datetime


@dataclass(frozen=True)
class TickResult:
    processed_count: int
    decision_count: int
    skipped_count: int
    failed_count: int


def _next_weekday_start(local: datetime) -> datetime:
    candidate = local.replace(hour=8, minute=0, second=0, microsecond=0)
    if local >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def analysis_slot(now: datetime) -> tuple[AnalysisSlot | None, datetime]:
    observed = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    local = observed.astimezone(KST)
    if local.weekday() >= 5 or local.time() < time(8) or local.time() >= time(20):
        next_due = _next_weekday_start(local)
        return None, next_due
    if local.time() < time(11):
        base = local.replace(hour=8, minute=0, second=0, microsecond=0)
        interval_minutes = 5
    else:
        base = local.replace(hour=11, minute=0, second=0, microsecond=0)
        interval_minutes = 10
    elapsed_minutes = int((local - base).total_seconds() // 60)
    slot_local = base + timedelta(
        minutes=(elapsed_minutes // interval_minutes) * interval_minutes
    )
    next_local = slot_local + timedelta(minutes=interval_minutes)
    key = slot_local.strftime("%Y%m%dT%H%M%z")
    return AnalysisSlot(key, slot_local.astimezone(UTC), next_local.astimezone(UTC)), next_local.astimezone(UTC)


def evaluation_request_id(user_id: str, market: str, symbol: str, slot_key: str) -> str:
    raw = f"{user_id}:{market}:{symbol}:{slot_key}:{MODEL_ID}:{PROMPT_VERSION}"
    return "sched-" + hashlib.sha256(raw.encode()).hexdigest()[:58]


def run_analysis_tick(
    db: Session, *, slot: AnalysisSlot, settings: Settings, now: datetime
) -> TickResult:
    targets = list(
        db.execute(
            select(
                WatchlistItem.user_id,
                WatchlistItem.market,
                WatchlistItem.symbol,
            )
            .join(User, User.id == WatchlistItem.user_id)
            .where(User.status == "ACTIVE")
            .order_by(WatchlistItem.user_id, WatchlistItem.market, WatchlistItem.symbol)
        ).all()
    )
    processed = decisions = skipped = failed = 0
    for user_id, market, symbol in targets:
        processed += 1
        try:
            stream = db.get(MarketStreamState, (market, symbol))
            if stream is None or stream.current_snapshot_id is None:
                skipped += 1
                db.rollback()
                continue
            user = db.get(User, user_id)
            if user is None or user.status != "ACTIVE":
                skipped += 1
                db.rollback()
                continue
            decision, created = create_mock_trading_decision(
                db,
                evaluation_request_id=evaluation_request_id(user_id, market, symbol, slot.key),
                symbol=symbol,
                market=market,
                settings=settings,
                now=now,
            )
            route_trading_decision(
                db,
                decision=decision,
                user=user,
                correlation_id=uuid7(),
                settings=settings,
                now=now,
            )
            decisions += int(created)
        except Exception:
            db.rollback()
            failed += 1
            logger.exception("Scheduled analysis failed market=%s symbol=%s", market, symbol)
    return TickResult(processed, decisions, skipped, failed)


class AnalysisSchedulerWorker:
    def __init__(self, settings: Settings, *, owner_id: str | None = None) -> None:
        self.settings = settings
        self.owner_id = owner_id or uuid7()
        self.identity: SchedulerIdentity | None = None
        self.stop_event = asyncio.Event()

    def stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> int:
        while not self.stop_event.is_set() and self.identity is None:
            with SessionLocal() as db:
                self.identity = acquire_scheduler_lease(
                    db,
                    self.owner_id,
                    lease_seconds=self.settings.analysis_scheduler_lease_seconds,
                )
            if self.identity is None:
                await self._wait()
        if self.identity is None:
            return 0
        with SessionLocal() as db:
            update_scheduler_state(db, self.identity, "STARTING")
        try:
            while not self.stop_event.is_set():
                observed_at = datetime.now(UTC)
                with SessionLocal() as db:
                    if not renew_scheduler_lease(
                        db,
                        self.identity,
                        lease_seconds=self.settings.analysis_scheduler_lease_seconds,
                        now=observed_at,
                    ):
                        return 3
                    slot, next_due = analysis_slot(observed_at)
                    state = db.get(AnalysisSchedulerState, SCHEDULER_NAME)
                    last_slot_key = state.last_slot_key if state else None
                if slot is None:
                    with SessionLocal() as db:
                        update_scheduler_state(
                            db, self.identity, "IDLE", now=observed_at, next_due_at=next_due
                        )
                elif last_slot_key != slot.key:
                    with SessionLocal() as db:
                        update_scheduler_state(
                            db,
                            self.identity,
                            "RUNNING",
                            now=observed_at,
                            next_due_at=slot.next_due_at,
                        )
                    try:
                        with SessionLocal() as db:
                            result = await asyncio.to_thread(
                                run_analysis_tick,
                                db,
                                slot=slot,
                                settings=self.settings,
                                now=observed_at,
                            )
                        with SessionLocal() as db:
                            update_scheduler_state(
                                db,
                                self.identity,
                                "DEGRADED" if result.failed_count else "RUNNING",
                                now=datetime.now(UTC),
                                slot_key=slot.key,
                                next_due_at=slot.next_due_at,
                                completed=True,
                                processed_count=result.processed_count,
                                decision_count=result.decision_count,
                                skipped_count=result.skipped_count,
                                failed_count=result.failed_count,
                                error_code=("ITEM_FAILURE" if result.failed_count else None),
                            )
                    except Exception:
                        logger.exception("Analysis scheduler tick failed slot=%s", slot.key)
                        with SessionLocal() as db:
                            update_scheduler_state(
                                db,
                                self.identity,
                                "DEGRADED",
                                now=datetime.now(UTC),
                                next_due_at=slot.next_due_at,
                                error_code="SCHEDULER_TICK_FAILED",
                            )
                await self._wait()
            return 0
        finally:
            if self.identity is not None:
                with SessionLocal() as db:
                    release_scheduler_lease(db, self.identity)

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(
                self.stop_event.wait(), timeout=self.settings.analysis_scheduler_poll_seconds
            )
        except TimeoutError:
            pass
