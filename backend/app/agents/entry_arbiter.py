from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.activation_gate import ActivationGateError, validate_frozen_activation_provenance
from app.agents.contracts import (
    ArbiterResult,
    DecisionAgentResult,
    EntryArbiterInput,
    EntryArbiterInputResult,
)
from app.agents.decision_context import (
    V7_DAG_VERSION,
    DecisionContextFreezeError,
    canonical_context_json,
    context_digest,
    validate_decision_context_integrity,
)
from app.agents.policy_profiles import (
    ROLE_AGENT_TYPES,
    PolicyProfileError,
    resolve_decision_agent_policy,
)
from app.agents.reason_codes import ARBITER_PATTERN_REASONS
from app.models import AgentRun, AgentStageRun, DecisionContext

logger = logging.getLogger("cresta.entry_arbiter")

ENTRY_ARBITER_ROLE = "ENTRY_ARBITER"
ENTRY_ARBITER_SEQUENCE = 80
ENTRY_ARBITER_INPUT_VERSION = "entry-arbiter-input-v1"
ENTRY_ARBITER_RESULT_VERSION = "entry-consensus-v1"
CONSENSUS_POLICY_VERSION = "consensus-policy-v1"
ENTRY_ARBITER_ROLE_ORDER = tuple(ROLE_AGENT_TYPES)
ENTRY_ARBITER_DEPENDENCIES = ENTRY_ARBITER_ROLE_ORDER
DECISION_AGENT_TERMINAL_STATES = {
    "SUCCEEDED",
    "INSUFFICIENT_DATA",
    "CONFLICTED",
    "TIMED_OUT",
    "FAILED",
    "INVALID_OUTPUT",
}


