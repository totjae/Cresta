from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.activation_gate import (
    ActivationGateError,
    ActivationValidationPolicy,
    EvidenceLoader,
    GateOutcome,
    GateResolution,
    validate_frozen_activation_provenance,
    verify_frozen_v7_entry_activation_gate,
)
from app.agents.contracts import ArbiterResult, EntryArbiterInput
from app.agents.decision_context import (
    DecisionContextFreezeError,
    canonical_context_json,
    context_digest,
)
from app.agents.entry_arbiter import (
    CONSENSUS_POLICY_VERSION,
    ENTRY_ARBITER_ROLE,
    EntryArbiterError,
    arbiter_result_json,
    validate_entry_arbiter_stage,
)
from app.decision_contracts import (
    SOURCED_ENTRY_DECISION_SCHEMA,
    DecisionRepresentationError,
    validate_decision_representation,
)
from app.models import (
    AgentRun,
    AgentStageRun,
    AuditLog,
    Decision,
    DecisionContext,
    DecisionInputSnapshot,
)

FINALIZATION_IDENTITY_SCHEMA = "entry-finalization-identity-v1"
FINALIZATION_AUDIT_SCHEMA = "finalization-audit-v1"
V7_DAG_VERSION = "agent-dag-v7"
TERMINAL_RUN_STATES = frozenset({"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"})


class EntryFinalizationIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["entry-finalization-identity-v1"] = (
        FINALIZATION_IDENTITY_SCHEMA
    )
    agent_run_id: str
    decision_context_id: str
    decision_context_hash: str
    arbiter_stage_run_id: str
    arbiter_output_hash: str
    consensus_policy_version: Literal["consensus-policy-v1"]


class DecisionFinalizationError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class FinalizationSource:
    run: AgentRun
    context: DecisionContext
    arbiter_stage: AgentStageRun
    arbiter_input: EntryArbiterInput
    arbiter_result: ArbiterResult
    decision_input: DecisionInputSnapshot


@dataclass(frozen=True)
class FinalizationIntent:
    identity: EntryFinalizationIdentity
    evaluation_request_id: str
    values: dict[str, object]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(func.now()))
    if not isinstance(value, datetime):
        raise SQLAlchemyError("database clock did not return a timestamp")
    return _aware(value)


def finalization_identity_json(identity: EntryFinalizationIdentity) -> str:
    return canonical_context_json(identity.model_dump(mode="json"))


def finalization_evaluation_request_id(identity: EntryFinalizationIdentity) -> str:
    digest = hashlib.sha256(finalization_identity_json(identity).encode("utf-8")).hexdigest()
    return f"v7fin-{digest[:58]}"


def build_entry_finalization_identity(
    *,
    run: AgentRun,
    context: DecisionContext,
    arbiter_stage: AgentStageRun,
    arbiter_result: ArbiterResult,
) -> EntryFinalizationIdentity:
    if not arbiter_stage.output_hash:
        raise DecisionFinalizationError("SOURCE_CONFLICTED")
    identity = EntryFinalizationIdentity(
        agent_run_id=run.id,
        decision_context_id=context.id,
        decision_context_hash=context.context_hash,
        arbiter_stage_run_id=arbiter_stage.id,
        arbiter_output_hash=arbiter_stage.output_hash,
        consensus_policy_version=arbiter_result.policy_version,
    )
    if len(finalization_evaluation_request_id(identity)) != 64:
        raise DecisionFinalizationError("FINALIZATION_IDENTITY_CONFLICT")
    return identity


