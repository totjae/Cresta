from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.crypto import token_hash
from app.auth.service import AuthenticationError, as_utc, create_reauth_proof
from app.config import Settings
from app.models import ReauthProof, User
from tests.conftest import TEST_TOTP_SECRET


def test_reauth_proof_is_hashed_and_bound_to_target(
    db: Session,
    admin: User,
    settings: Settings,
) -> None:
    base = datetime.now(UTC).replace(microsecond=0)
    future = base + timedelta(seconds=60)
    code = pyotp.TOTP(TEST_TOTP_SECRET).at(future)
    proof, expires_at = create_reauth_proof(
        db,
        user=admin,
        code=code,
        target_action="APPROVE_ORDER",
        target_id="approval-123",
        request_ip="127.0.0.1",
        user_agent="pytest",
        correlation_id="019fb7f6-0000-7000-8000-000000000001",
        settings=settings,
        now=future,
    )
    stored = db.scalar(select(ReauthProof))
    assert stored is not None
    assert stored.proof_hash == token_hash(proof)
    assert stored.target_action == "APPROVE_ORDER"
    assert stored.target_id == "approval-123"
    assert as_utc(stored.expires_at) == expires_at


def test_reauth_honors_shared_account_and_ip_lock(
    db: Session,
    admin: User,
    settings: Settings,
) -> None:
    current = datetime.now(UTC).replace(microsecond=0)
    common = {
        "db": db,
        "user": admin,
        "target_action": "APPROVE_ORDER",
        "target_id": "approval-locked",
        "request_ip": "127.0.0.2",
        "user_agent": "pytest",
        "correlation_id": "019fb7f6-0000-7000-8000-000000000002",
        "settings": settings,
        "now": current,
    }
    for _ in range(settings.auth_max_failures):
        with pytest.raises(AuthenticationError):
            create_reauth_proof(code="invalid", **common)

    valid_code = pyotp.TOTP(TEST_TOTP_SECRET).at(current)
    with pytest.raises(AuthenticationError):
        create_reauth_proof(code=valid_code, **common)

    assert db.scalar(select(ReauthProof)) is None
