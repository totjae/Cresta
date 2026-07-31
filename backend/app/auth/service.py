from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.crypto import (
    decrypt_totp_secret,
    hash_password,
    new_token,
    token_hash,
    verify_password,
    verify_totp,
)
from app.config import Settings
from app.models import AuditLog, AuthChallenge, AuthRateLimit, ReauthProof, User, UserSession

GENERIC_AUTH_MESSAGE = "인증 정보를 확인할 수 없습니다."
DUMMY_PASSWORD_HASH = hash_password("Cresta timing equalization only")


class AuthenticationError(Exception):
    pass


class CsrfError(Exception):
    pass


@dataclass(frozen=True)
class SessionTokens:
    session_token: str
    csrf_token: str
    expires_at: datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_login_id(value: str) -> str:
    return value.strip().casefold()


def _subject_hash(kind: str, value: str) -> str:
    return token_hash(f"{kind}:{value.casefold()}")


def _rate_subjects(login_id: str, request_ip: str) -> list[str]:
    return [_subject_hash("login", login_id), _subject_hash("ip", request_ip)]


def _is_locked(value: AuthRateLimit | None, now: datetime) -> bool:
    return bool(value and value.locked_until and as_utc(value.locked_until) > now)


def _check_rate_limits(db: Session, login_id: str, request_ip: str, now: datetime) -> None:
    rows = db.scalars(
        select(AuthRateLimit).where(AuthRateLimit.subject_hash.in_(_rate_subjects(login_id, request_ip)))
    ).all()
    if any(_is_locked(row, now) for row in rows):
        raise AuthenticationError(GENERIC_AUTH_MESSAGE)


def _record_failure(db: Session, login_id: str, request_ip: str, settings: Settings, now: datetime) -> None:
    for subject in _rate_subjects(login_id, request_ip):
        row = db.get(AuthRateLimit, subject)
        if row is None:
            row = AuthRateLimit(subject_hash=subject, failure_count=0, lockout_level=0)
            db.add(row)
        row.failure_count += 1
        if row.failure_count >= settings.auth_max_failures:
            row.lockout_level += 1
            multiplier = min(2 ** (row.lockout_level - 1), 16)
            row.locked_until = now + timedelta(minutes=settings.auth_lock_minutes * multiplier)
            row.failure_count = 0


def _clear_failures(db: Session, login_id: str, request_ip: str) -> None:
    for subject in _rate_subjects(login_id, request_ip):
        row = db.get(AuthRateLimit, subject)
        if row:
            row.failure_count = 0
            row.locked_until = None


def _audit(
    db: Session,
    *,
    action: str,
    result: str,
    correlation_id: str,
    request_ip: str,
    user_agent: str,
    user_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_type="USER" if user_id else "ANONYMOUS",
            actor_id=user_id,
            action=action,
            target="AUTH",
            result=result,
            request_ip=request_ip,
            user_agent=user_agent[:256],
            correlation_id=correlation_id,
            metadata_json=json.dumps(metadata or {}, separators=(",", ":"), ensure_ascii=False),
        )
    )