def validate_finalization_source(
    db: Session,
    *,
    run: AgentRun,
    now: datetime,
    allow_terminal: bool = False,
    lock_inputs: bool = True,
) -> FinalizationSource:
    if (
        run.dag_version != V7_DAG_VERSION
        or run.purpose != "TRADING"
        or run.analysis_context != "ENTRY"
        or run.activation_gate_version_id is None
        or run.activation_gate_version_hash is None
        or run.state
        not in ({"CREATED", "RUNNING", "SUCCEEDED"} if allow_terminal else {"CREATED", "RUNNING"})
    ):
        raise DecisionFinalizationError("SOURCE_CONFLICTED")
    context_statement = select(DecisionContext).where(DecisionContext.run_id == run.id)
    if lock_inputs:
        context_statement = context_statement.with_for_update()
    context = db.scalar(context_statement)
    if context is None:
        raise DecisionFinalizationError("SOURCE_CONFLICTED")
    if _aware(context.valid_until) <= now:
        raise DecisionFinalizationError("SOURCE_EXPIRED")
    stage_statement = select(AgentStageRun).where(
        AgentStageRun.run_id == run.id,
        AgentStageRun.role == ENTRY_ARBITER_ROLE,
    )
    if lock_inputs:
        stage_statement = stage_statement.with_for_update()
    stage = db.scalar(stage_statement)
    if (
        stage is None
        or stage.state != "SUCCEEDED"
        or stage.route_id is not None
        or stage.invocation_id is not None
        or stage.output_json is None
        or stage.output_hash is None
    ):
        raise DecisionFinalizationError("SOURCE_CONFLICTED")
    try:
        arbiter_input = validate_entry_arbiter_stage(
            db,
            stage=stage,
            run=run,
            now=now,
            lock_inputs=lock_inputs,
            allow_terminal=allow_terminal,
            validate_activation=False,
        )
        arbiter = ArbiterResult.model_validate_json(stage.output_json)
    except (EntryArbiterError, DecisionContextFreezeError, TypeError, ValueError) as exc:
        raise DecisionFinalizationError("SOURCE_CONFLICTED") from exc
    if (
        arbiter_result_json(arbiter) != stage.output_json
        or context_digest(stage.output_json) != stage.output_hash
        or arbiter.decision_context_id != context.id
        or arbiter.decision_context_hash != context.context_hash
        or arbiter.policy_version != CONSENSUS_POLICY_VERSION
        or arbiter.input_result_ids
        != [item.stage_run_id for item in arbiter_input.input_results]
        or arbiter.input_results != arbiter_input.input_results
        or arbiter.valid_until != arbiter_input.valid_until
    ):
        raise DecisionFinalizationError("SOURCE_CONFLICTED")
    arbiter_valid_until = _aware(datetime.fromisoformat(arbiter.valid_until))
    if arbiter_valid_until != _aware(context.valid_until) or arbiter_valid_until <= now:
        raise DecisionFinalizationError("SOURCE_EXPIRED")
    decision_input = db.get(DecisionInputSnapshot, context.decision_input_snapshot_id)
    if (
        decision_input is None
        or decision_input.purpose != "TRADING"
        or decision_input.user_id != run.owner_id
        or decision_input.market != run.market
        or decision_input.symbol != run.symbol
        or decision_input.market_snapshot_id != run.market_snapshot_id
        or decision_input.input_hash != run.input_hash
    ):
        raise DecisionFinalizationError("SOURCE_CONFLICTED")
    return FinalizationSource(run, context, stage, arbiter_input, arbiter, decision_input)


def build_sourced_entry_decision_intent(source: FinalizationSource) -> FinalizationIntent:
    identity = build_entry_finalization_identity(
        run=source.run,
        context=source.context,
        arbiter_stage=source.arbiter_stage,
        arbiter_result=source.arbiter_result,
    )
    evaluation_request_id = finalization_evaluation_request_id(identity)
    values: dict[str, object] = {
        "decision_input_id": source.decision_input.id,
        "purpose": "TRADING",
        "evaluation_request_id": evaluation_request_id,
        "input_snapshot_id": source.decision_input.market_snapshot_id,
        "symbol": source.decision_input.symbol,
        "market": source.decision_input.market,
        "decision_kind": "ENTRY",
        "model_provider": None,
        "model_id": None,
        "prompt_version": None,
        "schema_version": SOURCED_ENTRY_DECISION_SCHEMA,
        "scout_output_json": None,
        "core_output_json": None,
        "action": source.arbiter_result.action,
        "confidence": None,
        "risk_level": None,
        "reason_codes_json": canonical_context_json(source.arbiter_result.reason_codes),
        "valid_until": _aware(datetime.fromisoformat(source.arbiter_result.valid_until)),
        "configuration_version_id": None,
        "execution_mode": None,
        "execution_outcome": None,
        "validation_status": "VALID",
        "latency_ms": None,
        "source_agent_run_id": source.run.id,
        "source_stage_run_id": source.arbiter_stage.id,
        "source_stage_output_hash": source.arbiter_stage.output_hash,
    }
    try:
        validate_decision_representation(values)
    except DecisionRepresentationError as exc:
        raise DecisionFinalizationError("SOURCE_CONFLICTED") from exc
    return FinalizationIntent(identity, evaluation_request_id, values)


