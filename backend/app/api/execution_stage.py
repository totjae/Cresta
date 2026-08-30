from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.db import get_db
from app.execution_stage import (
    EvidenceLoader,
    ExecutionStagePayload,
    StageResolutionStatus,
    StageStrictModel,
    activate_execution_stage,
    create_execution_stage_draft,
    execution_stage_history,
    resolve_current_execution_stage,
    validate_execution_stage_draft,
)
from app.models import ConfigurationVersion

router = APIRouter(prefix="/settings/v7-entry-execution-stage", tags=["settings"])


class ExecutionStageDraftRequest(StageStrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    control: ExecutionStagePayload
    reason: str = Field(min_length=1, max_length=500)


class ExecutionStageActionRequest(StageStrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")


class ExecutionStageVersionResponse(StageStrictModel):
    schema_version: str = "1.0"
    request_id: str
    version_id: str
    sequence: int
    state: str
    payload_hash: str
    control: ExecutionStagePayload
    reason: str
    created_at: datetime
    validated_at: datetime | None
    activated_at: datetime | None


class ExecutionStageHistoryResponse(StageStrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[ExecutionStageVersionResponse]


class CurrentExecutionStageResponse(StageStrictModel):
    schema_version: str = "1.0"
    request_id: str
    status: StageResolutionStatus
    item: ExecutionStageVersionResponse | None


def _unavailable_evidence_loader(_reference: str) -> bytes:
    raise KeyError("execution-stage evidence artifact resolver is not configured")


def get_execution_stage_evidence_loader() -> EvidenceLoader:
    """Return the deployment-owned immutable artifact resolver dependency."""

    return _unavailable_evidence_loader


def _response(request_id: str, version: ConfigurationVersion) -> ExecutionStageVersionResponse:
    return ExecutionStageVersionResponse(
        request_id=request_id,
        version_id=version.id,
        sequence=version.sequence,
        state=version.state,
        payload_hash=version.payload_hash,
        control=ExecutionStagePayload.model_validate_json(version.payload_json),
        reason=version.reason,
        created_at=version.created_at,
        validated_at=version.validated_at,
        activated_at=version.activated_at,
    )


@router.post("/drafts", response_model=ExecutionStageVersionResponse)
def post_execution_stage_draft(
    payload: ExecutionStageDraftRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    evidence_loader: EvidenceLoader = Depends(get_execution_stage_evidence_loader),
) -> ExecutionStageVersionResponse:
    version = create_execution_stage_draft(
        db,
        user=context.user,
        payload=payload.control,
        reason=payload.reason,
        now=datetime.now(UTC),
        evidence_loader=evidence_loader,
    )
    return _response(request.state.request_id, version)


@router.post("/{version_id}/validate", response_model=ExecutionStageVersionResponse)
def validate_execution_stage_version(
    version_id: str,
    _payload: ExecutionStageActionRequest,
    request: Request,
    _: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    evidence_loader: EvidenceLoader = Depends(get_execution_stage_evidence_loader),
) -> ExecutionStageVersionResponse:
    version = validate_execution_stage_draft(
        db,
        version_id=version_id,
        now=datetime.now(UTC),
        evidence_loader=evidence_loader,
    )
    return _response(request.state.request_id, version)


@router.post("/{version_id}/activate", response_model=ExecutionStageVersionResponse)
def activate_execution_stage_version(
    version_id: str,
    _payload: ExecutionStageActionRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    evidence_loader: EvidenceLoader = Depends(get_execution_stage_evidence_loader),
) -> ExecutionStageVersionResponse:
    version = activate_execution_stage(
        db,
        user=context.user,
        version_id=version_id,
        now=datetime.now(UTC),
        evidence_loader=evidence_loader,
        correlation_id=request.state.request_id,
        request_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", "unknown"),
    )
    return _response(request.state.request_id, version)


@router.get("/current", response_model=CurrentExecutionStageResponse)
def get_current_execution_stage(
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
    evidence_loader: EvidenceLoader = Depends(get_execution_stage_evidence_loader),
) -> CurrentExecutionStageResponse:
    resolved = resolve_current_execution_stage(
        db, now=datetime.now(UTC), evidence_loader=evidence_loader
    )
    return CurrentExecutionStageResponse(
        request_id=request.state.request_id,
        status=resolved.status,
        item=(
            _response(request.state.request_id, resolved.version)
            if resolved.status is StageResolutionStatus.PASS and resolved.version is not None
            else None
        ),
    )


@router.get("/history", response_model=ExecutionStageHistoryResponse)
def get_execution_stage_history(
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ExecutionStageHistoryResponse:
    return ExecutionStageHistoryResponse(
        request_id=request.state.request_id,
        items=[_response(request.state.request_id, item) for item in execution_stage_history(db)],
    )
