from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.auth.service import resolve_session, verify_csrf
from app.config import Settings, get_settings
from app.db import get_db
from app.models import User, UserSession


@dataclass(frozen=True)
class AuthContext:
    session: UserSession
    user: User


def get_auth_context(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    session_token = request.cookies.get(settings.session_cookie_name)
    user_session, user = resolve_session(db, session_token, settings)
    return AuthContext(user_session, user)


def require_csrf(
    request: Request,
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    context: AuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    origin = request.headers.get("origin", "").rstrip("/")
    if origin not in settings.origin_allowlist:
        from app.auth.service import CsrfError

        raise CsrfError("Origin validation failed")
    verify_csrf(context.session, csrf_token)
    return context