def _matches_intent(decision: Decision, intent: FinalizationIntent) -> bool:
    for field, expected in intent.values.items():
        actual = getattr(decision, field)
        if isinstance(expected, datetime):
            if not isinstance(actual, datetime) or _aware(actual) != expected:
                return False
        elif actual != expected:
            return False
    try:
        return validate_decision_representation(decision) == "SOURCED_V7"
    except DecisionRepresentationError:
        return False


def validate_persisted_sourced_entry_decision(
    db: Session, *, decision: Decision
) -> FinalizationSource:
    if decision.source_agent_run_id is None:
        raise DecisionFinalizationError("SOURCE_CONFLICTED")
    run = db.get(AgentRun, decision.source_agent_run_id)
    if run is None or run.state != "SUCCEEDED" or run.completed_at is None:
        raise DecisionFinalizationError("SOURCE_CONFLICTED")
    observed = min(_aware(run.completed_at), _aware(run.valid_until))
    source = validate_finalization_source(
        db,
        run=run,
        now=observed,
        allow_terminal=True,
        lock_inputs=False,
    )
    intent = build_sourced_entry_decision_intent(source)
    if not _matches_intent(decision, intent):
        raise DecisionFinalizationError("SOURCE_CONFLICTED")
    return source


def _existing_decision(db: Session, intent: FinalizationIntent) -> Decision | None:
    matches = list(
        db.scalars(
            select(Decision)
            .where(
                or_(
                    Decision.evaluation_request_id == intent.evaluation_request_id,
                    Decision.source_agent_run_id == intent.values["source_agent_run_id"],
                    Decision.source_stage_run_id == intent.values["source_stage_run_id"],
                )
            )
            .with_for_update()
        )
    )
    if not matches:
        return None
    if len(matches) != 1 or not _matches_intent(matches[0], intent):
        raise DecisionFinalizationError("FINALIZATION_IDENTITY_CONFLICT")
    return matches[0]


def _audit_metadata(
    *,
    run: AgentRun,
    decision: Decision | None,
    source: FinalizationSource | None,
    intent: FinalizationIntent | None,
    retryable: bool,
) -> str:
    return canonical_context_json(
        {
            "schema_version": FINALIZATION_AUDIT_SCHEMA,
            "agent_run_id": run.id,
            "decision_id": decision.id if decision is not None else None,
            "evaluation_request_id": (
                intent.evaluation_request_id if intent is not None else None
            ),
            "decision_context_id": source.context.id if source is not None else None,
            "source_stage_run_id": (
                source.arbiter_stage.id if source is not None else None
            ),
            "source_stage_output_hash": (
                source.arbiter_stage.output_hash if source is not None else None
            ),
            "activation_gate_version_id": run.activation_gate_version_id,
            "activation_gate_version_hash": run.activation_gate_version_hash,
            "retryable": retryable,
        }
    )


def _append_audit_once(
    db: Session,
    *,
    run: AgentRun,
    action: str,
    result: str,
    decision: Decision | None,
    source: FinalizationSource | None,
    intent: FinalizationIntent | None,
    retryable: bool = False,
) -> None:
    existing = db.scalar(
        select(AuditLog.id).where(
            AuditLog.actor_type == "SYSTEM",
            AuditLog.actor_id == run.owner_id,
            AuditLog.action == action,
            AuditLog.target == run.id,
            AuditLog.correlation_id == run.id,
        )
    )
    if existing is not None:
        return
    db.add(
        AuditLog(
            actor_type="SYSTEM",
            actor_id=run.owner_id,
            action=action,
            target=run.id,
            result=result,
            correlation_id=run.id,
            metadata_json=_audit_metadata(
                run=run,
                decision=decision,
                source=source,
                intent=intent,
                retryable=retryable,
            ),
        )
    )


