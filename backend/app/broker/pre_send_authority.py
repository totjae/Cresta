from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.decision_finalizer import (
    DecisionFinalizationError,
    validate_persisted_sourced_entry_decision,
)
from app.config import Settings
from app.decision_execution import (
    ACTIVE_ORDER_STATES,
    TRADABLE_SESSIONS_BY_MARKET,
    buy_pre_order_guard_rules,
)
from app.emergency_stop import active_pause_entry
from app.execution_authority import (
    ActionMode,
    ExecutionStage,
    effective_action_mode,
    effective_execution_stage,
    order_authority_key,
    validate_sourced_execution_representation,
)
from app.execution_policy import policy_payload
from app.execution_stage import (
    EvidenceLoader,
    ExecutionStagePayload,
    ExecutionStageValidationPolicy,
    StageResolutionStatus,
    canonical_stage_json,
    resolve_current_execution_stage,
    stage_payload_hash,
)
from app.financial_authority import build_buy_financial_context, financial_guard_rules
from app.guard import blocking_code, persist_guard_evaluation, rule
from app.models import (
    Approval,
    AuditLog,
    ConfigurationVersion,
    Decision,
    DecisionExecution,
    GuardEvaluation,
    MarketSnapshot,
    MarketStreamState,
    OrderEvent,
    OrderIntent,
    Position,
    RiskEvent,
    StopTrigger,
    TradingGate,
    TradingOrder,
    User,
)
from app.risk_events import RISK_EVENT_SCOPE_FIXED_STOP, create_risk_event
from app.risk_policy import risk_policy_payload
from app.venue_selection import classify_session

ACCOUNT_ALIAS = "KIWOOM_MOCK_PRIMARY"
ACTIVE_CONFLICT_STATES = set(ACTIVE_ORDER_STATES) | {"UNKNOWN", "RECONCILING"}
REVOCATION_EVENT = "ORDER_AUTHORITY_REVOKED_BEFORE_SEND"
REVOCATION_RESULT = "EXECUTION_AUTHORITY_REVOKED_BEFORE_SEND"


class PreSendStatus(StrEnum):
    PASS = "PASS"
    REVOKED = "REVOKED"
    RETRYABLE = "RETRYABLE"


@dataclass(frozen=True)
class PreSendAuthorityResult:
    status: PreSendStatus
    reason: str | None = None
    guard_id: str | None = None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _strict_mock(
    db: Session, settings: Settings, order: TradingOrder, intent: OrderIntent
) -> bool:
    gate = db.get(TradingGate, order.account_alias)
    return (
        order.account_alias == ACCOUNT_ALIAS
        and intent.account_alias == ACCOUNT_ALIAS
        and order.environment == "MOCK"
        and intent.environment == "MOCK"
        and gate is not None
        and gate.environment == "MOCK"
        and settings.environment.upper() == "MOCK"
        and not settings.live_trading_enabled
        and settings.kiwoom_rest_base_url.rstrip("/") == "https://mockapi.kiwoom.com"
        and settings.kiwoom_ws_base_url.rstrip("/")
        == "wss://mockapi.kiwoom.com:10000"
    )


def _exact_active_configuration(
    db: Session, *, category: str, target_id: str
) -> ConfigurationVersion | None:
    rows = list(
        db.scalars(
            select(ConfigurationVersion)
            .where(
                ConfigurationVersion.scope == "USER_DEFAULT",
                ConfigurationVersion.target_id == target_id,
                ConfigurationVersion.category == category,
                ConfigurationVersion.state == "ACTIVE",
            )
            .order_by(ConfigurationVersion.sequence.desc(), ConfigurationVersion.id)
            .limit(2)
        )
    )
    return rows[0] if len(rows) == 1 else None


