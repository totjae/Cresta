from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    GuardEvaluation,
    MarketSnapshot,
    MarketStreamState,
    Position,
    StopTrigger,
    TradingGate,
    TradingOrder,
)
from app.risk_events import (
    RISK_EVENT_SCOPE_FIXED_STOP,
    create_risk_event,
    resolve_risk_event,
)
from app.risk_policy import active_risk_policy, risk_policy_payload
from app.schemas import RiskPolicyPayload
from app.venue_selection import classify_session

KST = ZoneInfo("Asia/Seoul")
ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"
PRICE_QUANTUM = Decimal("0.0001")

# Sell orders that reserve position quantity (mirror trading/paper.py).
ACTIVE_SELL_STATES = {
    "CREATED",
    "VALIDATING",
    "SUBMITTING",
    "ACKNOWLEDGED",
    "OPEN",
    "PARTIALLY_FILLED",
    "CANCEL_PENDING",
    "REPLACE_PENDING",
    "UNKNOWN",
    "RECONCILING",
}

# Active/uncertain orders that block new orders on a symbol (EXE-064).
ACTIVE_SYMBOL_LOCK_STATES = {"UNKNOWN", "RECONCILING"}
BLOCKING_ORDER_STATES = {
    "CREATED",
    "VALIDATING",
    "SUBMITTING",
    "ACKNOWLEDGED",
    "OPEN",
    "PARTIALLY_FILLED",
    "CANCEL_PENDING",
    "REPLACE_PENDING",
    "UNKNOWN",
    "RECONCILING",
}

STOP_POLICY_VERSION = "fixed-stop-v1"
# Sessions where a KRX sell order can be placed (not auctions, not closed).
TRADABLE_SESSIONS = {
    "KRX_ONLY",
    "DUAL_CONTINUOUS",
}


class StopTriggerError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StopQuote:
    snapshot_id: str
    bid_price: Decimal | None
    trading_status: str
    quality: str
    event_at: datetime


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def compute_stop_price(average_price: Decimal, stop_loss_pct: Decimal) -> Decimal:
    """Stop price below average cost by the configured loss percentage.

    ``stop_loss_pct`` is negative (e.g. -2.0 means -2%). The stop price is
    ``average_price * (1 + pct/100)`` quantized to the price quantum.
    """
    multiplier = Decimal(1) + (stop_loss_pct / Decimal(100))
    raw = average_price * multiplier
    return raw.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def should_trigger(bid_price: Decimal | None, stop_price: Decimal) -> bool:
    """Fire when the best bid is at or below the stop price (boundary inclusive)."""
    if bid_price is None or bid_price <= 0:
        return False
    return bid_price <= stop_price


def _latest_krx_snapshot(db: Session, symbol: str) -> MarketSnapshot | None:
    stream = db.get(MarketStreamState, ("KRX", symbol))
    if stream is None or stream.current_snapshot_id is None:
        return None
    return db.get(MarketSnapshot, stream.current_snapshot_id)


def _sell_quantity_available(
    db: Session, account_alias: str, symbol: str, position_quantity: int
) -> int:
    reserved = db.scalar(
        select(func.coalesce(func.sum(TradingOrder.remaining_quantity), 0)).where(
            TradingOrder.account_alias == account_alias,
            TradingOrder.symbol == symbol,
            TradingOrder.side == "SELL",
            TradingOrder.status.in_(ACTIVE_SELL_STATES),
        )
    )
    return position_quantity - int(reserved or 0)


def _has_blocking_order(db: Session, account_alias: str, symbol: str) -> bool:
    existing = db.scalar(
        select(TradingOrder.id).where(
            TradingOrder.account_alias == account_alias,
            TradingOrder.symbol == symbol,
            TradingOrder.status.in_(BLOCKING_ORDER_STATES),
        )
    )
    return existing is not None


def _rule(code: str, passed: bool) -> dict[str, object]:
    return {"code": code, "result": "PASSED" if passed else "BLOCKED"}


