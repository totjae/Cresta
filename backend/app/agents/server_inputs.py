from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import MarketSnapshot, Position, User
from app.risk_policy import active_risk_policy, risk_policy_payload

SERVER_INPUT_POLICY_VERSION = "agent-server-input-v1"
POSITION_CALCULATION_VERSION = "position-risk-input-v1"
_FOUR_DP = Decimal("0.0001")
_SIX_DP = Decimal("0.000001")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _decimal(value: Decimal, places: Decimal = _SIX_DP) -> str:
    return format(value.quantize(places, rounding=ROUND_HALF_UP), "f")


def _policy_hash(payload: object) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_position_snapshot(
    db: Session,
    *,
    user: User,
    position: Position,
    market_snapshot: MarketSnapshot,
    settings: Settings,
) -> dict[str, object]:
    policy_version = active_risk_policy(db, user.id)
    policy = risk_policy_payload(policy_version)
    policy_payload = policy.model_dump(mode="json")
    policy_hash = (
        policy_version.payload_hash if policy_version else _policy_hash(policy_payload)
    )
    quantity = position.quantity
    average_price = Decimal(position.average_price)
    last_price = Decimal(market_snapshot.last_price)
    session_high = Decimal(market_snapshot.high_price)
    cost_basis = average_price * quantity
    market_value = last_price * quantity
    unrealized_pnl = market_value - cost_basis
    unrealized_return_pct = (
        unrealized_pnl / cost_basis * Decimal(100) if cost_basis > 0 else Decimal(0)
    )
    drawdown_pct = (
        (last_price / session_high - Decimal(1)) * Decimal(100)
        if session_high > 0
        else Decimal(0)
    )
    stop_price = average_price * (
        Decimal(1) + Decimal(policy.fixed_stop_loss_pct) / Decimal(100)
    )
    distance_to_stop_pct = (
        (last_price - stop_price) / last_price * Decimal(100)
        if last_price > 0
        else Decimal(0)
    )
    observed_at = _aware(market_snapshot.event_at)
    position_updated_at = _aware(position.updated_at)
    tracked_since_at = _aware(position.created_at)
    age_seconds = max(0, int((observed_at - position_updated_at).total_seconds()))
    tracked_seconds = max(0, int((observed_at - tracked_since_at).total_seconds()))
    stale_threshold_seconds = max(
        120, settings.kiwoom_reconcile_interval_seconds * 2
    )
    return {
        "marker": "OPEN_POSITION",
        "calculation_version": POSITION_CALCULATION_VERSION,
        "position_id": position.id,
        "account_alias": position.account_alias,
        "symbol": position.symbol,
        "quantity": quantity,
        "remaining_quantity": quantity,
        "average_price": _decimal(average_price, _FOUR_DP),
        "current_price": _decimal(last_price, _FOUR_DP),
        "cost_basis_amount": _decimal(cost_basis, _FOUR_DP),
        "market_value_amount": _decimal(market_value, _FOUR_DP),
        "unrealized_pnl_amount": _decimal(unrealized_pnl, _FOUR_DP),
        "unrealized_return_pct": _decimal(unrealized_return_pct),
        "session_high_price": _decimal(session_high, _FOUR_DP),
        "drawdown_from_session_high_pct": _decimal(drawdown_pct),
        "fixed_stop_loss_pct": _decimal(Decimal(policy.fixed_stop_loss_pct)),
        "fixed_stop_price": _decimal(stop_price, _FOUR_DP),
        "distance_to_fixed_stop_pct": _decimal(distance_to_stop_pct),
        "tracked_since_at": tracked_since_at.isoformat(),
        "tracked_duration_seconds": tracked_seconds,
        "position_observed_at": position_updated_at.isoformat(),
        "market_observed_at": observed_at.isoformat(),
        "freshness": {
            "status": "STALE" if age_seconds > stale_threshold_seconds else "FRESH",
            "age_seconds": age_seconds,
            "stale_threshold_seconds": stale_threshold_seconds,
        },
        "risk_policy": {
            "source": "ACTIVE" if policy_version else "SAFE_DEFAULT",
            "version_id": policy_version.id if policy_version else None,
            "payload_hash": policy_hash,
        },
        "source_refs": [market_snapshot.id, position.id],
        "state": position.state,
        "version": position.version,
    }
