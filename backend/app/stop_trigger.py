from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.execution_authority import ActionMode, ExecutionStage, order_authority_key
from app.execution_policy import policy_payload
from app.execution_stage import (
    EvidenceLoader,
    ExecutionStageValidationPolicy,
    StageResolutionStatus,
    resolve_current_execution_stage,
)
from app.ids import uuid7
from app.models import (
    ConfigurationVersion,
    GuardEvaluation,
    MarketSnapshot,
    MarketStreamState,
    Position,
    RiskEvent,
    StopTrigger,
    TradingGate,
    TradingOrder,
)
from app.risk_events import (
    RISK_EVENT_SCOPE_FIXED_STOP,
    create_risk_event,
    resolve_risk_event,
)
from app.risk_policy import SAFE_DEFAULT_POLICY, risk_policy_payload
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
CONFIG_SCOPE = "USER_DEFAULT"
EXECUTION_POLICY_CATEGORY = "EXECUTION_POLICY"
RISK_POLICY_CATEGORY = "RISK_POLICY"


class StopTriggerError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FixedStopActionAuthority:
    stage: ExecutionStage
    stage_version: ConfigurationVersion
    execution_policy_version: ConfigurationVersion
    mode: ActionMode


def _exact_active_configuration(
    db: Session, category: str
) -> ConfigurationVersion | None:
    versions = list(
        db.scalars(
            select(ConfigurationVersion)
            .where(
                ConfigurationVersion.scope == CONFIG_SCOPE,
                ConfigurationVersion.category == category,
                ConfigurationVersion.state == "ACTIVE",
            )
            .order_by(ConfigurationVersion.sequence.desc(), ConfigurationVersion.id)
            .limit(2)
        )
    )
    return versions[0] if len(versions) == 1 else None


def _fixed_stop_action_authority(
    db: Session,
    *,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    validation_policy: ExecutionStageValidationPolicy | None,
) -> FixedStopActionAuthority | None:
    resolution = resolve_current_execution_stage(
        db,
        now=now,
        evidence_loader=evidence_loader,
        policy=validation_policy,
    )
    if resolution.status is StageResolutionStatus.DB_RETRYABLE_FAILURE:
        raise StopTriggerError("EXECUTION_STAGE_DB_RETRYABLE_FAILURE")
    if (
        resolution.status is not StageResolutionStatus.PASS
        or resolution.payload is None
        or resolution.version is None
        or resolution.payload.target != "MOCK"
    ):
        return None
    version = _exact_active_configuration(db, EXECUTION_POLICY_CATEGORY)
    if version is None:
        return None
    try:
        mode = ActionMode(policy_payload(version).fixed_stop_loss)
    except (ValidationError, ValueError, TypeError):
        return None
    return FixedStopActionAuthority(
        stage=resolution.payload.stage,
        stage_version=resolution.version,
        execution_policy_version=version,
        mode=mode,
    )


def _strict_mock_authority(
    db: Session, settings: Settings, trigger: StopTrigger
) -> bool:
    gate = db.get(TradingGate, trigger.account_alias)
    return (
        trigger.account_alias == ACCOUNT_ALIAS
        and gate is not None
        and gate.environment == "MOCK"
        and settings.environment.upper() == "MOCK"
        and not settings.live_trading_enabled
        and settings.kiwoom_rest_base_url.rstrip("/") == "https://mockapi.kiwoom.com"
        and settings.kiwoom_ws_base_url.rstrip("/")
        == "wss://mockapi.kiwoom.com:10000"
    )


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
    db: Session,
    account_alias: str,
    symbol: str,
    managed_quantity: int,
    broker_available_quantity: int,
) -> int:
    reserved = db.scalar(
        select(func.coalesce(func.sum(TradingOrder.remaining_quantity), 0)).where(
            TradingOrder.account_alias == account_alias,
            TradingOrder.symbol == symbol,
            TradingOrder.side == "SELL",
            TradingOrder.status.in_(ACTIVE_SELL_STATES),
        )
    )
    broker_sellable_managed = min(managed_quantity, broker_available_quantity)
    return max(0, broker_sellable_managed - int(reserved or 0))


