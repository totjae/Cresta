from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from app.models import AuditLog
from tests.test_v7_persistence_foundation import _alembic

FIXED_AUDIT_RESULTS = {
    "BLOCKED",
    "CONFLICTED",
    "EXPIRED",
    "FAILED",
    "INVALID",
    "ORDER_CREATED",
    "PASSED",
    "PENDING",
    "REJECTED",
    "RETRYABLE_FAILURE",
    "SUCCEEDED",
    "SUCCESS",
}
EXECUTION_AUDIT_STATES = {
    "APPROVAL_PENDING",
    "DISABLED",
    "FAILED_SAFE",
    "GUARD_BLOCKED",
    "NO_ACTION",
    "SHADOW_RECORDED",
}
APPROVAL_INVALIDATION_RESULTS = {
    "ACTION_MODE_DOWNGRADED",
    "APPROVAL_EXPIRED",
    "EMERGENCY_STOP_ACTIVE",
    "EXECUTION_STAGE_DOWNGRADED",
    "EXECUTION_STAGE_UNAVAILABLE",
    "RISK_POLICY_UNAVAILABLE",
    "SOURCE_AUTHORITY_INVALID",
}
BUY_GUARD_RESULTS = {
    "BROKER_CONNECTION_OK",
    "BROKER_NOT_READY",
    "CONSECUTIVE_LOSS_LIMIT",
    "DAILY_ENTRIES_LIMIT",
    "DAILY_LOSS_LIMIT",
    "DECISION_EXPIRED",
    "EMERGENCY_STOP_ACTIVE",
    "ENVIRONMENT_NOT_MOCK",
    "MARKET_DATA_STALE",
    "NO_ACTIVE_DAILY_LOSS_EVENT",
    "OPEN_POSITIONS_LIMIT",
    "ORDER_SIZE_NOT_CONFIGURED",
    "SNAPSHOT_MISSING",
    "SPREAD_LIMIT",
    "SYMBOL_EXPOSURE_LIMIT",
    "SYMBOL_NOT_WATCHED",
    "TOTAL_EXPOSURE_LIMIT",
}
SELL_GUARD_RESULTS = {
    "BROKER_READY",
    "MARKET_DATA_FRESH",
    "MARKET_SESSION_TRADABLE",
    "MARKETABLE_SELL_PRICE_AVAILABLE",
    "NO_ACTIVE_OR_UNKNOWN_ORDER",
    "NOT_RECONCILING",
    "POSITION_FOUND",
    "POSITION_ID_MATCH",
    "POSITION_MANAGED_QUANTITY_POSITIVE",
    "POSITION_VERSION_MATCH",
    "QUANTITY_BELOW_ONE",
    "SELL_QUANTITY_AVAILABLE",
    "SELL_RATIO_VALID",
}
SHARED_GUARD_RESULTS = {
    "ACCOUNT_FUNDS_FRESH",
    "ACTION_NOT_IMPLEMENTED",
    "CURRENT_ORDER_AMOUNT_ALLOWED",
    "FINANCIAL_CONTEXT_INVALID",
    "FROZEN_ORDER_AMOUNT_ALLOWED",
    "GENERIC_ORDERABLE_AMOUNT_SUFFICIENT",
    "INSTRUMENT_TRADABLE",
    "MARGIN_100_AMOUNT_SUFFICIENT",
    "MARGIN_100_QUANTITY_SUFFICIENT",
    "ORDER_CAPACITY_FRESH",
    "ORDERABLE_CASH_SUFFICIENT",
    "PRICE_DEVIATION_EXCEEDED",
    "STRICT_MOCK_AUTHORITY",
}
PRE_SEND_DIRECT_RESULTS = {
    "APPROVAL_AUTHORITY_REVOKED",
    "AUTOMATIC_AUTHORITY_REVOKED",
    "BROKER_DIAGNOSTIC_AUTHORITY_INVALID",
    "CURRENT_POLICY_UNAVAILABLE",
    "EXECUTION_STAGE_PROVENANCE_INVALID",
    "ORDER_AUTHORITY_KEY_INVALID",
    "ORDER_SOURCE_NOT_SENDABLE",
    "ORDER_SOURCE_UNCLASSIFIED",
    "SOURCE_OWNER_UNAVAILABLE",
}
CURRENT_BUY_GUARD_RESULTS = {f"CURRENT_{item}" for item in BUY_GUARD_RESULTS}
AUDIT_RESULT_LITERALS = frozenset(
    FIXED_AUDIT_RESULTS
    | EXECUTION_AUDIT_STATES
    | APPROVAL_INVALIDATION_RESULTS
    | BUY_GUARD_RESULTS
    | SELL_GUARD_RESULTS
    | SHARED_GUARD_RESULTS
    | PRE_SEND_DIRECT_RESULTS
    | CURRENT_BUY_GUARD_RESULTS
)


