"""Approval lifecycle for MANUAL_APPROVAL BUY and decision-driven SELL orders.

When ``decision_execution.route_trading_decision`` routes a BUY decision whose
execution mode is ``MANUAL_APPROVAL`` and the hard Guard passes, it creates an
``Approval`` (state ``PENDING``) bound to the ``DecisionExecution`` and stops
short of creating an order. The user then approves or rejects through the API;
on approval the Guard is re-evaluated against the *current* market snapshot and
risk policy, the price deviation is checked against ``max_price_deviation_pct``,
and only then is an ``OrderIntent`` + ``TradingOrder(CREATED)`` atomically
created via ``app.order_creation.create_order``. The broker worker transmits the
CREATED order; this service never sends it.

Decision-driven ``PARTIAL_SELL`` and ``FULL_SELL`` approvals capture the
position identity/version and exact Cresta-managed quantity. ``FIXED_STOP``
SELL remains automatic and calls ``create_order`` from ``stop_trigger``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.agents.decision_finalizer import (
    DecisionFinalizationError,
    validate_persisted_sourced_entry_decision,
)
from app.auth.service import ReauthProofError, consume_reauth_proof
from app.broker.kiwoom import KiwoomMockClient
from app.config import Settings
from app.emergency_stop import active_pause_entry
from app.execution_authority import (
    ActionMode,
    ExecutionStage,
    effective_action_mode,
    effective_execution_stage,
    order_authority_key,
)
from app.execution_policy import active_policy, policy_payload
from app.execution_stage import (
    EvidenceLoader,
    ExecutionStageValidationPolicy,
    StageResolutionStatus,
    resolve_current_execution_stage,
)
from app.financial_authority import (
    build_buy_financial_context,
    configured_financial_client,
    financial_guard_rules,
    refresh_financial_evidence_if_needed,
)
from app.guard import blocking_code, persist_guard_evaluation, rule
from app.models import (
    Approval,
    AuditLog,
    ConfigurationVersion,
    Decision,
    DecisionExecution,
    MarketSnapshot,
    MarketStreamState,
    User,
)
from app.order_creation import (
    OrderAuthority,
    OrderCreationError,
    OrderRequest,
    create_order,
)
from app.risk_policy import active_risk_policy, risk_policy_payload

APPROVAL_WINDOW_SECONDS = 60


class ApprovalError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _commit_approval_versioned_mutation(db: Session, approval: Approval) -> None:
    """Commit one Approval CAS mutation without leaking its ORM stale error.

    Flushing only ``approval`` makes this catch specific to the Approval
    optimistic-version UPDATE. Errors from the later transaction commit,
    including other versioned entities and retryable database failures, retain
    their original type.
    """
    try:
        db.flush([approval])
    except StaleDataError:
        db.rollback()
        raise ApprovalError("APPROVAL_VERSION_CONFLICT", 409) from None
    db.commit()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _scope_snapshot(
    decision: Decision,
    reference_price: Decimal | None,
    quantity: int,
    now: datetime,
    *,
    position_id: str | None = None,
    position_version: int | None = None,
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
        "position_id": position_id,
        "position_version": position_version,
        "valid_until": _utc(decision.valid_until).isoformat(),
        "captured_at": _utc(now).isoformat(),
    }


def _marketable_buy_price(snapshot: MarketSnapshot | None) -> Decimal | None:
    """MARKETABLE_LIMIT buy price = best ask (the price you'd pay to fill now)."""
    if snapshot is None:
        return None
    return snapshot.best_ask_price


def _marketable_sell_price(snapshot: MarketSnapshot | None) -> Decimal | None:
    """MARKETABLE_LIMIT sell price = current best bid."""
    if snapshot is None:
        return None
    return snapshot.best_bid_price


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
    """Create one immutable ``PENDING`` approval for a supported decision.

    Captures the reference price and whole-share quantity into an immutable
    scope snapshot so the approval can be invalidated if the price deviates
    beyond ``max_price_deviation_pct`` or the decision expires before the user
    acts. Does not create an order.
    """
    current = now or datetime.now(UTC)
    existing = db.scalar(
        select(Approval).where(Approval.execution_id == execution.id)
    )
    if existing is not None:
        if existing.decision_id != decision.id or existing.user_id != user.id:
            raise ApprovalError("APPROVAL_AUTHORITY_CONFLICT")
        execution.approval_id = existing.id
        return existing
    snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
    from app.risk_policy import active_risk_policy, risk_policy_payload

    risk_config = active_risk_policy(db, user.id)
    risk_policy = risk_policy_payload(risk_config)
    position_id: str | None = None
    position_version: int | None = None
    if decision.action == "BUY":
        reference_price = _marketable_buy_price(snapshot)
        quantity = _buy_quantity(risk_policy.entry_order_amount, reference_price)
    elif decision.action in {"PARTIAL_SELL", "FULL_SELL"}:
        from app.decision_execution import _sell_plan

        plan = _sell_plan(db, decision, snapshot=snapshot, lock_position=True)
        reference_price = _marketable_sell_price(snapshot)
        quantity = plan.quantity
        if plan.position is not None:
            position_id = plan.position.id
            position_version = plan.position.version
    else:
        raise ApprovalError("ACTION_NOT_IMPLEMENTED", 409)
    if quantity <= 0:
        raise ApprovalError("APPROVAL_QUANTITY_INVALID", 409)
    scope = _scope_snapshot(
        decision,
        reference_price,
        quantity,
        current,
        position_id=position_id,
        position_version=position_version,
    )
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
    from app.decision_execution import _buy_guard_rules, _sell_guard_rules, _sell_plan
    from app.risk_policy import active_risk_policy, risk_policy_payload

    risk_config = active_risk_policy(db, user.id)
    risk_policy = risk_policy_payload(risk_config)
    snapshot = _latest_snapshot_for_approval(db, decision)
    scope = json.loads(approval.scope_snapshot_json)
    quantity = int(scope.get("quantity") or 0)
    if decision.action == "BUY":
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
    elif decision.action in {"PARTIAL_SELL", "FULL_SELL"}:
        plan = _sell_plan(db, decision, snapshot=snapshot, lock_position=True)
        rules = _sell_guard_rules(
            db,
            decision,
            settings,
            risk_policy,
            now,
            plan=plan,
            expected_position_id=(
                str(scope["position_id"]) if scope.get("position_id") else None
            ),
            expected_position_version=(
                int(scope["position_version"])
                if scope.get("position_version") is not None
                else None
            ),
            requested_quantity=quantity,
        )
        current_price = _marketable_sell_price(snapshot)
    else:
        rules = [rule("ACTION_NOT_IMPLEMENTED", False)]
        current_price = None
    # Price-deviation gate (ORD-010/ORD-011): reject if the executable quote
    # moved beyond the configured tolerance since the approval was captured.
    reference_price = (
        Decimal(str(scope["reference_price"])) if scope.get("reference_price") else None
    )
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


def _invalidate_sourced_approval(
    db: Session,
    *,
    approval: Approval,
    execution: DecisionExecution,
    code: str,
    correlation_id: str,
    expired: bool = False,
) -> None:
    approval.state = "EXPIRED" if expired else "INVALIDATED"
    approval.result_code = code
    approval.version += 1
    execution.state = "EXPIRED" if expired else "INVALIDATED"
    execution.result_code = code
    db.add(
        AuditLog(
            actor_type="SYSTEM",
            actor_id=approval.user_id,
            action="SOURCED_APPROVAL_INVALIDATED",
            target=approval.id,
            result=code,
            correlation_id=correlation_id,
            metadata_json=json.dumps(
                {"execution_id": execution.id}, sort_keys=True, separators=(",", ":")
            ),
        )
    )
    _commit_approval_versioned_mutation(db, approval)


def _approve_sourced(
    db: Session,
    *,
    approval_id: str,
    user: User,
    settings: Settings,
    correlation_id: str,
    idempotency_key: str,
    expected_version: int,
    reauth_proof: str | None,
    current: datetime,
    stage_evidence_loader: EvidenceLoader | None,
    stage_validation_policy: ExecutionStageValidationPolicy | None,
    financial_client: KiwoomMockClient | None,
    before_commit: Callable[[], None] | None,
) -> Approval:
    # Preflight financial refresh occurs before the authority transaction.
    preview = db.get(Approval, approval_id)
    if preview is None:
        raise ApprovalError("APPROVAL_NOT_FOUND", 404)
    preview_execution = db.get(DecisionExecution, preview.execution_id)
    preview_decision = db.get(Decision, preview.decision_id)
    if preview_execution is None or preview_decision is None:
        raise ApprovalError("SOURCE_AUTHORITY_INVALID")
    preview_snapshot = _latest_snapshot_for_approval(db, preview_decision)
    preview_price = _marketable_buy_price(preview_snapshot)
    preview_scope = json.loads(preview.scope_snapshot_json)
    preview_quantity = int(preview_scope.get("quantity") or 0)
    frozen_risk_version = (
        db.get(ConfigurationVersion, preview_execution.risk_policy_version_id)
        if preview_execution.risk_policy_version_id
        else None
    )
    current_risk_version = active_risk_policy(db, user.id)
    try:
        frozen_risk = risk_policy_payload(frozen_risk_version)
        current_risk = risk_policy_payload(current_risk_version)
        if frozen_risk_version is None or current_risk_version is None or preview_price is None:
            raise ValueError("risk/price unavailable")
        financial_context = build_buy_financial_context(
            symbol=preview_decision.symbol,
            price=preview_price,
            quantity=preview_quantity,
            frozen_policy=frozen_risk,
            current_policy=current_risk,
        )
        refresh_financial_evidence_if_needed(
            db,
            client=financial_client or configured_financial_client(settings),
            context=financial_context,
            now=current,
        )
    except (ValidationError, ValueError, TypeError):
        financial_context = None

    approval = db.scalar(
        select(Approval).where(Approval.id == approval_id).with_for_update()
    )
    if approval is None:
        raise ApprovalError("APPROVAL_NOT_FOUND", 404)
    if approval.user_id != user.id:
        raise ApprovalError("APPROVAL_OWNER_MISMATCH", 403)
    if approval.version != expected_version:
        raise ApprovalError("APPROVAL_VERSION_CONFLICT", 409)
    if approval.state == "APPROVED":
        return approval
    if approval.state != "PENDING":
        raise ApprovalError("APPROVAL_NOT_PENDING", 409)
    execution = db.get(DecisionExecution, approval.execution_id)
    decision = _load_decision(db, approval)
    if execution is None or execution.contract_version != "sourced-entry-execution-v1":
        raise ApprovalError("SOURCE_AUTHORITY_INVALID")
    if _utc(approval.expires_at) <= current or _utc(decision.valid_until) <= current:
        _invalidate_sourced_approval(
            db,
            approval=approval,
            execution=execution,
            code="APPROVAL_EXPIRED",
            correlation_id=correlation_id,
            expired=True,
        )
        raise ApprovalError("APPROVAL_EXPIRED")
    try:
        validate_persisted_sourced_entry_decision(db, decision=decision)
    except DecisionFinalizationError:
        _invalidate_sourced_approval(
            db,
            approval=approval,
            execution=execution,
            code="SOURCE_AUTHORITY_INVALID",
            correlation_id=correlation_id,
        )
        raise ApprovalError("SOURCE_AUTHORITY_INVALID") from None

    stage_resolution = resolve_current_execution_stage(
        db,
        now=current,
        evidence_loader=stage_evidence_loader,
        policy=stage_validation_policy,
    )
    if stage_resolution.status is StageResolutionStatus.DB_RETRYABLE_FAILURE:
        db.rollback()
        raise ApprovalError("EXECUTION_STAGE_DB_RETRYABLE_FAILURE", 503)
    if stage_resolution.status is not StageResolutionStatus.PASS:
        _invalidate_sourced_approval(
            db,
            approval=approval,
            execution=execution,
            code="EXECUTION_STAGE_UNAVAILABLE",
            correlation_id=correlation_id,
        )
        raise ApprovalError("EXECUTION_STAGE_UNAVAILABLE")
    assert stage_resolution.payload is not None
    effective_stage = effective_execution_stage(
        ExecutionStage(str(execution.stage)), stage_resolution.payload.stage
    )
    current_execution_version = active_policy(db, user.id)
    try:
        current_execution_policy = policy_payload(current_execution_version)
        effective_mode = effective_action_mode(
            ActionMode(str(execution.mode)), current_execution_policy.buy
        )
    except (ValidationError, ValueError, TypeError):
        effective_mode = ActionMode.DISABLED
    if effective_stage is ExecutionStage.SHADOW or effective_mode is not ActionMode.MANUAL_APPROVAL:
        _invalidate_sourced_approval(
            db,
            approval=approval,
            execution=execution,
            code=(
                "EXECUTION_STAGE_DOWNGRADED"
                if effective_stage is ExecutionStage.SHADOW
                else "ACTION_MODE_DOWNGRADED"
            ),
            correlation_id=correlation_id,
        )
        raise ApprovalError(approval.result_code or "APPROVAL_INVALIDATED")
    if active_pause_entry(db, execution.account_alias) is not None:
        _invalidate_sourced_approval(
            db,
            approval=approval,
            execution=execution,
            code="EMERGENCY_STOP_ACTIVE",
            correlation_id=correlation_id,
        )
        raise ApprovalError("EMERGENCY_STOP_ACTIVE")

    current_risk_version = active_risk_policy(db, user.id)
    try:
        frozen_risk_version = db.get(ConfigurationVersion, execution.risk_policy_version_id)
        if frozen_risk_version is None or current_risk_version is None:
            raise ValueError("risk policy unavailable")
        frozen_risk = risk_policy_payload(frozen_risk_version)
        current_risk = risk_policy_payload(current_risk_version)
    except (ValidationError, ValueError, TypeError):
        _invalidate_sourced_approval(
            db,
            approval=approval,
            execution=execution,
            code="RISK_POLICY_UNAVAILABLE",
            correlation_id=correlation_id,
        )
        raise ApprovalError("RISK_POLICY_UNAVAILABLE") from None
    snapshot = _latest_snapshot_for_approval(db, decision)
    price = _marketable_buy_price(snapshot)
    scope = json.loads(approval.scope_snapshot_json)
    quantity = int(scope.get("quantity") or 0)
    from app.decision_execution import buy_pre_order_guard_rules

    rules = buy_pre_order_guard_rules(
        db, decision, user, settings, current_risk, current, snapshot=snapshot
    )
    reference_price = Decimal(str(scope["reference_price"])) if scope.get("reference_price") else None
    rules.append(rule("PRICE_DEVIATION_EXCEEDED", _price_deviation_ok(
        reference_price, price, min(frozen_risk.max_price_deviation_pct, current_risk.max_price_deviation_pct)
    )))
    if financial_context is None or price is None:
        rules.append(rule("FINANCIAL_CONTEXT_INVALID", False))
    else:
        try:
            financial_context = build_buy_financial_context(
                symbol=decision.symbol,
                price=price,
                quantity=quantity,
                frozen_policy=frozen_risk,
                current_policy=current_risk,
            )
            rules.extend(financial_guard_rules(
                db,
                context=financial_context,
                now=current,
                frozen_risk_policy_id=frozen_risk_version.id,
                current_risk_policy_id=current_risk_version.id,
                frozen_policy=frozen_risk,
                current_policy=current_risk,
            ))
        except ValueError:
            rules.append(rule("FINANCIAL_CONTEXT_INVALID", False))
    blocked = blocking_code(rules) if any(item["result"] == "BLOCKED" for item in rules) else None
    guard = persist_guard_evaluation(
        db,
        execution_id=execution.id,
        subject_type="DECISION_EXECUTION",
        subject_id=execution.id,
        rules=rules,
        snapshot_id=snapshot.id if snapshot else None,
        position_version=None,
        execution_policy_version_id=execution.execution_policy_version_id,
        risk_policy_version_id=execution.risk_policy_version_id,
        halt_scope="ENTRY_HALT" if blocked else None,
        valid_until=decision.valid_until,
        now=current,
        phase="APPROVAL_REVALIDATION",
    )
    if blocked:
        execution.guard_evaluation_id = guard.id
        _invalidate_sourced_approval(
            db,
            approval=approval,
            execution=execution,
            code=blocked,
            correlation_id=correlation_id,
        )
        raise ApprovalError(blocked)
    if reauth_proof is None:
        db.rollback()
        raise ApprovalError("REAUTH_PROOF_REQUIRED", 403)
    try:
        proof = consume_reauth_proof(
            db,
            user=user,
            raw_proof=reauth_proof,
            target_action="APPROVE_ORDER",
            target_id=f"{approval.id}:{expected_version}",
            now=current,
        )
    except ReauthProofError:
        db.rollback()
        raise ApprovalError("REAUTH_PROOF_INVALID", 403) from None
    authority_key = order_authority_key(
        source_type="DECISION_EXECUTION",
        source_id=execution.id,
        approval_id=approval.id,
    )
    request = OrderRequest(
        symbol=decision.symbol,
        market=decision.market,
        side="BUY",
        action="BUY",
        order_type="LIMIT",
        limit_price=price,
        quantity=quantity,
        idempotency_key=idempotency_key,
        request_payload={
            "environment": "MOCK",
            "symbol": decision.symbol,
            "market": decision.market,
            "side": "BUY",
            "action": "BUY",
            "order_type": "LIMIT",
            "limit_price": str(price),
            "quantity": quantity,
            "approval_id": approval.id,
            "authority_key": authority_key,
        },
        correlation_id=correlation_id,
    )
    order = create_order(
        db,
        user=user,
        request=request,
        audit_action="SOURCED_APPROVAL_ORDER_CREATED",
        now=current,
        authority=OrderAuthority(
            source_type="DECISION_EXECUTION",
            source_id=execution.id,
            decision_execution_id=execution.id,
            stop_trigger_id=None,
            guard_evaluation_id=guard.id,
            approval_id=approval.id,
            execution_policy_version_id=execution.execution_policy_version_id,
            risk_policy_version_id=execution.risk_policy_version_id,
            execution_stage_version_id=str(execution.execution_stage_version_id),
            execution_stage_payload_hash=str(execution.execution_stage_payload_hash),
            authority_key=authority_key,
        ),
    )
    approval.state = "APPROVED"
    approval.actor_id = user.id
    approval.reauth_proof_id = proof.id
    approval.order_id = order.id
    approval.result_code = "ORDER_CREATED"
    approval.version += 1
    execution.state = "ORDER_CREATED"
    execution.result_code = "ORDER_CREATED"
    execution.guard_evaluation_id = guard.id
    execution.order_intent_id = order.intent_id
    db.add(AuditLog(
        actor_type="USER",
        actor_id=user.id,
        action="SOURCED_APPROVAL_APPROVED",
        target=approval.id,
        result="ORDER_CREATED",
        correlation_id=correlation_id,
        metadata_json=json.dumps({"order_id": order.id, "authority_key": authority_key}, sort_keys=True, separators=(",", ":")),
    ))
    if before_commit is not None:
        before_commit()
    _commit_approval_versioned_mutation(db, approval)
    db.refresh(approval)
    return approval


def approve(
    db: Session,
    *,
    approval_id: str,
    user: User,
    settings: Settings,
    correlation_id: str,
    idempotency_key: str,
    expected_version: int = 1,
    reauth_proof: str | None = None,
    now: datetime | None = None,
    stage_evidence_loader: EvidenceLoader | None = None,
    stage_validation_policy: ExecutionStageValidationPolicy | None = None,
    financial_client: KiwoomMockClient | None = None,
    before_commit: Callable[[], None] | None = None,
) -> Approval:
    """Approve a PENDING approval and create the CREATED order atomically.

    Re-evaluates the hard Guard and price deviation; on any block the approval
    becomes ``INVALIDATED`` (not ``APPROVED``) and no order is created.
    Idempotent on ``idempotency_key`` via ``create_order``.
    """
    current = now or datetime.now(UTC)
    preview = db.get(Approval, approval_id)
    preview_execution = (
        db.get(DecisionExecution, preview.execution_id) if preview is not None else None
    )
    if (
        preview_execution is not None
        and preview_execution.contract_version == "sourced-entry-execution-v1"
    ):
        return _approve_sourced(
            db,
            approval_id=approval_id,
            user=user,
            settings=settings,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            reauth_proof=reauth_proof,
            current=current,
            stage_evidence_loader=stage_evidence_loader,
            stage_validation_policy=stage_validation_policy,
            financial_client=financial_client,
            before_commit=before_commit,
        )
    approval = db.scalar(select(Approval).where(Approval.id == approval_id).with_for_update())
    if approval is None:
        raise ApprovalError("APPROVAL_NOT_FOUND", 404)
    if approval.user_id != user.id:
        raise ApprovalError("APPROVAL_OWNER_MISMATCH", 403)
    if approval.version != expected_version:
        raise ApprovalError("APPROVAL_VERSION_CONFLICT", 409)
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
    approval_scope = json.loads(approval.scope_snapshot_json)

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
        position_version=(
            int(approval_scope["position_version"])
            if approval_scope.get("position_version") is not None
            else None
        ),
        execution_policy_version_id=execution.execution_policy_version_id,
        risk_policy_version_id=execution.risk_policy_version_id,
        halt_scope="ENTRY_HALT" if blocked and decision.action == "BUY" else None,
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

    side = "BUY" if decision.action == "BUY" else "SELL"
    request = OrderRequest(
        symbol=decision.symbol,
        market=decision.market,
        side=side,
        action=decision.action,
        order_type="LIMIT",
        limit_price=current_price,
        quantity=quantity,
        idempotency_key=idempotency_key,
        request_payload={
            "environment": "MOCK",
            "symbol": decision.symbol,
            "market": decision.market,
            "side": side,
            "action": decision.action,
            "order_type": "LIMIT",
            "limit_price": str(current_price) if current_price is not None else None,
            "quantity": quantity,
            "approval_id": approval.id,
            "decision_id": decision.id,
            "reference_snapshot_id": decision.input_snapshot_id,
            "approval_snapshot_id": approval_snapshot.id if approval_snapshot else None,
            "position_id": approval_scope.get("position_id"),
            "position_version": approval_scope.get("position_version"),
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
    expected_version: int = 1,
    now: datetime | None = None,
) -> Approval:
    del now  # rejected approvals do not need a timestamp gate
    approval = db.scalar(select(Approval).where(Approval.id == approval_id).with_for_update())
    if approval is None:
        raise ApprovalError("APPROVAL_NOT_FOUND", 404)
    if approval.user_id != user.id:
        raise ApprovalError("APPROVAL_OWNER_MISMATCH", 403)
    if approval.version != expected_version:
        raise ApprovalError("APPROVAL_VERSION_CONFLICT", 409)
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
    _commit_approval_versioned_mutation(db, approval)
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