def _managed_quantity(position: Position) -> int:
    return max(0, int(position.managed_quantity))


def _managed_average_price(position: Position) -> Decimal:
    if _managed_quantity(position) > 0:
        return Decimal(position.managed_average_price)
    # Purely external positions may still produce a blocked trigger for audit
    # visibility. They can never pass POSITION_MANAGED_QUANTITY_POSITIVE.
    return Decimal(position.average_price)


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


class _SystemAuditActor:
    """Minimal stand-in for the order-creation audit actor.

    The fixed-stop trigger is system-driven (no user session), but
    ``create_order`` records a ``User.id``-shaped ``actor_id`` on the audit
    log. ``AuditLog.actor_id`` is a free ``String(36)`` (not a FK), so a stable
    sentinel identifier is safe and keeps the audit trail honest about who
    acted.
    """

    id = "SYSTEM_FIXED_STOP_TRIGGER"


def _system_audit_user(db: Session) -> _SystemAuditActor:
    return _SystemAuditActor()


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
    managed_quantity = _managed_quantity(position)
    sellable = _sell_quantity_available(
        db,
        trigger.account_alias,
        trigger.symbol,
        managed_quantity,
        position.available_quantity,
    )
    blocking_order = _has_blocking_order(db, trigger.account_alias, trigger.symbol)
    fresh = bool(
        snapshot is not None
        and snapshot.quality == "NORMAL"
        and snapshot.trading_status == "TRADING"
        and (_utc(snapshot.received_at) - now).total_seconds() <= 0
        and (now - _utc(snapshot.received_at)).total_seconds() <= risk_policy.quote_stale_seconds
    )
    session = classify_session(now)
    return [
        _rule("POSITION_FOUND", position.state == "OPEN" and position.quantity > 0),
        _rule(
            "POSITION_VERSION_MATCH",
            position.version == trigger.position_version,
        ),
        _rule(
            "POSITION_MANAGED_QUANTITY_POSITIVE",
            managed_quantity > 0,
        ),
        _rule(
            "SELL_QUANTITY_AVAILABLE",
            sellable >= managed_quantity,
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
        "schema_version": "fixed-stop-input-v2",
        "policy_version": STOP_POLICY_VERSION,
        "account_alias": ACCOUNT_ALIAS,
        "position_id": position.id,
        "position_version": position.version,
        "symbol": position.symbol,
        "broker_average_price": str(position.average_price),
        "managed_average_price": str(position.managed_average_price),
        "total_quantity": position.quantity,
        "available_quantity": position.available_quantity,
        "managed_quantity": position.managed_quantity,
        "external_quantity": position.quantity - position.managed_quantity,
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
        execution_id=None,
        stop_trigger_id=trigger.id,
        phase="PRE_ORDER",
        subject_type="STOP_TRIGGER",
        subject_id=trigger.id,
        result="BLOCKED" if blocked else "PASSED",
        rule_results_json=json.dumps(rules, separators=(",", ":"), sort_keys=True),
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


def _set_exit_pending(
    db: Session,
    *,
    trigger: StopTrigger,
    position: Position,
    snapshot: MarketSnapshot | None,
    code: str,
    now: datetime,
) -> None:
    event = db.get(RiskEvent, trigger.risk_event_id) if trigger.risk_event_id else None
    if event is None or event.state != "ACTIVE":
        event = create_risk_event(
            db,
            scope=RISK_EVENT_SCOPE_FIXED_STOP,
            rule_code=code,
            severity="HIGH",
            account_alias=trigger.account_alias,
            symbol=trigger.symbol,
            input_record={
                "position_id": position.id,
                "position_version": position.version,
                "trigger_id": trigger.id,
                "rule_code": code,
                "evaluated_at": _utc(now).isoformat(),
            },
            correlation_id=trigger.correlation_id,
            input_snapshot_id=snapshot.id if snapshot else None,
            now=now,
        )
        trigger.risk_event_id = event.id
    trigger.state = "EXIT_PENDING"
    trigger.result_code = code


def _apply_fixed_stop_authority(
    db: Session,
    *,
    trigger: StopTrigger,
    position: Position,
    snapshot: MarketSnapshot | None,
    guard: GuardEvaluation,
    stop_price: Decimal,
    settings: Settings,
    now: datetime,
    stage_evidence_loader: EvidenceLoader | None,
    stage_validation_policy: ExecutionStageValidationPolicy | None,
) -> None:
    authority = _fixed_stop_action_authority(
        db,
        now=now,
        evidence_loader=stage_evidence_loader,
        validation_policy=stage_validation_policy,
    )
    if authority is None:
        _set_exit_pending(
            db,
            trigger=trigger,
            position=position,
            snapshot=snapshot,
            code="FIXED_STOP_ACTION_AUTHORITY_UNAVAILABLE",
            now=now,
        )
        return
    if authority.stage is ExecutionStage.SHADOW:
        if trigger.risk_event_id:
            resolve_risk_event(db, trigger.risk_event_id, resolution="SHADOW_ONLY", now=now)
        trigger.state = "SHADOW_RECORDED"
        trigger.result_code = "SHADOW_ONLY"
        return
    if authority.stage is ExecutionStage.APPROVAL_ONLY:
        _set_exit_pending(
            db,
            trigger=trigger,
            position=position,
            snapshot=snapshot,
            code="AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY",
            now=now,
        )
        return
    if authority.mode is not ActionMode.AUTOMATIC:
        _set_exit_pending(
            db,
            trigger=trigger,
            position=position,
            snapshot=snapshot,
            code=(
                "ACTION_DISABLED"
                if authority.mode is ActionMode.DISABLED
                else "FIXED_STOP_MANUAL_AUTHORITY_UNAVAILABLE"
            ),
            now=now,
        )
        return
    if not _strict_mock_authority(db, settings, trigger):
        _set_exit_pending(
            db,
            trigger=trigger,
            position=position,
            snapshot=snapshot,
            code="STRICT_MOCK_AUTHORITY_REQUIRED",
            now=now,
        )
        return
    if trigger.risk_policy_version_id is None:
        _set_exit_pending(
            db,
            trigger=trigger,
            position=position,
            snapshot=snapshot,
            code="RISK_POLICY_UNAVAILABLE",
            now=now,
        )
        return
    _emit_fixed_stop_order(
        db,
        trigger=trigger,
        position=position,
        snapshot=snapshot,
        guard=guard,
        stop_price=stop_price,
        correlation_id=trigger.correlation_id,
        now=now,
        authority=authority,
    )


def _existing_trigger(
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
                StopTrigger.state.in_(("PENDING", "SHADOW_RECORDED", "EXIT_PENDING")),
            )
        )
    )
    resolved_at = now or datetime.now(UTC)
    for trigger in stale:
        _supersede(trigger)
        if trigger.risk_event_id:
            resolve_risk_event(db, trigger.risk_event_id, resolution="SUPERSEDED", now=resolved_at)


