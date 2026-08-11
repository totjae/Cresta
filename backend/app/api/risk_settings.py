from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.db import get_db
from app.models import ConfigurationVersion
from app.risk_policy import (
    activate_risk_version,
    active_risk_policy,
    create_risk_draft,
    risk_history,
    risk_policy_payload,
    validate_risk_draft,
)
from app.schemas import (
    RiskPolicyActivateRequest,
    RiskPolicyDraftRequest,
    RiskPolicyHistoryResponse,
    RiskPolicyResponse,
    RiskPolicyVersionResponse,
)

router = APIRouter(prefix="/settings/risk-policy", tags=["settings"])


def _version_response(request_id: str, version: ConfigurationVersion) -> RiskPolicyVersionResponse:
    return RiskPolicyVersionResponse(
        request_id=request_id,
        version_id=version.id,
        sequence=version.sequence,
        state=version.state,
        policy=risk_policy_payload(version),
        reason=version.reason,
        created_at=version.created_at,
        validated_at=version.validated_at,
        activated_at=version.activated_at,
    )


@router.get("", response_model=RiskPolicyResponse)
def get_risk_policy(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> RiskPolicyResponse:
    version = active_risk_policy(db, context.user.id)
    return RiskPolicyResponse(
        request_id=request.state.request_id,
        active_version_id=version.id if version else None,
        source="USER_DEFAULT" if version else "SAFE_DEFAULT",
        policy=risk_policy_payload(version),
    )


@router.post("/drafts", response_model=RiskPolicyVersionResponse)
def post_risk_policy_draft(
    payload: RiskPolicyDraftRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> RiskPolicyVersionResponse:
    version = create_risk_draft(
        db, user=context.user, policy=payload.policy, reason=payload.reason
    )
    return _version_response(request.state.request_id, version)


@router.post("/{version_id}/validate", response_model=RiskPolicyVersionResponse)
def validate_risk_policy(
    version_id: str,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> RiskPolicyVersionResponse:
    return _version_response(
        request.state.request_id,
        validate_risk_draft(db, user=context.user, version_id=version_id),
    )


@router.post("/{version_id}/activate", response_model=RiskPolicyVersionResponse)
def activate_risk_policy(
    version_id: str,
    _: RiskPolicyActivateRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> RiskPolicyVersionResponse:
    version = activate_risk_version(
        db,
        user=context.user,
        version_id=version_id,
        correlation_id=request.state.request_id,
        request_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", "unknown"),
    )
    return _version_response(request.state.request_id, version)


@router.get("/history", response_model=RiskPolicyHistoryResponse)
def get_risk_policy_history(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> RiskPolicyHistoryResponse:
    return RiskPolicyHistoryResponse(
        request_id=request.state.request_id,
        items=[
            _version_response(request.state.request_id, item)
            for item in risk_history(db, context.user.id)
        ],
    )