class EntryArbiterError(Exception):
    def __init__(
        self,
        code: str,
        status_code: int = 422,
        *,
        failure_state: str = "CONFLICTED",
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.failure_state = failure_state


@dataclass(frozen=True)
class ConsensusOutcome:
    action: str
    decision_pattern: str
    reason_code: str


@dataclass(frozen=True)
class PreparedEntryArbiter:
    stage_id: str
    run_id: str
    fencing_token: int
    input: EntryArbiterInput
    input_hash: str


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    normalized = _aware(value)
    return normalized.isoformat(
        timespec="seconds" if normalized.microsecond == 0 else "microseconds"
    )


def entry_arbiter_input_json(value: EntryArbiterInput) -> str:
    return canonical_context_json(value.model_dump(mode="json"))


def entry_arbiter_input_hash(value: EntryArbiterInput) -> str:
    return context_digest(entry_arbiter_input_json(value))


def arbiter_result_json(value: ArbiterResult) -> str:
    return canonical_context_json(value.model_dump(mode="json"))


def arbiter_result_hash(value: ArbiterResult) -> str:
    return context_digest(arbiter_result_json(value))


def _load_decision_results(
    db: Session,
    *,
    run: AgentRun,
    context: DecisionContext,
    now: datetime,
    lock: bool,
    allow_terminal: bool = False,
    validate_activation: bool = True,
) -> tuple[EntryArbiterInputResult, ...]:
    observed = _aware(now)
    if _aware(run.valid_until) <= observed or _aware(context.valid_until) <= observed:
        raise EntryArbiterError(
            "ENTRY_ARBITER_INPUT_EXPIRED", failure_state="TIMED_OUT"
        )
    try:
        validate_decision_context_integrity(
            db,
            run=run,
            context=context,
            now=observed,
            allow_terminal=allow_terminal,
            validate_activation=validate_activation,
        )
    except DecisionContextFreezeError as exc:
        raise EntryArbiterError("ENTRY_ARBITER_CONTEXT_INVALID") from exc

    statement = select(AgentStageRun).where(
        AgentStageRun.run_id == run.id,
        AgentStageRun.role.in_(ENTRY_ARBITER_ROLE_ORDER),
    )
    if lock:
        statement = statement.with_for_update()
    stages = list(db.scalars(statement))
    by_role = {stage.role: stage for stage in stages}
    if len(stages) != len(ENTRY_ARBITER_ROLE_ORDER) or set(by_role) != set(
        ENTRY_ARBITER_ROLE_ORDER
    ):
        raise EntryArbiterError("ENTRY_ARBITER_RESULTS_NOT_READY")

    valid_until = _timestamp(context.valid_until)
    normalized: list[EntryArbiterInputResult] = []
    for role in ENTRY_ARBITER_ROLE_ORDER:
        stage = by_role[role]
        if (
            stage.run_id != run.id
            or stage.state not in DECISION_AGENT_TERMINAL_STATES
            or not stage.output_json
            or not stage.output_hash
        ):
            raise EntryArbiterError("ENTRY_ARBITER_RESULT_INVALID")
        try:
            payload = json.loads(stage.output_json)
            canonical = canonical_context_json(payload)
            result = DecisionAgentResult.model_validate(payload)
        except (TypeError, ValueError) as exc:
            raise EntryArbiterError("ENTRY_ARBITER_RESULT_INVALID") from exc
        if (
            canonical != stage.output_json
            or context_digest(canonical) != stage.output_hash
            or result.stage_run_id != stage.id
            or result.role != role
            or result.agent_type != ROLE_AGENT_TYPES[role]
            or result.status != stage.state
            or result.decision_context_id != context.id
            or result.decision_context_hash != context.context_hash
            or result.valid_until != valid_until
            or stage.route_id != result.route_id
        ):
            raise EntryArbiterError("ENTRY_ARBITER_RESULT_INVALID")
        try:
            _, policy = resolve_decision_agent_policy(db, run_id=run.id, role=role)
        except PolicyProfileError as exc:
            raise EntryArbiterError("ENTRY_ARBITER_POLICY_INVALID") from exc
        if (
            result.policy_profile_id != policy.configuration_version_id
            or result.policy_profile_hash != policy.payload_hash
            or result.policy_profile_version != policy.sequence
            or result.policy_profile_category != policy.category
        ):
            raise EntryArbiterError("ENTRY_ARBITER_POLICY_INVALID")
        normalized.append(
            EntryArbiterInputResult(
                role=role,
                agent_type=result.agent_type,
                stage_run_id=stage.id,
                output_hash=stage.output_hash,
                status=result.status,
                action=result.action,
            )
        )
    return tuple(normalized)


def build_entry_arbiter_input(
    db: Session,
    *,
    run_id: str,
    now: datetime | None = None,
    lock: bool = False,
    allow_terminal: bool = False,
    validate_activation: bool = True,
) -> EntryArbiterInput:
    observed = _aware(now or datetime.now(UTC))
    run_statement = select(AgentRun).where(AgentRun.id == run_id)
    context_statement = select(DecisionContext).where(DecisionContext.run_id == run_id)
    if lock:
        run_statement = run_statement.with_for_update()
        context_statement = context_statement.with_for_update()
    run = db.scalar(run_statement)
    context = db.scalar(context_statement)
    if (
        run is None
        or context is None
        or run.dag_version != V7_DAG_VERSION
        or run.purpose not in {"DIAGNOSTIC", "TRADING"}
        or run.analysis_context != "ENTRY"
        or run.state
        not in ({"CREATED", "RUNNING", "SUCCEEDED"} if allow_terminal else {"CREATED", "RUNNING"})
    ):
        raise EntryArbiterError("ENTRY_ARBITER_RUN_NOT_ELIGIBLE")
    if validate_activation:
        try:
            validate_frozen_activation_provenance(db, run=run)
        except ActivationGateError as exc:
            raise EntryArbiterError("ENTRY_ARBITER_RUN_NOT_ELIGIBLE") from exc
    results = _load_decision_results(
        db,
        run=run,
        context=context,
        now=observed,
        lock=lock,
        allow_terminal=allow_terminal,
        validate_activation=validate_activation,
    )
    return EntryArbiterInput(
        decision_context_id=context.id,
        decision_context_hash=context.context_hash,
        input_results=list(results),
        valid_until=_timestamp(context.valid_until),
    )


def evaluate_consensus(
    input_results: list[EntryArbiterInputResult]
    | tuple[EntryArbiterInputResult, ...],
) -> ConsensusOutcome:
    ordered = list(input_results)
    expected = list(ENTRY_ARBITER_ROLE_ORDER)
    if [item.role for item in ordered] != expected:
        raise ValueError("Arbiter evaluator requires canonical C/B/A ordering")
    if any(item.status != "SUCCEEDED" or item.action == "UNKNOWN" for item in ordered):
        pattern = "MANDATORY_UNKNOWN"
    else:
        actions = [item.action for item in ordered]
        reject_count = actions.count("REJECT")
        if reject_count >= 2:
            pattern = "MULTIPLE_REJECT"
        elif reject_count == 1:
            pattern = "SINGLE_REJECT"
        elif actions == ["BUY", "BUY", "BUY"]:
            pattern = "ALL_BUY"
        elif actions[1] == "BUY" and sorted((actions[0], actions[2])) == ["BUY", "WAIT"]:
            pattern = "BALANCED_PLUS_ONE_BUY"
        else:
            pattern = "DEFAULT_WAIT"
    action, reason_code = ARBITER_PATTERN_REASONS[pattern]
    return ConsensusOutcome(
        action=action,
        decision_pattern=pattern,
        reason_code=reason_code,
    )


def build_arbiter_result(value: EntryArbiterInput) -> ArbiterResult:
    outcome = evaluate_consensus(value.input_results)
    return ArbiterResult(
        decision_context_id=value.decision_context_id,
        decision_context_hash=value.decision_context_hash,
        action=outcome.action,
        input_result_ids=[item.stage_run_id for item in value.input_results],
        input_results=value.input_results,
        decision_pattern=outcome.decision_pattern,
        reason_codes=[outcome.reason_code],
        valid_until=value.valid_until,
    )


def validate_entry_arbiter_stage(
    db: Session,
    *,
    stage: AgentStageRun,
    run: AgentRun,
    now: datetime,
    lock_inputs: bool = False,
    allow_terminal: bool = False,
    validate_activation: bool = True,
) -> EntryArbiterInput:
    if (
        stage.run_id != run.id
        or stage.role != ENTRY_ARBITER_ROLE
        or stage.sequence != ENTRY_ARBITER_SEQUENCE
        or stage.route_id is not None
        or stage.invocation_id is not None
        or stage.dependency_roles_json
        != canonical_context_json(list(ENTRY_ARBITER_DEPENDENCIES))
    ):
        raise EntryArbiterError("ENTRY_ARBITER_STAGE_INVALID")
    value = build_entry_arbiter_input(
        db,
        run_id=run.id,
        now=now,
        lock=lock_inputs,
        allow_terminal=allow_terminal,
        validate_activation=validate_activation,
    )
    if stage.input_hash != entry_arbiter_input_hash(value):
        raise EntryArbiterError("ENTRY_ARBITER_INPUT_HASH_MISMATCH")
    return value


def materialize_entry_arbiter_stage(
    db: Session,
    *,
    run_id: str,
    now: datetime | None = None,
) -> AgentStageRun:
    observed = _aware(now or datetime.now(UTC))
    try:
        value = build_entry_arbiter_input(db, run_id=run_id, now=observed, lock=True)
        input_hash = entry_arbiter_input_hash(value)
        existing = db.scalar(
            select(AgentStageRun)
            .where(
                AgentStageRun.run_id == run_id,
                AgentStageRun.role == ENTRY_ARBITER_ROLE,
            )
            .with_for_update()
        )
        dependency_json = canonical_context_json(list(ENTRY_ARBITER_DEPENDENCIES))
        if existing is not None:
            if (
                existing.sequence != ENTRY_ARBITER_SEQUENCE
                or existing.dependency_roles_json != dependency_json
                or existing.route_id is not None
                or existing.invocation_id is not None
                or existing.input_hash != input_hash
                or existing.max_attempts != 2
            ):
                raise EntryArbiterError(
                    "ENTRY_ARBITER_MATERIALIZATION_CONFLICT", 409
                )
            db.commit()
            return existing
        stage = AgentStageRun(
            run_id=run_id,
            role=ENTRY_ARBITER_ROLE,
            sequence=ENTRY_ARBITER_SEQUENCE,
            dependency_roles_json=dependency_json,
            route_id=None,
            state="PENDING",
            input_hash=input_hash,
            max_attempts=2,
            available_at=observed,
        )
        db.add(stage)
        db.flush()
        db.commit()
        return stage
    except (EntryArbiterError, DecisionContextFreezeError, PolicyProfileError):
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise EntryArbiterError(
            "ENTRY_ARBITER_MATERIALIZATION_CONFLICT", 409
        ) from exc


def _terminalize_failure(
    stage: AgentStageRun,
    *,
    state: str,
    error_code: str,
    now: datetime,
) -> None:
    stage.state = state
    stage.output_json = None
    stage.output_hash = None
    stage.error_code = error_code
    stage.completed_at = now
    stage.lease_owner_id = None
    stage.lease_expires_at = None
    stage.heartbeat_at = now


def prepare_entry_arbiter_execution(
    db: Session,
    *,
    stage: AgentStageRun,
    run: AgentRun,
    fencing_token: int,
    worker_id: str,
    now: datetime,
) -> PreparedEntryArbiter:
    if (
        stage.state != "RUNNING"
        or stage.lease_owner_id != worker_id
        or stage.fencing_token != fencing_token
        or stage.lease_expires_at is None
        or _aware(stage.lease_expires_at) <= _aware(now)
    ):
        raise EntryArbiterError("ENTRY_ARBITER_CLAIM_INVALID")
    value = validate_entry_arbiter_stage(
        db,
        stage=stage,
        run=run,
        now=now,
        lock_inputs=True,
    )
    return PreparedEntryArbiter(
        stage_id=stage.id,
        run_id=run.id,
        fencing_token=fencing_token,
        input=value,
        input_hash=entry_arbiter_input_hash(value),
    )


def complete_entry_arbiter_execution(
    db: Session,
    *,
    prepared: PreparedEntryArbiter,
    result: ArbiterResult | None,
    worker_id: str,
    now: datetime,
    internal_error: bool = False,
) -> bool:
    observed = _aware(now)
    stage = db.scalar(
        select(AgentStageRun)
        .where(AgentStageRun.id == prepared.stage_id)
        .with_for_update()
    )
    if (
        stage is None
        or stage.state != "RUNNING"
        or stage.lease_owner_id != worker_id
        or stage.fencing_token != prepared.fencing_token
        or stage.lease_expires_at is None
        or _aware(stage.lease_expires_at) <= observed
    ):
        db.rollback()
        return False
    run = db.scalar(select(AgentRun).where(AgentRun.id == stage.run_id).with_for_update())
    if run is None:
        db.rollback()
        return False
    if internal_error:
        _terminalize_failure(
            stage,
            state="FAILED",
            error_code="ENTRY_ARBITER_INTERNAL_ERROR",
            now=observed,
        )
        db.commit()
        return True
    try:
        fresh = validate_entry_arbiter_stage(
            db,
            stage=stage,
            run=run,
            now=observed,
            lock_inputs=True,
        )
        if (
            fresh != prepared.input
            or entry_arbiter_input_hash(fresh) != prepared.input_hash
            or result is None
            or result != build_arbiter_result(fresh)
        ):
            raise EntryArbiterError("ENTRY_ARBITER_COMPLETION_CONFLICT")
    except EntryArbiterError as exc:
        _terminalize_failure(
            stage,
            state=exc.failure_state,
            error_code=exc.code,
            now=observed,
        )
        db.commit()
        return True
    encoded = arbiter_result_json(result)
    stage.state = "SUCCEEDED"
    stage.output_json = encoded
    stage.output_hash = context_digest(encoded)
    stage.error_code = None
    stage.completed_at = observed
    stage.lease_owner_id = None
    stage.lease_expires_at = None
    stage.heartbeat_at = observed
    db.commit()
    return True


def execute_entry_arbiter_stage(
    db: Session,
    *,
    claim_stage_id: str,
    fencing_token: int,
    worker_id: str,
) -> bool:
    stage = db.scalar(
        select(AgentStageRun)
        .where(AgentStageRun.id == claim_stage_id)
        .with_for_update()
    )
    if (
        stage is None
        or stage.state != "RUNNING"
        or stage.lease_owner_id != worker_id
        or stage.fencing_token != fencing_token
    ):
        db.rollback()
        return False
    run = db.get(AgentRun, stage.run_id)
    if run is None:
        db.rollback()
        return False
    try:
        prepared = prepare_entry_arbiter_execution(
            db,
            stage=stage,
            run=run,
            fencing_token=fencing_token,
            worker_id=worker_id,
            now=datetime.now(UTC),
        )
    except EntryArbiterError as exc:
        _terminalize_failure(
            stage,
            state=exc.failure_state,
            error_code=exc.code,
            now=datetime.now(UTC),
        )
        db.commit()
        return True
    db.commit()
    try:
        result = build_arbiter_result(prepared.input)
    except Exception:
        logger.exception("ENTRY_ARBITER evaluator failed stage=%s", prepared.stage_id)
        return complete_entry_arbiter_execution(
            db,
            prepared=prepared,
            result=None,
            worker_id=worker_id,
            now=datetime.now(UTC),
            internal_error=True,
        )
    return complete_entry_arbiter_execution(
        db,
        prepared=prepared,
        result=result,
        worker_id=worker_id,
        now=datetime.now(UTC),
    )