def _sell_guard_rules(
    db: Session,
    *,
    position: Position,
    trigger: StopTrigger,
    snapshot: MarketSnapshot | None,
    settings: Settings,
    risk_policy: RiskPolicyPayload,
    now: datetime,
) -> list[dict[str, object]]:
    gate = db.get(TradingGate, trigger.account_alias)
    sellable = _sell_quantity_available(
        db, trigger.account_alias, trigger.symbol, position.quantity
    )
    blocking_order = _has_blocking_order(db, trigger.account_alias, trigger.symbol)
    fresh = bool(
        snapshot is not None
        and snapshot.quality == "NORMAL"
        and snapshot.trading_status == "TRADING"
        and (_utc(snapshot.received_at) - now).total_seconds() <= 0
        and (now - _utc(snapshot.received_at)).total_seconds()
        <= risk_policy.quote_stale_seconds
    )
    session = classify_session(now)
    return [
        _rule("POSITION_FOUND", position.state == "OPEN" and position.quantity > 0),
        _rule(
            "POSITION_VERSION_MATCH",
            position.version == trigger.position_version,
        ),
        _rule(
            "SELL_QUANTITY_AVAILABLE",
            sellable >= position.quantity,
        ),
        _rule(
            "BROKER_READY",
            gate is not None and gate.status == "READY",
        ),
        _rule(
            "NOT_RECONCILING",
            gate is not None and gate.status != "RECONCILING",
        ),
        _rule("NO_ACTIVE_OR_UNKNOWN_ORDER", not blocking_order),
        _rule("MARKET_DATA_FRESH", fresh),
        _rule(
            "INSTRUMENT_TRADABLE",
            snapshot is not None
            and snapshot.trading_status == "TRADING"
            and snapshot.quality == "NORMAL",
        ),
        _rule("MARKET_SESSION_TRADABLE", session in TRADABLE_SESSIONS),
        _rule("ENVIRONMENT_NOT_MOCK", settings.environment.upper() == "MOCK"),
    ]


def _trigger_input_record(
    *,
    position: Position,
    risk_policy_version_id: str | None,
    risk_policy: RiskPolicyPayload,
    stop_price: Decimal,
    quote: StopQuote | None,
    now: datetime,
) -> dict[str, object]:
    return {
        "schema_version": "fixed-stop-input-v1",
        "policy_version": STOP_POLICY_VERSION,
        "account_alias": ACCOUNT_ALIAS,
        "position_id": position.id,
        "position_version": position.version,
        "symbol": position.symbol,
        "average_price": str(position.average_price),
        "quantity": position.quantity,
        "stop_loss_pct": str(risk_policy.fixed_stop_loss_pct),
        "risk_policy_version_id": risk_policy_version_id,
        "stop_price": str(stop_price),
        "evaluated_at": _utc(now).isoformat(),
        "snapshot_id": quote.snapshot_id if quote else None,
        "bid_price": str(quote.bid_price) if quote and quote.bid_price is not None else None,
    }


def _persist_guard_evaluation(
    db: Session,
    *,
    trigger: StopTrigger,
    rules: list[dict[str, object]],
    snapshot: MarketSnapshot | None,
    risk_policy_version_id: str | None,
    now: datetime,
) -> GuardEvaluation:
    blocked = [item for item in rules if item["result"] == "BLOCKED"]
    guard = GuardEvaluation(
        execution_id=trigger.id,
        phase="PRE_ORDER",
        subject_type="STOP_TRIGGER",
        subject_id=trigger.id,
        result="BLOCKED" if blocked else "PASSED",
        rule_results_json=json.dumps(
            rules, separators=(",", ":"), sort_keys=True
        ),
        halt_scope="ENTRY_HALT" if blocked else None,
        snapshot_id=snapshot.id if snapshot else None,
        position_version=trigger.position_version,
        execution_policy_version_id=None,
        risk_policy_version_id=risk_policy_version_id,
        evaluated_at=now,
        valid_until=None,
    )
    db.add(guard)
    db.flush()
    return guard


def _existing_active_trigger(
    db: Session,
    *,
    position_id: str,
    position_version: int,
    risk_policy_version_id: str | None,
) -> StopTrigger | None:
    return db.scalar(
        select(StopTrigger).where(
            StopTrigger.position_id == position_id,
            StopTrigger.position_version == position_version,
            StopTrigger.risk_policy_version_id == risk_policy_version_id,
            StopTrigger.state.in_(
                ("PENDING", "SHADOW_RECORDED", "EXIT_PENDING")
            ),
        )
    )


def _supersede(trigger: StopTrigger) -> None:
    trigger.state = "SUPERSEDED"
    trigger.updated_at = datetime.now(UTC)


def _supersede_stale_triggers(
    db: Session,
    *,
    position_id: str,
    risk_policy_version_id: str | None,
    now: datetime | None = None,
) -> None:
    """Mark active triggers for this position+policy as SUPERSEDED.

    Called when the position version has advanced (fill/cancel) so a fresh
    evaluation can create a new trigger bound to the current version. Resolves
    any associated risk event so a stale EXIT_PENDING does not linger.
    """
    stale = list(
        db.scalars(
            select(StopTrigger).where(
                StopTrigger.position_id == position_id,
                StopTrigger.risk_policy_version_id == risk_policy_version_id,
                StopTrigger.state.in_(
                    ("PENDING", "SHADOW_RECORDED", "EXIT_PENDING")
                ),
            )
        )
    )
    resolved_at = now or datetime.now(UTC)
    for trigger in stale:
        _supersede(trigger)
        if trigger.risk_event_id:
            resolve_risk_event(
                db, trigger.risk_event_id, resolution="SUPERSEDED", now=resolved_at
            )