def _frozen_stage(
    intent: OrderIntent, version: ConfigurationVersion | None
) -> ExecutionStage | None:
    if intent.execution_stage_version_id is None or intent.execution_stage_payload_hash is None:
        return None
    if version is None:
        return None
    try:
        payload = ExecutionStagePayload.model_validate_json(version.payload_json)
    except (ValidationError, ValueError, TypeError):
        return None
    if (
        version.id != intent.execution_stage_version_id
        or version.payload_hash != intent.execution_stage_payload_hash
        or canonical_stage_json(payload) != version.payload_json
        or stage_payload_hash(payload) != version.payload_hash
    ):
        return None
    return payload.stage


def _intent_terms_match(order: TradingOrder, intent: OrderIntent) -> bool:
    return (
        order.intent_id == intent.id
        and order.order_group_id == intent.order_group_id
        and order.account_alias == intent.account_alias
        and order.environment == intent.environment
        and order.symbol == intent.symbol
        and order.market == intent.market
        and order.side == intent.side
        and order.requested_quantity == intent.requested_quantity
        and order.remaining_quantity == order.requested_quantity
        and order.filled_quantity == 0
        and order.cancelled_quantity == 0
    )


def _other_order_conflict(db: Session, order: TradingOrder) -> bool:
    return (
        db.scalar(
            select(TradingOrder.id).where(
                TradingOrder.id != order.id,
                TradingOrder.account_alias == order.account_alias,
                TradingOrder.symbol == order.symbol,
                TradingOrder.status.in_(ACTIVE_CONFLICT_STATES),
            )
        )
        is not None
    )


def _current_snapshot(db: Session, order: TradingOrder) -> MarketSnapshot | None:
    stream = db.get(MarketStreamState, (order.market, order.symbol))
    if stream is None or stream.current_snapshot_id is None:
        return None
    return db.get(MarketSnapshot, stream.current_snapshot_id)


def _market_rules(
    db: Session,
    order: TradingOrder,
    *,
    now: datetime,
    quote_ttl: int,
) -> tuple[MarketSnapshot | None, list[dict[str, object]]]:
    stream = db.get(MarketStreamState, (order.market, order.symbol))
    snapshot = _current_snapshot(db, order)
    fresh = bool(
        snapshot is not None
        and stream is not None
        and stream.quality == "NORMAL"
        and stream.current_snapshot_id == snapshot.id
        and snapshot.quality == "NORMAL"
        and snapshot.trading_status == "TRADING"
        and _utc(snapshot.received_at) <= now
        and (now - _utc(snapshot.received_at)).total_seconds() <= quote_ttl
    )
    return snapshot, [
        rule("MARKET_DATA_FRESH", fresh),
        rule(
            "INSTRUMENT_TRADABLE",
            snapshot is not None
            and snapshot.quality == "NORMAL"
            and snapshot.trading_status == "TRADING",
        ),
        rule(
            "MARKET_SESSION_TRADABLE",
            classify_session(now) in TRADABLE_SESSIONS_BY_MARKET.get(order.market, set()),
        ),
        rule("NO_ACTIVE_OR_UNKNOWN_ORDER", not _other_order_conflict(db, order)),
    ]


def _persist_guard(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    rules: list[dict[str, object]],
    snapshot_id: str | None,
    position_version: int | None,
    execution_policy_version_id: str | None,
    risk_policy_version_id: str | None,
    valid_until: datetime | None,
    now: datetime,
) -> GuardEvaluation:
    return persist_guard_evaluation(
        db,
        execution_id=source_id if source_type == "DECISION_EXECUTION" else None,
        subject_type=source_type,
        subject_id=source_id,
        rules=rules,
        snapshot_id=snapshot_id,
        position_version=position_version,
        execution_policy_version_id=execution_policy_version_id,
        risk_policy_version_id=risk_policy_version_id,
        halt_scope=(
            "ENTRY_HALT" if any(item["result"] == "BLOCKED" for item in rules) else None
        ),
        valid_until=valid_until,
        now=now,
        phase="BROKER_SEND",
        stop_trigger_id=source_id if source_type == "STOP_TRIGGER" else None,
    )


