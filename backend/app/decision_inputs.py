from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.indicators import CALCULATOR_VERSION
from app.models import (
    DecisionInputSnapshot,
    IndicatorSnapshot,
    MarketContextSnapshot,
    MarketSnapshot,
    MarketStreamState,
)

INPUT_SCHEMA_VERSION = "scout-input-v1"
V2_INPUT_SCHEMA_VERSION = "scout-input-v2"
V2_SERVER_INPUT_POLICY_VERSION = "agent-server-input-v1"
V2_CONFIGURATION_SCHEMA_VERSION = "v7-upstream-configuration-v1"


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
        select(IndicatorSnapshot).where(IndicatorSnapshot.market_snapshot_id == snapshot.id)
    )
    indicator_ready = bool(
        indicator is not None and indicator.calculator_version == CALCULATOR_VERSION
    )
    indicators: dict[str, object] = {
        "status": (
            "READY" if indicator_ready else "MISSING" if indicator is None else "VERSION_MISMATCH"
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
            "NORMAL" if state.quality == "NORMAL" and snapshot.quality == "NORMAL" else "DEGRADED"
        ),
        session_state=snapshot.trading_status,
        input_json=encoded,
        input_hash=digest,
    )
    db.add(decision_input)
    db.flush()
    return decision_input, payload


def _indicator_payload(indicator: IndicatorSnapshot) -> dict[str, object]:
    return {
        "calculator_version": indicator.calculator_version,
        "drawdown_from_high_pct": _decimal(indicator.drawdown_from_high_pct),
        "input_end_at": _utc(indicator.input_end_at).isoformat(),
        "input_start_at": _utc(indicator.input_start_at).isoformat(),
        "market": indicator.market,
        "market_snapshot_id": indicator.market_snapshot_id,
        "minute_bar_count": indicator.minute_bar_count,
        "price_vs_vwap_pct": _decimal(indicator.price_vs_vwap_pct),
        "realized_volatility_pct": _decimal(indicator.realized_volatility_pct),
        "relative_volume_5": _decimal(indicator.relative_volume_5),
        "session_high": _decimal(indicator.session_high),
        "sma5": _decimal(indicator.sma5),
        "sma5_slope_pct": _decimal(indicator.sma5_slope_pct),
        "snapshot_id": indicator.id,
        "spread_pct": _decimal(indicator.spread_pct),
        "symbol": indicator.symbol,
        "vwap": _decimal(indicator.vwap),
    }


