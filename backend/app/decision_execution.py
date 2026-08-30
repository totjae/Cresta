from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.approvals import create_approval
from app.config import Settings
from app.emergency_stop import active_pause_entry
from app.execution_policy import active_policy, policy_payload
from app.models import (
    AuditLog,
    Decision,
    DecisionExecution,
    GuardEvaluation,
    MarketSnapshot,
    MarketStreamState,
    Position,
    TradingGate,
    TradingOrder,
    User,
    WatchlistItem,
)
from app.risk_calc import (
    broker_connection_ok,
    consecutive_loss_count,
    daily_entry_count,
    daily_loss_pct,
    open_position_count,
    open_position_exposure,
    spread_pct,
)
from app.risk_events import RISK_EVENT_SCOPE_DAILY_LOSS, active_risk_events
from app.risk_policy import active_risk_policy, risk_policy_payload
from app.schemas import RiskPolicyPayload
from app.venue_selection import classify_session

ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"
NO_ACTIONS = {"WAIT", "REJECT", "UNKNOWN", "RISK_BLOCK", "HOLD"}
SUPPORTED_ACTIONS = {"BUY", "PARTIAL_SELL", "FULL_SELL", "FIXED_STOP"}
SELL_ACTIONS = {"PARTIAL_SELL", "FULL_SELL"}
ACTIVE_ORDER_STATES = {
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
TRADABLE_SESSIONS_BY_MARKET = {
    "KRX": {"KRX_ONLY", "DUAL_CONTINUOUS"},
    "NXT": {"NXT_PRE", "DUAL_CONTINUOUS", "NXT_AFTER"},
}
ORDER_ENABLED_STAGES = {"APPROVAL_ONLY", "MOCK_AUTOMATIC"}


@dataclass(frozen=True)
class SellPlan:
    position: Position | None
    snapshot: MarketSnapshot | None
    sellable_quantity: int
    quantity: int
    price: Decimal | None
    sell_ratio: Decimal | None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _mode_for(action: str, policy: object) -> str:
    mapping = {
        "BUY": "buy",
        "PARTIAL_SELL": "partial_sell",
        "FULL_SELL": "full_sell",
        "FIXED_STOP": "fixed_stop_loss",
    }
    field = mapping.get(action)
    return str(getattr(policy, field)) if field else "DISABLED"


def _key(
    decision: Decision,
    action: str,
    policy_version_id: str | None,
    risk_policy_version_id: str | None,
) -> str:
    raw = (
        f"{decision.id}:{action}:{policy_version_id or 'SAFE_DEFAULT'}:"
        f"{risk_policy_version_id or 'SAFE_RISK_DEFAULT'}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _rule(code: str, passed: bool) -> dict[str, object]:
    return {"code": code, "result": "PASSED" if passed else "BLOCKED"}


def buy_pre_order_guard_rules(
    db: Session,
    decision: Decision,
    user: User,
    settings: Settings,
    risk_policy: RiskPolicyPayload,
    now: datetime,
    *,
    snapshot: MarketSnapshot | None,
) -> list[dict[str, object]]:
    stream = db.get(MarketStreamState, (decision.market, decision.symbol))
    watched = db.scalar(
        select(WatchlistItem.id).where(
            WatchlistItem.user_id == user.id,
            WatchlistItem.market == decision.market,
            WatchlistItem.symbol == decision.symbol,
        )
    )
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    emergency_stop = active_pause_entry(db, ACCOUNT_ALIAS)
    fresh = bool(
        snapshot
        and stream
        and stream.current_snapshot_id == snapshot.id
        and snapshot.quality == "NORMAL"
        and stream.quality == "NORMAL"
        and (now - _utc(snapshot.received_at)).total_seconds()
        <= risk_policy.quote_stale_seconds
    )
    entry_amount = Decimal(risk_policy.entry_order_amount) if risk_policy.entry_order_amount else Decimal(0)
    per_symbol, total_exposure = open_position_exposure(db, ACCOUNT_ALIAS, now=now)
    symbol_exposure = per_symbol.get(decision.symbol, Decimal(0))
    open_positions = open_position_count(db, ACCOUNT_ALIAS)
    entries_today = daily_entry_count(db, ACCOUNT_ALIAS, now=now)
    loss_pct = daily_loss_pct(
        db,
        ACCOUNT_ALIAS,
        basis=risk_policy.daily_loss_basis,
        now=now,
        denominator=Decimal(risk_policy.max_total_position_amount),
    )
    consecutive_losses = consecutive_loss_count(db, ACCOUNT_ALIAS)
    snapshot_spread = spread_pct(snapshot)
    connection_ok, _ = broker_connection_ok(db, ACCOUNT_ALIAS, now=now)
    active_daily_loss_events = active_risk_events(db, scope=RISK_EVENT_SCOPE_DAILY_LOSS, account_alias=ACCOUNT_ALIAS)
    return [
        _rule("ENVIRONMENT_NOT_MOCK", settings.environment.upper() == "MOCK"),
        _rule("DECISION_EXPIRED", now <= _utc(decision.valid_until)),
        _rule("SNAPSHOT_MISSING", snapshot is not None),
        _rule("MARKET_DATA_STALE", fresh),
        _rule("SYMBOL_NOT_WATCHED", watched is not None),
        _rule("BROKER_NOT_READY", gate is not None and gate.status == "READY"),
        _rule("EMERGENCY_STOP_ACTIVE", emergency_stop is None),
        _rule(
            "ORDER_SIZE_NOT_CONFIGURED",
            risk_policy.entry_order_amount is not None,
        ),
        # Full Risk Guard (#2): exposure, entries, daily loss, spread, connection.
        _rule(
            "TOTAL_EXPOSURE_LIMIT",
            total_exposure + entry_amount <= Decimal(risk_policy.max_total_position_amount),
        ),
        _rule(
            "SYMBOL_EXPOSURE_LIMIT",
            symbol_exposure + entry_amount <= Decimal(risk_policy.max_position_amount_per_symbol),
        ),
        _rule("OPEN_POSITIONS_LIMIT", open_positions < risk_policy.max_open_positions),
        _rule("DAILY_ENTRIES_LIMIT", entries_today < risk_policy.max_daily_entries),
        _rule("DAILY_LOSS_LIMIT", loss_pct < risk_policy.daily_loss_limit_pct),
        _rule(
            "CONSECUTIVE_LOSS_LIMIT",
            consecutive_losses < risk_policy.max_consecutive_losses,
        ),
        _rule(
            "SPREAD_LIMIT",
            snapshot_spread is not None and snapshot_spread <= risk_policy.max_spread_pct,
        ),
        _rule("BROKER_CONNECTION_OK", connection_ok),
        _rule("NO_ACTIVE_DAILY_LOSS_EVENT", not active_daily_loss_events),
    ]


# Compatibility alias for the existing Approval revalidation path. New callers
# use the public name; legacy semantics remain unchanged.
_buy_guard_rules = buy_pre_order_guard_rules


def _sell_ratio(decision: Decision) -> Decimal | None:
    if decision.action != "PARTIAL_SELL":
        return None
    try:
        value = json.loads(decision.core_output_json).get("sell_ratio")
        ratio = Decimal(str(value))
    except (AttributeError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError):
        return None
    return ratio if Decimal("0.01") <= ratio <= Decimal("1.0") else None


def _sellable_managed_quantity(db: Session, position: Position | None) -> int:
    if position is None:
        return 0
    reserved = int(
        db.scalar(
            select(func.coalesce(func.sum(TradingOrder.remaining_quantity), 0)).where(
                TradingOrder.account_alias == position.account_alias,
                TradingOrder.symbol == position.symbol,
                TradingOrder.side == "SELL",
                TradingOrder.status.in_(ACTIVE_ORDER_STATES),
            )
        )
        or 0
    )
    broker_sellable_managed = min(
        max(0, int(position.managed_quantity)),
        max(0, int(position.available_quantity)),
    )
    return max(0, broker_sellable_managed - reserved)


def _has_active_symbol_order(db: Session, decision: Decision) -> bool:
    return (
        db.scalar(
            select(TradingOrder.id).where(
                TradingOrder.account_alias == ACCOUNT_ALIAS,
                TradingOrder.symbol == decision.symbol,
                TradingOrder.status.in_(ACTIVE_ORDER_STATES),
            )
        )
        is not None
    )


def _position_for_sell(
    db: Session, decision: Decision, *, lock: bool = False
) -> Position | None:
    query = select(Position).where(
        Position.account_alias == ACCOUNT_ALIAS,
        Position.symbol == decision.symbol,
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def _sell_plan(
    db: Session,
    decision: Decision,
    *,
    snapshot: MarketSnapshot | None,
    lock_position: bool = False,
) -> SellPlan:
    position = _position_for_sell(db, decision, lock=lock_position)
    sellable = _sellable_managed_quantity(db, position)
    ratio = _sell_ratio(decision)
    if decision.action == "FULL_SELL":
        quantity = sellable
    elif ratio is not None:
        quantity = int(
            (Decimal(sellable) * ratio).to_integral_value(rounding=ROUND_DOWN)
        )
    else:
        quantity = 0
    price = snapshot.best_bid_price if snapshot is not None else None
    return SellPlan(
        position=position,
        snapshot=snapshot,
        sellable_quantity=sellable,
        quantity=quantity,
        price=price,
        sell_ratio=ratio,
    )


def _sell_guard_rules(
    db: Session,
    decision: Decision,
    settings: Settings,
    risk_policy: RiskPolicyPayload,
    now: datetime,
    *,
    plan: SellPlan,
    expected_position_id: str | None = None,
    expected_position_version: int | None = None,
    requested_quantity: int | None = None,
) -> list[dict[str, object]]:
    position = plan.position
    snapshot = plan.snapshot
    stream = db.get(MarketStreamState, (decision.market, decision.symbol))
    gate = db.get(TradingGate, ACCOUNT_ALIAS)
    quantity = plan.quantity if requested_quantity is None else requested_quantity
    fresh = bool(
        snapshot
        and stream
        and stream.current_snapshot_id == snapshot.id
        and snapshot.quality == "NORMAL"
        and stream.quality == "NORMAL"
        and snapshot.trading_status == "TRADING"
        and _utc(snapshot.received_at) <= now
        and (now - _utc(snapshot.received_at)).total_seconds()
        <= risk_policy.quote_stale_seconds
    )
    session = classify_session(now)
    return [
        _rule("ENVIRONMENT_NOT_MOCK", settings.environment.upper() == "MOCK"),
        _rule("DECISION_EXPIRED", now <= _utc(decision.valid_until)),
        _rule(
            "POSITION_FOUND",
            position is not None and position.state == "OPEN" and position.quantity > 0,
        ),
        _rule(
            "POSITION_ID_MATCH",
            expected_position_id is None
            or (position is not None and position.id == expected_position_id),
        ),
        _rule(
            "POSITION_VERSION_MATCH",
            expected_position_version is None
            or (position is not None and position.version == expected_position_version),
        ),
        _rule(
            "POSITION_MANAGED_QUANTITY_POSITIVE",
            position is not None and position.managed_quantity > 0,
        ),
        _rule(
            "SELL_RATIO_VALID",
            decision.action != "PARTIAL_SELL" or plan.sell_ratio is not None,
        ),
        _rule("NO_ACTIVE_OR_UNKNOWN_ORDER", not _has_active_symbol_order(db, decision)),
        _rule("QUANTITY_BELOW_ONE", quantity >= 1),
        _rule(
            "SELL_QUANTITY_AVAILABLE",
            quantity >= 1 and quantity <= plan.sellable_quantity,
        ),
        _rule("BROKER_READY", gate is not None and gate.status == "READY"),
        _rule(
            "NOT_RECONCILING", gate is not None and gate.status != "RECONCILING"
        ),
        _rule("MARKET_DATA_FRESH", fresh),
        _rule(
            "MARKETABLE_SELL_PRICE_AVAILABLE",
            plan.price is not None and plan.price > 0,
        ),
        _rule(
            "MARKET_SESSION_TRADABLE",
            session in TRADABLE_SESSIONS_BY_MARKET.get(decision.market, set()),
        ),
    ]


def route_trading_decision(
    db: Session,
    *,
    decision: Decision,
    user: User,
    correlation_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> DecisionExecution | None:
    """Route one idempotent TRADING decision through Guard to its terminal state.

    In the ``SHADOW`` stage no approval or order is ever created — the execution
    ends in ``SHADOW_RECORDED`` (or ``GUARD_BLOCKED``). In the ``APPROVAL_ONLY``
    stage a supported decision whose hard Guard passes either creates a
    ``PENDING`` approval (``MANUAL_APPROVAL`` mode) or, for ``AUTOMATIC`` mode,
    creates the CREATED order directly via the shared order creation service.
    Decision-driven SELL orders are limited to the Cresta-managed, currently
    broker-sellable portion of the position.
    """
    if decision.purpose != "TRADING":
        return None

    current = now or datetime.now(UTC)
    config = active_policy(db, user.id)
    policy = policy_payload(config)
    risk_config = active_risk_policy(db, user.id)
    risk_policy = risk_policy_payload(risk_config)
    action = decision.action
    normalized_action = "NO_ACTION" if action in NO_ACTIONS else action
    mode = _mode_for(action, policy)
    execution_key = _key(
        decision,
        normalized_action,
        config.id if config else None,
        risk_config.id if risk_config else None,
    )
    existing = db.scalar(
        select(DecisionExecution).where(DecisionExecution.execution_key == execution_key)
    )
    if existing is not None:
        return existing

    execution = DecisionExecution(
        execution_key=execution_key,
        decision_id=decision.id,
        user_id=user.id,
        account_alias=ACCOUNT_ALIAS,
        symbol=decision.symbol,
        market=decision.market,
        action=normalized_action,
        mode=mode,
        stage=settings.execution_stage,
        state="ROUTING",
        execution_policy_version_id=config.id if config else None,
        risk_policy_version_id=risk_config.id if risk_config else None,
        correlation_id=correlation_id,
    )
    db.add(execution)
    db.flush()

    guard: GuardEvaluation | None = None
    sell_plan: SellPlan | None = None
    if normalized_action == "NO_ACTION":
        execution.state = "NO_ACTION"
        execution.result_code = action
    elif mode == "DISABLED":
        execution.state = "DISABLED"
        execution.result_code = "ACTION_DISABLED"
    else:
        if action not in SUPPORTED_ACTIONS:
            rules = [_rule("ACTION_NOT_IMPLEMENTED", False)]
        elif action == "BUY":
            decision_snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
            rules = buy_pre_order_guard_rules(
                db,
                decision,
                user,
                settings,
                risk_policy,
                current,
                snapshot=decision_snapshot,
            )
        elif action in SELL_ACTIONS:
            decision_snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
            sell_plan = _sell_plan(
                db,
                decision,
                snapshot=decision_snapshot,
                lock_position=True,
            )
            rules = _sell_guard_rules(
                db,
                decision,
                settings,
                risk_policy,
                current,
                plan=sell_plan,
            )
        else:
            rules = [_rule("ACTION_NOT_IMPLEMENTED", False)]
        blocked = [item for item in rules if item["result"] == "BLOCKED"]
        guard = GuardEvaluation(
            execution_id=execution.id,
            phase="PRE_ORDER",
            subject_type="DECISION_EXECUTION",
            subject_id=execution.id,
            result="BLOCKED" if blocked else "PASSED",
            rule_results_json=json.dumps(rules, separators=(",", ":"), sort_keys=True),
            halt_scope="ENTRY_HALT" if blocked and action == "BUY" else None,
            snapshot_id=decision.input_snapshot_id,
            position_version=(
                sell_plan.position.version
                if sell_plan is not None and sell_plan.position is not None
                else None
            ),
            execution_policy_version_id=config.id if config else None,
            risk_policy_version_id=risk_config.id if risk_config else None,
            evaluated_at=current,
            valid_until=decision.valid_until,
        )
        db.add(guard)
        db.flush()
        execution.guard_evaluation_id = guard.id
        if blocked:
            execution.state = "GUARD_BLOCKED"
            execution.result_code = str(blocked[0]["code"])
        elif action in {"BUY", *SELL_ACTIONS} and settings.execution_stage in ORDER_ENABLED_STAGES:
            if mode == "MANUAL_APPROVAL":
                # Defer order creation to user approval; the approval service
                # re-runs the Guard and price-deviation check before creating
                # the CREATED order on approval.
                approval = create_approval(
                    db,
                    execution=execution,
                    decision=decision,
                    user=user,
                    settings=settings,
                    now=current,
                )
                execution.approval_id = approval.id
                # Caller owns the transaction; create_approval only flushes.
            elif (
                mode == "AUTOMATIC"
                and settings.execution_stage == "APPROVAL_ONLY"
                and action in {"BUY", *SELL_ACTIONS}
            ):
                execution.state = "FAILED_SAFE"
                execution.result_code = "AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY"
            elif mode == "AUTOMATIC" and action == "BUY":
                execution = _create_buy_order(
                    db,
                    execution=execution,
                    decision=decision,
                    user=user,
                    risk_policy=risk_policy,
                    settings=settings,
                    correlation_id=correlation_id,
                    current=current,
                )
            elif mode == "AUTOMATIC" and sell_plan is not None:
                execution = _create_sell_order(
                    db,
                    execution=execution,
                    decision=decision,
                    user=user,
                    plan=sell_plan,
                    correlation_id=correlation_id,
                    current=current,
                )
            else:
                execution.state = "SHADOW_RECORDED"
                execution.result_code = "SHADOW_ONLY"
        else:
            execution.state = "SHADOW_RECORDED"
            execution.result_code = "SHADOW_ONLY"

    execution.updated_at = current
    db.add(
        AuditLog(
            actor_type="SYSTEM",
            actor_id=user.id,
            action="DECISION_EXECUTION_ROUTED",
            target=execution.id,
            result=execution.state,
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {
                    "decision_id": decision.id,
                    "stage": settings.execution_stage,
                    "action": normalized_action,
                    "result_code": execution.result_code,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    )
    db.commit()
    db.refresh(execution)
    return execution


def _buy_quantity(entry_order_amount, price) -> int:
    """Whole-share quantity for an automatic BUY (mirrors approvals service)."""
    from decimal import ROUND_DOWN, Decimal

    if entry_order_amount is None or price is None or price <= 0:
        return 0
    return int((entry_order_amount / price).quantize(Decimal(1), rounding=ROUND_DOWN))


def _create_buy_order(
    db: Session,
    *,
    execution: DecisionExecution,
    decision: Decision,
    user: User,
    risk_policy: RiskPolicyPayload,
    settings: Settings,
    correlation_id: str,
    current: datetime,
) -> DecisionExecution:
    """Create the CREATED BUY order directly (AUTOMATIC mode, APPROVAL_ONLY stage)."""
    from app.approvals import _marketable_buy_price  # reuse the same pricing helper
    from app.order_creation import OrderCreationError, OrderRequest, create_order

    snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
    price = _marketable_buy_price(snapshot)
    quantity = _buy_quantity(risk_policy.entry_order_amount, price)
    if quantity <= 0 or price is None:
        execution.state = "GUARD_BLOCKED"
        execution.result_code = "ORDER_SIZE_NOT_CONFIGURED"
        return execution
    request = OrderRequest(
        symbol=decision.symbol,
        market=decision.market,
        side="BUY",
        action="BUY",
        order_type="LIMIT",
        limit_price=price,
        quantity=quantity,
        idempotency_key=f"auto-buy:{decision.id}",
        request_payload={
            "environment": "MOCK",
            "symbol": decision.symbol,
            "market": decision.market,
            "side": "BUY",
            "action": "BUY",
            "order_type": "LIMIT",
            "limit_price": str(price),
            "quantity": quantity,
            "decision_id": decision.id,
            "idempotency_key": f"auto-buy:{decision.id}",
        },
        correlation_id=correlation_id,
    )
    try:
        order = create_order(
            db,
            user=user,
            request=request,
            audit_action="AUTOMATIC_BUY_ORDER_CREATED",
            now=current,
        )
    except OrderCreationError as exc:
        execution.state = "FAILED_SAFE"
        execution.result_code = exc.code
        return execution
    execution.state = "ORDER_CREATED"
    execution.result_code = "ORDER_CREATED"
    execution.order_intent_id = order.intent_id
    return execution


def _create_sell_order(
    db: Session,
    *,
    execution: DecisionExecution,
    decision: Decision,
    user: User,
    plan: SellPlan,
    correlation_id: str,
    current: datetime,
) -> DecisionExecution:
    """Create one decision-driven SELL order after its Guard has passed."""
    from app.order_creation import OrderCreationError, OrderRequest, create_order

    if plan.position is None or plan.quantity <= 0 or plan.price is None:
        execution.state = "GUARD_BLOCKED"
        execution.result_code = "SELL_QUANTITY_AVAILABLE"
        return execution
    idempotency_key = f"auto-sell:{decision.id}:{decision.action}"
    request = OrderRequest(
        symbol=decision.symbol,
        market=decision.market,
        side="SELL",
        action=decision.action,
        order_type="LIMIT",
        limit_price=plan.price,
        quantity=plan.quantity,
        idempotency_key=idempotency_key,
        request_payload={
            "environment": "MOCK",
            "symbol": decision.symbol,
            "market": decision.market,
            "side": "SELL",
            "action": decision.action,
            "order_type": "LIMIT",
            "limit_price": str(plan.price),
            "quantity": plan.quantity,
            "decision_id": decision.id,
            "position_id": plan.position.id,
            "position_version": plan.position.version,
            "reference_snapshot_id": decision.input_snapshot_id,
            "idempotency_key": idempotency_key,
        },
        correlation_id=correlation_id,
    )
    try:
        order = create_order(
            db,
            user=user,
            request=request,
            audit_action="AUTOMATIC_DECISION_SELL_ORDER_CREATED",
            now=current,
        )
    except OrderCreationError as exc:
        execution.state = "FAILED_SAFE"
        execution.result_code = exc.code
        return execution
    execution.state = "ORDER_CREATED"
    execution.result_code = "ORDER_CREATED"
    execution.order_intent_id = order.intent_id
    return execution
