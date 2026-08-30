from __future__ import annotations

import json
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.api.execution_stage import get_execution_stage_evidence_loader
from app.approvals import (
    approve as approve_service,
)
from app.approvals import (
    expire_stale,
    list_pending,
    list_recent,
)
from app.approvals import (
    reject as reject_service,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import ResourceNotFoundError
from app.execution_stage import EvidenceLoader
from app.models import Approval, Decision
from app.schemas import (
    ApprovalActionResponse,
    ApprovalApproveRequest,
    ApprovalListResponse,
    ApprovalRejectRequest,
    ApprovalResponse,
)

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _scope_fields(scope_json: str) -> dict[str, object]:
    return json.loads(scope_json) if scope_json else {}


def _to_response(request_id: str, approval: Approval, db: Session) -> ApprovalResponse:
    decision = db.get(Decision, approval.decision_id)
    scope = _scope_fields(approval.scope_snapshot_json)
    return ApprovalResponse(
        request_id=request_id,
        approval_id=approval.id,
        execution_id=approval.execution_id,
        decision_id=approval.decision_id,
        user_id=approval.user_id,
        state=approval.state,
        symbol=decision.symbol if decision else (str(scope.get("symbol") or "")),
        market=decision.market if decision else (str(scope.get("market") or "")),
        action=str(scope.get("action") or (decision.action if decision else "")),
        reference_price=(
            Decimal(str(scope["reference_price"]))
            if scope.get("reference_price")
            else None
        ),
        quantity=int(scope.get("quantity") or 0),
        order_id=approval.order_id,
        result_code=approval.result_code,
        version=approval.version,
        expires_at=approval.expires_at,
        created_at=approval.created_at,
        updated_at=approval.updated_at,
    )


@router.get("", response_model=ApprovalListResponse)
def list_approvals(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ApprovalListResponse:
    expire_stale(db)
    items = list_recent(db, context.user.id)
    return ApprovalListResponse(
        request_id=request.state.request_id,
        items=[_to_response(request.state.request_id, item, db) for item in items],
    )


@router.get("/pending", response_model=ApprovalListResponse)
def list_pending_approvals(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ApprovalListResponse:
    expire_stale(db)
    items = list_pending(db, context.user.id)
    return ApprovalListResponse(
        request_id=request.state.request_id,
        items=[_to_response(request.state.request_id, item, db) for item in items],
    )


@router.get("/{approval_id}", response_model=ApprovalResponse)
def get_approval(
    approval_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ApprovalResponse:
    expire_stale(db)
    approval = db.scalar(
        select(Approval).where(
            Approval.id == approval_id, Approval.user_id == context.user.id
        )
    )
    if approval is None:
        raise ResourceNotFoundError("APPROVAL_NOT_FOUND", "승인을 찾을 수 없습니다.")
    return _to_response(request.state.request_id, approval, db)


@router.post("/{approval_id}/approve", response_model=ApprovalActionResponse)
def approve_approval(
    approval_id: str,
    payload: ApprovalApproveRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    stage_evidence_loader: EvidenceLoader = Depends(get_execution_stage_evidence_loader),
) -> ApprovalActionResponse:
    approval = approve_service(
        db,
        approval_id=approval_id,
        user=context.user,
        settings=settings,
        correlation_id=request.state.request_id,
        idempotency_key=payload.idempotency_key,
        expected_version=payload.expected_version,
        reauth_proof=payload.reauth_proof,
        stage_evidence_loader=stage_evidence_loader,
    )
    return ApprovalActionResponse(
        request_id=request.state.request_id,
        approval_id=approval.id,
        state=approval.state,
        order_id=approval.order_id,
        result_code=approval.result_code,
    )


@router.post("/{approval_id}/reject", response_model=ApprovalActionResponse)
def reject_approval(
    approval_id: str,
    payload: ApprovalRejectRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ApprovalActionResponse:
    approval = reject_service(
        db,
        approval_id=approval_id,
        user=context.user,
        correlation_id=request.state.request_id,
        expected_version=payload.expected_version,
    )
    return ApprovalActionResponse(
        request_id=request.state.request_id,
        approval_id=approval.id,
        state=approval.state,
        order_id=approval.order_id,
        result_code=approval.result_code,
    )