def _decision_authority(
    db: Session,
    order: TradingOrder,
    intent: OrderIntent,
    *,
    settings: Settings,
    now: datetime,
    current_stage: ExecutionStage,
    frozen_stage: ExecutionStage,
) -> tuple[str | None, GuardEvaluation | None]:
    execution = db.get(DecisionExecution, intent.decision_execution_id)
    if (
        execution is None
        or intent.source_id != intent.decision_execution_id
        or intent.source_id != execution.id
        or execution.order_intent_id != intent.id
        or execution.execution_stage_version_id != intent.execution_stage_version_id
        or execution.execution_stage_payload_hash != intent.execution_stage_payload_hash
        or execution.execution_policy_version_id != intent.execution_policy_version_id
        or execution.risk_policy_version_id != intent.risk_policy_version_id
    ):
        return "SOURCE_AUTHORITY_INVALID", None
    decision = db.get(Decision, execution.decision_id)
    if decision is None:
        return "SOURCE_AUTHORITY_INVALID", None
    try:
        validate_sourced_execution_representation(decision, execution)
        validate_persisted_sourced_entry_decision(db, decision=decision)
    except (DecisionFinalizationError, ValueError):
        return "SOURCE_AUTHORITY_INVALID", None
    if decision.action != "BUY" or order.side != "BUY" or intent.action != "BUY":
        return "SOURCE_AUTHORITY_INVALID", None
    if now >= _utc(decision.valid_until):
        return "DECISION_EXPIRED", None
    expected_key = order_authority_key(
        source_type="DECISION_EXECUTION",
        source_id=execution.id,
        approval_id=intent.approval_id,
    )
    if intent.authority_key != expected_key:
        return "ORDER_AUTHORITY_KEY_INVALID", None
    effective_stage = effective_execution_stage(frozen_stage, current_stage)
    frozen_execution = db.get(ConfigurationVersion, execution.execution_policy_version_id)
    current_execution = _exact_active_configuration(
        db, category="EXECUTION_POLICY", target_id=execution.user_id
    )
    frozen_risk = db.get(ConfigurationVersion, execution.risk_policy_version_id)
    current_risk = _exact_active_configuration(
        db, category="RISK_POLICY", target_id=execution.user_id
    )
    try:
        if frozen_execution is None or current_execution is None:
            raise ValueError("execution policy unavailable")
        policy_payload(frozen_execution)
        current_execution_payload = policy_payload(current_execution)
        frozen_risk_payload = risk_policy_payload(frozen_risk)
        current_risk_payload = risk_policy_payload(current_risk)
        if frozen_risk is None or current_risk is None:
            raise ValueError("risk policy unavailable")
        effective_mode = effective_action_mode(
            ActionMode(execution.mode), ActionMode(current_execution_payload.buy)
        )
    except (ValidationError, ValueError, TypeError):
        return "CURRENT_POLICY_UNAVAILABLE", None
    if intent.approval_id is None:
        if (
            execution.approval_id is not None
            or ExecutionStage(execution.stage) is not ExecutionStage.MOCK_AUTOMATIC
            or effective_stage is not ExecutionStage.MOCK_AUTOMATIC
            or ActionMode(execution.mode) is not ActionMode.AUTOMATIC
            or effective_mode is not ActionMode.AUTOMATIC
        ):
            return "AUTOMATIC_AUTHORITY_REVOKED", None
    else:
        approval = db.get(Approval, intent.approval_id)
        if (
            approval is None
            or execution.approval_id != approval.id
            or approval.execution_id != execution.id
            or approval.decision_id != decision.id
            or approval.user_id != execution.user_id
            or approval.order_id != order.id
            or approval.state != "APPROVED"
            or _utc(approval.expires_at) <= now
            or ActionMode(execution.mode) is not ActionMode.MANUAL_APPROVAL
            or effective_mode is not ActionMode.MANUAL_APPROVAL
            or effective_stage is ExecutionStage.SHADOW
        ):
            return "APPROVAL_AUTHORITY_REVOKED", None
    if active_pause_entry(db, order.account_alias) is not None:
        return "EMERGENCY_STOP_ACTIVE", None
    snapshot = _current_snapshot(db, order)
    user = db.get(User, execution.user_id)
    if user is None:
        return "SOURCE_OWNER_UNAVAILABLE", None
    rules = list(
        buy_pre_order_guard_rules(
            db, decision, user, settings, frozen_risk_payload, now, snapshot=snapshot
        )
    )
    rules.extend(
        {
            **item,
            "code": f"CURRENT_{item['code']}",
        }
        for item in buy_pre_order_guard_rules(
            db, decision, user, settings, current_risk_payload, now, snapshot=snapshot
        )
    )
    rules.append(
        rule("STRICT_MOCK_AUTHORITY", _strict_mock(db, settings, order, intent))
    )
    _, market_rules = _market_rules(
        db,
        order,
        now=now,
        quote_ttl=min(
            frozen_risk_payload.quote_stale_seconds,
            current_risk_payload.quote_stale_seconds,
        ),
    )
    rules.extend(market_rules)
    price = order.limit_price
    if price is None:
        rules.append(rule("FINANCIAL_CONTEXT_INVALID", False))
    else:
        try:
            context = build_buy_financial_context(
                symbol=order.symbol,
                price=Decimal(price),
                quantity=order.requested_quantity,
                frozen_policy=frozen_risk_payload,
                current_policy=current_risk_payload,
            )
            rules.extend(
                financial_guard_rules(
                    db,
                    context=context,
                    now=now,
                    frozen_risk_policy_id=frozen_risk.id,
                    current_risk_policy_id=current_risk.id,
                    frozen_policy=frozen_risk_payload,
                    current_policy=current_risk_payload,
                )
            )
            notional = Decimal(price) * order.requested_quantity
            rules.extend(
                [
                    rule(
                        "FROZEN_ORDER_AMOUNT_ALLOWED",
                        frozen_risk_payload.entry_order_amount is not None
                        and notional <= Decimal(frozen_risk_payload.entry_order_amount)
                        and notional <= Decimal(frozen_risk_payload.max_single_order_amount),
                    ),
                    rule(
                        "CURRENT_ORDER_AMOUNT_ALLOWED",
                        current_risk_payload.entry_order_amount is not None
                        and notional <= Decimal(current_risk_payload.entry_order_amount)
                        and notional <= Decimal(current_risk_payload.max_single_order_amount),
                    ),
                ]
            )
        except (ValidationError, ValueError, TypeError):
            rules.append(rule("FINANCIAL_CONTEXT_INVALID", False))
    guard = _persist_guard(
        db,
        source_type="DECISION_EXECUTION",
        source_id=execution.id,
        rules=rules,
        snapshot_id=snapshot.id if snapshot else None,
        position_version=None,
        execution_policy_version_id=frozen_execution.id,
        risk_policy_version_id=frozen_risk.id,
        valid_until=decision.valid_until,
        now=now,
    )
    execution.guard_evaluation_id = guard.id
    reason = (
        blocking_code(rules)
        if any(item["result"] == "BLOCKED" for item in rules)
        else None
    )
    return reason, guard


