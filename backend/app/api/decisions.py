from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import ResourceNotFoundError
from app.mock_ai import evaluate_mock_decision, list_decisions
from app.models import Decision
from app.schemas import (
    CoreOutputResponse,
    DecisionListResponse,
    DecisionResponse,
    MockDecisionRequest,
    ScoutOutputResponse,
)

router = APIRouter(prefix="/decisions", tags=["decisions"])


def _response(request_id: str, decision: Decision) -> DecisionResponse:
    return DecisionResponse(
        request_id=request_id,
        decision_id=decision.id,
        evaluation_request_id=decision.evaluation_request_id,
        symbol=decision.symbol,
        market=decision.market,
        input_snapshot_id=decision.input_snapshot_id,
        model_id=decision.model_id,
        prompt_version=decision.prompt_version,
        scout=ScoutOutputResponse.model_validate(json.loads(decision.scout_output_json)),
        core=CoreOutputResponse.model_validate(json.loads(decision.core_output_json)),
        configuration_version_id=decision.configuration_version_id,
        execution_mode=decision.execution_mode,
        execution_outcome=decision.execution_outcome,
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
        items=[_response(request.state.request_id, item) for item in list_decisions(db, limit)],
    )


@router.get("/{decision_id}", response_model=DecisionResponse)
def get_decision(
    decision_id: str,
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> DecisionResponse:
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise ResourceNotFoundError("DECISION_NOT_FOUND", "AI 판단을 찾을 수 없습니다.")
    return _response(request.state.request_id, decision)


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
    return _response(request.state.request_id, decision)