def build_v7_scout_input(
    db: Session,
    *,
    user_id: str,
    snapshot: MarketSnapshot,
    state: MarketStreamState,
    observed_at: datetime,
    quote_stale_seconds: int,
    dart_lookback_days: int,
    krx_lookback_days: int,
    naver_news_lookback_hours: int,
    market_context: MarketContextSnapshot | None = None,
    purpose: str = "DIAGNOSTIC",
) -> tuple[DecisionInputSnapshot, dict[str, object]]:
    """Build the immutable server-owned v7 ENTRY Scout input."""
    if purpose not in {"DIAGNOSTIC", "TRADING"}:
        raise ValueError("V7_SCOUT_INPUT_PURPOSE_INVALID")
    observed = _utc(observed_at)
    received_at = _utc(snapshot.received_at)
    if (
        state.current_snapshot_id != snapshot.id
        or state.quality != "NORMAL"
        or snapshot.quality != "NORMAL"
        or not snapshot.payload_hash
        or quote_stale_seconds <= 0
    ):
        raise ValueError("V7_SCOUT_INPUT_SOURCE_INVALID")
    indicator = db.scalar(
        select(IndicatorSnapshot).where(IndicatorSnapshot.market_snapshot_id == snapshot.id)
    )
    if (
        indicator is None
        or indicator.calculator_version != CALCULATOR_VERSION
        or indicator.market != snapshot.market
        or indicator.symbol != snapshot.symbol
    ):
        raise ValueError("V7_SCOUT_INPUT_INDICATOR_INVALID")

    market_valid_until = received_at + timedelta(seconds=quote_stale_seconds)
    indicator_valid_until = _utc(indicator.input_end_at) + timedelta(seconds=quote_stale_seconds)
    validity = [market_valid_until, indicator_valid_until]
    market_context_payload: dict[str, object] | None = None
    market_context_provenance: dict[str, object] | None = None
    if market_context is not None:
        try:
            decoded = json.loads(market_context.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("V7_SCOUT_INPUT_MARKET_CONTEXT_INVALID") from exc
        if (
            not isinstance(decoded, dict)
            or market_context.market != snapshot.market
            or market_context.symbol != snapshot.symbol
            or market_context.quality != "NORMAL"
            or canonical_input_json(decoded) != market_context.payload_json
            or input_digest(market_context.payload_json) != market_context.payload_hash
        ):
            raise ValueError("V7_SCOUT_INPUT_MARKET_CONTEXT_INVALID")
        market_context_payload = decoded
        validity.append(_utc(market_context.valid_until))
        market_context_provenance = {
            "observed_at": _utc(market_context.observed_at).isoformat(),
            "payload_hash": market_context.payload_hash,
            "quality": market_context.quality,
            "received_at": _utc(market_context.received_at).isoformat(),
            "schema_version": decoded.get("schema_version"),
            "snapshot_id": market_context.id,
            "valid_until": _utc(market_context.valid_until).isoformat(),
        }
    valid_until = min(validity)
    if valid_until <= observed:
        raise ValueError("V7_SCOUT_INPUT_EXPIRED")

    indicator_payload = _indicator_payload(indicator)
    indicator_hash = input_digest(canonical_input_json(indicator_payload))
    configuration_values = {
        "dart_lookback_days": dart_lookback_days,
        "krx_lookback_days": krx_lookback_days,
        "naver_news_lookback_hours": naver_news_lookback_hours,
        "quote_stale_seconds": quote_stale_seconds,
    }
    configuration_hash = input_digest(canonical_input_json(configuration_values))
    age_seconds = max((observed - received_at).total_seconds(), 0)
    payload: dict[str, object] = {
        "schema_version": V2_INPUT_SCHEMA_VERSION,
        "user_id": user_id,
        "purpose": purpose,
        "analysis_context": "ENTRY",
        "snapshot_id": snapshot.id,
        "market": snapshot.market,
        "symbol": snapshot.symbol,
        "observed_at": observed.isoformat(),
        "valid_until": valid_until.isoformat(),
        "data_quality": {
            "age_seconds": f"{age_seconds:.3f}",
            "snapshot": snapshot.quality,
            "stream": state.quality,
        },
        "session_state": snapshot.trading_status,
        "quote": {
            "best_ask_price": _decimal(snapshot.best_ask_price),
            "best_ask_quantity": snapshot.best_ask_quantity,
            "best_bid_price": _decimal(snapshot.best_bid_price),
            "best_bid_quantity": snapshot.best_bid_quantity,
            "cumulative_volume": snapshot.cumulative_volume,
            "event_at": _utc(snapshot.event_at).isoformat(),
            "high_price": _decimal(snapshot.high_price),
            "last_price": _decimal(snapshot.last_price),
            "low_price": _decimal(snapshot.low_price),
            "open_price": _decimal(snapshot.open_price),
            "received_at": received_at.isoformat(),
        },
        "indicators": indicator_payload,
        "position": None,
        "open_orders": [],
        "account_risk_summary": None,
        "market_context": market_context_payload,
        "strategy": {"quote_stale_seconds": quote_stale_seconds},
        "configuration_version": {
            "payload_hash": configuration_hash,
            "schema_version": V2_CONFIGURATION_SCHEMA_VERSION,
            "values": configuration_values,
        },
        "prior_decision_summary": None,
        "server_input_policy_version": V2_SERVER_INPUT_POLICY_VERSION,
        "market_snapshot_provenance": {
            "event_at": _utc(snapshot.event_at).isoformat(),
            "payload_hash": snapshot.payload_hash,
            "received_at": received_at.isoformat(),
            "snapshot_id": snapshot.id,
            "source": snapshot.source,
            "valid_until": market_valid_until.isoformat(),
        },
        "indicator_provenance": {
            "calculator_version": indicator.calculator_version,
            "input_end_at": _utc(indicator.input_end_at).isoformat(),
            "input_start_at": _utc(indicator.input_start_at).isoformat(),
            "payload_hash": indicator_hash,
            "snapshot_id": indicator.id,
            "valid_until": indicator_valid_until.isoformat(),
        },
        "market_context_provenance": market_context_provenance,
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
        if existing.input_json != encoded or existing.schema_version != V2_INPUT_SCHEMA_VERSION:
            raise ValueError("DECISION_INPUT_HASH_CONFLICT")
        return existing, payload
    decision_input = DecisionInputSnapshot(
        user_id=user_id,
        purpose=purpose,
        schema_version=V2_INPUT_SCHEMA_VERSION,
        market=snapshot.market,
        symbol=snapshot.symbol,
        market_snapshot_id=snapshot.id,
        indicator_snapshot_id=indicator.id,
        observed_at=observed,
        data_quality="NORMAL",
        session_state=snapshot.trading_status,
        input_json=encoded,
        input_hash=digest,
    )
    db.add(decision_input)
    db.flush()
    return decision_input, payload