def _stop_authority(
    db: Session,
    order: TradingOrder,
    intent: OrderIntent,
    *,
    settings: Settings,
    now: datetime,
    current_stage: ExecutionStage,
    frozen_stage: ExecutionStage,
) -> tuple[str | None, GuardEvaluation | None]:
    trigger = db.get(StopTrigger, intent.stop_trigger_id)
    if (
        trigger is None
        or intent.source_id != intent.stop_trigger_id
        or intent.source_id != trigger.id
        or intent.decision_execution_id is not None
        or intent.approval_id is not None
        or order.side != "SELL"
        or intent.action != "FIXED_STOP"
        or order.symbol != trigger.symbol
        or order.account_alias != trigger.account_alias
        or trigger.state != "FULFILLED"
        or intent.authority_key
        != order_authority_key(
            source_type="STOP_TRIGGER", source_id=trigger.id, approval_id=None
        )
    ):
        return "SOURCE_AUTHORITY_INVALID", None
    effective_stage = effective_execution_stage(frozen_stage, current_stage)
    if (
        frozen_stage is not ExecutionStage.MOCK_AUTOMATIC
        or effective_stage is not ExecutionStage.MOCK_AUTOMATIC
    ):
        return "EXECUTION_STAGE_DOWNGRADED", None
    frozen_execution = db.get(ConfigurationVersion, intent.execution_policy_version_id)
    frozen_risk = db.get(ConfigurationVersion, intent.risk_policy_version_id)
    if frozen_execution is None or frozen_risk is None:
        return "CURRENT_POLICY_UNAVAILABLE", None
    current_execution = _exact_active_configuration(
        db, category="EXECUTION_POLICY", target_id=frozen_execution.target_id
    )
    current_risk = _exact_active_configuration(
        db, category="RISK_POLICY", target_id=frozen_risk.target_id
    )
    try:
        if current_execution is None or current_risk is None:
            raise ValueError("current policy unavailable")
        frozen_execution_payload = policy_payload(frozen_execution)
        current_execution_payload = policy_payload(current_execution)
        frozen_risk_payload = risk_policy_payload(frozen_risk)
        current_risk_payload = risk_policy_payload(current_risk)
        effective_mode = effective_action_mode(
            ActionMode(frozen_execution_payload.fixed_stop_loss),
            ActionMode(current_execution_payload.fixed_stop_loss),
        )
    except (ValidationError, ValueError, TypeError):
        return "CURRENT_POLICY_UNAVAILABLE", None
    if effective_mode is not ActionMode.AUTOMATIC:
        return "ACTION_MODE_DOWNGRADED", None
    position = db.get(Position, trigger.position_id)
    reserved = int(
        db.scalar(
            select(func.coalesce(func.sum(TradingOrder.remaining_quantity), 0)).where(
                TradingOrder.id != order.id,
                TradingOrder.account_alias == order.account_alias,
                TradingOrder.symbol == order.symbol,
                TradingOrder.side == "SELL",
                TradingOrder.status.in_(ACTIVE_CONFLICT_STATES),
            )
        )
        or 0
    )
    available = (
        min(position.managed_quantity, position.available_quantity) - reserved
        if position is not None
        else 0
    )
    snapshot, market_rules = _market_rules(
        db,
        order,
        now=now,
        quote_ttl=min(
            frozen_risk_payload.quote_stale_seconds,
            current_risk_payload.quote_stale_seconds,
        ),
    )
    rules = [
        rule("STRICT_MOCK_AUTHORITY", _strict_mock(db, settings, order, intent)),
        rule(
            "POSITION_FOUND",
            position is not None and position.state == "OPEN" and position.quantity > 0,
        ),
        rule(
            "POSITION_ID_MATCH", position is not None and position.id == trigger.position_id
        ),
        rule(
            "POSITION_VERSION_MATCH",
            position is not None and position.version == trigger.position_version,
        ),
        rule(
            "POSITION_MANAGED_QUANTITY_POSITIVE",
            position is not None and position.managed_quantity > 0,
        ),
        rule("SELL_QUANTITY_AVAILABLE", available >= order.requested_quantity),
    ]
    rules.extend(market_rules)
    guard = _persist_guard(
        db,
        source_type="STOP_TRIGGER",
        source_id=trigger.id,
        rules=rules,
        snapshot_id=snapshot.id if snapshot else None,
        position_version=trigger.position_version,
        execution_policy_version_id=frozen_execution.id,
        risk_policy_version_id=frozen_risk.id,
        valid_until=None,
        now=now,
    )
    reason = (
        blocking_code(rules)
        if any(item["result"] == "BLOCKED" for item in rules)
        else None
    )
    return reason, guard