def begin_password_login(
    db: Session,
    *,
    login_id: str,
    password: str,
    request_ip: str,
    user_agent: str,
    correlation_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    current = now or utcnow()
    normalized = normalize_login_id(login_id)
    _check_rate_limits(db, normalized, request_ip, current)
    user = db.scalar(select(User).where(User.login_id == normalized))
    candidate_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
    password_valid = verify_password(candidate_hash, password)
    valid = bool(user and user.status == "ACTIVE" and user.totp and user.totp.verified and password_valid)
    if not valid:
        _record_failure(db, normalized, request_ip, settings, current)
        _audit(
            db,
            action="LOGIN_PASSWORD",
            result="FAILED",
            correlation_id=correlation_id,
            request_ip=request_ip,
            user_agent=user_agent,
            user_id=user.id if user else None,
        )
        db.commit()
        raise AuthenticationError(GENERIC_AUTH_MESSAGE)

    challenge_token = new_token()
    expires_at = current + timedelta(minutes=settings.challenge_minutes)
    db.add(
        AuthChallenge(
            token_hash=token_hash(challenge_token),
            user_id=user.id,
            expires_at=expires_at,
        )
    )
    _audit(
        db,
        action="LOGIN_PASSWORD",
        result="PASSED",
        correlation_id=correlation_id,
        request_ip=request_ip,
        user_agent=user_agent,
        user_id=user.id,
    )
    db.commit()
    return challenge_token, expires_at


def complete_totp_login(
    db: Session,
    *,
    challenge_token: str,
    code: str,
    request_ip: str,
    user_agent: str,
    correlation_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> SessionTokens:
    current = now or utcnow()
    challenge = db.scalar(
        select(AuthChallenge).where(AuthChallenge.token_hash == token_hash(challenge_token))
    )
    if not challenge or challenge.consumed_at or as_utc(challenge.expires_at) <= current:
        raise AuthenticationError(GENERIC_AUTH_MESSAGE)
    user = db.get(User, challenge.user_id)
    if not user or not user.totp or not user.totp.verified:
        raise AuthenticationError(GENERIC_AUTH_MESSAGE)
    _check_rate_limits(db, user.login_id, request_ip, current)
    secret = decrypt_totp_secret(user.totp.encrypted_secret, settings.load_totp_encryption_key())
    match = verify_totp(secret, code, user.totp.last_used_step, current)
    if not match.valid:
        challenge.attempts += 1
        if challenge.attempts >= settings.auth_max_failures:
            challenge.consumed_at = current
        _record_failure(db, user.login_id, request_ip, settings, current)
        _audit(
            db,
            action="LOGIN_TOTP",
            result="FAILED",
            correlation_id=correlation_id,
            request_ip=request_ip,
            user_agent=user_agent,
            user_id=user.id,
        )
        db.commit()
        raise AuthenticationError(GENERIC_AUTH_MESSAGE)

    user.totp.last_used_step = match.step
    challenge.consumed_at = current
    _clear_failures(db, user.login_id, request_ip)
    session_token = new_token()
    csrf_token = new_token()
    expires_at = current + timedelta(hours=settings.session_absolute_hours)
    db.add(
        UserSession(
            token_hash=token_hash(session_token),
            csrf_hash=token_hash(csrf_token),
            user_id=user.id,
            expires_at=expires_at,
            request_ip=request_ip,
            user_agent=user_agent[:256],
        )
    )
    _audit(
        db,
        action="LOGIN_TOTP",
        result="PASSED",
        correlation_id=correlation_id,
        request_ip=request_ip,
        user_agent=user_agent,
        user_id=user.id,
    )
    db.commit()
    return SessionTokens(session_token, csrf_token, expires_at)


def resolve_session(
    db: Session,
    session_token: str | None,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[UserSession, User]:
    if not session_token:
        raise AuthenticationError(GENERIC_AUTH_MESSAGE)
    current = now or utcnow()
    user_session = db.scalar(
        select(UserSession).where(UserSession.token_hash == token_hash(session_token))
    )
    idle_deadline = current - timedelta(minutes=settings.session_idle_minutes)
    if (
        not user_session
        or user_session.revoked_at
        or as_utc(user_session.expires_at) <= current
        or as_utc(user_session.last_seen_at) < idle_deadline
    ):
        raise AuthenticationError(GENERIC_AUTH_MESSAGE)
    user = db.get(User, user_session.user_id)
    if not user or user.status != "ACTIVE":
        raise AuthenticationError(GENERIC_AUTH_MESSAGE)
    user_session.last_seen_at = current
    db.commit()
    return user_session, user


def rotate_csrf(db: Session, user_session: UserSession) -> str:
    csrf_token = new_token()
    user_session.csrf_hash = token_hash(csrf_token)
    db.commit()
    return csrf_token


def verify_csrf(user_session: UserSession, csrf_token: str | None) -> None:
    if not csrf_token or user_session.csrf_hash != token_hash(csrf_token):
        raise CsrfError("CSRF validation failed")


def revoke_session(
    db: Session,
    user_session: UserSession,
    *,
    user: User,
    request_ip: str,
    user_agent: str,
    correlation_id: str,
) -> None:
    user_session.revoked_at = utcnow()
    _audit(
        db,
        action="LOGOUT",
        result="PASSED",
        correlation_id=correlation_id,
        request_ip=request_ip,
        user_agent=user_agent,
        user_id=user.id,
    )
    db.commit()


def create_reauth_proof(
    db: Session,
    *,
    user: User,
    code: str,
    target_action: str,
    target_id: str,
    request_ip: str,
    user_agent: str,
    correlation_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    current = now or utcnow()
    _check_rate_limits(db, user.login_id, request_ip, current)
    if not user.totp or not user.totp.verified:
        raise AuthenticationError(GENERIC_AUTH_MESSAGE)
    secret = decrypt_totp_secret(user.totp.encrypted_secret, settings.load_totp_encryption_key())
    match = verify_totp(secret, code, user.totp.last_used_step, current)
    if not match.valid:
        _record_failure(db, user.login_id, request_ip, settings, current)
        _audit(
            db,
            action="REAUTH_TOTP",
            result="FAILED",
            correlation_id=correlation_id,
            request_ip=request_ip,
            user_agent=user_agent,
            user_id=user.id,
            metadata={"target_action": target_action, "target_id": target_id},
        )
        db.commit()
        raise AuthenticationError(GENERIC_AUTH_MESSAGE)
    user.totp.last_used_step = match.step
    raw_proof = new_token()
    expires_at = current + timedelta(minutes=settings.reauth_minutes)
    db.add(
        ReauthProof(
            proof_hash=token_hash(raw_proof),
            user_id=user.id,
            target_action=target_action,
            target_id=target_id,
            expires_at=expires_at,
        )
    )
    _audit(
        db,
        action="REAUTH_TOTP",
        result="PASSED",
        correlation_id=correlation_id,
        request_ip=request_ip,
        user_agent=user_agent,
        user_id=user.id,
        metadata={"target_action": target_action, "target_id": target_id},
    )
    db.commit()
    return raw_proof, expires_at
