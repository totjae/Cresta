from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runtime import (
    AgentRuntimeError,
    create_diagnostic_run,
    get_agent_run,
    list_agent_runs,
)
from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.db import get_db
from app.models import (
    AgentRun,
    AgentStageRun,
    EvidenceBundle,
    LlmInvocation,
    LlmModelProfile,
)
from app.schemas import (
    AgentDiagnosticRunRequest,
    AgentEvidenceBundleResponse,
    AgentInvocationOutputResponse,
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
        invocations = list(
            db.scalars(
                select(LlmInvocation)
                .where(LlmInvocation.stage_run_id == stage.id)
                .order_by(LlmInvocation.created_at, LlmInvocation.id)
            )
        )
        requested_models = {
            model_id: db.get(LlmModelProfile, model_id)
            for model_id in {
                invocation.requested_model_profile_id
                for invocation in invocations
                if invocation.requested_model_profile_id
            }
        }
        invocation_responses = [
            AgentInvocationResponse(
                invocation_id=invocation.id,
                attempt_number=index,
                requested_model_profile_id=invocation.requested_model_profile_id,
                requested_model_alias=(
                    requested_models[invocation.requested_model_profile_id].alias
                    if invocation.requested_model_profile_id
                    and requested_models.get(invocation.requested_model_profile_id)
                    else None
                ),
                state=invocation.state,
                actual_provider=invocation.actual_provider,
                actual_model=invocation.actual_model,
                latency_ms=invocation.latency_ms,
                validation_status=invocation.validation_status,
                error_code=invocation.error_code,
                fallback_path=json.loads(invocation.fallback_path_json),
                runtime_context_at=invocation.runtime_context_at,
                web_search_enabled=invocation.web_search_enabled,
                created_at=invocation.created_at,
            )
            for index, invocation in enumerate(invocations, start=1)
        ]
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
                attempt_count=stage.attempt_count,
                max_attempts=stage.max_attempts,
                fencing_token=stage.fencing_token,
                lease_expires_at=stage.lease_expires_at,
                timeout_at=stage.timeout_at,
                invocation=invocation_responses[0] if invocation_responses else None,
                invocations=invocation_responses,
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


@router.get(
    "/{run_id}/invocations/{invocation_id}/output",
    response_model=AgentInvocationOutputResponse,
)
def get_invocation_output(
    run_id: str,
    invocation_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> AgentInvocationOutputResponse:
    run = get_agent_run(db, context.user.id, run_id)
    invocation = db.get(LlmInvocation, invocation_id)
    stage = db.get(AgentStageRun, invocation.stage_run_id) if invocation else None
    if invocation is None or stage is None or stage.run_id != run.id:
        raise AgentRuntimeError("AGENT_INVOCATION_NOT_FOUND", 404)
    return AgentInvocationOutputResponse(
        request_id=request.state.request_id,
        run_id=run.id,
        stage_run_id=stage.id,
        invocation_id=invocation.id,
        state=invocation.state,
        validation_status=invocation.validation_status,
        error_code=invocation.error_code,
        output_available=invocation.model_output_json is not None,
        model_output=(
            json.loads(invocation.model_output_json)
            if invocation.model_output_json is not None
            else None
        ),
        model_output_hash=invocation.model_output_hash,
        captured_at=invocation.model_output_captured_at,
    )


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