def _evaluate_position(
    db: Session,
    *,
    position: Position,
    settings: Settings,
    now: datetime,
    correlation_id: str,
) -> None:
    """Evaluate one open position for a fixed stop trigger.

    Persists a ``StopTrigger`` + ``GuardEvaluation`` (+ ``RiskEvent`` on block)
    but never creates an ``OrderIntent``/``TradingOrder``/``Decision``/``Approval``.
    """
    risk_config = active_risk_policy(db, ACCOUNT_ALIAS)
    risk_policy = risk_policy_payload(risk_config)
    risk_policy_version_id = risk_config.id if risk_config else None

    snapshot = _latest_krx_snapshot(db, position.symbol)
    quote = None
    if snapshot is not None:
        quote = StopQuote(
            snapshot_id=snapshot.id,
            bid_price=snapshot.best_bid_price,
            trading_status=snapshot.trading_status,
            quality=snapshot.quality,
            event_at=snapshot.event_at,
        )

    stop_price = compute_stop_price(
        position.average_price, risk_policy.fixed_stop_loss_pct
    )
    input_record = _trigger_input_record(
        position=position,
        risk_policy_version_id=risk_policy_version_id,
        risk_policy=risk_policy,
        stop_price=stop_price,
        quote=quote,
        now=now,
    )

    existing = _existing_active_trigger(
        db,
        position_id=position.id,
        position_version=position.version,
        risk_policy_version_id=risk_policy_version_id,
    )

    if existing is not None:
        # Re-evaluate in place; never create a duplicate trigger.
        _re_evaluate_trigger(
            db,
            trigger=existing,
            position=position,
            snapshot=snapshot,
            settings=settings,
            risk_policy=risk_policy,
            risk_policy_version_id=risk_policy_version_id,
            stop_price=stop_price,
            now=now,
        )
        return

    # Supersede any active triggers for this position bound to an older
    # position version (a fill/cancel bumped the version). The new evaluation
    # proceeds against the fresh position state.
    _supersede_stale_triggers(
        db, position_id=position.id, risk_policy_version_id=risk_policy_version_id
    )

    if not should_trigger(quote.bid_price if quote else None, stop_price):
        # Stop not reached; no trigger row created.
        return

    trigger = StopTrigger(
        account_alias=ACCOUNT_ALIAS,
        position_id=position.id,
        position_version=position.version,
        symbol=position.symbol,
        market="KRX",
        risk_policy_version_id=risk_policy_version_id,
        stop_price=stop_price,
        trigger_price=quote.bid_price if quote else None,
        snapshot_id=snapshot.id if snapshot else None,
        state="PENDING",
        correlation_id=correlation_id,
    )
    db.add(trigger)
    db.flush()

    rules = _sell_guard_rules(
        db,
        position=position,
        trigger=trigger,
        snapshot=snapshot,
        settings=settings,
        risk_policy=risk_policy,
        now=now,
    )
    guard = _persist_guard_evaluation(
        db,
        trigger=trigger,
        rules=rules,
        snapshot=snapshot,
        risk_policy_version_id=risk_policy_version_id,
        now=now,
    )
    trigger.guard_evaluation_id = guard.id
    blocked = guard.result == "BLOCKED"

    if blocked:
        rule_code = str(
            next((r["code"] for r in rules if r["result"] == "BLOCKED"), "BLOCKED"
            )
        )
        event = create_risk_event(
            db,
            scope=RISK_EVENT_SCOPE_FIXED_STOP,
            rule_code=rule_code,
            severity="HIGH",
            account_alias=ACCOUNT_ALIAS,
            symbol=position.symbol,
            input_record=input_record,
            correlation_id=correlation_id,
            input_snapshot_id=snapshot.id if snapshot else None,
            now=now,
        )
        trigger.risk_event_id = event.id
        trigger.state = "EXIT_PENDING"
        trigger.result_code = rule_code
    else:
        trigger.state = "SHADOW_RECORDED"
        trigger.result_code = None
    trigger.updated_at = now
    db.flush()


