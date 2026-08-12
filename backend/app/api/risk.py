from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.db import get_db
from app.emergency_stop import (
    activate_pause_entry,
    current_emergency_stop,
    release_pause_entry,
)
from app.models import EmergencyStop
from app.schemas import EmergencyStopRequest, EmergencyStopResponse

router = APIRouter(prefix="/risk", tags=["risk"])


def _response(request_id: str, item: EmergencyStop | None) -> EmergencyStopResponse:
    return EmergencyStopResponse(
        request_id=request_id,
        stop_id=item.id if item else None,
        account_alias=item.account_alias if item else "KIWOOM_MOCK_PRIMARY",
        level=item.level if item else "PAUSE_ENTRY",
        state=item.state if item else "RELEASED",
        reason=item.reason if item else None,
        version=item.version if item else 0,
        activated_at=item.activated_at if item else None,
        released_at=item.released_at if item else None,
    )


@router.get("/emergency-stop", response_model=EmergencyStopResponse)
def get_emergency_stop(
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> EmergencyStopResponse:
    return _response(request.state.request_id, current_emergency_stop(db))


@router.post("/emergency-stop", response_model=EmergencyStopResponse)
def post_emergency_stop(
    payload: EmergencyStopRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> EmergencyStopResponse:
    item = activate_pause_entry(
        db,
        user=context.user,
        reason=payload.reason,
        idempotency_key=idempotency_key,
        correlation_id=request.state.request_id,
        request_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", "unknown"),
    )
    return _response(request.state.request_id, item)


@router.post("/emergency-stop/release", response_model=EmergencyStopResponse)
def post_emergency_stop_release(
    payload: EmergencyStopRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> EmergencyStopResponse:
    item = release_pause_entry(
        db,
        user=context.user,
        reason=payload.reason,
        idempotency_key=idempotency_key,
        correlation_id=request.state.request_id,
        request_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", "unknown"),
    )
    return _response(request.state.request_id, item)