def _gate_resolution(
    db: Session,
    *,
    run: AgentRun,
    now: datetime,
    evidence_loader: EvidenceLoader | None,
    validation_policy: ActivationValidationPolicy | None,
) -> GateResolution:
    try:
        validate_frozen_activation_provenance(db, run=run)
    except ActivationGateError:
        return GateResolution(GateOutcome.INVALID)
    assert run.activation_gate_version_id is not None
    assert run.activation_gate_version_hash is not None
    return verify_frozen_v7_entry_activation_gate(
        db,
        frozen_version_id=run.activation_gate_version_id,
        frozen_payload_hash=run.activation_gate_version_hash,
        now=now,
        evidence_loader=evidence_loader,
        policy=validation_policy,
        lock=True,
    )


_TERMINAL_OUTCOMES = {
    "ACTIVATION_GATE_CLOSED": ("CANCELLED", "BLOCKED"),
    "ACTIVATION_GATE_SUPERSEDED": ("CANCELLED", "BLOCKED"),
    "ACTIVATION_GATE_INVALID": ("FAILED", "INVALID"),
    "SOURCE_EXPIRED": ("FAILED", "EXPIRED"),
    "SOURCE_CONFLICTED": ("FAILED", "CONFLICTED"),
    "FINALIZATION_IDENTITY_CONFLICT": ("FAILED", "CONFLICTED"),
}


def _gate_error_code(resolution: GateResolution) -> str:
    return {
        GateOutcome.CLOSED: "ACTIVATION_GATE_CLOSED",
        GateOutcome.SUPERSEDED: "ACTIVATION_GATE_SUPERSEDED",
        GateOutcome.INVALID: "ACTIVATION_GATE_INVALID",
        GateOutcome.DB_RETRYABLE_FAILURE: "FINALIZATION_DB_RETRYABLE_FAILURE",
    }[resolution.outcome]


def _persist_terminal_failure(
    db: Session,
    *,
    run_id: str,
    code: str,
    source: FinalizationSource | None,
    intent: FinalizationIntent | None,
) -> None:
    run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if run is None or run.state in TERMINAL_RUN_STATES:
        db.rollback()
        return
    state, result = _TERMINAL_OUTCOMES[code]
    run.state = state
    run.error_code = code
    run.completed_at = run.completed_at or _database_now(db)
    _append_audit_once(
        db,
        run=run,
        action=code,
        result=result,
        decision=None,
        source=source,
        intent=intent,
    )
    db.commit()


