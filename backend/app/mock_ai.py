from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.server_inputs import build_position_snapshot
from app.config import Settings
from app.decision_inputs import build_decision_input
from app.models import Decision, MarketSnapshot, MarketStreamState, Position, User

MODEL_ID = "deterministic-mock-v2"
PROMPT_VERSION = "mock-entry-indicators-v2"
POSITION_MODEL_ID = "deterministic-position-v1"
POSITION_PROMPT_VERSION = "mock-position-policy-v1"
TRADING_STATES = {"TRADING", "OPEN", "NORMAL", "CONTINUOUS"}


class MockDecisionError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _outputs(
    decision_input: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    quality = decision_input["data_quality"]
    indicators = decision_input["indicators"]
    strategy = decision_input["strategy"]
    assert isinstance(quality, dict)
    assert isinstance(indicators, dict)
    assert isinstance(strategy, dict)
    age = Decimal(str(quality["age_seconds"]))
    data_ready = (
        quality["stream"] == "NORMAL"
        and quality["snapshot"] == "NORMAL"
        and age <= Decimal(str(strategy["quote_stale_seconds"]))
        and decision_input["session_state"] in TRADING_STATES
        and indicators["status"] == "READY"
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

    def metric(name: str) -> Decimal | None:
        value = indicators.get(name)
        return Decimal(str(value)) if value is not None else None

    price_vs_vwap = metric("price_vs_vwap_pct")
    sma5_slope = metric("sma5_slope_pct")
    relative_volume = metric("relative_volume_5")
    volatility = metric("realized_volatility_pct")
    drawdown = metric("drawdown_from_high_pct")
    spread = metric("spread_pct")
    score = 50
    reasons: list[str] = []
    if price_vs_vwap is not None and price_vs_vwap >= 0:
        score += 15
        reasons.append("ABOVE_VWAP")
    else:
        score -= 15
        reasons.append("BELOW_VWAP")
    if sma5_slope is not None and sma5_slope > Decimal("0.05"):
        score += 10
        reasons.append("BREAKOUT_CONFIRMED")
    elif sma5_slope is not None and sma5_slope < Decimal("-0.05"):
        score -= 10
        reasons.append("BREAKDOWN_DETECTED")
    if relative_volume is not None and relative_volume >= Decimal("1.2"):
        score += 10
        reasons.append("VOLUME_STRENGTHENING")
        volume_state = "STRENGTHENING"
    elif relative_volume is not None and relative_volume <= Decimal("0.8"):
        score -= 10
        reasons.append("VOLUME_WEAKENING")
        volume_state = "WEAKENING"
    else:
        volume_state = "NORMAL" if relative_volume is not None else "UNKNOWN"
    if drawdown is not None and drawdown <= Decimal("-1.0"):
        score -= 15
        reasons.append("DRAWDOWN_FROM_HIGH")
    if volatility is None:
        volatility_state = "UNKNOWN"
    elif volatility >= Decimal("3.0"):
        volatility_state = "EXTREME"
        reasons.append("VOLATILITY_EXPANDING")
    elif volatility >= Decimal("1.5"):
        volatility_state = "EXPANDING"
        score -= 10
        reasons.append("VOLATILITY_EXPANDING")
    else:
        volatility_state = "NORMAL"
    hard_block = bool(
        (spread is not None and spread > Decimal("0.5"))
        or volatility_state == "EXTREME"
    )
    if spread is not None and spread > Decimal("0.5"):
        reasons.append("SPREAD_WIDE")
    score = min(max(score, 0), 100)
    if hard_block:
        action, confidence = "RISK_BLOCK", "1.0"
    elif score >= 70:
        action, confidence = "BUY", "0.75"
    elif score >= 45:
        action, confidence = "WAIT", "0.60"
    else:
        action, confidence = "REJECT", "0.70"
    if price_vs_vwap is not None and price_vs_vwap >= 0 and (
        sma5_slope is None or sma5_slope >= 0
    ):
        trend = "UPTREND"
    elif price_vs_vwap is not None and price_vs_vwap >= 0:
        trend = "UPTREND_WEAKENING"
    elif sma5_slope is not None and sma5_slope < 0:
        trend = "DOWNTREND"
    else:
        trend = "RANGE"
    scout = {
        "trend_state": trend, "volume_state": volume_state,
        "volatility_state": volatility_state,
        "entry_score": score, "exit_risk_score": max(0, 100 - score),
        "core_review_required": True, "suggested_review": "ENTRY", "reason_codes": reasons,
    }
    core = {
        "action": action, "confidence": confidence,
        "risk_level": "HIGH" if action == "RISK_BLOCK" else "MEDIUM" if action in {"BUY", "REJECT"} else "LOW",
        "sell_ratio": None, "reason_codes": reasons,
    }
    return scout, core


def _position_outputs(
    decision_input: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    quality = decision_input["data_quality"]
    indicators = decision_input["indicators"]
    strategy = decision_input["strategy"]
    position = decision_input.get("position")
    assert isinstance(quality, dict)
    assert isinstance(indicators, dict)
    assert isinstance(strategy, dict)
    freshness = position.get("freshness") if isinstance(position, dict) else None
    data_ready = bool(
        isinstance(position, dict)
        and position.get("marker") == "OPEN_POSITION"
        and position.get("state") == "OPEN"
        and isinstance(freshness, dict)
        and freshness.get("status") == "FRESH"
        and Decimal(str(position.get("average_price", 0))) > 0
        and Decimal(str(position.get("current_price", 0))) > 0
        and quality["stream"] == "NORMAL"
        and quality["snapshot"] == "NORMAL"
        and Decimal(str(quality["age_seconds"]))
        <= Decimal(str(strategy["quote_stale_seconds"]))
        and decision_input["session_state"] in TRADING_STATES
        and indicators["status"] == "READY"
    )
    if not data_ready:
        scout = {
            "trend_state": "UNKNOWN",
            "volume_state": "UNKNOWN",
            "volatility_state": "UNKNOWN",
            "entry_score": 0,
            "exit_risk_score": 100,
            "core_review_required": True,
            "suggested_review": "HOLDING",
            "reason_codes": ["DATA_INSUFFICIENT"],
        }
        core = {
            "action": "HOLD",
            "confidence": "1.0",
            "risk_level": "HIGH",
            "sell_ratio": None,
            "reason_codes": ["DATA_INSUFFICIENT"],
        }
        return scout, core

    assert isinstance(position, dict)

    def metric(name: str) -> Decimal | None:
        value = indicators.get(name)
        return Decimal(str(value)) if value is not None else None

    unrealized_return = Decimal(str(position["unrealized_return_pct"]))
    distance_to_stop = Decimal(str(position["distance_to_fixed_stop_pct"]))
    drawdown = Decimal(str(position["drawdown_from_session_high_pct"]))
    price_vs_vwap = metric("price_vs_vwap_pct")
    sma5_slope = metric("sma5_slope_pct")
    relative_volume = metric("relative_volume_5")
    volatility = metric("realized_volatility_pct")
    score = 15
    reasons: list[str] = []

    if unrealized_return < 0:
        score += 10
        reasons.append("POSITION_LOSING")
    else:
        reasons.append("POSITION_PROFITABLE")
    if distance_to_stop <= 0:
        score = 100
        reasons.append("FIXED_STOP_TRIGGERED")
    elif distance_to_stop <= Decimal("0.5"):
        score += 35
        reasons.append("FIXED_STOP_NEAR")
    if price_vs_vwap is not None and price_vs_vwap < 0:
        score += 15
        reasons.append("BELOW_VWAP")
    if sma5_slope is not None and sma5_slope < Decimal("-0.05"):
        score += 15
        reasons.append("BREAKDOWN_DETECTED")
        trend = "DOWNTREND"
    elif price_vs_vwap is not None and price_vs_vwap >= 0:
        trend = "UPTREND"
    else:
        trend = "RANGE"
    if drawdown <= Decimal("-2.0"):
        score += 25
        reasons.append("DRAWDOWN_HIGH")
    elif drawdown <= Decimal("-1.0"):
        score += 15
        reasons.append("DRAWDOWN_MODERATE")
    else:
        reasons.append("DRAWDOWN_LOW")
    if relative_volume is not None and relative_volume <= Decimal("0.8"):
        score += 5
        volume_state = "WEAKENING"
        reasons.append("VOLUME_WEAKENING")
    elif relative_volume is not None and relative_volume >= Decimal("1.2"):
        volume_state = "STRENGTHENING"
    else:
        volume_state = "NORMAL" if relative_volume is not None else "UNKNOWN"
    if volatility is None:
        volatility_state = "UNKNOWN"
    elif volatility >= Decimal("3.0"):
        score += 20
        volatility_state = "EXTREME"
        reasons.append("VOLATILITY_EXPANDING")
    elif volatility >= Decimal("1.5"):
        score += 10
        volatility_state = "EXPANDING"
        reasons.append("VOLATILITY_EXPANDING")
    else:
        volatility_state = "NORMAL"

    score = min(max(score, 0), 100)
    if score >= 90:
        action, confidence, ratio = "FULL_SELL", "0.85", None
        risk_level = "HIGH"
        reasons.append("POSITION_RISK_CRITICAL")
    elif score >= 70:
        action, confidence, ratio = "PARTIAL_SELL", "0.75", "0.5"
        risk_level = "HIGH"
        reasons.append("POSITION_RISK_ELEVATED")
    else:
        action, confidence, ratio = "HOLD", "0.70", None
        risk_level = "LOW" if score < 45 else "MEDIUM"
        reasons.append("POSITION_RISK_NORMAL")
    scout = {
        "trend_state": trend,
        "volume_state": volume_state,
        "volatility_state": volatility_state,
        "entry_score": 0,
        "exit_risk_score": score,
        "core_review_required": action != "HOLD",
        "suggested_review": "HOLDING",
        "reason_codes": reasons,
    }
    core = {
        "action": action,
        "confidence": confidence,
        "risk_level": risk_level,
        "sell_ratio": ratio,
        "reason_codes": reasons,
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
    decision, created = create_mock_decision(
        db,
        user_id=user.id,
        evaluation_request_id=evaluation_request_id,
        symbol=symbol,
        market=market,
        settings=settings,
        purpose="DIAGNOSTIC",
        now=now,
    )
    if created:
        db.commit()
        db.refresh(decision)
    return decision


def create_mock_trading_decision(
    db: Session,
    *,
    user: User,
    evaluation_request_id: str,
    symbol: str,
    market: str,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[Decision, bool]:
    return create_mock_decision(
        db,
        user_id=user.id,
        evaluation_request_id=evaluation_request_id,
        symbol=symbol,
        market=market,
        settings=settings,
        purpose="TRADING",
        now=now,
    )


def create_mock_position_trading_decision(
    db: Session,
    *,
    user: User,
    position: Position,
    evaluation_request_id: str,
    symbol: str,
    market: str,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[Decision, bool]:
    return create_mock_decision(
        db,
        user_id=user.id,
        evaluation_request_id=evaluation_request_id,
        symbol=symbol,
        market=market,
        settings=settings,
        purpose="TRADING",
        decision_kind="POSITION",
        position=position,
        now=now,
    )


def create_mock_decision(
    db: Session,
    *,
    user_id: str,
    evaluation_request_id: str,
    symbol: str,
    market: str,
    settings: Settings,
    purpose: str,
    decision_kind: str = "ENTRY",
    position: Position | None = None,
    now: datetime | None = None,
) -> tuple[Decision, bool]:
    existing = db.scalar(
        select(Decision).where(Decision.evaluation_request_id == evaluation_request_id)
    )
    if existing is not None:
        if (
            existing.symbol != symbol
            or existing.market != market
            or existing.purpose != purpose
            or existing.decision_kind != decision_kind
        ):
            raise MockDecisionError("DECISION_IDEMPOTENCY_CONFLICT")
        return existing, False
    state = db.get(MarketStreamState, (market, symbol))
    if state is None or state.current_snapshot_id is None:
        raise MockDecisionError("DECISION_SNAPSHOT_NOT_FOUND", 404)
    snapshot = db.get(MarketSnapshot, state.current_snapshot_id)
    if snapshot is None:
        raise MockDecisionError("DECISION_SNAPSHOT_NOT_FOUND", 404)
    current = now or datetime.now(UTC)
    position_user = db.get(User, user_id)
    position_input: dict[str, object] | None = None
    if decision_kind == "POSITION" and position is not None:
        if position_user is None:
            raise MockDecisionError("DECISION_USER_NOT_FOUND", 404)
        position_input = build_position_snapshot(
            db,
            user=position_user,
            position=position,
            market_snapshot=snapshot,
            settings=settings,
        )
    decision_input, input_payload = build_decision_input(
        db,
        user_id=user_id,
        purpose=purpose,
        snapshot=snapshot,
        state=state,
        observed_at=current,
        quote_stale_seconds=settings.quote_stale_seconds,
        position_snapshot=position_input,
    )
    scout, core = (
        _position_outputs(input_payload)
        if decision_kind == "POSITION"
        else _outputs(input_payload)
    )
    action = str(core["action"])
    decision = Decision(
        decision_input_id=decision_input.id,
        purpose=purpose,
        evaluation_request_id=evaluation_request_id,
        input_snapshot_id=snapshot.id,
        symbol=symbol,
        market=market,
        decision_kind=decision_kind,
        model_provider="CRESTA",
        model_id=POSITION_MODEL_ID if decision_kind == "POSITION" else MODEL_ID,
        prompt_version=(
            POSITION_PROMPT_VERSION if decision_kind == "POSITION" else PROMPT_VERSION
        ),
        schema_version="1.0",
        scout_output_json=json.dumps(scout, separators=(",", ":"), sort_keys=True),
        core_output_json=json.dumps(core, separators=(",", ":"), sort_keys=True),
        action=action,
        confidence=Decimal(str(core["confidence"])),
        risk_level=str(core["risk_level"]),
        reason_codes_json=json.dumps(core["reason_codes"], separators=(",", ":")),
        valid_until=current + timedelta(minutes=5 if decision_kind == "POSITION" else 1),
        configuration_version_id=None,
        execution_mode=None,
        execution_outcome="NO_ACTION",
        validation_status="VALID",
        latency_ms=0,
    )
    db.add(decision)
    db.flush()
    return decision, True


def list_decisions(db: Session, limit: int = 50) -> list[Decision]:
    return list(db.scalars(select(Decision).order_by(Decision.created_at.desc()).limit(limit)))
