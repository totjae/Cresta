from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.decision_finalizer import (
    DecisionFinalizationError,
    validate_persisted_sourced_entry_decision,
)
from app.approvals import create_approval
from app.broker.kiwoom import KiwoomMockClient
from app.config import Settings
from app.decision_execution import (
    ACCOUNT_ALIAS,
    ACTIVE_ORDER_STATES,
    TRADABLE_SESSIONS_BY_MARKET,
    buy_pre_order_guard_rules,
)
from app.execution_authority import (
    SOURCED_EXECUTION_CONTRACT,
    ActionMode,
    ExecutionStage,
    effective_action_mode,
    effective_execution_stage,
    order_authority_key,
    sourced_execution_key,
    validate_sourced_execution_representation,
)
from app.execution_policy import active_policy, policy_payload
from app.execution_stage import (
    EvidenceLoader,
    ExecutionStageValidationPolicy,
    StageResolution,
    StageResolutionStatus,
    resolve_current_execution_stage,
)
from app.financial_authority import (
    build_buy_financial_context,
    configured_financial_client,
    financial_guard_rules,
    refresh_financial_evidence_if_needed,
)
from app.models import (
    AuditLog,
    Decision,
    DecisionExecution,
    DecisionInputSnapshot,
    GuardEvaluation,
    MarketSnapshot,
    TradingOrder,
    User,
)
from app.order_creation import (
    OrderAuthority,
    OrderCreationError,
    OrderRequest,
    create_order,
)
from app.risk_policy import active_risk_policy, risk_policy_payload
from app.venue_selection import classify_session

NO_ACTIONS = {"WAIT", "REJECT", "UNKNOWN"}
TERMINAL_AUDIT_ACTION = "SOURCED_DECISION_EXECUTION_TERMINAL"


class SourcedExecutionError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ReconciliationResult:
    scanned: int
    completed: int
    deferred: int
    failed: int


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _current(now: datetime | None) -> datetime:
    return _utc(now or datetime.now(UTC))


def _owner_id(db: Session, decision: Decision) -> str:
    if decision.decision_input_id:
        snapshot = db.get(DecisionInputSnapshot, decision.decision_input_id)
        if snapshot is not None:
            return snapshot.user_id
    raise SourcedExecutionError("SOURCE_OWNER_UNAVAILABLE")


def _find_existing(db: Session, decision_id: str) -> DecisionExecution | None:
    return db.scalar(
        select(DecisionExecution).where(
            DecisionExecution.decision_id == decision_id,
            DecisionExecution.contract_version == SOURCED_EXECUTION_CONTRACT,
        )
    )


def _validate_existing(
    decision: Decision, execution: DecisionExecution, *, user_id: str
) -> None:
    try:
        validate_sourced_execution_representation(decision, execution)
    except ValueError as exc:
        raise SourcedExecutionError("EXECUTION_IDENTITY_CONFLICT") from exc
    if (
        execution.account_alias != ACCOUNT_ALIAS
        or execution.user_id != user_id
        or execution.symbol != decision.symbol
        or execution.market != decision.market
        or execution.action
        != ("NO_ACTION" if decision.action in NO_ACTIONS else decision.action)
    ):
        raise SourcedExecutionError("EXECUTION_IDENTITY_CONFLICT")