def _evaluate_position(
    db: Session,
    *,
    position: Position,
    settings: Settings,
    now: datetime,
    correlation_id: str,
    stage_evidence_loader: EvidenceLoader | None,
    stage_validation_policy: ExecutionStageValidationPolicy | None,
) -> None:
    """Evaluate one open position for a fixed stop trigger.

    Persists the typed trigger/Guard evidence. Only validated MOCK_AUTOMATIC plus
    explicit AUTOMATIC fixed-stop authority may add a CREATED MOCK SELL order;
    Decision, DecisionExecution and Approval are never synthesized.
    """
    risk_config = _exact_active_configuration(db, RISK_POLICY_CATEGORY)
    risk_policy = (
        risk_policy_payload(risk_config) if risk_config is not None else SAFE_DEFAULT_POLICY
    )
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
        _managed_average_price(position), risk_policy.fixed_stop_loss_pct
    )
    input_record = _trigger_input_record(
        position=position,
        risk_policy_version_id=risk_policy_version_id,
        risk_policy=risk_policy,
        stop_price=stop_price,
        quote=quote,
        now=now,
    )

    existing = _existing_trigger(
        db,
        position_id=position.id,
        position_version=position.version,
        risk_policy_version_id=risk_policy_version_id,
    )

    if existing is not None:
        if existing.state == "FULFILLED":
            return
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
            stage_evidence_loader=stage_evidence_loader,
            stage_validation_policy=stage_validation_policy,
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
        rule_code = str(next((r["code"] for r in rules if r["result"] == "BLOCKED"), "BLOCKED"))
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
        _apply_fixed_stop_authority(
            db,
            trigger=trigger,
            position=position,
            snapshot=snapshot,
            guard=guard,
            stop_price=stop_price,
            settings=settings,
            now=now,
            stage_evidence_loader=stage_evidence_loader,
            stage_validation_policy=stage_validation_policy,
        )
    trigger.updated_at = now
    db.flush()


