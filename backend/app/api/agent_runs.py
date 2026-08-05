from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runtime import create_diagnostic_run, get_agent_run, list_agent_runs
from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.db import get_db
from app.models import AgentRun, AgentStageRun, EvidenceBundle, LlmInvocation
from app.schemas import (
    AgentDiagnosticRunRequest,
    AgentEvidenceBundleResponse,
    AgentInvocationResponse,
    AgentRunListResponse,
    AgentRunResponse,
    AgentStageRunResponse,
)

router = APIRouter(prefix="/ai/agent-runs", tags=["ai-agent-runtime"])


def _response(
    db: Session, request_id: str, run: AgentRun, *, created: bool = False
) -> AgentRunResponse:
    stages = list(
        db.scalars(
            select(AgentStageRun)
            .where(AgentStageRun.run_id == run.id)
            .order_by(AgentStageRun.sequence)
        )
    )
    stage_responses: list[AgentStageRunResponse] = []
    for stage in stages:
        invocation = db.get(LlmInvocation, stage.invocation_id) if stage.invocation_id else None
        invocation_response = (
            AgentInvocationResponse(
                invocation_id=invocation.id,
                state=invocation.state,
                actual_provider=invocation.actual_provider,
                actual_model=invocation.actual_model,
                latency_ms=invocation.latency_ms,
                validation_status=invocation.validation_status,
                error_code=invocation.error_code,
            )
            if invocation
            else None
        )
        stage_responses.append(
            AgentStageRunResponse(
                stage_run_id=stage.id,
                role=stage.role,
                sequence=stage.sequence,
                dependencies=json.loads(stage.dependency_roles_json),
                route_id=stage.route_id,
                state=stage.state,
                input_hash=stage.input_hash,
                output=json.loads(stage.output_json) if stage.output_json else None,
                output_hash=stage.output_hash,
                error_code=stage.error_code,
                invocation=invocation_response,
                started_at=stage.started_at,
                completed_at=stage.completed_at,
            )
        )
    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run.id))
    bundle_response = (
        AgentEvidenceBundleResponse(
            bundle_id=bundle.id,
            state=bundle.state,
            policy_version=bundle.policy_version,
            evidence_ids=json.loads(bundle.evidence_ids_json),
            reason_codes=json.loads(bundle.reason_codes_json),
            bundle_hash=bundle.bundle_hash,
            as_of=bundle.as_of,
        )
        if bundle
        else None
    )
    return AgentRunResponse(
        request_id=request_id,
        run_id=run.id,
        created=created,
        purpose=run.purpose,
        execution_stage=run.execution_stage,
        market=run.market,
        symbol=run.symbol,
        market_snapshot_id=run.market_snapshot_id,
        input_hash=run.input_hash,
        dag_version=run.dag_version,
        route_versions=json.loads(run.route_versions_json),
        state=run.state,
        core_action=run.core_action,
        valid_until=run.valid_until,
        stages=stage_responses,
        evidence_bundle=bundle_response,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


@router.get("", response_model=AgentRunListResponse)
def get_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=50),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> AgentRunListResponse:
    return AgentRunListResponse(
        request_id=request.state.request_id,
        items=[
            _response(db, request.state.request_id, run)
            for run in list_agent_runs(db, context.user.id, limit)
        ],
    )


@router.get("/{run_id}", response_model=AgentRunResponse)
def get_run(
    run_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> AgentRunResponse:
    return _response(db, request.state.request_id, get_agent_run(db, context.user.id, run_id))


@router.post("/diagnostic", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
def post_diagnostic_run(
    payload: AgentDiagnosticRunRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> AgentRunResponse:
    run, created = create_diagnostic_run(
        db,
        user=context.user,
        market=payload.market,
        symbol=payload.symbol,
        route_ids={role: route_id for role, route_id in payload.route_ids.items()},
    )
    return _response(db, request.state.request_id, run, created=created)
