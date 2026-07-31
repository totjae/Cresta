from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.auth.crypto import token_hash
from app.auth.service import (
    begin_password_login,
    complete_totp_login,
    create_reauth_proof,
    revoke_session,
    rotate_csrf,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.models import User, UserSession
from app.schemas import (
    MessageResponse,
    PasswordLoginRequest,
    PasswordLoginResponse,
    ReauthRequest,
    ReauthResponse,
    SessionResponse,
    TotpLoginRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _request_metadata(request: Request) -> tuple[str, str, str]:
    request_id = request.state.request_id
    request_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    return request_id, request_ip, user_agent


@router.post("/login/password", response_model=PasswordLoginResponse)
def password_login(
    payload: PasswordLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PasswordLoginResponse:
    request_id, request_ip, user_agent = _request_metadata(request)
    challenge_id, expires_at = begin_password_login(
        db,
        login_id=payload.login_id,
        password=payload.password,
        request_ip=request_ip,
        user_agent=user_agent,
        correlation_id=request_id,
        settings=settings,
    )
    return PasswordLoginResponse(
        request_id=request_id,
        challenge_id=challenge_id,
        expires_at=expires_at,
    )


@router.post("/login/totp", response_model=SessionResponse)
def totp_login(
    payload: TotpLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    request_id, request_ip, user_agent = _request_metadata(request)
    tokens = complete_totp_login(
        db,
        challenge_token=payload.challenge_id,
        code=payload.totp_code,
        request_ip=request_ip,
        user_agent=user_agent,
        correlation_id=request_id,
        settings=settings,
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=tokens.session_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
        max_age=settings.session_absolute_hours * 3600,
    )
    context_session = db.scalar(
        select(UserSession).where(UserSession.token_hash == token_hash(tokens.session_token))
    )
    if context_session is None:  # pragma: no cover - committed in the service transaction
        raise RuntimeError("Created session was not persisted")
    user = db.get(User, context_session.user_id)
    if user is None:  # pragma: no cover - foreign key invariant
        raise RuntimeError("Session user was not persisted")
    return SessionResponse(
        request_id=request_id,
        login_id=user.login_id,
        expires_at=tokens.expires_at,
        csrf_token=tokens.csrf_token,
    )


@router.get("/session", response_model=SessionResponse)
def current_session(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> SessionResponse:
    csrf_token = rotate_csrf(db, context.session)
    return SessionResponse(
        request_id=request.state.request_id,
        login_id=context.user.login_id,
        expires_at=context.session.expires_at,
        csrf_token=csrf_token,
    )


@router.post("/reauth/totp", response_model=ReauthResponse)
def reauthenticate(
    payload: ReauthRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReauthResponse:
    request_id, request_ip, user_agent = _request_metadata(request)
    proof, expires_at = create_reauth_proof(
        db,
        user=context.user,
        code=payload.totp_code,
        target_action=payload.target_action,
        target_id=payload.target_id,
        request_ip=request_ip,
        user_agent=user_agent,
        correlation_id=request_id,
        settings=settings,
    )
    return ReauthResponse(
        request_id=request_id,
        reauth_proof=proof,
        target_action=payload.target_action,
        target_id=payload.target_id,
        expires_at=expires_at,
    )


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    response: Response,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    request_id, request_ip, user_agent = _request_metadata(request)
    revoke_session(
        db,
        context.session,
        user=context.user,
        request_ip=request_ip,
        user_agent=user_agent,
        correlation_id=request_id,
    )
    response.delete_cookie(settings.session_cookie_name, path="/")
    return MessageResponse(request_id=request_id, status="LOGGED_OUT")
