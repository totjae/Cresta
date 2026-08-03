from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.auth.crypto import (
    encrypt_totp_secret,
    generate_totp_secret,
    hash_password,
    new_recovery_code,
    token_hash,
    totp_uri,
    validate_password,
    verify_totp,
)
from app.broker.kiwoom import KiwoomAdapterError, KiwoomMockClient
from app.broker.worker_state import get_broker_status
from app.config import get_settings
from app.db import SessionLocal
from app.models import RecoveryCode, TotpCredential, User
from app.reconciliation import run_kiwoom_reconciliation


def create_admin(login_id: str) -> int:
    normalized = login_id.strip().casefold()
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Confirm password: "):
        print("Passwords do not match", file=sys.stderr)
        return 2
    try:
        validate_password(normalized, password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    settings = get_settings()
    secret = generate_totp_secret()
    print("Register this one-time TOTP URI in an RFC 6238-compatible authenticator:")
    print(totp_uri(secret, normalized))
    first_code = getpass.getpass("Current 6-digit TOTP: ")
    first_match = verify_totp(secret, first_code, None, datetime.now(UTC))
    if not first_match.valid:
        print("TOTP verification failed", file=sys.stderr)
        return 2
    print("Wait for the next 30-second code before continuing.")
    second_code = getpass.getpass("Next 6-digit TOTP: ")
    second_match = verify_totp(secret, second_code, first_match.step, datetime.now(UTC))
    if not second_match.valid:
        print("Second TOTP verification failed", file=sys.stderr)
        return 2

    recovery_codes = [new_recovery_code() for _ in range(10)]
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.login_id == normalized)):
            print("Account already exists", file=sys.stderr)
            return 2
        user = User(login_id=normalized, password_hash=hash_password(password))
        db.add(user)
        db.flush()
        db.add(
            TotpCredential(
                user_id=user.id,
                encrypted_secret=encrypt_totp_secret(secret, settings.load_totp_encryption_key()),
                verified=True,
                last_used_step=second_match.step,
            )
        )
        db.add_all(
            RecoveryCode(user_id=user.id, code_hash=token_hash(code)) for code in recovery_codes
        )
        db.commit()

    print("Store these one-time recovery codes offline. They will not be shown again:")
    for code in recovery_codes:
        print(code)
    return 0


def check_kiwoom() -> int:
    settings = get_settings()
    try:
        verification = KiwoomMockClient(settings).verify_account()
    except KiwoomAdapterError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "environment": "MOCK",
                    "status": "FAILED",
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                },
                separators=(",", ":"),
            )
        )
        configuration_errors = {
            "KIWOOM_NOT_CONFIGURED",
            "KIWOOM_ACCOUNT_ID_INVALID",
            "KIWOOM_ACCOUNT_MISMATCH",
        }
        return 2 if exc.code in configuration_errors else 3

    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "environment": "MOCK",
                "status": verification.status,
                "account": verification.masked_account,
            },
            separators=(",", ":"),
        )
    )
    return 0


def reconcile_kiwoom() -> int:
    settings = get_settings()
    try:
        with SessionLocal() as db:
            result = run_kiwoom_reconciliation(db, KiwoomMockClient(settings))
    except KiwoomAdapterError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "environment": "MOCK",
                    "status": "DEGRADED",
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                },
                separators=(",", ":"),
            )
        )
        return 3

    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "environment": "MOCK",
                "status": result.gate_status,
                "reason": result.gate_reason,
                "run_id": result.run_id,
                "snapshot": {
                    "open_orders": result.open_order_count,
                    "fills": result.fill_count,
                    "positions": result.position_count,
                },
                "mismatch_count": result.mismatch_count,
                "critical_mismatch_count": result.critical_mismatch_count,
            },
            separators=(",", ":"),
        )
    )
    return 4 if result.critical_mismatch_count else 0


def kiwoom_worker_status() -> int:
    with SessionLocal() as db:
        status = get_broker_status(db)
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "environment": "MOCK",
                "account_alias": "KIWOOM_MOCK_PRIMARY",
                "state": status.state,
                "gate_status": status.gate_status,
                "gate_reason": status.gate_reason,
                "fencing_token": status.fencing_token,
                "lease_valid": status.lease_valid,
                "websocket_connected": status.websocket_connected,
                "subscriptions_ready": status.subscriptions_ready,
                "last_heartbeat_at": _iso(status.last_heartbeat_at),
                "last_reconciliation_at": _iso(status.last_reconciliation_at),
                "last_reconciliation_run_id": status.last_reconciliation_run_id,
                "last_error_code": status.last_error_code,
            },
            separators=(",", ":"),
        )
    )
    return 0 if status.state == "READY" and status.lease_valid else 5


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def main() -> None:
    parser = argparse.ArgumentParser(prog="cresta-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-admin")
    create.add_argument("--login-id", required=True)
    subparsers.add_parser("kiwoom-check")
    subparsers.add_parser("kiwoom-reconcile-check")
    subparsers.add_parser("kiwoom-worker-status")
    args = parser.parse_args()
    if args.command == "create-admin":
        raise SystemExit(create_admin(args.login_id))
    if args.command == "kiwoom-check":
        raise SystemExit(check_kiwoom())
    if args.command == "kiwoom-reconcile-check":
        raise SystemExit(reconcile_kiwoom())
    if args.command == "kiwoom-worker-status":
        raise SystemExit(kiwoom_worker_status())


if __name__ == "__main__":
    main()
