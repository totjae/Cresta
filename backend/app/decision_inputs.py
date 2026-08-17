from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.indicators import CALCULATOR_VERSION
from app.models import (
    DecisionInputSnapshot,
    IndicatorSnapshot,
    MarketSnapshot,
    MarketStreamState,
)

INPUT_SCHEMA_VERSION = "scout-input-v1"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _decimal(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def canonical_input_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def input_digest(input_json: str) -> str:
    return hashlib.sha256(input_json.encode("utf-8")).hexdigest()


def build_decision_input(
    db: Session,
    *,
    user_id: str,
    purpose: str,
    snapshot: MarketSnapshot,
    state: MarketStreamState,
    observed_at: datetime,
    quote_stale_seconds: int,
    position_snapshot: dict[str, object] | None = None,
) -> tuple[DecisionInputSnapshot, dict[str, object]]:
    observed = _utc(observed_at)
    received_at = _utc(snapshot.received_at)
    age_seconds = max((observed - received_at).total_seconds(), 0)
    indicator = db.scalar(
        select(IndicatorSnapshot).where(
            IndicatorSnapshot.market_snapshot_id == snapshot.id
        )
    )
    indicator_ready = bool(
        indicator is not None and indicator.calculator_version == CALCULATOR_VERSION
    )
    indicators: dict[str, object] = {
        "status": (
            "READY"
            if indicator_ready
            else "MISSING"
            if indicator is None
            else "VERSION_MISMATCH"
        ),
        "snapshot_id": indicator.id if indicator is not None else None,
        "calculator_version": indicator.calculator_version if indicator is not None else None,
    }
    if indicator_ready and indicator is not None:
        indicators.update(
            {
                "vwap": _decimal(indicator.vwap),
                "sma5": _decimal(indicator.sma5),
                "session_high": _decimal(indicator.session_high),
                "drawdown_from_high_pct": _decimal(indicator.drawdown_from_high_pct),
                "spread_pct": _decimal(indicator.spread_pct),
                "price_vs_vwap_pct": _decimal(indicator.price_vs_vwap_pct),
                "sma5_slope_pct": _decimal(indicator.sma5_slope_pct),
                "relative_volume_5": _decimal(indicator.relative_volume_5),
                "realized_volatility_pct": _decimal(indicator.realized_volatility_pct),
                "minute_bar_count": indicator.minute_bar_count,
                "input_start_at": _utc(indicator.input_start_at).isoformat(),
                "input_end_at": _utc(indicator.input_end_at).isoformat(),
            }
        )
    payload: dict[str, object] = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "purpose": purpose,
        "snapshot_id": snapshot.id,
        "symbol": snapshot.symbol,
        "market": snapshot.market,
        "observed_at": observed.isoformat(),
        "data_quality": {
            "stream": state.quality,
            "snapshot": snapshot.quality,
            "age_seconds": f"{age_seconds:.3f}",
        },
        "session_state": snapshot.trading_status,
        "quote": {
            "last_price": _decimal(snapshot.last_price),
            "open_price": _decimal(snapshot.open_price),
            "high_price": _decimal(snapshot.high_price),
            "low_price": _decimal(snapshot.low_price),
            "cumulative_volume": snapshot.cumulative_volume,
            "best_bid_price": _decimal(snapshot.best_bid_price),
            "best_bid_quantity": snapshot.best_bid_quantity,
            "best_ask_price": _decimal(snapshot.best_ask_price),
            "best_ask_quantity": snapshot.best_ask_quantity,
            "event_at": _utc(snapshot.event_at).isoformat(),
            "received_at": received_at.isoformat(),
        },
        "indicators": indicators,
        "position": position_snapshot,
        "open_orders": [],
        "account_risk_summary": None,
        "market_context": None,
        "strategy": {
            "score_policy_version": "mock-score-v2",
            "quote_stale_seconds": quote_stale_seconds,
        },
        "configuration_version": None,
        "prior_decision_summary": None,
    }
    encoded = canonical_input_json(payload)
    digest = input_digest(encoded)
    existing = db.scalar(
        select(DecisionInputSnapshot).where(
            DecisionInputSnapshot.user_id == user_id,
            DecisionInputSnapshot.purpose == purpose,
            DecisionInputSnapshot.market_snapshot_id == snapshot.id,
            DecisionInputSnapshot.input_hash == digest,
        )
    )
    if existing is not None:
        if existing.input_json != encoded:
            raise ValueError("DECISION_INPUT_HASH_CONFLICT")
        return existing, payload
    decision_input = DecisionInputSnapshot(
        user_id=user_id,
        purpose=purpose,
        schema_version=INPUT_SCHEMA_VERSION,
        market=snapshot.market,
        symbol=snapshot.symbol,
        market_snapshot_id=snapshot.id,
        indicator_snapshot_id=indicator.id if indicator is not None else None,
        observed_at=observed,
        data_quality=(
            "NORMAL"
            if state.quality == "NORMAL" and snapshot.quality == "NORMAL"
            else "DEGRADED"
        ),
        session_state=snapshot.trading_status,
        input_json=encoded,
        input_hash=digest,
    )
    db.add(decision_input)
    db.flush()
    return decision_input, payload
