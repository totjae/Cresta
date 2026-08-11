from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
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
    """Persist one idempotent SHADOW execution; never create approval or order rows."""
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