def _revocation_event(db: Session, order: TradingOrder, reason: str, now: datetime) -> None:
    payload = json.dumps({"reason": reason}, separators=(",", ":"), sort_keys=True)
    db.add(
        OrderEvent(
            order_id=order.id,
            event_type=REVOCATION_EVENT,
            source="CRESTA",
            source_key=f"{order.id}:{REVOCATION_EVENT}",
            payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
            payload_json=payload,
            correlation_id=order.correlation_id,
            occurred_at=now,
        )
    )


def _restore_stop_risk(
    db: Session, trigger: StopTrigger, *, reason: str, now: datetime
) -> None:
    event = db.get(RiskEvent, trigger.risk_event_id) if trigger.risk_event_id else None
    if event is None:
        event = create_risk_event(
            db,
            scope=RISK_EVENT_SCOPE_FIXED_STOP,
            rule_code=reason,
            severity="HIGH",
            account_alias=trigger.account_alias,
            symbol=trigger.symbol,
            input_snapshot_id=trigger.snapshot_id,
            input_record={
                "trigger_id": trigger.id,
                "position_id": trigger.position_id,
                "position_version": trigger.position_version,
                "reason": reason,
                "evaluated_at": _utc(now).isoformat(),
            },
            correlation_id=trigger.correlation_id,
            now=now,
        )
        trigger.risk_event_id = event.id
    else:
        event.state = "ACTIVE"
        event.resolution = None
        event.resolved_at = None
        event.updated_at = now
    trigger.state = "EXIT_PENDING"
    trigger.result_code = reason
    trigger.version += 1


