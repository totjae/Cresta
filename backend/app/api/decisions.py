from __future__ import annotations

import json
from datetime import UTC

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import ArbiterResult
from app.agents.decision_context import canonical_context_json, context_digest
from app.agents.decision_finalizer import (
    DecisionFinalizationError,
    validate_persisted_sourced_entry_decision,
)
from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.config import Settings, get_settings
from app.db import get_db
from app.decision_contracts import (
    DecisionRepresentationError,
    validate_decision_representation,
)
from app.errors import ResourceNotFoundError
from app.mock_ai import evaluate_mock_decision, list_decisions
from app.models import (
    Decision,
    DecisionExecution,
    DecisionInputSnapshot,
    IndicatorSnapshot,
)
from app.schemas import (
    CoreOutputResponse,
    DecisionExecutionResponse,
    DecisionListResponse,
    DecisionResponse,
    MockDecisionRequest,
    ScoutOutputResponse,
    SourcedDecisionLineageResponse,
    SourcedEntryDecisionResponse,
)

router = APIRouter(prefix="/decisions", tags=["decisions"])


DecisionApiResponse = DecisionResponse | SourcedEntryDecisionResponse


def _execution_response(decision: Decision, db: Session) -> DecisionExecutionResponse | None:
    execution = db.scalar(
        select(DecisionExecution)
        .where(DecisionExecution.decision_id == decision.id)
        .order_by(DecisionExecution.created_at.desc())
        .limit(1)
    )
    if execution is None:
        return None
    return DecisionExecutionResponse(
        execution_id=execution.id,
        action=execution.action,
        mode=execution.mode,
        stage=execution.stage,
        state=execution.state,
        result_code=execution.result_code,
        guard_evaluation_id=execution.guard_evaluation_id,
        approval_id=execution.approval_id,
        order_intent_id=execution.order_intent_id,
        created_at=execution.created_at,
        updated_at=execution.updated_at,
    )


def _sourced_response(
    request_id: str, decision: Decision, db: Session
) -> SourcedEntryDecisionResponse:
    try:
        source = validate_persisted_sourced_entry_decision(db, decision=decision)
    except DecisionFinalizationError as exc:
        raise DecisionRepresentationError("SOURCED_DECISION_LINEAGE_INVALID") from exc
    stage = source.arbiter_stage
    if not stage.output_json or not stage.output_hash:
        raise DecisionRepresentationError("SOURCED_DECISION_LINEAGE_INVALID")
    try:
        output_payload = json.loads(stage.output_json)
        arbiter = ArbiterResult.model_validate(output_payload)
        reason_codes = json.loads(decision.reason_codes_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DecisionRepresentationError("SOURCED_DECISION_LINEAGE_INVALID") from exc
    if (
        canonical_context_json(output_payload) != stage.output_json
        or context_digest(stage.output_json) != stage.output_hash
        or stage.output_hash != decision.source_stage_output_hash
        or stage.run_id != decision.source_agent_run_id
        or not isinstance(reason_codes, list)
        or reason_codes != arbiter.reason_codes
        or decision.action != arbiter.action
        or decision.valid_until.replace(tzinfo=decision.valid_until.tzinfo or UTC).astimezone(
            UTC
        ).isoformat() != arbiter.valid_until
    ):
        raise DecisionRepresentationError("SOURCED_DECISION_LINEAGE_INVALID")
    return SourcedEntryDecisionResponse(
        schema_version="sourced-entry-decision-v1",
        request_id=request_id,
        decision_id=decision.id,
        purpose="TRADING",
        evaluation_request_id=decision.evaluation_request_id,
        decision_kind="ENTRY",
        symbol=decision.symbol,
        market=decision.market,
        input_snapshot_id=decision.input_snapshot_id,
        decision_input_id=decision.decision_input_id,
        action=decision.action,
        reason_codes=reason_codes,
        confidence=None,
        risk_level=None,
        configuration_version_id=None,
        execution_mode=None,
        execution_outcome=None,
        validation_status="VALID",
        execution=_execution_response(decision, db),
        valid_until=decision.valid_until,
        created_at=decision.created_at,
        lineage=SourcedDecisionLineageResponse(
            source_agent_run_id=decision.source_agent_run_id,
            source_stage_run_id=decision.source_stage_run_id,
            source_stage_output_hash=decision.source_stage_output_hash,
            decision_context_id=arbiter.decision_context_id,
            decision_context_hash=arbiter.decision_context_hash,
            consensus_policy_version=arbiter.policy_version,
            decision_pattern=arbiter.decision_pattern,
            input_results=[item.model_dump() for item in arbiter.input_results],
        ),
    )


def _response(request_id: str, decision: Decision, db: Session) -> DecisionApiResponse:
    representation = validate_decision_representation(decision)
    if representation == "SOURCED_V7":
        return _sourced_response(request_id, decision, db)
    decision_input = (
        db.get(DecisionInputSnapshot, decision.decision_input_id)
        if decision.decision_input_id
        else None
    )
    indicator = (
        db.get(IndicatorSnapshot, decision_input.indicator_snapshot_id)
        if decision_input and decision_input.indicator_snapshot_id
        else None
    )
    return DecisionResponse(
        request_id=request_id,
        decision_id=decision.id,
        purpose=decision.purpose,
        evaluation_request_id=decision.evaluation_request_id,
        symbol=decision.symbol,
        market=decision.market,
        input_snapshot_id=decision.input_snapshot_id,
        decision_input_id=decision_input.id if decision_input else None,
        input_schema_version=decision_input.schema_version if decision_input else None,
        input_hash=decision_input.input_hash if decision_input else None,
        indicator_snapshot_id=decision_input.indicator_snapshot_id if decision_input else None,
        indicator_calculator_version=indicator.calculator_version if indicator else None,
        model_id=decision.model_id,
        prompt_version=decision.prompt_version,
        scout=ScoutOutputResponse.model_validate(json.loads(decision.scout_output_json)),
        core=CoreOutputResponse.model_validate(json.loads(decision.core_output_json)),
        configuration_version_id=decision.configuration_version_id,
        execution_mode=decision.execution_mode,
        execution_outcome=decision.execution_outcome,
        execution=_execution_response(decision, db),
        valid_until=decision.valid_until,
        created_at=decision.created_at,
    )


@router.get("", response_model=DecisionListResponse)
def get_decisions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> DecisionListResponse:
    return DecisionListResponse(
        request_id=request.state.request_id,
        items=[_response(request.state.request_id, item, db) for item in list_decisions(db, limit)],
    )


@router.get("/{decision_id}", response_model=DecisionResponse | SourcedEntryDecisionResponse)
def get_decision(
    decision_id: str,
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> DecisionApiResponse:
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise ResourceNotFoundError("DECISION_NOT_FOUND", "AI 판단을 찾을 수 없습니다.")
    return _response(request.state.request_id, decision, db)


@router.post("/mock-evaluate", response_model=DecisionResponse)
def post_mock_decision(
    payload: MockDecisionRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DecisionResponse:
    decision = evaluate_mock_decision(
        db,
        user=context.user,
        evaluation_request_id=payload.evaluation_request_id,
        symbol=payload.symbol,
        market=payload.market,
        settings=settings,
    )
    return _response(request.state.request_id, decision, db)
