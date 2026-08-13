"""Full Risk Guard (#2) integration: each risk rule blocks a BUY in APPROVAL_ONLY."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.decision_execution import route_trading_decision
from app.models import (
    BrokerWorkerState,
    Decision,
    GuardEvaluation,
    MarketSnapshot,
    MarketStreamState,
    Position,
    TradingGate,
    TradingOrder,
    User,
    WatchlistItem,
)
from app.risk_events import RISK_EVENT_SCOPE_DAILY_LOSS, create_risk_event
from app.risk_policy import (
    activate_risk_version,
    create_risk_draft,
    validate_risk_draft,
)
from app.schemas import RiskPolicyPayload

ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"
NOW = datetime(2026, 8, 13, 1, 30, tzinfo=UTC)  # 10:30 KST DUAL_CONTINUOUS
_seq = [0]


def _next_seq() -> int:
    _seq[0] += 1
    return _seq[0]


def _settings_aproval_only(settings: Settings) -> Settings:
    settings.execution_stage = "APPROVAL_ONLY"
    return settings


def _gate_ready(db: Session) -> None:
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    if gate is None:
        gate = TradingGate(account_alias=ACCOUNT_ALIAS, environment="MOCK", status="READY", reason="TEST", version=1)
        db.add(gate)
    else:
        gate.status = "READY"
    worker = db.get(BrokerWorkerState, ACCOUNT_ALIAS)
    if worker is None:
        worker = BrokerWorkerState(
            account_alias=ACCOUNT_ALIAS, environment="MOCK", state="READY", fencing_token=1,
            websocket_connected=True, subscriptions_ready=True, last_heartbeat_at=NOW, started_at=NOW,
        )
        db.add(worker)
    else:
        worker.state = "READY"
        worker.websocket_connected = True
        worker.subscriptions_ready = True
        worker.last_heartbeat_at = NOW
    db.flush()


def _watch(db: Session, admin: User, symbol: str = "005930") -> None:
    db.add(WatchlistItem(user_id=admin.id, market="KRX", symbol=symbol, created_at=NOW))
    db.flush()


def _snapshot(db: Session, symbol: str = "005930", *, ask: Decimal = Decimal(101), bid: Decimal = Decimal("100.7"), last: Decimal = Decimal(101)) -> None:
    n = _next_seq()
    snap = MarketSnapshot(
        symbol=symbol, market="KRX", source="TEST", sequence_or_hash=f"rg-{symbol}-{n}",
        source_sequence=n, payload_hash="a" * 64, last_price=last,
        open_price=last, high_price=last, low_price=last, cumulative_volume=10000,
        best_bid_price=bid, best_bid_quantity=100, best_ask_price=ask, best_ask_quantity=100,
        trading_status="TRADING", quality="NORMAL", recovery_snapshot=False,
        event_at=NOW, received_at=NOW,
    )
    db.add(snap)
    db.flush()
    stream = db.get(MarketStreamState, ("KRX", symbol))
    if stream is None:
        stream = MarketStreamState(market="KRX", symbol=symbol, source="TEST", current_snapshot_id=snap.id, quality="NORMAL")
        db.add(stream)
    else:
        stream.current_snapshot_id = snap.id
        stream.quality = "NORMAL"
    db.flush()


def _activate_risk(db: Session, admin: User, **overrides) -> None:
    base = {
        "entry_order_amount": 500_000,
        "max_single_order_amount": 1_000_000,
        "max_position_amount_per_symbol": 1_000_000,
        "max_total_position_amount": 3_000_000,
        "max_open_positions": 3,
        "max_daily_entries": 5,
        "fixed_stop_loss_pct": "-2.0",
        "quote_stale_seconds": 2,
        "max_spread_pct": "0.30",
        "max_price_deviation_pct": "0.50",
        "daily_loss_limit_pct": "5.0",
        "daily_loss_basis": "REALIZED_PLUS_UNREALIZED",
        "max_consecutive_losses": 3,
    }
    base.update(overrides)
    payload = RiskPolicyPayload(**base)
    draft = create_risk_draft(db, user=admin, policy=payload, reason="risk guard test")
    validated = validate_risk_draft(db, user=admin, version_id=draft.id)
    activate_risk_version(db, user=admin, version_id=validated.id, correlation_id="rg-risk", request_ip="127.0.0.1", user_agent="test")


def _decision(db: Session, action: str = "BUY") -> Decision:
    # Reuse the latest snapshot created by _seed/_snapshot for this symbol.
    stream = db.get(MarketStreamState, ("KRX", "005930"))
    snapshot_id = stream.current_snapshot_id if stream else None
    n = _next_seq()
    decision = Decision(
        purpose="TRADING", evaluation_request_id=f"rg-eval-{action}-{n}",
        input_snapshot_id=snapshot_id, symbol="005930", market="KRX", decision_kind="ENTRY",
        model_provider="CRESTA", model_id="deterministic-mock-v1", prompt_version="test-v1",
        schema_version="1.0", scout_output_json="{}", core_output_json="{}", action=action,
        confidence=Decimal("0.75"), risk_level="MEDIUM", reason_codes_json="[]",
        valid_until=NOW + timedelta(minutes=5), configuration_version_id=None,
        execution_mode=None, execution_outcome="NO_ACTION", validation_status="VALID", latency_ms=0,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return decision


def _blocked_codes(db: Session, execution_id: str) -> set[str]:
    guard = db.scalar(select(GuardEvaluation).where(GuardEvaluation.execution_id == execution_id))
    return {item["code"] for item in json.loads(guard.rule_results_json) if item["result"] == "BLOCKED"}


def _seed(db: Session, admin: User, settings: Settings, *, risk_overrides: dict | None = None) -> Decision:
    _settings_aproval_only(settings)
    _gate_ready(db)
    _watch(db, admin)
    _snapshot(db)
    _activate_risk(db, admin, **(risk_overrides or {}))
    return _decision(db)


def test_buy_passes_full_risk_guard_when_clean(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _seed(db, admin, settings)
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="clean",
        settings=settings,
        now=NOW,
    )
    assert execution is not None
    assert execution.state != "GUARD_BLOCKED"
    assert _blocked_codes(db, execution.id) == set()


def test_buy_blocked_when_total_exposure_exceeded(
    db: Session, admin: User, settings: Settings
) -> None:
    # Position worth 26260 (260 * 101) + entry 10000 > 30000 total limit.
    _seed(db, admin, settings, risk_overrides={
        "max_total_position_amount": 30_000,
        "max_position_amount_per_symbol": 30_000,
        "max_single_order_amount": 30_000,
        "entry_order_amount": 10_000,
    })
    pos = Position(account_alias=ACCOUNT_ALIAS, symbol="005930", quantity=260, average_price=Decimal(101), state="OPEN")
    db.add(pos)
    db.commit()
    decision = _decision(db)
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="exposure",
        settings=settings,
        now=NOW,
    )
    assert execution.state == "GUARD_BLOCKED"
    assert "TOTAL_EXPOSURE_LIMIT" in _blocked_codes(db, execution.id)
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_buy_blocked_when_open_positions_limit(
    db: Session, admin: User, settings: Settings
) -> None:
    _seed(db, admin, settings, risk_overrides={"max_open_positions": 1})
    db.add(Position(account_alias=ACCOUNT_ALIAS, symbol="005931", quantity=5, average_price=Decimal(60000), state="OPEN"))
    db.commit()
    decision = _decision(db)
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="open-pos",
        settings=settings,
        now=NOW,
    )
    assert "OPEN_POSITIONS_LIMIT" in _blocked_codes(db, execution.id)


def test_buy_blocked_when_daily_loss_exceeded(
    db: Session, admin: User, settings: Settings
) -> None:
    # An open position with a deep unrealized loss pushes daily loss > 1%.
    _seed(db, admin, settings, risk_overrides={
        "max_total_position_amount": 100_000,
        "max_position_amount_per_symbol": 100_000,
        "max_single_order_amount": 100_000,
        "entry_order_amount": 10_000,
        "daily_loss_limit_pct": "1.0",
    })
    # snapshot last=101, cost=200 -> -99 * 10 = -990 loss; denominator 100000 -> 0.99% (just under 1.0). Use deeper.
    db.add(Position(account_alias=ACCOUNT_ALIAS, symbol="005930", quantity=10, average_price=Decimal(300), state="OPEN"))
    db.commit()
    decision = _decision(db)
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="daily-loss",
        settings=settings,
        now=NOW,
    )
    assert "DAILY_LOSS_LIMIT" in _blocked_codes(db, execution.id)


def test_buy_blocked_when_spread_too_wide(
    db: Session, admin: User, settings: Settings
) -> None:
    _seed(db, admin, settings, risk_overrides={"max_spread_pct": "0.10"})
    # Wipe the clean snapshot and add a wide-spread one (bid 100 / ask 105 -> ~4.76%).
    db.query(MarketSnapshot).delete()
    db.query(MarketStreamState).delete()
    _snapshot(db, bid=Decimal(100), ask=Decimal(105))
    db.commit()
    decision = _decision(db)
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="spread",
        settings=settings,
        now=NOW,
    )
    assert "SPREAD_LIMIT" in _blocked_codes(db, execution.id)


def test_buy_blocked_when_broker_connection_down(
    db: Session, admin: User, settings: Settings
) -> None:
    _seed(db, admin, settings)
    worker = db.get(BrokerWorkerState, ACCOUNT_ALIAS)
    worker.websocket_connected = False
    db.commit()
    decision = _decision(db)
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="conn",
        settings=settings,
        now=NOW,
    )
    assert "BROKER_CONNECTION_OK" in _blocked_codes(db, execution.id)


def test_buy_blocked_when_active_daily_loss_event(
    db: Session, admin: User, settings: Settings
) -> None:
    _seed(db, admin, settings)
    create_risk_event(
        db, scope=RISK_EVENT_SCOPE_DAILY_LOSS, rule_code="DAILY_LOSS_LIMIT", severity="HIGH",
        account_alias=ACCOUNT_ALIAS, input_record={"note": "halt"}, correlation_id="halt",
        now=NOW,
    )
    db.commit()
    decision = _decision(db)
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="halt-event",
        settings=settings,
        now=NOW,
    )
    assert "NO_ACTIVE_DAILY_LOSS_EVENT" in _blocked_codes(db, execution.id)


def test_shadow_stage_still_zero_orders_under_full_guard(
    db: Session, admin: User, settings: Settings
) -> None:
    # SHADOW stage: even with a clean full-guard pass, no order/approval.
    _gate_ready(db)
    _watch(db, admin)
    _snapshot(db)
    _activate_risk(db, admin)
    decision = _decision(db)
    execution = route_trading_decision(
        db,
        decision=decision,
        user=admin,
        correlation_id="shadow",
        settings=settings,
        now=NOW,
    )
    assert execution.state == "SHADOW_RECORDED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