def revoke_created_order(
    db: Session,
    order: TradingOrder,
    intent: OrderIntent | None,
    *,
    reason: str,
    now: datetime,
) -> PreSendAuthorityResult:
    if order.status != "CREATED":
        return PreSendAuthorityResult(PreSendStatus.REVOKED, reason)
    order.status = "INVALIDATED"
    order.version += 1
    _revocation_event(db, order, reason, now)
    if intent is not None and intent.source_type == "DECISION_EXECUTION":
        execution = db.get(DecisionExecution, intent.decision_execution_id)
        if execution is not None:
            execution.state = "FAILED_SAFE"
            execution.result_code = REVOCATION_RESULT
            execution.version += 1
            execution.updated_at = now
        if intent.approval_id is not None:
            approval = db.get(Approval, intent.approval_id)
            if approval is not None and approval.state == "APPROVED":
                approval.state = "INVALIDATED"
                approval.result_code = "EXECUTION_AUTHORITY_REVOKED"
                approval.version += 1
                approval.updated_at = now
    elif intent is not None and intent.source_type == "STOP_TRIGGER":
        trigger = db.get(StopTrigger, intent.stop_trigger_id)
        if trigger is not None:
            _restore_stop_risk(db, trigger, reason=reason, now=now)
    db.add(
        AuditLog(
            actor_type="SYSTEM",
            actor_id="KIWOOM_BROKER_WORKER",
            action=REVOCATION_EVENT,
            target=order.id,
            result=reason,
            correlation_id=order.correlation_id,
            metadata_json=json.dumps(
                {"reason": reason, "source_type": intent.source_type if intent else None},
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    )
    db.flush()
    return PreSendAuthorityResult(PreSendStatus.REVOKED, reason)


def validate_created_order_authority(
    db: Session,
    order: TradingOrder,
    *,
    settings: Settings,
    now: datetime,
    stage_evidence_loader: EvidenceLoader | None = None,
    stage_validation_policy: ExecutionStageValidationPolicy | None = None,
) -> PreSendAuthorityResult:
    try:
        intent = db.get(OrderIntent, order.intent_id)
        if intent is None or not _intent_terms_match(order, intent):
            return revoke_created_order(
                db, order, intent, reason="ORDER_SOURCE_UNCLASSIFIED", now=now
            )
        if intent.source_type is None:
            return revoke_created_order(
                db, order, intent, reason="ORDER_SOURCE_UNCLASSIFIED", now=now
            )
        if intent.source_type in {"BROKER_IMPORTED", "LEGACY_EXECUTION"}:
            return revoke_created_order(
                db, order, intent, reason="ORDER_SOURCE_NOT_SENDABLE", now=now
            )
        if intent.source_type == "BROKER_DIAGNOSTIC":
            allowed = (
                intent.source_id is not None
                and intent.authority_key is not None
                and intent.action == "MOCK_CONNECTION_TEST"
                and order.side == "BUY"
                and order.requested_quantity == 1
                and _strict_mock(db, settings, order, intent)
                and active_pause_entry(db, order.account_alias) is None
            )
            if not allowed:
                return revoke_created_order(
                    db, order, intent, reason="BROKER_DIAGNOSTIC_AUTHORITY_INVALID", now=now
                )
            return PreSendAuthorityResult(PreSendStatus.PASS)
        if intent.source_type not in {"DECISION_EXECUTION", "STOP_TRIGGER"}:
            return revoke_created_order(
                db, order, intent, reason="ORDER_SOURCE_UNCLASSIFIED", now=now
            )
        stage_version = db.get(ConfigurationVersion, intent.execution_stage_version_id)
        frozen_stage = _frozen_stage(intent, stage_version)
        if frozen_stage is None:
            return revoke_created_order(
                db, order, intent, reason="EXECUTION_STAGE_PROVENANCE_INVALID", now=now
            )
        resolution = resolve_current_execution_stage(
            db,
            now=now,
            evidence_loader=stage_evidence_loader,
            policy=stage_validation_policy,
        )
        if resolution.status is StageResolutionStatus.DB_RETRYABLE_FAILURE:
            db.rollback()
            return PreSendAuthorityResult(
                PreSendStatus.RETRYABLE, "EXECUTION_STAGE_DB_RETRYABLE_FAILURE"
            )
        if resolution.status is not StageResolutionStatus.PASS or resolution.payload is None:
            return revoke_created_order(
                db, order, intent, reason="EXECUTION_STAGE_UNAVAILABLE", now=now
            )
        if intent.source_type == "DECISION_EXECUTION":
            reason, guard = _decision_authority(
                db,
                order,
                intent,
                settings=settings,
                now=now,
                current_stage=resolution.payload.stage,
                frozen_stage=frozen_stage,
            )
        else:
            reason, guard = _stop_authority(
                db,
                order,
                intent,
                settings=settings,
                now=now,
                current_stage=resolution.payload.stage,
                frozen_stage=frozen_stage,
            )
        if reason is not None:
            result = revoke_created_order(db, order, intent, reason=reason, now=now)
            return PreSendAuthorityResult(result.status, result.reason, guard.id if guard else None)
        return PreSendAuthorityResult(
            PreSendStatus.PASS, guard_id=guard.id if guard else None
        )
    except SQLAlchemyError:
        db.rollback()
        return PreSendAuthorityResult(PreSendStatus.RETRYABLE, "DATABASE_RETRYABLE_FAILURE")


def reconcile_next_unsent_authority(
    db: Session,
    *,
    settings: Settings,
    now: datetime,
    stage_evidence_loader: EvidenceLoader | None = None,
    stage_validation_policy: ExecutionStageValidationPolicy | None = None,
) -> PreSendAuthorityResult | None:
    order = db.scalar(
        select(TradingOrder)
        .where(
            TradingOrder.account_alias == ACCOUNT_ALIAS,
            TradingOrder.environment == "MOCK",
            TradingOrder.status == "CREATED",
        )
        .order_by(TradingOrder.created_at, TradingOrder.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if order is None:
        db.rollback()
        return None
    result = validate_created_order_authority(
        db,
        order,
        settings=settings,
        now=now,
        stage_evidence_loader=stage_evidence_loader,
        stage_validation_policy=stage_validation_policy,
    )
    if result.status is PreSendStatus.REVOKED:
        db.commit()
    else:
        db.rollback()
    return result
