"""Approval lifecycle for MANUAL_APPROVAL BUY orders.

When ``decision_execution.route_trading_decision`` routes a BUY decision whose
execution mode is ``MANUAL_APPROVAL`` and the hard Guard passes, it creates an
``Approval`` (state ``PENDING``) bound to the ``DecisionExecution`` and stops
short of creating an order. The user then approves or rejects through the API;
on approval the Guard is re-evaluated against the *current* market snapshot and
risk policy, the price deviation is checked against ``max_price_deviation_pct``,
and only then is an ``OrderIntent`` + ``TradingOrder(CREATED)`` atomically
created via ``app.order_creation.create_order``. The broker worker transmits the
CREATED order; this service never sends it.

FIXED_STOP SELL is automatic (``AUTOMATIC`` mode) and does not go through
approval — it calls ``create_order`` directly from ``stop_trigger``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.guard import blocking_code, persist_guard_evaluation, rule
from app.models import (
    Approval,
    AuditLog,
    Decision,
    DecisionExecution,
    MarketSnapshot,
    MarketStreamState,
    User,
)
from app.order_creation import OrderCreationError, OrderRequest, create_order

APPROVAL_WINDOW_SECONDS = 60


class ApprovalError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _scope_snapshot(
    decision: Decision,
    reference_price: Decimal | None,
    quantity: int,
    now: datetime,
) -> dict[str, object]:
    return {
        "schema_version": "approval-scope-v1",
        "decision_id": decision.id,
        "reference_snapshot_id": decision.input_snapshot_id,
        "symbol": decision.symbol,
        "market": decision.market,
        "action": decision.action,
        "reference_price": str(reference_price) if reference_price is not None else None,
        "quantity": quantity,
        "valid_until": _utc(decision.valid_until).isoformat(),
        "captured_at": _utc(now).isoformat(),
    }


def _marketable_buy_price(snapshot: MarketSnapshot | None) -> Decimal | None:
    """MARKETABLE_LIMIT buy price = best ask (the price you'd pay to fill now)."""
    if snapshot is None:
        return None
    return snapshot.best_ask_price


def _buy_quantity(entry_order_amount: Decimal | None, price: Decimal | None) -> int:
    """Whole-share quantity from the configured entry amount and current price.

    Falls back to 0 when either input is missing — the hard Guard's
    ``ORDER_SIZE_NOT_CONFIGURED`` rule blocks the order before it reaches here,
    so a 0 quantity surfaces as a clean rejection rather than a bad order.
    """
    if entry_order_amount is None or price is None or price <= 0:
        return 0
    raw = (entry_order_amount / price).quantize(Decimal(1), rounding=ROUND_DOWN)
    return int(raw)


def create_approval(
    db: Session,
    *,
    execution: DecisionExecution,
    decision: Decision,
    user: User,
    settings: Settings,
    now: datetime | None = None,
) -> Approval:
    """Create a ``PENDING`` approval for a MANUAL_APPROVAL BUY execution.

    Captures the reference price and whole-share quantity into an immutable
    scope snapshot so the approval can be invalidated if the price deviates
    beyond ``max_price_deviation_pct`` or the decision expires before the user
    acts. Does not create an order.
    """
    current = now or datetime.now(UTC)
    snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
    reference_price = _marketable_buy_price(snapshot)
    from app.risk_policy import active_risk_policy, risk_policy_payload

    risk_config = active_risk_policy(db, user.id)
    risk_policy = risk_policy_payload(risk_config)
    quantity = _buy_quantity(risk_policy.entry_order_amount, reference_price)
    if quantity <= 0:
        raise ApprovalError("APPROVAL_QUANTITY_INVALID", 409)
    scope = _scope_snapshot(decision, reference_price, quantity, current)
    approval = Approval(
        execution_id=execution.id,
        decision_id=decision.id,
        user_id=user.id,
        state="PENDING",
        scope_snapshot_json=json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        expires_at=decision.valid_until,
    )
    db.add(approval)
    db.flush()
    execution.state = "APPROVAL_PENDING"
    execution.approval_id = approval.id
    db.add(
        AuditLog(
            actor_type="SYSTEM",
            actor_id=user.id,
            action="APPROVAL_PENDING",
            target=approval.id,
            result="PENDING",
            correlation_id=execution.correlation_id,
            metadata_json=json.dumps(
                {"execution_id": execution.id, "decision_id": decision.id},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    db.refresh(approval)
    return approval


def _load_decision(db: Session, approval: Approval) -> Decision:
    decision = db.get(Decision, approval.decision_id)
    if decision is None:
        raise ApprovalError("DECISION_NOT_FOUND", 404)
    return decision


def _latest_snapshot_for_approval(db: Session, decision: Decision) -> MarketSnapshot | None:
    """Lock the stream head and return its latest immutable snapshot.

    A decision's input snapshot remains the immutable approval reference. The
    stream is expected to advance while a user reviews an approval, so the
    pre-order Guard must use the latest stream head instead of requiring the
    reference snapshot to still be current.
    """
    stream = db.scalar(
        select(MarketStreamState)
        .where(
            MarketStreamState.market == decision.market,
            MarketStreamState.symbol == decision.symbol,
        )
        .with_for_update()
    )
    if stream is None or stream.current_snapshot_id is None:
        return None
    return db.get(MarketSnapshot, stream.current_snapshot_id)


def _price_deviation_ok(
    reference: Decimal | None, current: Decimal | None, max_deviation_pct: Decimal
) -> bool:
    if reference is None or current is None:
        return False
    if reference <= 0:
        return False
    deviation = abs(current - reference) / reference * Decimal(100)
    return deviation <= max_deviation_pct


def _evaluate_approval(
    db: Session,
    *,
    approval: Approval,
    decision: Decision,
    user: User,
    settings: Settings,
    now: datetime,
) -> tuple[list[dict[str, object]], str | None, Decimal | None, int, MarketSnapshot | None]:
    """Re-run the hard Guard and price-deviation check at approval time.

    Returns (rules, blocking_code_or_None, current_price, quantity,
    approval_snapshot). When ``blocking_code`` is None the approval may
    proceed to order creation.
    """
    from app.decision_execution import _buy_guard_rules
    from app.risk_policy import active_risk_policy, risk_policy_payload

    risk_config = active_risk_policy(db, user.id)
    risk_policy = risk_policy_payload(risk_config)
    snapshot = _latest_snapshot_for_approval(db, decision)
    rules = _buy_guard_rules(
        db,
        decision,
        user,
        settings,
        risk_policy,
        now,
        snapshot=snapshot,
    )
    current_price = _marketable_buy_price(snapshot)
    # Price-deviation gate (ORD-010/ORD-011): reject if the ask moved beyond
    # the configured tolerance since the approval was captured.
    scope = json.loads(approval.scope_snapshot_json)
    reference_price = (
        Decimal(str(scope["reference_price"])) if scope.get("reference_price") else None
    )
    quantity = int(scope.get("quantity") or 0)
    if not _price_deviation_ok(
        reference_price, current_price, risk_policy.max_price_deviation_pct
    ):
        rules.append(rule("PRICE_DEVIATION_EXCEEDED", False))
    if quantity <= 0:
        rules.append(rule("ORDER_SIZE_NOT_CONFIGURED", False))
    return (
        rules,
        blocking_code(rules) if any(r["result"] == "BLOCKED" for r in rules) else None,
        current_price,
        quantity,
        snapshot,
    )


def approve(
    db: Session,
    *,
    approval_id: str,
    user: User,
    settings: Settings,
    correlation_id: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> Approval:
    """Approve a PENDING approval and create the CREATED order atomically.

    Re-evaluates the hard Guard and price deviation; on any block the approval
    becomes ``INVALIDATED`` (not ``APPROVED``) and no order is created.
    Idempotent on ``idempotency_key`` via ``create_order``.
    """
    current = now or datetime.now(UTC)
    approval = db.scalar(select(Approval).where(Approval.id == approval_id).with_for_update())
    if approval is None:
        raise ApprovalError("APPROVAL_NOT_FOUND", 404)
    if approval.state == "APPROVED":
        return approval
    if approval.state != "PENDING":
        raise ApprovalError("APPROVAL_NOT_PENDING", 409)
    if _utc(approval.expires_at) <= current:
        approval.state = "EXPIRED"
        approval.result_code = "APPROVAL_EXPIRED"
        approval.version += 1
        db.commit()
        raise ApprovalError("APPROVAL_EXPIRED", 409)

    decision = _load_decision(db, approval)
    execution = db.get(DecisionExecution, approval.execution_id)
    if execution is None:
        raise ApprovalError("EXECUTION_NOT_FOUND", 404)

    rules, blocked, current_price, quantity, approval_snapshot = _evaluate_approval(
        db, approval=approval, decision=decision, user=user, settings=settings, now=current
    )
    guard = persist_guard_evaluation(
        db,
        execution_id=execution.id,
        subject_type="APPROVAL",
        subject_id=approval.id,
        rules=rules,
        snapshot_id=approval_snapshot.id if approval_snapshot else None,
        position_version=None,
        execution_policy_version_id=execution.execution_policy_version_id,
        risk_policy_version_id=execution.risk_policy_version_id,
        halt_scope="ENTRY_HALT" if blocked else None,
        valid_until=decision.valid_until,
        now=current,
    )
    if blocked:
        approval.state = "INVALIDATED"
        approval.result_code = blocked
        approval.version += 1
        execution.state = "INVALIDATED"
        execution.result_code = blocked
        execution.guard_evaluation_id = guard.id
        db.add(
            AuditLog(
                actor_type="USER",
                actor_id=user.id,
                action="APPROVAL_INVALIDATED",
                target=approval.id,
                result=blocked,
                correlation_id=correlation_id,
                metadata_json=json.dumps(
                    {
                        "execution_id": execution.id,
                        "reference_snapshot_id": decision.input_snapshot_id,
                        "approval_snapshot_id": (
                            approval_snapshot.id if approval_snapshot else None
                        ),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
        db.commit()
        raise ApprovalError(blocked, 409)

    request = OrderRequest(
        symbol=decision.symbol,
        market=decision.market,
        side="BUY",
        action="BUY",
        order_type="LIMIT",
        limit_price=current_price,
        quantity=quantity,
        idempotency_key=idempotency_key,
        request_payload={
            "environment": "MOCK",
            "symbol": decision.symbol,
            "market": decision.market,
            "side": "BUY",
            "action": "BUY",
            "order_type": "LIMIT",
            "limit_price": str(current_price) if current_price is not None else None,
            "quantity": quantity,
            "approval_id": approval.id,
            "decision_id": decision.id,
            "reference_snapshot_id": decision.input_snapshot_id,
            "approval_snapshot_id": approval_snapshot.id if approval_snapshot else None,
            "idempotency_key": idempotency_key,
        },
        correlation_id=correlation_id,
    )
    try:
        order = create_order(
            db,
            user=user,
            request=request,
            audit_action="APPROVAL_ORDER_CREATED",
            now=current,
        )
    except OrderCreationError as exc:
        approval.state = "INVALIDATED"
        approval.result_code = exc.code
        approval.version += 1
        execution.state = "FAILED_SAFE"
        execution.result_code = exc.code
        db.commit()
        raise ApprovalError(exc.code, exc.status_code) from None

    approval.state = "APPROVED"
    approval.actor_id = user.id
    approval.order_id = order.id
    approval.version += 1
    execution.state = "ORDER_CREATED"
    execution.result_code = "ORDER_CREATED"
    execution.guard_evaluation_id = guard.id
    execution.order_intent_id = order.intent_id
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="APPROVAL_APPROVED",
            target=approval.id,
            result="ORDER_CREATED",
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {
                    "order_id": order.id,
                    "execution_id": execution.id,
                    "reference_snapshot_id": decision.input_snapshot_id,
                    "approval_snapshot_id": approval_snapshot.id if approval_snapshot else None,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    db.refresh(approval)
    return approval


def reject(
    db: Session,
    *,
    approval_id: str,
    user: User,
    correlation_id: str,
    now: datetime | None = None,
) -> Approval:
    del now  # rejected approvals do not need a timestamp gate
    approval = db.scalar(select(Approval).where(Approval.id == approval_id).with_for_update())
    if approval is None:
        raise ApprovalError("APPROVAL_NOT_FOUND", 404)
    if approval.state == "REJECTED":
        return approval
    if approval.state != "PENDING":
        raise ApprovalError("APPROVAL_NOT_PENDING", 409)
    approval.state = "REJECTED"
    approval.actor_id = user.id
    approval.result_code = "USER_REJECTED"
    approval.version += 1
    execution = db.get(DecisionExecution, approval.execution_id)
    if execution is not None:
        execution.state = "REJECTED"
        execution.result_code = "USER_REJECTED"
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action="APPROVAL_REJECTED",
            target=approval.id,
            result="REJECTED",
            correlation_id=correlation_id,
            metadata_json="{}",
        )
    )
    db.commit()
    db.refresh(approval)
    return approval


def expire_stale(db: Session, *, now: datetime | None = None) -> int:
    """Transition PENDING approvals past their ``expires_at`` to EXPIRED.

    Called periodically (e.g. on approval list reads) so the Console does not
    show actionable approvals that are already past their window.
    """
    current = now or datetime.now(UTC)
    stale = list(
        db.scalars(
            select(Approval).where(
                Approval.state == "PENDING",
                Approval.expires_at <= current,
            )
        )
    )
    for approval in stale:
        approval.state = "EXPIRED"
        approval.result_code = "APPROVAL_EXPIRED"
        approval.version += 1
        execution = db.get(DecisionExecution, approval.execution_id)
        if execution is not None and execution.state == "APPROVAL_PENDING":
            execution.state = "EXPIRED"
            execution.result_code = "APPROVAL_EXPIRED"
    if stale:
        db.commit()
    return len(stale)


def list_pending(db: Session, user_id: str) -> list[Approval]:
    return list(
        db.scalars(
            select(Approval)
            .where(Approval.user_id == user_id, Approval.state == "PENDING")
            .order_by(Approval.created_at.desc())
            .limit(50)
        )
    )


def list_recent(db: Session, user_id: str) -> list[Approval]:
    return list(
        db.scalars(
            select(Approval)
            .where(Approval.user_id == user_id)
            .order_by(Approval.created_at.desc())
            .limit(50)
        )
    )
