from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.decision_execution import route_trading_decision
from app.models import (
    Approval,
    Decision,
    DecisionExecution,
    GuardEvaluation,
    MarketSnapshot,
    TradingOrder,
    User,
)


def _decision(db: Session, *, purpose: str = "TRADING", action: str = "BUY") -> Decision:
    now = datetime.now(UTC)
    snapshot = MarketSnapshot(
        symbol="005930", market="KRX", source="TEST", sequence_or_hash=f"shadow-{action}",
        source_sequence=1, payload_hash="a" * 64, last_price=Decimal(101),
        open_price=Decimal(100), high_price=Decimal(102), low_price=Decimal(99),
        cumulative_volume=10000, best_bid_price=Decimal("100.9"), best_bid_quantity=100,
        best_ask_price=Decimal("101.1"), best_ask_quantity=100,
        trading_status="TRADING", quality="NORMAL", recovery_snapshot=False,
        event_at=now, received_at=now,
    )
    db.add(snapshot)
    db.flush()
    decision = Decision(
        purpose=purpose, evaluation_request_id=f"shadow-evaluation-{purpose}-{action}",
        input_snapshot_id=snapshot.id, symbol="005930", market="KRX", decision_kind="ENTRY",
        model_provider="CRESTA", model_id="deterministic-mock-v1", prompt_version="test-v1",
        schema_version="1.0", scout_output_json="{}", core_output_json="{}", action=action,
        confidence=Decimal("0.75"), risk_level="MEDIUM", reason_codes_json="[]",
        valid_until=now + timedelta(minutes=1), configuration_version_id=None,
        execution_mode=None, execution_outcome="NO_ACTION", validation_status="VALID", latency_ms=0,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return decision


def test_diagnostic_decision_cannot_enter_execution_pipeline(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _decision(db, purpose="DIAGNOSTIC")
    result = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="diagnostic-correlation", settings=settings
    )
    assert result is None
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 0


def test_buy_shadow_is_idempotently_guard_blocked_without_order(
    db: Session, admin: User, settings: Settings
) -> None:
    decision = _decision(db)
    first = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="shadow-correlation", settings=settings
    )
    repeated = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="shadow-correlation-2", settings=settings
    )
    assert first is not None
    assert repeated is not None
    assert repeated.id == first.id
    assert first.stage == "SHADOW"
    assert first.state == "GUARD_BLOCKED"
    guard = db.get(GuardEvaluation, first.guard_evaluation_id)
    assert guard is not None
    assert guard.result == "BLOCKED"
    blocked_codes = {
        item["code"] for item in json.loads(guard.rule_results_json) if item["result"] == "BLOCKED"
    }
    assert "ORDER_SIZE_NOT_CONFIGURED" in blocked_codes
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 1
    assert db.scalar(select(func.count()).select_from(GuardEvaluation)) == 1
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_wait_records_no_action_without_guard(db: Session, admin: User, settings: Settings) -> None:
    decision = _decision(db, action="WAIT")
    execution = route_trading_decision(
        db, decision=decision, user=admin, correlation_id="wait-correlation", settings=settings
    )
    assert execution is not None
    assert execution.action == "NO_ACTION"
    assert execution.state == "NO_ACTION"
    assert execution.guard_evaluation_id is None
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
