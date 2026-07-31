from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate_password(login_id: str, password: str) -> None:
    if len(password) < 14:
        raise ValueError("Password must contain at least 14 characters")
    if login_id.casefold() in password.casefold():
        raise ValueError("Password must not contain the login ID")
    common = {"password", "qwerty", "123456", "letmein", "admin"}
    lowered = password.casefold()
    if any(item in lowered for item in common):
        raise ValueError("Password is too common")


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_recovery_code() -> str:
    return f"{secrets.token_hex(4)}-{secrets.token_hex(4)}"


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, login_id: str) -> str:
    return pyotp.TOTP(secret, digits=6, interval=30).provisioning_uri(
        name=login_id,
        issuer_name="Cresta",
    )


def _decode_key(encoded_key: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise ValueError("Invalid TOTP encryption key encoding") from exc
    if len(key) != 32:
        raise ValueError("TOTP encryption key must decode to 32 bytes")
    return key


def encrypt_totp_secret(secret: str, encoded_key: str) -> str:
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_decode_key(encoded_key)).encrypt(nonce, secret.encode("ascii"), b"cresta-totp-v1")
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_totp_secret(payload: str, encoded_key: str) -> str:
    raw = base64.urlsafe_b64decode(payload.encode("ascii"))
    if len(raw) < 29:
        raise ValueError("Invalid encrypted TOTP secret")
    value = AESGCM(_decode_key(encoded_key)).decrypt(raw[:12], raw[12:], b"cresta-totp-v1")
    return value.decode("ascii")


@dataclass(frozen=True)
class TotpMatch:
    valid: bool
    step: int | None = None


def verify_totp(secret: str, code: str, last_used_step: int | None, now: datetime | None = None) -> TotpMatch:
    if len(code) != 6 or not code.isdigit():
        return TotpMatch(False)
    current = now or datetime.now(UTC)
    base_step = int(current.timestamp()) // 30
    generator = pyotp.TOTP(secret, digits=6, interval=30)
    for step in (base_step - 1, base_step, base_step + 1):
        expected = generator.at(step * 30)
        if hmac.compare_digest(expected, code):
            if last_used_step is not None and step <= last_used_step:
                return TotpMatch(False)
            return TotpMatch(True, step)
    return TotpMatch(False)