def _re_evaluate_trigger(
    db: Session,
    *,
    trigger: StopTrigger,
    position: Position,
    snapshot: MarketSnapshot | None,
    settings: Settings,
    risk_policy: RiskPolicyPayload,
    risk_policy_version_id: str | None,
    stop_price: Decimal,
    now: datetime,
) -> None:
    """Re-evaluate an existing active trigger with the latest state.

    Keeps the trigger row (idempotent) and transitions between SHADOW_RECORDED
    and EXIT_PENDING as conditions change. Resolves any associated risk event
    when the trigger passes, so EXIT_PENDING does not linger after recovery.
    """
    if position.state != "OPEN" or position.quantity <= 0:
        _supersede(trigger)
        if trigger.risk_event_id:
            resolve_risk_event(
                db, trigger.risk_event_id, resolution="POSITION_CLOSED", now=now
            )
        return

    rules = _sell_guard_rules(
        db,
        position=position,
        trigger=trigger,
        snapshot=snapshot,
        settings=settings,
        risk_policy=risk_policy,
        now=now,
    )
    guard = _persist_guard_evaluation(
        db,
        trigger=trigger,
        rules=rules,
        snapshot=snapshot,
        risk_policy_version_id=risk_policy_version_id,
        now=now,
    )
    trigger.guard_evaluation_id = guard.id
    blocked = guard.result == "BLOCKED"

    if blocked:
        rule_code = str(
            next(
                (r["code"] for r in rules if r["result"] == "BLOCKED"), "BLOCKED"
            )
        )
        if trigger.risk_event_id is None:
            event = create_risk_event(
                db,
                scope=RISK_EVENT_SCOPE_FIXED_STOP,
                rule_code=rule_code,
                severity="HIGH",
                account_alias=trigger.account_alias,
                symbol=trigger.symbol,
                input_record={
                    "position_id": position.id,
                    "position_version": position.version,
                    "stop_price": str(stop_price),
                    "re_evaluated_at": _utc(now).isoformat(),
                    "rule_code": rule_code,
                },
                correlation_id=trigger.correlation_id,
                input_snapshot_id=snapshot.id if snapshot else None,
                now=now,
            )
            trigger.risk_event_id = event.id
        trigger.state = "EXIT_PENDING"
        trigger.result_code = rule_code
    else:
        if trigger.risk_event_id:
            resolve_risk_event(
                db, trigger.risk_event_id, resolution="BROKER_RECOVERED", now=now
            )
        trigger.state = "SHADOW_RECORDED"
        trigger.result_code = None
    trigger.updated_at = now
    db.flush()


def run_fixed_stop_triggers(
    db: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
    correlation_id: str | None = None,
    account_alias: str = ACCOUNT_ALIAS,
) -> int:
    """Evaluate all open positions for the fixed stop trigger.

    Returns the number of positions evaluated. The caller (broker worker) is
    responsible for lease ownership; this function evaluates every open position
    so that availability blocks (BROKER_NOT_READY, RECONCILING, stale data) are
    recorded as ``EXIT_PENDING`` rather than silently skipped — a stop signal must
    persist across data gaps and broker outages (GRD-085, EXE-053). Never creates
    orders, approvals, intents, or decisions.
    """
    positions = list(
        db.scalars(
            select(Position).where(
                Position.account_alias == account_alias,
                Position.state == "OPEN",
                Position.quantity > 0,
            )
        )
    )
    if not positions:
        return 0

    evaluated_at = now or datetime.now(UTC)
    for position in positions:
        _evaluate_position(
            db,
            position=position,
            settings=settings,
            now=evaluated_at,
            correlation_id=correlation_id or f"stop-{evaluated_at.isoformat()}",
        )
    db.commit()
    return len(positions)


def recover_exit_pending(
    db: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
    account_alias: str = ACCOUNT_ALIAS,
) -> int:
    """Re-evaluate ``EXIT_PENDING`` triggers after broker/market recovery.

    Called on each worker tick when the gate is READY so a data gap or broker
    outage does not erase a stop signal (EXE-053, ORD-023).
    """
    gate = db.get(TradingGate, account_alias)
    if gate is None or gate.status != "READY":
        return 0

    triggers = list(
        db.scalars(
            select(StopTrigger).where(
                StopTrigger.account_alias == account_alias,
                StopTrigger.state == "EXIT_PENDING",
            )
        )
    )
    if not triggers:
        return 0

    evaluated_at = now or datetime.now(UTC)
    risk_config = active_risk_policy(db, account_alias)
    risk_policy = risk_policy_payload(risk_config)
    risk_policy_version_id = risk_config.id if risk_config else None

    for trigger in triggers:
        position = db.get(Position, trigger.position_id)
        if position is None or position.state != "OPEN" or position.quantity <= 0:
            _supersede(trigger)
            if trigger.risk_event_id:
                resolve_risk_event(
                    db,
                    trigger.risk_event_id,
                    resolution="POSITION_CLOSED",
                    now=evaluated_at,
                )
            continue
        snapshot = _latest_krx_snapshot(db, trigger.symbol)
        stop_price = compute_stop_price(
            position.average_price, risk_policy.fixed_stop_loss_pct
        )
        _re_evaluate_trigger(
            db,
            trigger=trigger,
            position=position,
            snapshot=snapshot,
            settings=settings,
            risk_policy=risk_policy,
            risk_policy_version_id=risk_policy_version_id,
            stop_price=stop_price,
            now=evaluated_at,
        )
    db.commit()
    return len(triggers)
