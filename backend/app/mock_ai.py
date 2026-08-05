from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Decision, MarketSnapshot, MarketStreamState, User

MODEL_ID = "deterministic-mock-v1"
PROMPT_VERSION = "mock-entry-v1"
TRADING_STATES = {"TRADING", "OPEN", "NORMAL", "CONTINUOUS"}


class MockDecisionError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _outputs(
    snapshot: MarketSnapshot,
    state: MarketStreamState,
    settings: Settings,
    now: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    age = (now - _utc(snapshot.received_at)).total_seconds()
    data_ready = (
        state.quality == "NORMAL"
        and snapshot.quality == "NORMAL"
        and age <= settings.quote_stale_seconds
        and snapshot.trading_status in TRADING_STATES
    )
    if not data_ready:
        scout = {
            "trend_state": "UNKNOWN", "volume_state": "UNKNOWN",
            "volatility_state": "UNKNOWN", "entry_score": 0, "exit_risk_score": 100,
            "core_review_required": True, "suggested_review": "ENTRY",
            "reason_codes": ["DATA_INSUFFICIENT"],
        }
        core = {
            "action": "RISK_BLOCK", "confidence": "1.0", "risk_level": "HIGH",
            "sell_ratio": None, "reason_codes": ["DATA_INSUFFICIENT"],
        }
        return scout, core

    open_price = Decimal(snapshot.open_price)
    last_price = Decimal(snapshot.last_price)
    change = (last_price - open_price) / open_price if open_price > 0 else Decimal(0)
    spread = Decimal(0)
    if snapshot.best_ask_price is not None and snapshot.best_bid_price is not None and last_price > 0:
        spread = (Decimal(snapshot.best_ask_price) - Decimal(snapshot.best_bid_price)) / last_price
    if change >= Decimal("0.005") and spread <= Decimal("0.005"):
        score, action, confidence = 75, "BUY", "0.75"
        reasons = ["PRICE_STABLE", "BREAKOUT_CONFIRMED"]
        trend = "UPTREND"
    elif change >= 0:
        score, action, confidence = 55, "WAIT", "0.60"
        reasons = ["PRICE_STABLE"]
        trend = "RANGE"
    else:
        score, action, confidence = 30, "REJECT", "0.70"
        reasons = ["MARKET_WEAKENING"]
        trend = "DOWNTREND"
    if spread > Decimal("0.005"):
        action, reasons = "RISK_BLOCK", ["SPREAD_WIDE"]
    scout = {
        "trend_state": trend, "volume_state": "NORMAL", "volatility_state": "NORMAL",
        "entry_score": score, "exit_risk_score": max(0, 100 - score),
        "core_review_required": True, "suggested_review": "ENTRY", "reason_codes": reasons,
    }
    core = {
        "action": action, "confidence": confidence,
        "risk_level": "MEDIUM" if action in {"BUY", "REJECT"} else "HIGH" if action == "RISK_BLOCK" else "LOW",
        "sell_ratio": None, "reason_codes": reasons,
    }
    return scout, core


def evaluate_mock_decision(
    db: Session,
    *,
    user: User,
    evaluation_request_id: str,
    symbol: str,
    market: str,
    settings: Settings,
    now: datetime | None = None,
) -> Decision:
    existing = db.scalar(
        select(Decision).where(Decision.evaluation_request_id == evaluation_request_id)
    )
    if existing is not None:
        if existing.symbol != symbol or existing.market != market:
            raise MockDecisionError("DECISION_IDEMPOTENCY_CONFLICT")
        return existing
    state = db.get(MarketStreamState, (market, symbol))
    if state is None or state.current_snapshot_id is None:
        raise MockDecisionError("DECISION_SNAPSHOT_NOT_FOUND", 404)
    snapshot = db.get(MarketSnapshot, state.current_snapshot_id)
    if snapshot is None:
        raise MockDecisionError("DECISION_SNAPSHOT_NOT_FOUND", 404)
    current = now or datetime.now(UTC)
    scout, core = _outputs(snapshot, state, settings, current)
    action = str(core["action"])
    decision = Decision(
        purpose="DIAGNOSTIC",
        evaluation_request_id=evaluation_request_id,
        input_snapshot_id=snapshot.id,
        symbol=symbol,
        market=market,
        decision_kind="ENTRY",
        model_provider="CRESTA",
        model_id=MODEL_ID,
        prompt_version=PROMPT_VERSION,
        schema_version="1.0",
        scout_output_json=json.dumps(scout, separators=(",", ":"), sort_keys=True),
        core_output_json=json.dumps(core, separators=(",", ":"), sort_keys=True),
        action=action,
        confidence=Decimal(str(core["confidence"])),
        risk_level=str(core["risk_level"]),
        reason_codes_json=json.dumps(core["reason_codes"], separators=(",", ":")),
        valid_until=current + timedelta(seconds=60),
        configuration_version_id=None,
        execution_mode=None,
        execution_outcome="NO_ACTION",
        validation_status="VALID",
        latency_ms=0,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return decision


def list_decisions(db: Session, limit: int = 50) -> list[Decision]:
    return list(db.scalars(select(Decision).order_by(Decision.created_at.desc()).limit(limit)))
