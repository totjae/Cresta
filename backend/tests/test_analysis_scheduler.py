from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis_scheduler import analysis_slot, run_analysis_tick
from app.analysis_scheduler_state import (
    acquire_scheduler_lease,
    get_scheduler_status,
    release_scheduler_lease,
    renew_scheduler_lease,
    update_scheduler_state,
)
from app.config import Settings
from app.models import (
    AgentRun,
    AgentStageRun,
    Approval,
    Decision,
    DecisionExecution,
    DecisionInputSnapshot,
    GuardEvaluation,
    IndicatorSnapshot,
    MarketSnapshot,
    MarketStreamState,
    TradingOrder,
    User,
    WatchlistItem,
)
from tests.test_agent_runtime import _routes
from tests.test_llm_role_assignments import _login

KST = ZoneInfo("Asia/Seoul")


def _at_kst(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST).astimezone(UTC)


def _watch_with_snapshot(db: Session, user: User, now: datetime) -> None:
    item = WatchlistItem(user_id=user.id, symbol="005930", market="KRX")
    db.add(item)
    snapshot = MarketSnapshot(
        symbol="005930", market="KRX", source="TEST", sequence_or_hash="scheduler-1",
        source_sequence=1, payload_hash="a" * 64, last_price=Decimal(101),
        open_price=Decimal(100), high_price=Decimal(102), low_price=Decimal(99),
        cumulative_volume=10000, best_bid_price=Decimal("100.9"), best_bid_quantity=100,
        best_ask_price=Decimal("101.1"), best_ask_quantity=100,
        trading_status="TRADING", quality="NORMAL", recovery_snapshot=False,
        event_at=now, received_at=now,
    )
    db.add(snapshot)
    db.flush()
    db.add(
        MarketStreamState(
            market="KRX", symbol="005930", source="TEST", current_snapshot_id=snapshot.id,
            last_sequence=1, last_event_at=now, last_received_at=now,
            cumulative_volume=10000, quality="NORMAL",
        )
    )
    db.add(
        IndicatorSnapshot(
            market_snapshot_id=snapshot.id,
            market="KRX",
            symbol="005930",
            calculator_version="watch-indicators-v2",
            vwap=Decimal(100),
            sma5=Decimal("100.5"),
            session_high=Decimal(102),
            drawdown_from_high_pct=Decimal("-0.5"),
            spread_pct=Decimal("0.2"),
            price_vs_vwap_pct=Decimal("1.0"),
            sma5_slope_pct=Decimal("0.2"),
            relative_volume_5=Decimal("1.5"),
            realized_volatility_pct=Decimal("0.5"),
            minute_bar_count=10,
            input_start_at=now - timedelta(minutes=10),
            input_end_at=now,
        )
    )
    db.commit()


def test_analysis_slots_use_kst_focus_normal_and_idle_boundaries() -> None:
    focus, _ = analysis_slot(_at_kst(2026, 8, 5, 10, 7))
    normal, _ = analysis_slot(_at_kst(2026, 8, 5, 11, 17))
    before, before_due = analysis_slot(_at_kst(2026, 8, 5, 7, 59))
    weekend, weekend_due = analysis_slot(_at_kst(2026, 8, 8, 10, 0))
    assert focus is not None and focus.key.endswith("1005+0900")
    assert normal is not None and normal.key.endswith("1110+0900")
    assert before is None and before_due == _at_kst(2026, 8, 5, 8, 0)
    assert weekend is None and weekend_due == _at_kst(2026, 8, 10, 8, 0)


def test_tick_creates_one_trading_shadow_execution_and_no_order(
    db: Session, admin: User, settings: Settings
) -> None:
    now = _at_kst(2026, 8, 5, 10, 5)
    _watch_with_snapshot(db, admin, now)
    slot, _ = analysis_slot(now)
    assert slot is not None
    first = run_analysis_tick(db, slot=slot, settings=settings, now=now)
    repeated = run_analysis_tick(db, slot=slot, settings=settings, now=now)
    decision = db.scalar(select(Decision))
    execution = db.scalar(select(DecisionExecution))
    assert first.decision_count == 1
    assert repeated.decision_count == 0
    assert decision is not None and decision.purpose == "TRADING"
    assert decision.decision_input_id is not None
    assert db.scalar(select(func.count()).select_from(DecisionInputSnapshot)) == 1
    assert execution is not None and execution.stage == "SHADOW"
    assert execution.state == "GUARD_BLOCKED"
    assert db.scalar(select(func.count()).select_from(Decision)) == 1
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 1
    assert db.scalar(select(func.count()).select_from(GuardEvaluation)) == 1
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_tick_skips_watch_item_without_snapshot(
    db: Session, admin: User, settings: Settings
) -> None:
    db.add(WatchlistItem(user_id=admin.id, symbol="005930", market="KRX"))
    db.commit()
    now = _at_kst(2026, 8, 5, 10, 5)
    slot, _ = analysis_slot(now)
    assert slot is not None
    result = run_analysis_tick(db, slot=slot, settings=settings, now=now)
    assert result.processed_count == 1
    assert result.skipped_count == 1
    assert db.scalar(select(func.count()).select_from(Decision)) == 0


def test_tick_admits_shadow_agent_run_when_all_active_routes_exist(
    client: TestClient, db: Session, admin: User, settings: Settings
) -> None:
    now = _at_kst(2026, 8, 5, 10, 5)
    _watch_with_snapshot(db, admin, now)
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    route_ids = _routes(client, headers)
    preview = client.post(
        "/api/v1/ai/role-assignments/activation-preview",
        headers=headers,
        json={"schema_version": "1.0", "route_ids": route_ids},
    )
    assert preview.status_code == 200
    activated = client.post(
        "/api/v1/ai/role-assignments/activate",
        headers=headers,
        json={
            "schema_version": "1.0",
            "route_ids": route_ids,
        },
    )
    assert activated.status_code == 200, activated.text
    slot, _ = analysis_slot(now)
    assert slot is not None
    run_analysis_tick(db, slot=slot, settings=settings, now=now)
    run = db.scalar(select(AgentRun))
    assert run is not None and run.state == "CREATED"
    assert db.scalar(select(func.count()).select_from(AgentStageRun)) == 7


def test_scheduler_lease_fences_duplicate_owner_and_reports_stale(db: Session) -> None:
    now = datetime(2026, 8, 5, 1, 0, tzinfo=UTC)
    first = acquire_scheduler_lease(db, "owner-1", lease_seconds=30, now=now)
    assert first is not None
    assert acquire_scheduler_lease(db, "owner-2", lease_seconds=30, now=now) is None
    assert update_scheduler_state(db, first, "IDLE", now=now, next_due_at=now + timedelta(hours=1))
    status = get_scheduler_status(db, now=now + timedelta(seconds=1))
    assert status.state == "IDLE"
    assert status.lease_valid is True
    assert renew_scheduler_lease(db, first, lease_seconds=30, now=now + timedelta(seconds=10))
    assert get_scheduler_status(db, now=now + timedelta(seconds=41)).state == "STALE"
    assert release_scheduler_lease(db, first, now=now + timedelta(seconds=20))