def _audit(db: Session, execution: DecisionExecution) -> None:
    db.add(
        AuditLog(
            actor_type="SYSTEM",
            actor_id=execution.user_id,
            action=TERMINAL_AUDIT_ACTION,
            target=execution.id,
            result=execution.state,
            correlation_id=execution.correlation_id,
            metadata_json=json.dumps(
                {
                    "contract_version": execution.contract_version,
                    "decision_id": execution.decision_id,
                    "execution_key": execution.execution_key,
                    "result_code": execution.result_code,
                    "stage": execution.stage,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    )


def _new_execution(
    decision: Decision,
    *,
    user_id: str,
    correlation_id: str,
    action: str,
    state: str,
    result_code: str,
    mode: str | None = None,
    stage: str | None = None,
    execution_policy_version_id: str | None = None,
    risk_policy_version_id: str | None = None,
    execution_stage_version_id: str | None = None,
    execution_stage_payload_hash: str | None = None,
    now: datetime,
) -> DecisionExecution:
    return DecisionExecution(
        execution_key=sourced_execution_key(decision.id),
        decision_id=decision.id,
        user_id=user_id,
        account_alias=ACCOUNT_ALIAS,
        symbol=decision.symbol,
        market=decision.market,
        action=action,
        mode=mode,
        stage=stage,
        state=state,
        result_code=result_code,
        contract_version=SOURCED_EXECUTION_CONTRACT,
        execution_policy_version_id=execution_policy_version_id,
        risk_policy_version_id=risk_policy_version_id,
        execution_stage_version_id=execution_stage_version_id,
        execution_stage_payload_hash=execution_stage_payload_hash,
        correlation_id=correlation_id,
        created_at=now,
        updated_at=now,
    )


def _commit_new(db: Session, decision: Decision, execution: DecisionExecution) -> DecisionExecution:
    try:
        db.add(execution)
        db.flush()
        _audit(db, execution)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = _find_existing(db, decision.id)
        if existing is None:
            raise SourcedExecutionError("EXECUTION_DB_RETRYABLE_FAILURE", retryable=True) from exc
        _validate_existing(decision, existing, user_id=execution.user_id)
        return existing
    except SQLAlchemyError as exc:
        db.rollback()
        try:
            existing = _find_existing(db, decision.id)
        except SQLAlchemyError:
            existing = None
        if existing is not None:
            _validate_existing(decision, existing, user_id=execution.user_id)
            return existing
        raise SourcedExecutionError("EXECUTION_DB_RETRYABLE_FAILURE", retryable=True) from exc
    db.refresh(execution)
    return execution


def _safe_terminal(
    db: Session,
    decision: Decision,
    *,
    user_id: str,
    correlation_id: str,
    code: str,
    now: datetime,
) -> DecisionExecution:
    execution = _new_execution(
        decision,
        user_id=user_id,
        correlation_id=correlation_id,
        action=decision.action,
        state="FAILED_SAFE",
        result_code=code,
        now=now,
    )
    return _commit_new(db, decision, execution)


def _resolve_stage(
    db: Session,
    *,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    policy: ExecutionStageValidationPolicy | None,
) -> StageResolution:
    resolution = resolve_current_execution_stage(
        db, now=now, evidence_loader=evidence_loader, policy=policy
    )
    if resolution.status is StageResolutionStatus.DB_RETRYABLE_FAILURE:
        raise SourcedExecutionError("EXECUTION_STAGE_DB_RETRYABLE_FAILURE", retryable=True)
    return resolution


def _policies(db: Session, user_id: str):
    try:
        execution_version = active_policy(db, user_id)
        execution_payload = policy_payload(execution_version)
        risk_version = active_risk_policy(db, user_id)
        risk_payload = risk_policy_payload(risk_version)
    except SQLAlchemyError as exc:
        raise SourcedExecutionError("EXECUTION_POLICY_DB_RETRYABLE_FAILURE", retryable=True) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise SourcedExecutionError("EXECUTION_POLICY_INVALID") from exc
    return execution_version, execution_payload, risk_version, risk_payload


def _strict_mock_authority(settings: Settings) -> bool:
    return (
        settings.environment.upper() == "MOCK"
        and not settings.live_trading_enabled
        and settings.kiwoom_rest_base_url.rstrip("/") == "https://mockapi.kiwoom.com"
        and settings.kiwoom_ws_base_url.rstrip("/")
        == "wss://mockapi.kiwoom.com:10000"
    )


def _create_automatic_buy_order(
    db: Session,
    *,
    execution: DecisionExecution,
    decision: Decision,
    user: User,
    guard: GuardEvaluation,
    price: Decimal,
    quantity: int,
    correlation_id: str,
    now: datetime,
) -> None:
    authority_key = order_authority_key(
        source_type="DECISION_EXECUTION",
        source_id=execution.id,
        approval_id=None,
    )
    request = OrderRequest(
        symbol=decision.symbol,
        market=decision.market,
        side="BUY",
        action="BUY",
        order_type="LIMIT",
        limit_price=price,
        quantity=quantity,
        idempotency_key=authority_key,
        request_payload={
            "environment": "MOCK",
            "symbol": decision.symbol,
            "market": decision.market,
            "side": "BUY",
            "action": "BUY",
            "order_type": "LIMIT",
            "limit_price": str(price),
            "quantity": quantity,
            "approval_id": None,
            "authority_key": authority_key,
        },
        correlation_id=correlation_id,
    )
    order = create_order(
        db,
        user=user,
        request=request,
        audit_action="SOURCED_AUTOMATIC_ORDER_CREATED",
        now=now,
        authority=OrderAuthority(
            source_type="DECISION_EXECUTION",
            source_id=execution.id,
            decision_execution_id=execution.id,
            stop_trigger_id=None,
            guard_evaluation_id=guard.id,
            approval_id=None,
            execution_policy_version_id=execution.execution_policy_version_id,
            risk_policy_version_id=execution.risk_policy_version_id,
            execution_stage_version_id=str(execution.execution_stage_version_id),
            execution_stage_payload_hash=str(execution.execution_stage_payload_hash),
            authority_key=authority_key,
        ),
    )
    execution.state = "ORDER_CREATED"
    execution.result_code = "ORDER_CREATED"
    execution.order_intent_id = order.intent_id


def execute_sourced_entry_decision(
    db: Session,
    *,
    decision: Decision,
    correlation_id: str,
    settings: Settings,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    stage_evidence_loader: EvidenceLoader | None = None,
    stage_validation_policy: ExecutionStageValidationPolicy | None = None,
    financial_client: KiwoomMockClient | None = None,
    before_commit: Callable[[], None] | None = None,
) -> DecisionExecution:
    """Consume one finalized sourced ENTRY Decision under current MOCK authority."""
    current = _utc(clock()) if clock is not None else _current(now)
    user_id = _owner_id(db, decision)
    existing = _find_existing(db, decision.id)
    if existing is not None:
        _validate_existing(decision, existing, user_id=user_id)
        return existing
    try:
        validate_persisted_sourced_entry_decision(db, decision=decision)
    except DecisionFinalizationError:
        return _safe_terminal(
            db,
            decision,
            user_id=user_id,
            correlation_id=correlation_id,
            code="SOURCE_AUTHORITY_INVALID",
            now=current,
        )
    except SQLAlchemyError as exc:
        db.rollback()
        raise SourcedExecutionError("SOURCE_VALIDATION_DB_RETRYABLE_FAILURE", retryable=True) from exc

    if decision.action in NO_ACTIONS:
        execution = _new_execution(
            decision,
            user_id=user_id,
            correlation_id=correlation_id,
            action="NO_ACTION",
            state="NO_ACTION",
            result_code=decision.action,
            now=current,
        )
        return _commit_new(db, decision, execution)
    if decision.action != "BUY":
        return _safe_terminal(
            db,
            decision,
            user_id=user_id,
            correlation_id=correlation_id,
            code="SOURCE_AUTHORITY_INVALID",
            now=current,
        )
    if current >= _utc(decision.valid_until):
        return _safe_terminal(
            db,
            decision,
            user_id=user_id,
            correlation_id=correlation_id,
            code="DECISION_EXPIRED",
            now=current,
        )

    resolution = _resolve_stage(
        db,
        now=current,
        evidence_loader=stage_evidence_loader,
        policy=stage_validation_policy,
    )
    if resolution.status is not StageResolutionStatus.PASS:
        return _safe_terminal(
            db,
            decision,
            user_id=user_id,
            correlation_id=correlation_id,
            code="EXECUTION_STAGE_UNAVAILABLE",
            now=current,
        )
    assert resolution.payload is not None and resolution.version is not None
    frozen_stage = resolution.payload.stage

    user = db.get(User, user_id)
    if user is None:
        raise SourcedExecutionError("SOURCE_OWNER_UNAVAILABLE")
    execution_version, execution_payload, risk_version, risk_payload = _policies(db, user_id)
    frozen_mode = ActionMode(execution_payload.buy)
    snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
    intended_price = snapshot.best_ask_price if snapshot is not None else None
    entry_amount = risk_payload.entry_order_amount
    quantity = 0
    financial_context = None
    if intended_price is not None and intended_price > 0 and entry_amount is not None:
        quantity = int(Decimal(entry_amount) // intended_price)
        if quantity > 0:
            try:
                financial_context = build_buy_financial_context(
                    symbol=decision.symbol,
                    price=intended_price,
                    quantity=quantity,
                    frozen_policy=risk_payload,
                    current_policy=risk_payload,
                )
                refresh_financial_evidence_if_needed(
                    db,
                    client=financial_client or configured_financial_client(settings),
                    context=financial_context,
                    now=current,
                )
            except ValueError:
                financial_context = None
    execution = _new_execution(
        decision,
        user_id=user_id,
        correlation_id=correlation_id,
        action="BUY",
        state="ROUTING",
        result_code="ROUTING",
        mode=frozen_mode.value,
        stage=frozen_stage.value,
        execution_policy_version_id=execution_version.id if execution_version else None,
        risk_policy_version_id=risk_version.id if risk_version else None,
        execution_stage_version_id=resolution.version.id,
        execution_stage_payload_hash=resolution.version.payload_hash,
        now=current,
    )
    try:
        db.add(execution)
        db.flush()
        current_stage_resolution = _resolve_stage(
            db,
            now=current,
            evidence_loader=stage_evidence_loader,
            policy=stage_validation_policy,
        )
        if current_stage_resolution.status is not StageResolutionStatus.PASS:
            execution.mode = None
            execution.stage = None
            execution.execution_stage_version_id = None
            execution.execution_stage_payload_hash = None
            execution.state = "FAILED_SAFE"
            execution.result_code = "EXECUTION_STAGE_UNAVAILABLE"
        else:
            assert current_stage_resolution.payload is not None
            effective_stage = effective_execution_stage(
                frozen_stage, current_stage_resolution.payload.stage
            )
            _, current_execution_payload, current_risk_version, current_risk_payload = _policies(
                db, user_id
            )
            effective_mode = effective_action_mode(frozen_mode, current_execution_payload.buy)
            if effective_mode is ActionMode.DISABLED:
                execution.state = "DISABLED"
                execution.result_code = "ACTION_DISABLED"
            else:
                rules = list(
                    buy_pre_order_guard_rules(
                        db,
                        decision,
                        user,
                        settings,
                        risk_payload,
                        current,
                        snapshot=snapshot,
                    )
                )
                current_rules = list(
                    buy_pre_order_guard_rules(
                        db,
                        decision,
                        user,
                        settings,
                        current_risk_payload,
                        current,
                        snapshot=snapshot,
                    )
                )
                rules.extend(
                    [
                    {
                        **item,
                        "code": f"CURRENT_{item['code']}",
                    }
                    for item in current_rules
                    ]
                )
                frozen_entry_amount = risk_payload.entry_order_amount
                current_entry_amount = current_risk_payload.entry_order_amount
                if frozen_entry_amount is None or current_entry_amount is None:
                    quantity = 0
                elif intended_price is not None and intended_price > 0:
                    quantity = int(
                        Decimal(min(frozen_entry_amount, current_entry_amount))
                        // intended_price
                    )
                else:
                    quantity = 0
                if effective_stage is not ExecutionStage.SHADOW:
                    session = classify_session(current)
                    rules.extend(
                        [
                            {
                                "code": "MARKET_SESSION_TRADABLE",
                                "result": (
                                    "PASSED"
                                    if session
                                    in TRADABLE_SESSIONS_BY_MARKET.get(decision.market, set())
                                    else "BLOCKED"
                                ),
                            },
                            {
                                "code": "SYMBOL_TRADING_STATUS",
                                "result": (
                                    "PASSED"
                                    if snapshot is not None
                                    and snapshot.trading_status == "TRADING"
                                    and _utc(snapshot.received_at) <= current
                                    else "BLOCKED"
                                ),
                            },
                            {
                                "code": "NO_ACTIVE_OR_UNKNOWN_ORDER",
                                "result": (
                                    "PASSED"
                                    if db.scalar(
                                        select(TradingOrder.id).where(
                                            TradingOrder.account_alias == ACCOUNT_ALIAS,
                                            TradingOrder.symbol == decision.symbol,
                                            TradingOrder.status.in_(ACTIVE_ORDER_STATES),
                                        )
                                    )
                                    is None
                                    else "BLOCKED"
                                ),
                            },
                        ]
                    )
                if effective_stage is not ExecutionStage.SHADOW and (
                    risk_version is None or current_risk_version is None
                ):
                    rules.append({"code": "RISK_POLICY_UNAVAILABLE", "result": "BLOCKED"})
                if effective_stage is not ExecutionStage.SHADOW:
                    rules.append(
                        {
                            "code": "STRICT_MOCK_AUTHORITY",
                            "result": (
                                "PASSED"
                                if _strict_mock_authority(settings)
                                else "BLOCKED"
                            ),
                        }
                    )
                if effective_stage is ExecutionStage.SHADOW:
                    pass
                elif financial_context is None:
                    rules.append({"code": "FINANCIAL_CONTEXT_INVALID", "result": "BLOCKED"})
                else:
                    # Rebuild with frozen/current minimum before the authoritative select.
                    financial_context = build_buy_financial_context(
                        symbol=decision.symbol,
                        price=intended_price,
                        quantity=quantity,
                        frozen_policy=risk_payload,
                        current_policy=current_risk_payload,
                    )
                    rules.extend(
                        financial_guard_rules(
                            db,
                            context=financial_context,
                            now=current,
                            frozen_risk_policy_id=risk_version.id if risk_version else None,
                            current_risk_policy_id=(
                                current_risk_version.id if current_risk_version else None
                            ),
                            frozen_policy=risk_payload,
                            current_policy=current_risk_payload,
                        )
                    )
                boundary_now = _utc(clock()) if clock is not None else current
                try:
                    validate_persisted_sourced_entry_decision(db, decision=decision)
                except DecisionFinalizationError:
                    execution.mode = None
                    execution.stage = None
                    execution.execution_stage_version_id = None
                    execution.execution_stage_payload_hash = None
                    execution.state = "FAILED_SAFE"
                    execution.result_code = "SOURCE_AUTHORITY_INVALID"
                else:
                    if boundary_now >= _utc(decision.valid_until):
                        execution.mode = None
                        execution.stage = None
                        execution.execution_stage_version_id = None
                        execution.execution_stage_payload_hash = None
                        execution.state = "FAILED_SAFE"
                        execution.result_code = "DECISION_EXPIRED"
                    else:
                        blocked = [item for item in rules if item["result"] == "BLOCKED"]
                        guard = GuardEvaluation(
                            execution_id=execution.id,
                            phase="PRE_ORDER",
                            subject_type="DECISION_EXECUTION",
                            subject_id=execution.id,
                            result="BLOCKED" if blocked else "PASSED",
                            rule_results_json=json.dumps(
                                rules, separators=(",", ":"), sort_keys=True
                            ),
                            halt_scope="ENTRY_HALT" if blocked else None,
                            snapshot_id=decision.input_snapshot_id,
                            position_version=None,
                            execution_policy_version_id=(
                                execution_version.id if execution_version else None
                            ),
                            risk_policy_version_id=risk_version.id if risk_version else None,
                            evaluated_at=boundary_now,
                            valid_until=decision.valid_until,
                        )
                        db.add(guard)
                        db.flush()
                        execution.guard_evaluation_id = guard.id
                        if blocked:
                            execution.state = "GUARD_BLOCKED"
                            execution.result_code = str(blocked[0]["code"])
                        elif effective_stage is ExecutionStage.SHADOW:
                            execution.state = "SHADOW_RECORDED"
                            execution.result_code = "SHADOW_ONLY"
                        else:
                            boundary_stage_resolution = _resolve_stage(
                                db,
                                now=boundary_now,
                                evidence_loader=stage_evidence_loader,
                                policy=stage_validation_policy,
                            )
                            if boundary_stage_resolution.status is not StageResolutionStatus.PASS:
                                execution.state = "FAILED_SAFE"
                                execution.result_code = "EXECUTION_STAGE_UNAVAILABLE"
                            else:
                                assert boundary_stage_resolution.payload is not None
                                boundary_stage = effective_execution_stage(
                                    frozen_stage,
                                    boundary_stage_resolution.payload.stage,
                                )
                                _, boundary_execution_payload, _, _ = _policies(db, user_id)
                                boundary_mode = effective_action_mode(
                                    frozen_mode, boundary_execution_payload.buy
                                )
                                if boundary_mode is ActionMode.DISABLED:
                                    execution.state = "DISABLED"
                                    execution.result_code = "ACTION_DISABLED"
                                elif boundary_mode is ActionMode.MANUAL_APPROVAL:
                                    if boundary_stage is ExecutionStage.SHADOW:
                                        execution.state = "FAILED_SAFE"
                                        execution.result_code = "EXECUTION_STAGE_DOWNGRADED"
                                    else:
                                        approval = create_approval(
                                            db,
                                            execution=execution,
                                            decision=decision,
                                            user=user,
                                            settings=settings,
                                            now=boundary_now,
                                        )
                                        execution.approval_id = approval.id
                                        execution.state = "APPROVAL_PENDING"
                                        execution.result_code = "APPROVAL_PENDING"
                                elif boundary_stage is ExecutionStage.APPROVAL_ONLY:
                                    execution.state = "FAILED_SAFE"
                                    execution.result_code = (
                                        "AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY"
                                    )
                                elif boundary_stage is ExecutionStage.SHADOW:
                                    execution.state = "FAILED_SAFE"
                                    execution.result_code = "EXECUTION_STAGE_DOWNGRADED"
                                elif intended_price is None or quantity <= 0:
                                    execution.state = "FAILED_SAFE"
                                    execution.result_code = "ORDER_SIZE_NOT_CONFIGURED"
                                else:
                                    try:
                                        _create_automatic_buy_order(
                                            db,
                                            execution=execution,
                                            decision=decision,
                                            user=user,
                                            guard=guard,
                                            price=intended_price,
                                            quantity=quantity,
                                            correlation_id=correlation_id,
                                            now=boundary_now,
                                        )
                                    except OrderCreationError as exc:
                                        execution.state = "FAILED_SAFE"
                                        execution.result_code = exc.code
        execution.updated_at = current
        _audit(db, execution)
        if before_commit is not None:
            before_commit()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = _find_existing(db, decision.id)
        if existing is None:
            raise SourcedExecutionError("EXECUTION_DB_RETRYABLE_FAILURE", retryable=True) from exc
        _validate_existing(decision, existing, user_id=user_id)
        return existing
    except SourcedExecutionError:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        try:
            existing = _find_existing(db, decision.id)
        except SQLAlchemyError:
            existing = None
        if existing is not None:
            _validate_existing(decision, existing, user_id=user_id)
            return existing
        raise SourcedExecutionError("EXECUTION_DB_RETRYABLE_FAILURE", retryable=True) from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(execution)
    return execution


def reconcile_sourced_entry_executions(
    db: Session,
    *,
    settings: Settings,
    correlation_id_factory: Callable[[Decision], str],
    now: datetime | None = None,
    limit: int = 100,
    stage_evidence_loader: EvidenceLoader | None = None,
    stage_validation_policy: ExecutionStageValidationPolicy | None = None,
) -> ReconciliationResult:
    candidates = list(
        db.scalars(
            select(Decision)
            .where(
                Decision.schema_version == "sourced-entry-decision-v1",
                Decision.purpose == "TRADING",
                Decision.decision_kind == "ENTRY",
                Decision.validation_status == "VALID",
                ~Decision.id.in_(
                    select(DecisionExecution.decision_id).where(
                        DecisionExecution.contract_version == SOURCED_EXECUTION_CONTRACT
                    )
                ),
            )
            .order_by(Decision.created_at, Decision.id)
            .limit(limit)
        )
    )
    completed = deferred = failed = 0
    for decision in candidates:
        try:
            execute_sourced_entry_decision(
                db,
                decision=decision,
                correlation_id=correlation_id_factory(decision),
                settings=settings,
                now=now,
                stage_evidence_loader=stage_evidence_loader,
                stage_validation_policy=stage_validation_policy,
            )
            completed += 1
        except SourcedExecutionError as exc:
            if exc.retryable:
                deferred += 1
            else:
                failed += 1
    return ReconciliationResult(len(candidates), completed, deferred, failed)