def _emit_fixed_stop_order(
    db: Session,
    *,
    trigger: StopTrigger,
    position: Position,
    snapshot: MarketSnapshot | None,
    guard: GuardEvaluation,
    stop_price: Decimal,
    correlation_id: str,
    now: datetime,
    authority: FixedStopActionAuthority,
) -> None:
    """Create the CREATED SELL order for a firing fixed-stop trigger.

    Uses the best bid (MARKETABLE_LIMIT sell) and only the Cresta-managed
    quantity. Broker-external shares in a MIXED position are never included.
    On success the trigger becomes FULFILLED and its risk event is resolved. If
    order creation fails the trigger stays EXIT_PENDING so the next tick retries.
    """
    from app.order_creation import (
        OrderAuthority,
        OrderCreationError,
        OrderRequest,
        create_order,
    )

    if snapshot is None or snapshot.best_bid_price is None or snapshot.best_bid_price <= 0:
        trigger.state = "EXIT_PENDING"
        trigger.result_code = "NO_FRESH_EXECUTABLE_KRX_QUOTE"
        return
    price = snapshot.best_bid_price
    quantity = _sell_quantity_available(
        db,
        trigger.account_alias,
        trigger.symbol,
        _managed_quantity(position),
        position.available_quantity,
    )
    if quantity <= 0:
        _set_exit_pending(
            db,
            trigger=trigger,
            position=position,
            snapshot=snapshot,
            code="SELL_QUANTITY_AVAILABLE",
            now=now,
        )
        return
    authority_key = order_authority_key(
        source_type="STOP_TRIGGER", source_id=trigger.id, approval_id=None
    )
    request = OrderRequest(
        symbol=position.symbol,
        market="KRX",
        side="SELL",
        action="FIXED_STOP",
        order_type="LIMIT",
        limit_price=price,
        quantity=quantity,
        idempotency_key=authority_key,
        request_payload={
            "environment": "MOCK",
            "symbol": position.symbol,
            "market": "KRX",
            "side": "SELL",
            "action": "FIXED_STOP",
            "order_type": "LIMIT",
            "limit_price": str(price),
            "quantity": quantity,
            "trigger_id": trigger.id,
            "position_id": position.id,
            "position_version": position.version,
            "stop_price": str(stop_price),
            "authority_key": authority_key,
        },
        correlation_id=correlation_id,
    )
    # The order creation service needs a User for the audit row; the stop
    # trigger is system-driven so reuse a system sentinel by looking up the
    # owning user is not available at account scope. We pass a minimal system
    # actor via a throwaway User-shaped object only for the audit actor_id.
    system_user = _system_audit_user(db)
    try:
        create_order(
            db,
            user=system_user,
            request=request,
            audit_action="FIXED_STOP_SELL_ORDER_CREATED",
            now=now,
            authority=OrderAuthority(
                source_type="STOP_TRIGGER",
                source_id=trigger.id,
                decision_execution_id=None,
                stop_trigger_id=trigger.id,
                guard_evaluation_id=guard.id,
                approval_id=None,
                execution_policy_version_id=authority.execution_policy_version.id,
                risk_policy_version_id=trigger.risk_policy_version_id,
                execution_stage_version_id=authority.stage_version.id,
                execution_stage_payload_hash=authority.stage_version.payload_hash,
                authority_key=authority_key,
            ),
        )
    except OrderCreationError as exc:
        trigger.state = "EXIT_PENDING"
        trigger.result_code = exc.code
        return
    trigger.state = "FULFILLED"
    trigger.result_code = "ORDER_CREATED"
    if trigger.risk_event_id:
        resolve_risk_event(db, trigger.risk_event_id, resolution="ORDER_CREATED", now=now)


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
    stage_evidence_loader: EvidenceLoader | None,
    stage_validation_policy: ExecutionStageValidationPolicy | None,
) -> None:
    """Re-evaluate an existing active trigger with the latest state.

    Keeps the trigger row (idempotent) and transitions between SHADOW_RECORDED
    and EXIT_PENDING as conditions change. Resolves any associated risk event
    when the trigger passes, so EXIT_PENDING does not linger after recovery.
    """
    if position.state != "OPEN" or position.quantity <= 0:
        _supersede(trigger)
        if trigger.risk_event_id:
            resolve_risk_event(db, trigger.risk_event_id, resolution="POSITION_CLOSED", now=now)
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
        rule_code = str(next((r["code"] for r in rules if r["result"] == "BLOCKED"), "BLOCKED"))
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
        _apply_fixed_stop_authority(
            db,
            trigger=trigger,
            position=position,
            snapshot=snapshot,
            guard=guard,
            stop_price=stop_price,
            settings=settings,
            now=now,
            stage_evidence_loader=stage_evidence_loader,
            stage_validation_policy=stage_validation_policy,
        )
    trigger.updated_at = now
    db.flush()


