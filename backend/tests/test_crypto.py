from __future__ import annotations

import base64
from datetime import UTC, datetime

import pyotp

from app.auth.crypto import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    hash_password,
    validate_password,
    verify_password,
    verify_totp,
)


def test_password_hash_is_not_plaintext() -> None:
    password = "Cresta!Secure-Password-2026"
    password_hash = hash_password(password)
    assert password not in password_hash
    assert verify_password(password_hash, password)
    assert not verify_password(password_hash, "wrong-password")


def test_password_policy_rejects_short_and_login_id() -> None:
    for password in ("short", "admin-Strong-Password-2026"):
        try:
            validate_password("admin", password)
        except ValueError:
            pass
        else:  # pragma: no cover - assertion branch
            raise AssertionError("password should have been rejected")


def test_totp_secret_encryption_round_trip() -> None:
    key = base64.urlsafe_b64encode(b"test-only-key-material-32-bytes!").decode("ascii")
    encrypted = encrypt_totp_secret("JBSWY3DPEHPK3PXP", key)
    assert "JBSWY3DPEHPK3PXP" not in encrypted
    assert decrypt_totp_secret(encrypted, key) == "JBSWY3DPEHPK3PXP"


def test_totp_replay_is_rejected() -> None:
    secret = "JBSWY3DPEHPK3PXP"
    now = datetime(2026, 7, 31, 0, 0, 0, tzinfo=UTC)
    code = pyotp.TOTP(secret).at(now)
    first = verify_totp(secret, code, None, now)
    assert first.valid and first.step is not None
    assert not verify_totp(secret, code, first.step, now).valid
