from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
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
    TradingGate,
    User,
    WatchlistItem,
)
from app.risk_policy import active_risk_policy, risk_policy_payload
from app.schemas import RiskPolicyPayload

ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"
NO_ACTIONS = {"WAIT", "REJECT", "RISK_BLOCK", "HOLD"}
SUPPORTED_ACTIONS = {"BUY", "PARTIAL_SELL", "FULL_SELL", "FIXED_STOP"}


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


def _buy_guard_rules(
    db: Session,
    decision: Decision,
    user: User,
    settings: Settings,
    risk_policy: RiskPolicyPayload,
    now: datetime,
) -> list[dict[str, object]]:
    snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
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
    stage a BUY decision whose hard Guard passes either creates a ``PENDING``
    approval (``MANUAL_APPROVAL`` mode) or, for ``AUTOMATIC`` mode, creates the
    CREATED order directly via the shared order creation service. Sell actions
    remain rule-triggered (FIXED_STOP) rather than decision-driven; non-BUY
    supported actions stay ``ACTION_NOT_IMPLEMENTED`` until the take-profit
    milestone wires them through the same service.
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
            rules = _buy_guard_rules(db, decision, user, settings, risk_policy, current)
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
        elif action == "BUY" and settings.execution_stage == "APPROVAL_ONLY":
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
                # create_approval commits and sets execution.state APPROVAL_PENDING.
            elif mode == "AUTOMATIC":
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
