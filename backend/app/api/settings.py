from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.db import get_db
from app.execution_policy import (
    activate_version,
    active_policy,
    create_draft,
    history,
    policy_payload,
    validate_draft,
)
from app.models import ConfigurationVersion
from app.schemas import (
    ExecutionPolicyActivateRequest,
    ExecutionPolicyDraftRequest,
    ExecutionPolicyHistoryResponse,
    ExecutionPolicyResponse,
    ExecutionPolicyVersionResponse,
)

router = APIRouter(prefix="/settings/execution-policy", tags=["settings"])


def _version_response(request_id: str, version: ConfigurationVersion) -> ExecutionPolicyVersionResponse:
    return ExecutionPolicyVersionResponse(
        request_id=request_id,
        version_id=version.id,
        sequence=version.sequence,
        state=version.state,
        policy=policy_payload(version),
        reason=version.reason,
        created_at=version.created_at,
        validated_at=version.validated_at,
        activated_at=version.activated_at,
    )


@router.get("", response_model=ExecutionPolicyResponse)
def get_execution_policy(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ExecutionPolicyResponse:
    version = active_policy(db, context.user.id)
    return ExecutionPolicyResponse(
        request_id=request.state.request_id,
        active_version_id=version.id if version else None,
        source="USER_DEFAULT" if version else "SAFE_DEFAULT",
        policy=policy_payload(version),
    )


@router.post("/drafts", response_model=ExecutionPolicyVersionResponse)
def post_execution_policy_draft(
    payload: ExecutionPolicyDraftRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ExecutionPolicyVersionResponse:
    version = create_draft(
        db, user=context.user, policy=payload.policy, reason=payload.reason
    )
    return _version_response(request.state.request_id, version)


@router.post("/{version_id}/validate", response_model=ExecutionPolicyVersionResponse)
def validate_execution_policy(
    version_id: str,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ExecutionPolicyVersionResponse:
    version = validate_draft(db, user=context.user, version_id=version_id)
    return _version_response(request.state.request_id, version)


@router.post("/{version_id}/activate", response_model=ExecutionPolicyVersionResponse)
def activate_execution_policy(
    version_id: str,
    payload: ExecutionPolicyActivateRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ExecutionPolicyVersionResponse:
    version = activate_version(
        db,
        user=context.user,
        version_id=version_id,
        reauth_proof=payload.reauth_proof,
        correlation_id=request.state.request_id,
        request_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", "unknown"),
    )
    return _version_response(request.state.request_id, version)


@router.get("/history", response_model=ExecutionPolicyHistoryResponse)
def get_execution_policy_history(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ExecutionPolicyHistoryResponse:
    return ExecutionPolicyHistoryResponse(
        request_id=request.state.request_id,
        items=[
            _version_response(request.state.request_id, item)
            for item in history(db, context.user.id)
        ],
    )