def run_fixed_stop_triggers(
    db: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
    correlation_id: str | None = None,
    account_alias: str = ACCOUNT_ALIAS,
    stage_evidence_loader: EvidenceLoader | None = None,
    stage_validation_policy: ExecutionStageValidationPolicy | None = None,
    before_commit: Callable[[], None] | None = None,
) -> int:
    """Evaluate all open positions for the fixed stop trigger.

    Returns the number of positions evaluated. The caller (broker worker) is
    responsible for lease ownership; this function evaluates every open position
    so that availability blocks (BROKER_NOT_READY, RECONCILING, stale data) are
    recorded as ``EXIT_PENDING`` rather than silently skipped — a stop signal must
    persist across data gaps and broker outages (GRD-085, EXE-053). A CREATED
    MOCK SELL is possible only through the Phase 10E authority matrix; this
    function never submits to Broker or creates Approval/Decision resources.
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
    try:
        for position in positions:
            _evaluate_position(
                db,
                position=position,
                settings=settings,
                now=evaluated_at,
                correlation_id=correlation_id or uuid7(),
                stage_evidence_loader=stage_evidence_loader,
                stage_validation_policy=stage_validation_policy,
            )
        if before_commit is not None:
            before_commit()
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(positions)


def recover_exit_pending(
    db: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
    account_alias: str = ACCOUNT_ALIAS,
    stage_evidence_loader: EvidenceLoader | None = None,
    stage_validation_policy: ExecutionStageValidationPolicy | None = None,
    before_commit: Callable[[], None] | None = None,
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
    risk_config = _exact_active_configuration(db, RISK_POLICY_CATEGORY)
    risk_policy = (
        risk_policy_payload(risk_config) if risk_config is not None else SAFE_DEFAULT_POLICY
    )
    risk_policy_version_id = risk_config.id if risk_config else None

    try:
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
                _managed_average_price(position), risk_policy.fixed_stop_loss_pct
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
                stage_evidence_loader=stage_evidence_loader,
                stage_validation_policy=stage_validation_policy,
            )
        if before_commit is not None:
            before_commit()
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(triggers)