def _persist_retryable_failure(db: Session, *, run_id: str) -> None:
    try:
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None or run.state in TERMINAL_RUN_STATES:
            db.rollback()
            return
        run.state = "RUNNING"
        run.error_code = "FINALIZATION_DB_RETRYABLE_FAILURE"
        run.completed_at = None
        _append_audit_once(
            db,
            run=run,
            action="FINALIZATION_DB_RETRYABLE_FAILURE",
            result="RETRYABLE_FAILURE",
            decision=None,
            source=None,
            intent=None,
            retryable=True,
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()


def _finish_success(
    db: Session,
    *,
    source: FinalizationSource,
    intent: FinalizationIntent,
    decision: Decision,
) -> None:
    run = source.run
    run.state = "SUCCEEDED"
    run.error_code = None
    run.completed_at = run.completed_at or _database_now(db)
    _append_audit_once(
        db,
        run=run,
        action="FINALIZATION_SUCCEEDED",
        result="SUCCEEDED",
        decision=decision,
        source=source,
        intent=intent,
    )


def finalize_entry_decision(
    db: Session,
    *,
    run_id: str,
    evidence_loader: EvidenceLoader | None,
    validation_policy: ActivationValidationPolicy | None = None,
    write_boundary_hook: Callable[[], None] | None = None,
    _race_retry: bool = True,
) -> Decision:
    source: FinalizationSource | None = None
    intent: FinalizationIntent | None = None
    try:
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None:
            raise DecisionFinalizationError("FINALIZATION_RUN_NOT_FOUND", 404)
        if run.purpose == "DIAGNOSTIC":
            raise DecisionFinalizationError("FINALIZATION_RUN_NOT_ELIGIBLE")
        if run.state in {"FAILED", "CANCELLED", "PARTIAL"}:
            raise DecisionFinalizationError(run.error_code or "FINALIZATION_RUN_NOT_ELIGIBLE")
        if run.state == "SUCCEEDED":
            decision = db.scalar(
                select(Decision).where(Decision.source_agent_run_id == run.id)
            )
            if decision is None:
                raise DecisionFinalizationError("FINALIZATION_IDENTITY_CONFLICT")
            retry_time = min(_aware(run.completed_at or run.valid_until), _aware(run.valid_until))
            source = validate_finalization_source(
                db,
                run=run,
                now=retry_time,
                allow_terminal=True,
            )
            intent = build_sourced_entry_decision_intent(source)
            if not _matches_intent(decision, intent):
                raise DecisionFinalizationError("FINALIZATION_IDENTITY_CONFLICT")
            db.commit()
            return decision

        observed = _database_now(db)
        gate = _gate_resolution(
            db,
            run=run,
            now=observed,
            evidence_loader=evidence_loader,
            validation_policy=validation_policy,
        )
        if gate.outcome != GateOutcome.PASS:
            raise DecisionFinalizationError(_gate_error_code(gate))
        try:
            source = validate_finalization_source(db, run=run, now=observed)
        except DecisionFinalizationError as exc:
            if exc.code in _TERMINAL_OUTCOMES:
                raise
            raise DecisionFinalizationError("SOURCE_CONFLICTED") from exc
        intent = build_sourced_entry_decision_intent(source)

        existing = _existing_decision(db, intent)
        if existing is not None:
            _finish_success(db, source=source, intent=intent, decision=existing)
            db.commit()
            return existing

        decision = Decision(**intent.values)
        db.add(decision)
        db.flush()
        if write_boundary_hook is not None:
            write_boundary_hook()

        boundary_time = _database_now(db)
        source = validate_finalization_source(db, run=run, now=boundary_time)
        boundary_intent = build_sourced_entry_decision_intent(source)
        if boundary_intent != intent:
            raise DecisionFinalizationError("SOURCE_CONFLICTED")
        gate = _gate_resolution(
            db,
            run=run,
            now=boundary_time,
            evidence_loader=evidence_loader,
            validation_policy=validation_policy,
        )
        if gate.outcome != GateOutcome.PASS:
            raise DecisionFinalizationError(_gate_error_code(gate))
        _finish_success(db, source=source, intent=intent, decision=decision)
        db.commit()
        db.refresh(decision)
        return decision
    except IntegrityError:
        db.rollback()
        if _race_retry:
            return finalize_entry_decision(
                db,
                run_id=run_id,
                evidence_loader=evidence_loader,
                validation_policy=validation_policy,
                write_boundary_hook=None,
                _race_retry=False,
            )
        _persist_terminal_failure(
            db,
            run_id=run_id,
            code="FINALIZATION_IDENTITY_CONFLICT",
            source=source,
            intent=intent,
        )
        raise DecisionFinalizationError("FINALIZATION_IDENTITY_CONFLICT")
    except DecisionFinalizationError as exc:
        db.rollback()
        if exc.code in _TERMINAL_OUTCOMES:
            _persist_terminal_failure(
                db,
                run_id=run_id,
                code=exc.code,
                source=source,
                intent=intent,
            )
        elif exc.code == "FINALIZATION_DB_RETRYABLE_FAILURE":
            _persist_retryable_failure(db, run_id=run_id)
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        _persist_retryable_failure(db, run_id=run_id)
        raise DecisionFinalizationError(
            "FINALIZATION_DB_RETRYABLE_FAILURE", 503
        ) from exc


def reconcile_v7_entry_finalizations(
    db: Session,
    *,
    evidence_loader: EvidenceLoader,
    validation_policy: ActivationValidationPolicy | None = None,
    run_id: str | None = None,
    limit: int = 10,
) -> int:
    statement = (
        select(AgentRun.id)
        .join(AgentStageRun, AgentStageRun.run_id == AgentRun.id)
        .where(
            AgentRun.dag_version == V7_DAG_VERSION,
            AgentRun.purpose == "TRADING",
            AgentRun.analysis_context == "ENTRY",
            AgentRun.state == "RUNNING",
            AgentStageRun.role == ENTRY_ARBITER_ROLE,
            AgentStageRun.state == "SUCCEEDED",
        )
        .order_by(AgentRun.created_at)
        .limit(limit)
    )
    if run_id is not None:
        statement = statement.where(AgentRun.id == run_id)
    candidates = list(db.scalars(statement))
    reconciled = 0
    for candidate_id in candidates:
        try:
            finalize_entry_decision(
                db,
                run_id=candidate_id,
                evidence_loader=evidence_loader,
                validation_policy=validation_policy,
            )
            reconciled += 1
        except DecisionFinalizationError:
            db.rollback()
            terminal = db.scalar(select(AgentRun.state).where(AgentRun.id == candidate_id))
            if terminal in {"FAILED", "CANCELLED"}:
                reconciled += 1
    return reconciled


def reconcile_v7_diagnostic_lifecycle(
    db: Session,
    *,
    run_id: str | None = None,
    limit: int = 10,
) -> int:
    statement = (
        select(AgentRun.id)
        .join(AgentStageRun, AgentStageRun.run_id == AgentRun.id)
        .where(
            AgentRun.dag_version == V7_DAG_VERSION,
            AgentRun.purpose == "DIAGNOSTIC",
            AgentRun.analysis_context == "ENTRY",
            AgentRun.state.in_(("CREATED", "RUNNING")),
            AgentStageRun.role == ENTRY_ARBITER_ROLE,
            AgentStageRun.state.in_(("SUCCEEDED", "CONFLICTED", "TIMED_OUT", "FAILED")),
        )
        .order_by(AgentRun.created_at)
        .limit(limit)
    )
    if run_id is not None:
        statement = statement.where(AgentRun.id == run_id)
    run_ids = list(db.scalars(statement))
    closed = 0
    for candidate_id in run_ids:
        run = db.scalar(
            select(AgentRun).where(AgentRun.id == candidate_id).with_for_update()
        )
        stage = db.scalar(
            select(AgentStageRun)
            .where(
                AgentStageRun.run_id == candidate_id,
                AgentStageRun.role == ENTRY_ARBITER_ROLE,
            )
            .with_for_update()
        )
        if run is None or stage is None or run.state not in {"CREATED", "RUNNING"}:
            db.rollback()
            continue
        completed = _aware(stage.completed_at or _database_now(db))
        if stage.state == "SUCCEEDED":
            try:
                validate_entry_arbiter_stage(
                    db,
                    stage=stage,
                    run=run,
                    now=completed,
                    lock_inputs=True,
                )
                arbiter = ArbiterResult.model_validate_json(stage.output_json)
                if (
                    stage.output_json is None
                    or stage.output_hash is None
                    or arbiter_result_json(arbiter) != stage.output_json
                    or context_digest(stage.output_json) != stage.output_hash
                ):
                    raise ValueError("diagnostic Arbiter output mismatch")
                run.state = "SUCCEEDED"
                run.error_code = None
            except (EntryArbiterError, TypeError, ValueError):
                run.state = "FAILED"
                run.error_code = "ENTRY_ARBITER_FAILED"
        else:
            run.state = "FAILED"
            run.error_code = {
                "CONFLICTED": "ENTRY_ARBITER_CONFLICTED",
                "TIMED_OUT": "ENTRY_ARBITER_TIMED_OUT",
                "FAILED": "ENTRY_ARBITER_FAILED",
            }[stage.state]
        run.completed_at = run.completed_at or _database_now(db)
        db.commit()
        closed += 1
    return closed