def _engine(database: Path):
    return create_engine(f"sqlite:///{database.as_posix()}", poolclass=NullPool)


def _capacity(engine) -> int:
    column = next(
        item
        for item in inspect(engine).get_columns("audit_logs")
        if item["name"] == "result"
    )
    return int(column["type"].length)


def _insert_results(engine, values: set[str] | frozenset[str]) -> None:
    now = datetime.now(UTC).isoformat()
    with engine.begin() as connection:
        for value in values:
            identity = str(uuid4())
            connection.execute(
                text(
                    "INSERT INTO audit_logs "
                    "(id, actor_type, action, target, result, correlation_id, "
                    "metadata_json, created_at) "
                    "VALUES (:id, 'SYSTEM', 'PHASE_10G1B_INVENTORY', :target, "
                    ":result, :correlation_id, '{}', :created_at)"
                ),
                {
                    "id": identity,
                    "target": identity,
                    "result": value,
                    "correlation_id": identity,
                    "created_at": now,
                },
            )


def test_audit_result_inventory_is_complete_for_64_character_capacity() -> None:
    assert len(AUDIT_RESULT_LITERALS) == 93
    assert max(map(len, AUDIT_RESULT_LITERALS)) == 35
    assert {
        "AUTOMATIC_AUTHORITY_REVOKED",
        "APPROVAL_AUTHORITY_REVOKED",
    } <= AUDIT_RESULT_LITERALS
    assert all(len(item) <= 64 for item in AUDIT_RESULT_LITERALS)


def test_0044_sqlite_upgrade_safe_downgrade_reupgrade_and_orm(tmp_path: Path) -> None:
    database = tmp_path / "phase-10g1b-safe.db"
    _alembic(database, "upgrade", "20260829_0043")
    engine = _engine(database)
    assert _capacity(engine) == 24
    _insert_results(engine, {"PASSED"})
    _alembic(database, "upgrade", "20260829_0044")
    assert _capacity(engine) == 64
    assert AuditLog.__table__.c.result.type.length == 64
    _alembic(database, "downgrade", "20260829_0043")
    assert _capacity(engine) == 24
    _alembic(database, "upgrade", "20260829_0044")
    assert _capacity(engine) == 64
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM audit_logs WHERE result='PASSED'")
        ) == 1
    engine.dispose()


def test_0044_sqlite_refuses_long_result_and_preserves_inventory(tmp_path: Path) -> None:
    database = tmp_path / "phase-10g1b-guard.db"
    _alembic(database, "upgrade", "20260829_0044")
    engine = _engine(database)
    _insert_results(engine, AUDIT_RESULT_LITERALS)
    result = _alembic(database, "downgrade", "20260829_0043", check=False)
    assert result.returncode != 0
    assert "Refusing downgrade of 20260829_0044" in result.stderr
    assert _capacity(engine) == 64
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260829_0044"
        )
        assert connection.scalar(text("SELECT COUNT(*) FROM audit_logs")) == len(
            AUDIT_RESULT_LITERALS
        )
    engine.dispose()
