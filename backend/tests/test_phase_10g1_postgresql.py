from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.pool import NullPool

import app.approvals as approvals_module
import app.broker.pre_send_authority as pre_send_authority_module
import app.decision_execution as decision_execution_module
import app.sourced_handoff as sourced_handoff_module
import tests.test_phase_10f_broker_pre_send as phase_10f_module
from app.activation_gate import (
    ACTIVATION_CATEGORY,
    ACTIVATION_SCOPE,
    ACTIVATION_TARGET,
    ActivationGateError,
    GateOutcome,
    activate_activation_gate,
    create_activation_gate_draft,
    select_current_v7_entry_activation_gate,
    validate_activation_gate_draft,
)
from app.agents.decision_finalizer import finalize_entry_decision
from app.api.dependencies import get_settings
from app.approvals import ApprovalError, approve, reject
from app.auth.crypto import encrypt_totp_secret, hash_password, token_hash
from app.auth.service import ReauthProofError, consume_reauth_proof
from app.broker.kiwoom import (
    KiwoomOrderAcknowledgement,
)
from app.broker.order_sender import KiwoomOrderSenderError, send_new_order_once
from app.broker.pre_send_authority import revoke_created_order
from app.broker.worker_state import acquire_lease, renew_lease, update_worker_state
from app.config import Settings
from app.db import get_db
from app.emergency_stop import activate_pause_entry
from app.execution_authority import order_authority_key
from app.execution_policy import SAFE_DEFAULT_POLICY
from app.execution_stage import (
    EXECUTION_STAGE_CATEGORY,
    EXECUTION_STAGE_SCOPE,
    EXECUTION_STAGE_TARGET,
    ExecutionStage,
    ExecutionStageError,
    StageResolution,
    StageResolutionStatus,
    activate_execution_stage,
    create_execution_stage_draft,
    resolve_current_execution_stage,
    validate_execution_stage_draft,
)
from app.main import create_app
from app.models import (
    Approval,
    AuditLog,
    ConfigurationVersion,
    Decision,
    DecisionExecution,
    GuardEvaluation,
    MarketSnapshot,
    OrderEvent,
    OrderIntent,
    Position,
    ReauthProof,
    RiskEvent,
    StopTrigger,
    TradingOrder,
    User,
)
from app.reconciliation import run_kiwoom_reconciliation
from app.sourced_execution import execute_sourced_entry_decision
from app.sourced_handoff import SourcedHandoffWorker
from app.stop_trigger import run_fixed_stop_triggers
from tests.conftest import TEST_KEY, TEST_PASSWORD, TEST_TOTP_SECRET
from tests.test_kiwoom_order_sender import FakeOrderClient, persisted_order, ready_worker
from tests.test_kiwoom_order_sender import (
    test_ambiguous_outcome_becomes_unknown_and_closes_gate as _ambiguous_send_case,
)
from tests.test_phase_9c1_foundation import NOW as ACTIVATION_NOW
from tests.test_phase_9c1_foundation import _gate_payload
from tests.test_phase_9d_decision_finalizer import _completed_trading
from tests.test_phase_9d_decision_finalizer import (
    test_post_flush_database_failure_rolls_back_and_remains_retryable as _finalizer_rollback_case,
)
from tests.test_phase_9d_decision_finalizer import (
    test_write_boundary_recheck_rolls_back_staged_decision as _finalizer_toctou_case,
)
from tests.test_phase_10c1_foundation import NOW as CONTROL_NOW
from tests.test_phase_10c1_foundation import _stage_payload
from tests.test_phase_10c2_sourced_execution import (
    _activate_mode,
    _activate_shadow,
    _finalized,
)
from tests.test_phase_10c2_sourced_execution import (
    test_no_action_is_exact_once_and_configuration_independent as _no_action_identity_case,
)
from tests.test_phase_10d1b_broker_authority import (
    test_append_only_funds_and_latest_selector as _funds_selector_case,
)
from tests.test_phase_10d1b_broker_authority import (
    test_capacity_selector_requires_full_exact_context as _capacity_selector_case,
)
from tests.test_phase_10d_execution_authority import (
    test_financial_ttl_exact_context_cash_only_and_future_timestamp as _financial_freshness_case,
)
from tests.test_phase_10d_execution_authority import (
    test_sourced_manual_approval_to_one_authoritative_created_order as _manual_case,
)
from tests.test_phase_10e_mock_automatic import (
    _activate_fixed_stop_policy,
    _activate_stage,
    _automatic_buy_ready,
)
from tests.test_phase_10e_mock_automatic import (
    test_fixed_stop_mock_automatic_uses_typed_exact_one_authority as _fixed_stop_case,
)
from tests.test_phase_10e_mock_automatic import (
    test_fixed_stop_pause_entry_does_not_block_mock_risk_reduction as _fixed_stop_pause_case,
)
from tests.test_phase_10e_mock_automatic import (
    test_fixed_stop_transaction_rollback_leaves_no_partial_authority as _fixed_stop_rollback_case,
)
from tests.test_phase_10e_mock_automatic import (
    test_sourced_mock_automatic_creates_exact_one_order_without_approval as _automatic_case,
)
from tests.test_phase_10f_broker_pre_send import (
    _automatic_order,
    _replace_stage,
)
from tests.test_phase_10f_broker_pre_send import (
    test_automatic_authority_revocation_is_unsent_and_idempotent as _automatic_revoke_case,
)
from tests.test_phase_10f_broker_pre_send import (
    test_fixed_stop_valid_send_and_lost_quantity_restores_exit_pending as _fixed_stop_send_case,
)
from tests.test_phase_10f_broker_pre_send import (
    test_source_integrity_corruption_is_fail_closed_before_broker_send as _source_dispatch_case,
)
from tests.test_phase_10f_broker_pre_send import (
    test_stage_db_retryable_keeps_created_and_commit_failure_never_calls_broker as _db_retryable_case,
)
from tests.test_phase_10f_broker_pre_send import (
    test_valid_automatic_authority_commits_submitting_before_mock_call as _automatic_send_case,
)
from tests.test_phase_10f_broker_pre_send import (
    test_valid_manual_approval_remains_eligible_and_sends_once as _manual_send_case,
)
from tests.test_phase_10g1b_audit_capacity import AUDIT_RESULT_LITERALS
from tests.test_reconciliation import SnapshotClient, empty_snapshot
from tests.test_stop_trigger import _position, _set_gate, _snapshot

pytestmark = pytest.mark.postgresql

EXPECTED_DATABASE = "cresta_acceptance"
EXPECTED_HOST = "127.0.0.1"
EXPECTED_HEAD = "20260829_0044"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _postgres_acceptance_is_explicitly_enabled() -> None:
    if os.environ.get("CRESTA_RUN_POSTGRES_ACCEPTANCE") != "1":
        pytest.skip("set CRESTA_RUN_POSTGRES_ACCEPTANCE=1 for isolated PostgreSQL acceptance")


def _base_url():
    raw = os.environ.get("CRESTA_DATABASE_URL")
    if not raw:
        pytest.skip("CRESTA_DATABASE_URL is not configured")
    url = make_url(raw)
    if url.host != EXPECTED_HOST or url.database != EXPECTED_DATABASE:
        pytest.fail("Phase 10G.1 requires the isolated local cresta_acceptance database")
    return url


def _schema_name(label: str) -> str:
    return f"pg10g1_{label}_{uuid4().hex[:12]}"


def _schema_url(schema: str) -> str:
    url = _base_url().update_query_dict({"options": f"-csearch_path={schema}"})
    return url.render_as_string(hide_password=False)


def _admin_engine() -> Engine:
    return create_engine(_base_url(), poolclass=NullPool)


def _schema_engine(schema: str) -> Engine:
    return create_engine(
        _base_url(),
        poolclass=NullPool,
        connect_args={"options": f"-csearch_path={schema}"},
    )


def _create_schema(schema: str) -> None:
    with _admin_engine().begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))


def _drop_schema(schema: str) -> None:
    with _admin_engine().begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def _alembic(schema: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["CRESTA_DATABASE_URL"] = _schema_url(schema)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


@contextmanager
def _migrated_schema(label: str, revision: str = "head"):
    schema = _schema_name(label)
    _create_schema(schema)
    try:
        result = _alembic(schema, "upgrade", revision)
        assert result.returncode == 0, result.stderr
        yield schema
    finally:
        _drop_schema(schema)


@pytest.fixture(scope="session")
def pg_schema() -> Generator[str, None, None]:
    with _migrated_schema("runtime") as schema:
        yield schema


@pytest.fixture(scope="session")
def pg_engine(pg_schema: str) -> Generator[Engine, None, None]:
    engine = _schema_engine(pg_schema)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(pg_engine: Engine, pg_schema: str):
    factory = sessionmaker(bind=pg_engine, expire_on_commit=False)
    yield factory
    pg_engine.dispose(close=False)
    tables = [
        name
        for name in inspect(pg_engine).get_table_names(schema=pg_schema)
        if name != "alembic_version"
    ]
    if tables:
        qualified = ", ".join(f'"{pg_schema}"."{name}"' for name in tables)
        with pg_engine.begin() as connection:
            connection.execute(text(f"TRUNCATE TABLE {qualified} CASCADE"))


@pytest.fixture
def db(session_factory) -> Generator[Session, None, None]:
    with session_factory() as database:
        yield database


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        cookie_secure=False,
        allowed_origins="https://testserver",
        totp_encryption_key=TEST_KEY,
    )


@pytest.fixture
def admin(db: Session, settings: Settings) -> User:
    user = User(login_id="admin", password_hash=hash_password(TEST_PASSWORD))
    db.add(user)
    db.flush()
    from app.models import TotpCredential

    db.add(
        TotpCredential(
            user_id=user.id,
            encrypted_secret=encrypt_totp_secret(
                TEST_TOTP_SECRET, settings.load_totp_encryption_key()
            ),
            verified=True,
        )
    )
    db.commit()
    return user


@pytest.fixture
def client(session_factory, settings: Settings, admin: User):
    application = create_app()

    def override_db():
        with session_factory() as database:
            yield database

    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_settings] = lambda: settings
    with TestClient(application, base_url="https://testserver") as test_client:
        yield test_client


def test_postgresql_environment_is_isolated_17_and_repository_head(pg_engine: Engine) -> None:
    with pg_engine.connect() as connection:
        row = connection.execute(
            text(
                "select current_database(), inet_server_addr()::text, "
                "current_setting('server_version_num')::int"
            )
        ).one()
        head = connection.scalar(text("select version_num from alembic_version"))
    assert row[0] == EXPECTED_DATABASE
    assert row[1] == "127.0.0.1/32"
    assert 170000 <= row[2] < 180000
    assert head == EXPECTED_HEAD


def test_fresh_database_schema_migrates_to_0044() -> None:
    with _migrated_schema("fresh") as schema:
        engine = _schema_engine(schema)
        try:
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("select version_num from alembic_version"))
                    == EXPECTED_HEAD
                )
                assert connection.scalar(text("select to_regclass('account_funds_snapshots')"))
                assert connection.scalar(text("select to_regclass('order_capacity_snapshots')"))
        finally:
            engine.dispose()


def test_incremental_0040_to_0041_to_0042_to_0043_to_0044() -> None:
    with _migrated_schema("incremental", "20260827_0040") as schema:
        engine = _schema_engine(schema)
        try:
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("select version_num from alembic_version"))
                    == "20260827_0040"
                )
                assert (
                    connection.scalar(text("select to_regclass('account_funds_snapshots')")) is None
                )
            for revision in (
                "20260828_0041",
                "20260828_0042",
                "20260829_0043",
                EXPECTED_HEAD,
            ):
                result = _alembic(schema, "upgrade", revision)
                assert result.returncode == 0, result.stderr
                with engine.connect() as connection:
                    assert (
                        connection.scalar(text("select version_num from alembic_version"))
                        == revision
                    )
            with engine.connect() as connection:
                assert connection.scalar(text("select to_regclass('account_funds_snapshots')"))
                assert connection.scalar(text("select to_regclass('order_capacity_snapshots')"))
        finally:
            engine.dispose()


def test_postgresql_catalog_matches_0040_to_0044_contract(
    pg_engine: Engine, pg_schema: str
) -> None:
    inspector = inspect(pg_engine)
    decision_columns = {column["name"]: column for column in inspector.get_columns("decisions")}
    assert decision_columns["schema_version"]["type"].length == 32
    assert decision_columns["model_provider"]["nullable"]
    assert decision_columns["source_agent_run_id"]["nullable"]

    execution_indexes = {
        item["name"]: item for item in inspector.get_indexes("decision_executions")
    }
    sourced = execution_indexes["uq_decision_executions_sourced_decision"]
    assert sourced["unique"]
    assert "sourced-entry-execution-v1" in sourced["dialect_options"]["postgresql_where"]

    active_indexes = {
        item["name"]: item for item in inspector.get_indexes("configuration_versions")
    }
    assert active_indexes["uq_configuration_active_target"]["unique"]
    active_predicate = active_indexes["uq_configuration_active_target"]["dialect_options"][
        "postgresql_where"
    ]
    assert "state" in active_predicate and "ACTIVE" in active_predicate

    guard_fks = {
        item["referred_table"]: item for item in inspector.get_foreign_keys("guard_evaluations")
    }
    assert guard_fks["decision_executions"]["options"]["ondelete"] == "CASCADE"
    assert guard_fks["stop_triggers"]["options"]["ondelete"] == "RESTRICT"

    funds = {column["name"]: column for column in inspector.get_columns("account_funds_snapshots")}
    capacity = {
        column["name"]: column for column in inspector.get_columns("order_capacity_snapshots")
    }
    assert str(funds["deposit"]["type"]) == "BIGINT" and funds["deposit"]["nullable"]
    assert str(capacity["requested_price"]["type"]) == "BIGINT"
    assert not capacity["requested_price"]["nullable"]
    assert capacity["margin_100_orderable_amount"]["nullable"]

    checks = {item["name"] for item in inspector.get_check_constraints("order_capacity_snapshots")}
    assert {
        "ck_order_capacity_price",
        "ck_order_capacity_quantities_nonnegative",
        "ck_order_capacity_side_trade_type",
    } <= checks
    indexes = {item["name"]: item for item in inspector.get_indexes("order_capacity_snapshots")}
    assert indexes["ix_order_capacity_authority_latest"]["column_names"][-1] == "received_at"

    with pg_engine.connect() as connection:
        varchar_lengths = dict(
            connection.execute(
                text(
                    "select column_name, character_maximum_length from information_schema.columns "
                    "where table_schema=:schema and table_name='order_intents' "
                    "and column_name in ('authority_key','source_type','source_id')"
                ),
                {"schema": pg_schema},
            ).all()
        )
    assert varchar_lengths == {"authority_key": 128, "source_id": 128, "source_type": 32}


def test_revocation_event_type_capacity_supports_normative_value(pg_engine: Engine) -> None:
    event_type = next(
        column
        for column in inspect(pg_engine).get_columns("order_events")
        if column["name"] == "event_type"
    )
    assert event_type["type"].length >= len("ORDER_AUTHORITY_REVOKED_BEFORE_SEND")


def test_order_event_accepts_normative_and_existing_short_values(db: Session) -> None:
    order = persisted_order(db, idempotency_key="pg-event-capacity")
    now = datetime.now(UTC)
    for event_type in ("ORDER_CREATED", "ORDER_AUTHORITY_REVOKED_BEFORE_SEND"):
        db.add(
            OrderEvent(
                order_id=order.id,
                event_type=event_type,
                source="CRESTA",
                source_key=f"pg-capacity:{event_type}",
                payload_hash="c" * 64,
                payload_json="{}",
                correlation_id=str(uuid4()),
                occurred_at=now,
            )
        )
    db.commit()
    assert set(db.scalars(select(OrderEvent.event_type))) >= {
        "ORDER_CREATED",
        "ORDER_AUTHORITY_REVOKED_BEFORE_SEND",
    }


def test_0042_to_0043_upgrade_and_safe_downgrade_reupgrade() -> None:
    with _migrated_schema("capacity_safe", "20260828_0042") as schema:
        engine = _schema_engine(schema)
        try:
            assert (
                next(
                    column
                    for column in inspect(engine).get_columns("order_events")
                    if column["name"] == "event_type"
                )["type"].length
                == 32
            )
            result = _alembic(schema, "upgrade", EXPECTED_HEAD)
            assert result.returncode == 0, result.stderr
            with Session(engine) as database:
                order = persisted_order(database, idempotency_key="pg-safe-downgrade")
                database.add(
                    OrderEvent(
                        order_id=order.id,
                        event_type="ORDER_CREATED",
                        source="CRESTA",
                        source_key="pg-safe-downgrade",
                        payload_hash="d" * 64,
                        payload_json="{}",
                        correlation_id=str(uuid4()),
                        occurred_at=datetime.now(UTC),
                    )
                )
                database.commit()
            result = _alembic(schema, "downgrade", "20260828_0042")
            assert result.returncode == 0, result.stderr
            assert (
                next(
                    column
                    for column in inspect(engine).get_columns("order_events")
                    if column["name"] == "event_type"
                )["type"].length
                == 32
            )
            result = _alembic(schema, "upgrade", EXPECTED_HEAD)
            assert result.returncode == 0, result.stderr
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("select count(*) from order_events where event_type='ORDER_CREATED'")
                    )
                    == 1
                )
        finally:
            engine.dispose()


def test_0043_downgrade_refuses_long_event_without_data_loss() -> None:
    with _migrated_schema("capacity_guard") as schema:
        engine = _schema_engine(schema)
        try:
            with Session(engine) as database:
                order = persisted_order(database, idempotency_key="pg-guard-downgrade")
                database.add(
                    OrderEvent(
                        order_id=order.id,
                        event_type="ORDER_AUTHORITY_REVOKED_BEFORE_SEND",
                        source="CRESTA",
                        source_key="pg-guard-downgrade",
                        payload_hash="e" * 64,
                        payload_json="{}",
                        correlation_id=str(uuid4()),
                        occurred_at=datetime.now(UTC),
                    )
                )
                database.commit()
            result = _alembic(schema, "downgrade", "20260828_0042")
            assert result.returncode != 0
            assert "Refusing downgrade of 20260829_0043" in result.stderr
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("select version_num from alembic_version"))
                    == EXPECTED_HEAD
                )
                assert (
                    connection.scalar(
                        text(
                            "select count(*) from order_events "
                            "where event_type='ORDER_AUTHORITY_REVOKED_BEFORE_SEND'"
                        )
                    )
                    == 1
                )
            assert (
                next(
                    column
                    for column in inspect(engine).get_columns("order_events")
                    if column["name"] == "event_type"
                )["type"].length
                == 64
            )
        finally:
            engine.dispose()


def test_all_inventoried_audit_results_fit_postgresql_catalog(db: Session) -> None:
    for value in AUDIT_RESULT_LITERALS:
        identity = str(uuid4())
        db.add(
            AuditLog(
                actor_type="SYSTEM",
                actor_id=None,
                action="PHASE_10G1B_INVENTORY",
                target=identity,
                result=value,
                correlation_id=identity,
                metadata_json="{}",
            )
        )
    db.commit()
    assert AuditLog.__table__.c.result.type.length == 64
    assert {
        column["name"]: column["type"].length
        for column in inspect(db.get_bind()).get_columns("audit_logs")
        if column["name"] == "result"
    } == {"result": 64}
    assert set(
        db.scalars(
            select(AuditLog.result).where(AuditLog.action == "PHASE_10G1B_INVENTORY")
        )
    ) == AUDIT_RESULT_LITERALS


def test_0043_to_0044_upgrade_safe_downgrade_and_reupgrade() -> None:
    with _migrated_schema("audit_capacity_safe", "20260829_0043") as schema:
        engine = _schema_engine(schema)
        try:
            assert (
                next(
                    column
                    for column in inspect(engine).get_columns("audit_logs")
                    if column["name"] == "result"
                )["type"].length
                == 24
            )
            with Session(engine) as database:
                database.add(
                    AuditLog(
                        actor_type="SYSTEM",
                        action="PHASE_10G1B_SAFE",
                        result="PASSED",
                        correlation_id=str(uuid4()),
                        metadata_json="{}",
                    )
                )
                database.commit()
            result = _alembic(schema, "upgrade", EXPECTED_HEAD)
            assert result.returncode == 0, result.stderr
            result = _alembic(schema, "downgrade", "20260829_0043")
            assert result.returncode == 0, result.stderr
            assert (
                next(
                    column
                    for column in inspect(engine).get_columns("audit_logs")
                    if column["name"] == "result"
                )["type"].length
                == 24
            )
            result = _alembic(schema, "upgrade", EXPECTED_HEAD)
            assert result.returncode == 0, result.stderr
            with engine.connect() as connection:
                assert connection.scalar(
                    text("select count(*) from audit_logs where result='PASSED'")
                ) == 1
        finally:
            engine.dispose()


def test_0044_downgrade_refuses_long_audit_result_without_data_loss() -> None:
    with _migrated_schema("audit_capacity_guard") as schema:
        engine = _schema_engine(schema)
        try:
            identity = str(uuid4())
            with Session(engine) as database:
                database.add(
                    AuditLog(
                        actor_type="SYSTEM",
                        action="PHASE_10G1B_GUARD",
                        target=identity,
                        result="AUTOMATIC_AUTHORITY_REVOKED",
                        correlation_id=identity,
                        metadata_json="{}",
                    )
                )
                database.commit()
            result = _alembic(schema, "downgrade", "20260829_0043")
            assert result.returncode != 0
            assert "Refusing downgrade of 20260829_0044" in result.stderr
            with engine.connect() as connection:
                assert connection.scalar(text("select version_num from alembic_version")) == (
                    EXPECTED_HEAD
                )
                assert connection.scalar(
                    text(
                        "select count(*) from audit_logs "
                        "where result='AUTOMATIC_AUTHORITY_REVOKED'"
                    )
                ) == 1
            assert (
                next(
                    column
                    for column in inspect(engine).get_columns("audit_logs")
                    if column["name"] == "result"
                )["type"].length
                == 64
            )
        finally:
            engine.dispose()


def test_active_execution_stage_partial_unique_is_real_postgresql(
    session_factory, admin: User
) -> None:
    barrier = Barrier(2)

    def insert(sequence: int) -> str:
        with session_factory() as worker:
            barrier.wait()
            worker.add(
                ConfigurationVersion(
                    scope="SYSTEM",
                    target_id="MOCK",
                    category="V7_ENTRY_EXECUTION_STAGE",
                    sequence=sequence,
                    state="ACTIVE",
                    payload_json="{}",
                    payload_hash=str(sequence) * 64,
                    reason="PostgreSQL exact-one",
                    created_by=admin.id,
                )
            )
            try:
                worker.commit()
                return "WIN"
            except IntegrityError:
                worker.rollback()
                return "LOSE"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(insert, (1, 2)))
    assert sorted(results) == ["LOSE", "WIN"]
    with session_factory() as check:
        assert (
            check.scalar(
                select(func.count())
                .select_from(ConfigurationVersion)
                .where(
                    ConfigurationVersion.category == "V7_ENTRY_EXECUTION_STAGE",
                    ConfigurationVersion.state == "ACTIVE",
                )
            )
            == 1
        )


def test_for_update_skip_locked_claims_distinct_created_orders(
    session_factory, db: Session
) -> None:
    first = persisted_order(db, idempotency_key="pg-skip-locked-1")
    second = persisted_order(db, idempotency_key="pg-skip-locked-2")
    barrier = Barrier(2)

    def claim() -> str:
        with session_factory() as worker:
            order = worker.scalar(
                select(TradingOrder)
                .where(TradingOrder.status == "CREATED")
                .order_by(TradingOrder.created_at, TradingOrder.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            assert order is not None
            barrier.wait()
            claimed = order.id
            worker.commit()
            return claimed

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda _: claim(), range(2)))
    assert set(claimed) == {first.id, second.id}


def test_broker_lease_fencing_rejects_stale_owner(session_factory) -> None:
    now = datetime.now(UTC)
    with session_factory() as first_db:
        stale = acquire_lease(first_db, "worker-a", lease_seconds=60, now=now)
    assert stale is not None
    with session_factory() as second_db:
        current = acquire_lease(
            second_db, "worker-b", lease_seconds=60, now=now + timedelta(seconds=61)
        )
    assert current is not None and current.fencing_token == stale.fencing_token + 1
    with session_factory() as stale_db:
        assert not renew_lease(stale_db, stale, lease_seconds=60, now=now + timedelta(seconds=62))


def test_finalizer_concurrent_finalization_returns_one_decision(
    client, db: Session, admin: User, monkeypatch, session_factory
) -> None:
    run, _, _, _, loader, _ = _completed_trading(client, db, admin, monkeypatch)
    run_id = run.id
    barrier = Barrier(2)

    def finalize() -> str:
        with session_factory() as worker:
            barrier.wait()
            return finalize_entry_decision(worker, run_id=run_id, evidence_loader=loader).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        decision_ids = list(pool.map(lambda _: finalize(), range(2)))
    assert len(set(decision_ids)) == 1
    with session_factory() as check:
        assert check.scalar(select(func.count()).select_from(Decision)) == 1


def test_sourced_no_action_concurrent_handoff_returns_one_execution(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    session_factory,
    settings: Settings,
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "WAIT")
    decision_id = decision.id
    barrier = Barrier(2)

    def handoff(number: int) -> str:
        with session_factory() as worker:
            current = worker.get(Decision, decision_id)
            assert current is not None
            barrier.wait()
            return execute_sourced_entry_decision(
                worker,
                decision=current,
                correlation_id=f"pg-concurrent-{number}",
                settings=settings,
            ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        execution_ids = list(pool.map(handoff, (1, 2)))
    assert len(set(execution_ids)) == 1
    with session_factory() as check:
        assert check.scalar(select(func.count()).select_from(DecisionExecution)) == 1


@pytest.mark.parametrize("action", ("WAIT", "REJECT", "UNKNOWN"))
def test_postgresql_e2e_no_action(
    client, db: Session, admin: User, monkeypatch, settings: Settings, action: str
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, action)
    execution = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id=f"pg-e2e-{action.lower()}",
        settings=settings,
    )
    assert (execution.action, execution.state, execution.result_code) == (
        "NO_ACTION",
        "NO_ACTION",
        action,
    )
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_postgresql_e2e_shadow_has_no_order(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    import app.sourced_execution as sourced_module

    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    now = datetime.now(UTC)
    _activate_shadow(db, admin, now)
    _activate_mode(db, admin, "MANUAL_APPROVAL")
    monkeypatch.setattr(
        sourced_module,
        "buy_pre_order_guard_rules",
        lambda *args, **kwargs: [{"code": "OK", "result": "PASSED"}],
    )
    execution = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id="pg-shadow",
        settings=settings,
        now=now,
    )
    assert execution.state == "SHADOW_RECORDED"
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_postgresql_e2e_manual_approval_and_atomic_rollback(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    _manual_case(client, db, admin, monkeypatch, settings)


def test_postgresql_e2e_automatic_buy(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    _automatic_case(client, db, admin, monkeypatch, settings)


def test_postgresql_e2e_fixed_stop_exact_one_and_rollback(
    db: Session, admin: User, settings: Settings
) -> None:
    _fixed_stop_case(db, admin, settings)
    db.rollback()


def test_postgresql_fixed_stop_default_correlation_fits_persistence_boundary(
    db: Session,
    settings: Settings,
) -> None:
    evaluated_at = datetime(2026, 8, 30, 21, 1, 22, 948000, tzinfo=UTC)
    oversized = f"stop-{evaluated_at.isoformat()}"
    assert len(oversized) > 36
    _set_gate(db, "RECONCILING")
    _position(db, average_price=Decimal(50000), quantity=10)
    _snapshot(db, bid_price=Decimal(49000), received_at=evaluated_at)
    db.commit()

    with pytest.raises(DataError):
        run_fixed_stop_triggers(
            db,
            settings=settings,
            now=evaluated_at,
            correlation_id=oversized,
        )
    assert db.scalar(select(func.count()).select_from(StopTrigger)) == 0

    assert run_fixed_stop_triggers(db, settings=settings, now=evaluated_at) == 1
    trigger = db.scalar(select(StopTrigger))
    event = db.scalar(select(RiskEvent))
    assert trigger is not None and event is not None
    assert len(trigger.correlation_id) == 36
    assert str(UUID(trigger.correlation_id)) == trigger.correlation_id
    assert event.correlation_id == trigger.correlation_id


def _runtime_handoff_ready(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    *,
    stage: ExecutionStage,
    mode: str,
):
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    now = datetime.now(UTC)
    phase_10f_module._activate_risk(db, admin, entry_order_amount=500_000)
    _activate_mode(db, admin, mode)
    stage_version, loader = phase_10f_module._dynamic_stage(db, admin, now, stage)
    payload = phase_10f_module.ExecutionStagePayload.model_validate_json(
        stage_version.payload_json
    )
    resolution = StageResolution(StageResolutionStatus.PASS, stage_version, payload)
    monkeypatch.setattr(
        phase_10f_module.sourced_module,
        "resolve_current_execution_stage",
        lambda *args, **kwargs: resolution,
    )
    monkeypatch.setattr(
        phase_10f_module.sourced_module, "classify_session", lambda value: "KRX_ONLY"
    )
    monkeypatch.setattr(
        phase_10f_module.sourced_module,
        "buy_pre_order_guard_rules",
        lambda *args, **kwargs: [{"code": "BASE", "result": "PASSED"}],
    )
    snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
    assert snapshot is not None and snapshot.best_ask_price is not None
    snapshot.received_at = now
    quantity = int(Decimal(500_000) // snapshot.best_ask_price)
    policy = phase_10f_module._policy()
    context = phase_10f_module.build_buy_financial_context(
        symbol=decision.symbol,
        price=snapshot.best_ask_price,
        quantity=quantity,
        frozen_policy=policy,
        current_policy=policy,
    )
    phase_10f_module.append_account_funds_snapshot(db, phase_10f_module._funds(now))
    phase_10f_module.append_order_capacity_snapshot(
        db, phase_10f_module._capacity(context.request, now)
    )
    db.commit()
    return decision, stage_version, resolution, loader, now


def _run_actual_handoff_once(monkeypatch, session_factory, settings: Settings):
    monkeypatch.setattr(sourced_handoff_module, "SessionLocal", session_factory)
    runtime_settings = settings.model_copy(
        update={"v7_sourced_handoff_enabled": True, "agent_worker_poll_seconds": 1}
    )
    worker = SourcedHandoffWorker(runtime_settings)
    original = worker._run_sweep
    observed = []

    def one_sweep():
        try:
            result = original()
            observed.append(result)
            return result
        finally:
            worker.stop()

    monkeypatch.setattr(worker, "_run_sweep", one_sweep)
    assert asyncio.run(worker.run()) == 0
    assert len(observed) == 1
    return worker, observed[0]


def test_postgresql_runtime_activation_off_does_not_handoff(
    client, db: Session, admin: User, monkeypatch, session_factory, settings: Settings
) -> None:
    _finalized(client, db, admin, monkeypatch, "WAIT")
    monkeypatch.setattr(sourced_handoff_module, "SessionLocal", session_factory)
    worker = SourcedHandoffWorker(
        settings.model_copy(update={"v7_sourced_handoff_enabled": False})
    )
    worker.stop()
    assert asyncio.run(worker.run()) == 0
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 0


@pytest.mark.parametrize("action", ("WAIT", "REJECT", "UNKNOWN"))
def test_postgresql_background_no_action_runtime(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    session_factory,
    settings: Settings,
    action: str,
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, action)
    _, result = _run_actual_handoff_once(monkeypatch, session_factory, settings)
    db.expire_all()
    execution = db.scalar(
        select(DecisionExecution).where(DecisionExecution.decision_id == decision.id)
    )
    assert result.scanned == result.completed == 1
    assert execution is not None and (execution.state, execution.result_code) == (
        "NO_ACTION",
        action,
    )
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_postgresql_background_shadow_runtime(
    client, db: Session, admin: User, monkeypatch, session_factory, settings: Settings
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    now = datetime.now(UTC)
    _activate_shadow(db, admin, now)
    _activate_mode(db, admin, "MANUAL_APPROVAL")
    monkeypatch.setattr(
        phase_10f_module.sourced_module,
        "buy_pre_order_guard_rules",
        lambda *args, **kwargs: [{"code": "OK", "result": "PASSED"}],
    )
    _run_actual_handoff_once(monkeypatch, session_factory, settings)
    db.expire_all()
    execution = db.scalar(
        select(DecisionExecution).where(DecisionExecution.decision_id == decision.id)
    )
    assert execution is not None and execution.state == "SHADOW_RECORDED"
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_postgresql_background_automatic_runtime_to_mock_broker(
    client, db: Session, admin: User, monkeypatch, session_factory, settings: Settings
) -> None:
    decision, _, resolution, loader, now = _runtime_handoff_ready(
        client,
        db,
        admin,
        monkeypatch,
        stage=ExecutionStage.MOCK_AUTOMATIC,
        mode="AUTOMATIC",
    )
    worker, _ = _run_actual_handoff_once(monkeypatch, session_factory, settings)
    for _ in range(10):
        assert worker._run_sweep().scanned == 0
    db.expire_all()
    execution = db.scalar(
        select(DecisionExecution).where(DecisionExecution.decision_id == decision.id)
    )
    order = db.scalar(select(TradingOrder))
    assert execution is not None and execution.state == "ORDER_CREATED"
    assert order is not None and db.scalar(select(func.count()).select_from(Approval)) == 0
    monkeypatch.setattr(
        pre_send_authority_module,
        "resolve_current_execution_stage",
        lambda *args, **kwargs: resolution,
    )
    monkeypatch.setattr(
        pre_send_authority_module,
        "buy_pre_order_guard_rules",
        lambda *args, **kwargs: [{"code": "BASE", "result": "PASSED"}],
    )
    monkeypatch.setattr(
        pre_send_authority_module, "classify_session", lambda value: "KRX_ONLY"
    )
    identity = _ready_worker_at(db, now)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("pg10g2-auto", "KRX"))
    sent = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    details = [
        (event.event_type, event.payload_json)
        for event in db.scalars(
            select(OrderEvent).where(OrderEvent.order_id == order.id)
        )
    ]
    assert sent.status == "ACKNOWLEDGED" and len(broker.requests) == 1, details
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 1
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1


def test_postgresql_background_manual_runtime_to_mock_broker(
    client, db: Session, admin: User, monkeypatch, session_factory, settings: Settings
) -> None:
    decision, _, resolution, loader, now = _runtime_handoff_ready(
        client,
        db,
        admin,
        monkeypatch,
        stage=ExecutionStage.APPROVAL_ONLY,
        mode="MANUAL_APPROVAL",
    )
    _run_actual_handoff_once(monkeypatch, session_factory, settings)
    db.expire_all()
    execution = db.scalar(
        select(DecisionExecution).where(DecisionExecution.decision_id == decision.id)
    )
    assert execution is not None and execution.state == "APPROVAL_PENDING"
    approval = db.get(Approval, execution.approval_id)
    assert approval is not None and approval.state == "PENDING"
    raw_proof = "phase-10g2-manual-proof"
    db.add(
        ReauthProof(
            proof_hash=token_hash(raw_proof),
            user_id=admin.id,
            target_action="APPROVE_ORDER",
            target_id=f"{approval.id}:1",
            expires_at=now + timedelta(minutes=5),
        )
    )
    db.commit()
    monkeypatch.setattr(
        approvals_module,
        "resolve_current_execution_stage",
        lambda *args, **kwargs: resolution,
    )
    monkeypatch.setattr(
        decision_execution_module,
        "buy_pre_order_guard_rules",
        lambda *args, **kwargs: [{"code": "BASE", "result": "PASSED"}],
    )
    approved = approve(
        db,
        approval_id=approval.id,
        user=admin,
        settings=settings,
        correlation_id="pg10g2-manual-approve",
        idempotency_key="pg10g2-manual-order",
        expected_version=1,
        reauth_proof=raw_proof,
        now=now,
    )
    order = db.get(TradingOrder, approved.order_id)
    assert order is not None
    monkeypatch.setattr(
        pre_send_authority_module,
        "resolve_current_execution_stage",
        lambda *args, **kwargs: resolution,
    )
    monkeypatch.setattr(
        pre_send_authority_module,
        "buy_pre_order_guard_rules",
        lambda *args, **kwargs: [{"code": "BASE", "result": "PASSED"}],
    )
    monkeypatch.setattr(
        pre_send_authority_module, "classify_session", lambda value: "KRX_ONLY"
    )
    identity = _ready_worker_at(db, now)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("pg10g2-manual", "KRX"))
    sent = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    details = [
        (event.event_type, event.payload_json)
        for event in db.scalars(
            select(OrderEvent).where(OrderEvent.order_id == order.id)
        )
    ]
    assert sent.status == "ACKNOWLEDGED" and len(broker.requests) == 1, details
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 1
    assert db.scalar(select(func.count()).select_from(Approval)) == 1
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 1
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1


def test_postgresql_dual_handoff_workers_and_restart_are_exact_one(
    client, db: Session, admin: User, monkeypatch, session_factory, settings: Settings
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "WAIT")
    monkeypatch.setattr(sourced_handoff_module, "SessionLocal", session_factory)
    runtime = settings.model_copy(update={"v7_sourced_handoff_enabled": True})
    barrier = Barrier(2)

    def sweep(_number: int):
        worker = SourcedHandoffWorker(runtime)
        barrier.wait()
        return worker._run_sweep()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(sweep, (1, 2)))
    restarted = SourcedHandoffWorker(runtime)
    assert restarted._run_sweep().scanned == 0
    assert sum(item.completed for item in results) in {1, 2}
    with session_factory() as check:
        assert check.scalar(
            select(func.count()).select_from(DecisionExecution).where(
                DecisionExecution.decision_id == decision.id
            )
        ) == 1


def test_postgresql_finalizer_commit_boundary_and_rollback_are_invisible(
    client, db: Session, admin: User, monkeypatch, session_factory, settings: Settings
) -> None:
    run, _, _, _, loader, _ = _completed_trading(client, db, admin, monkeypatch)
    monkeypatch.setattr(sourced_handoff_module, "SessionLocal", session_factory)
    worker = SourcedHandoffWorker(
        settings.model_copy(update={"v7_sourced_handoff_enabled": True})
    )
    observed = []
    decision = finalize_entry_decision(
        db,
        run_id=run.id,
        evidence_loader=loader,
        write_boundary_hook=lambda: observed.append(worker._run_sweep().scanned),
    )
    assert observed == [0]
    assert worker._run_sweep().completed == 1
    assert worker._run_sweep().scanned == 0
    assert db.scalar(
        select(func.count()).select_from(DecisionExecution).where(
            DecisionExecution.decision_id == decision.id
        )
    ) == 1


def test_postgresql_finalizer_rollback_never_handoffs(
    client, db: Session, admin: User, monkeypatch, session_factory, settings: Settings
) -> None:
    run, _, _, _, loader, _ = _completed_trading(client, db, admin, monkeypatch)
    monkeypatch.setattr(sourced_handoff_module, "SessionLocal", session_factory)
    worker = SourcedHandoffWorker(
        settings.model_copy(update={"v7_sourced_handoff_enabled": True})
    )

    def fail_after_visibility_check() -> None:
        assert worker._run_sweep().scanned == 0
        raise RuntimeError("rollback finalizer")

    with pytest.raises(RuntimeError, match="rollback finalizer"):
        finalize_entry_decision(
            db,
            run_id=run.id,
            evidence_loader=loader,
            write_boundary_hook=fail_after_visibility_check,
        )
    db.rollback()
    assert worker._run_sweep().scanned == 0
    assert db.scalar(select(func.count()).select_from(Decision)) == 0
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 0


def test_postgresql_background_db_outage_recovers_without_partial_execution(
    client, db: Session, admin: User, monkeypatch, session_factory, settings: Settings
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "WAIT")
    calls = 0

    def fail_once_session_factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("simulated unavailable database", {}, RuntimeError())
        return session_factory()

    monkeypatch.setattr(
        sourced_handoff_module, "SessionLocal", fail_once_session_factory
    )
    worker = SourcedHandoffWorker(
        settings.model_copy(
            update={"v7_sourced_handoff_enabled": True, "agent_worker_poll_seconds": 1}
        )
    )
    original = worker._run_sweep

    def recover_on_next_permitted_iteration():
        if calls == 1:
            with session_factory() as check:
                assert check.scalar(select(func.count()).select_from(DecisionExecution)) == 0
        result = original()
        if result.completed:
            worker.stop()
        return result

    monkeypatch.setattr(worker, "_run_sweep", recover_on_next_permitted_iteration)
    assert asyncio.run(worker.run()) == 0
    assert calls == 2
    with session_factory() as check:
        execution = check.scalar(
            select(DecisionExecution).where(DecisionExecution.decision_id == decision.id)
        )
        assert execution is not None and execution.state == "NO_ACTION"



def test_postgresql_fixed_stop_atomic_rollback(
    db: Session, admin: User, settings: Settings
) -> None:
    _fixed_stop_rollback_case(db, admin, settings)


def _install_fresh_phase10f_setup(monkeypatch) -> None:
    observed_times: list[datetime] = []

    def fresh_setup(*args, **kwargs):
        result = _fresh_automatic_order(*args, **kwargs)
        observed_times.append(result[-1])
        return result

    monkeypatch.setattr(phase_10f_module, "_automatic_order", fresh_setup)
    monkeypatch.setattr(
        phase_10f_module,
        "ready_worker",
        lambda session: _ready_worker_at(session, observed_times[-1]),
    )


def test_postgresql_broker_send_automatic_commits_before_adapter(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    _install_fresh_phase10f_setup(monkeypatch)
    _automatic_send_case(client, db, admin, monkeypatch, settings)


def test_postgresql_broker_send_manual_is_exact_once(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    _install_fresh_phase10f_setup(monkeypatch)
    _manual_send_case(client, db, admin, monkeypatch, settings)


def test_postgresql_broker_send_fixed_stop_and_unclassified_revoke(
    db: Session, admin: User, settings: Settings
) -> None:
    _fixed_stop_send_case(db, admin, settings)


def test_postgresql_revocation_event_and_execution_transition_commit(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    _automatic_revoke_case(client, db, admin, monkeypatch, settings, "PAUSE")
    order = db.scalar(select(TradingOrder))
    intent = db.scalar(select(OrderIntent))
    execution = db.scalar(select(DecisionExecution))
    assert order is not None and order.status == "INVALIDATED"
    assert intent is not None and intent.source_type == "DECISION_EXECUTION"
    assert execution is not None
    assert (execution.state, execution.result_code) == (
        "FAILED_SAFE",
        "EXECUTION_AUTHORITY_REVOKED_BEFORE_SEND",
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(OrderEvent)
            .where(OrderEvent.event_type == "ORDER_AUTHORITY_REVOKED_BEFORE_SEND")
        )
        == 1
    )


def test_postgresql_manual_approval_revocation_calls_no_broker(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    _, execution, order, _, loader, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    intent = db.get(OrderIntent, order.intent_id)
    current = db.get(ConfigurationVersion, execution.execution_policy_version_id)
    assert intent is not None and current is not None
    payload = SAFE_DEFAULT_POLICY.model_copy(update={"buy": "MANUAL_APPROVAL"})
    encoded = json.dumps(payload.model_dump(), separators=(",", ":"), sort_keys=True)
    current.payload_json = encoded
    current.payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
    execution.mode = "MANUAL_APPROVAL"
    approval = Approval(
        execution_id=execution.id,
        decision_id=execution.decision_id,
        user_id=admin.id,
        state="APPROVED",
        scope_snapshot_json="{}",
        expires_at=now + timedelta(minutes=1),
        order_id=order.id,
        result_code="ORDER_CREATED",
    )
    db.add(approval)
    db.flush()
    execution.approval_id = approval.id
    intent.approval_id = approval.id
    intent.authority_key = order_authority_key(
        source_type="DECISION_EXECUTION",
        source_id=execution.id,
        approval_id=approval.id,
    )
    immutable_intent = (
        intent.id,
        intent.source_type,
        intent.source_id,
        intent.symbol,
        intent.side,
        intent.requested_quantity,
        intent.authority_key,
    )
    db.commit()
    activate_pause_entry(
        db,
        user=admin,
        reason="Phase 10G.1A manual revocation",
        idempotency_key="phase10g1a-pause-entry",
        correlation_id="phase10g1a-pause",
        request_ip="127.0.0.1",
        user_agent="test",
    )
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    result = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    assert result.status == "INVALIDATED" and broker.requests == []
    db.refresh(approval)
    db.refresh(intent)
    assert order is not None and order.status == "INVALIDATED"
    assert approval is not None and approval.state == "INVALIDATED"
    assert intent is not None and intent.approval_id == approval.id
    assert immutable_intent == (
        intent.id,
        intent.source_type,
        intent.source_id,
        intent.symbol,
        intent.side,
        intent.requested_quantity,
        intent.authority_key,
    )


def test_postgresql_automatic_revocation_persists_exact_audit_result(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    _automatic_revoke_case(client, db, admin, monkeypatch, settings, "STAGE")
    order = db.scalar(select(TradingOrder))
    execution = db.scalar(select(DecisionExecution))
    audit = db.scalar(
        select(AuditLog).where(AuditLog.result == "AUTOMATIC_AUTHORITY_REVOKED")
    )
    assert order is not None and order.status == "INVALIDATED"
    assert execution is not None and execution.state == "FAILED_SAFE"
    assert audit is not None and audit.target == order.id


def test_postgresql_manual_revocation_persists_exact_audit_and_lifecycle(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    _, execution, order, _, loader, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    intent = db.get(OrderIntent, order.intent_id)
    current = db.get(ConfigurationVersion, execution.execution_policy_version_id)
    assert intent is not None and current is not None
    manual_payload = SAFE_DEFAULT_POLICY.model_copy(update={"buy": "MANUAL_APPROVAL"})
    encoded = json.dumps(
        manual_payload.model_dump(), separators=(",", ":"), sort_keys=True
    )
    current.payload_json = encoded
    current.payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
    execution.mode = "MANUAL_APPROVAL"
    approval = Approval(
        execution_id=execution.id,
        decision_id=execution.decision_id,
        user_id=admin.id,
        state="APPROVED",
        scope_snapshot_json="{}",
        expires_at=now + timedelta(minutes=1),
        order_id=order.id,
        result_code="ORDER_CREATED",
    )
    db.add(approval)
    db.flush()
    execution.approval_id = approval.id
    intent.approval_id = approval.id
    intent.authority_key = order_authority_key(
        source_type="DECISION_EXECUTION",
        source_id=execution.id,
        approval_id=approval.id,
    )
    db.commit()
    immutable_intent = (
        intent.id,
        intent.source_type,
        intent.source_id,
        intent.symbol,
        intent.side,
        intent.requested_quantity,
        intent.authority_key,
    )
    disabled_payload = SAFE_DEFAULT_POLICY.model_copy(update={"buy": "DISABLED"})
    encoded = json.dumps(
        disabled_payload.model_dump(), separators=(",", ":"), sort_keys=True
    )
    current.payload_json = encoded
    current.payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
    db.commit()
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    result = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    db.refresh(order)
    db.refresh(execution)
    db.refresh(approval)
    db.refresh(intent)
    audit = db.scalar(
        select(AuditLog).where(AuditLog.result == "APPROVAL_AUTHORITY_REVOKED")
    )
    assert result.status == "INVALIDATED" and broker.requests == []
    assert order.status == "INVALIDATED"
    assert (execution.state, execution.result_code) == (
        "FAILED_SAFE",
        "EXECUTION_AUTHORITY_REVOKED_BEFORE_SEND",
    )
    assert (approval.state, approval.result_code) == (
        "INVALIDATED",
        "EXECUTION_AUTHORITY_REVOKED",
    )
    assert audit is not None and audit.target == order.id
    assert immutable_intent == (
        intent.id,
        intent.source_type,
        intent.source_id,
        intent.symbol,
        intent.side,
        intent.requested_quantity,
        intent.authority_key,
    )
    assert db.scalar(
        select(func.count()).select_from(OrderEvent).where(
            OrderEvent.order_id == order.id,
            OrderEvent.event_type == "ORDER_AUTHORITY_REVOKED_BEFORE_SEND",
        )
    ) == 1


def test_postgresql_revocation_commit_failure_has_no_partial_state(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    _, execution, order, stage, _, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    loader = _replace_stage(db, stage, admin, now, ExecutionStage.APPROVAL_ONLY)
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    original_commit = db.commit

    def fail_commit() -> None:
        db.rollback()
        raise RuntimeError("injected revocation commit failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="injected revocation commit failure"):
        send_new_order_once(
            db,
            broker,
            identity,
            order.id,
            now=now,
            settings=settings,
            stage_evidence_loader=loader,
        )
    monkeypatch.setattr(db, "commit", original_commit)
    db.expire_all()
    assert db.get(TradingOrder, order.id).status == "CREATED"
    assert db.get(DecisionExecution, execution.id).state == "ORDER_CREATED"
    assert broker.requests == []
    assert db.scalar(
        select(func.count()).select_from(OrderEvent).where(
            OrderEvent.order_id == order.id,
            OrderEvent.event_type == "ORDER_AUTHORITY_REVOKED_BEFORE_SEND",
        )
    ) == 0
    assert db.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.target == order.id,
            AuditLog.result == "AUTOMATIC_AUTHORITY_REVOKED",
        )
    ) == 0


def test_postgresql_finalizer_rollback_is_atomic(
    client, db: Session, admin: User, monkeypatch
) -> None:
    _finalizer_rollback_case(client, db, admin, monkeypatch)


@pytest.mark.parametrize("boundary_failure", ("GATE", "EXPIRY"))
def test_postgresql_finalizer_gate_and_expiry_toctou_are_atomic(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    boundary_failure: str,
) -> None:
    _finalizer_toctou_case(
        client, db, admin, monkeypatch, boundary_failure
    )


@pytest.mark.parametrize("action", ("WAIT", "REJECT", "UNKNOWN"))
def test_postgresql_sourced_identity_is_configuration_independent(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    action: str,
) -> None:
    _no_action_identity_case(client, db, admin, monkeypatch, settings, action)


def test_postgresql_activation_gate_concurrent_service_activation_exact_one(
    db: Session, admin: User, session_factory
) -> None:
    payload, artifacts = _gate_payload()
    loader = artifacts.__getitem__
    version_ids = []
    for number in (1, 2):
        version = create_activation_gate_draft(
            db,
            user=admin,
            payload=payload,
            reason=f"Phase 10G.1 concurrent Gate {number}",
            now=ACTIVATION_NOW,
            evidence_loader=loader,
            snapshot_verifier=lambda _snapshot: None,
        )
        validate_activation_gate_draft(
            db,
            version_id=version.id,
            now=ACTIVATION_NOW,
            evidence_loader=loader,
            snapshot_verifier=lambda _snapshot: None,
        )
        version_ids.append(version.id)
    barrier = Barrier(2)

    def activate(version_id: str) -> str:
        with session_factory() as worker:
            user = worker.get(User, admin.id)
            assert user is not None
            barrier.wait()
            try:
                activated = activate_activation_gate(
                    worker,
                    user=user,
                    version_id=version_id,
                    now=ACTIVATION_NOW,
                    evidence_loader=loader,
                    correlation_id=str(uuid4()),
                    request_ip="127.0.0.1",
                    user_agent="pytest",
                    snapshot_verifier=lambda _snapshot: None,
                )
                return f"ACTIVE:{activated.id}"
            except (ActivationGateError, IntegrityError) as exc:
                worker.rollback()
                return f"LOSER:{type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(activate, version_ids))
    assert sum(item.startswith("ACTIVE:") for item in outcomes) == 1
    db.expire_all()
    assert db.scalar(
        select(func.count()).select_from(ConfigurationVersion).where(
            ConfigurationVersion.category == ACTIVATION_CATEGORY,
            ConfigurationVersion.state == "ACTIVE",
        )
    ) == 1
    assert select_current_v7_entry_activation_gate(
        db,
        now=ACTIVATION_NOW,
        evidence_loader=loader,
        snapshot_verifier=lambda _snapshot: None,
    ).outcome is GateOutcome.PASS


def test_postgresql_execution_stage_concurrent_service_activation_exact_one(
    db: Session, admin: User, session_factory
) -> None:
    value, artifacts = _stage_payload(ExecutionStage.MOCK_AUTOMATIC)
    loader = artifacts.__getitem__
    version_ids = []
    for number in (1, 2):
        version = create_execution_stage_draft(
            db,
            user=admin,
            payload=value,
            reason=f"Phase 10G.1 concurrent Stage {number}",
            now=CONTROL_NOW,
            evidence_loader=loader,
        )
        validate_execution_stage_draft(
            db,
            version_id=version.id,
            now=CONTROL_NOW,
            evidence_loader=loader,
        )
        version_ids.append(version.id)
    barrier = Barrier(2)

    def activate(version_id: str) -> str:
        with session_factory() as worker:
            user = worker.get(User, admin.id)
            assert user is not None
            barrier.wait()
            try:
                activated = activate_execution_stage(
                    worker,
                    user=user,
                    version_id=version_id,
                    now=CONTROL_NOW,
                    evidence_loader=loader,
                    correlation_id=str(uuid4()),
                    request_ip="127.0.0.1",
                    user_agent="pytest",
                )
                return f"ACTIVE:{activated.id}"
            except (ExecutionStageError, IntegrityError) as exc:
                worker.rollback()
                return f"LOSER:{type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(activate, version_ids))
    assert sum(item.startswith("ACTIVE:") for item in outcomes) == 1
    db.expire_all()
    assert db.scalar(
        select(func.count()).select_from(ConfigurationVersion).where(
            ConfigurationVersion.category == EXECUTION_STAGE_CATEGORY,
            ConfigurationVersion.state == "ACTIVE",
        )
    ) == 1
    assert resolve_current_execution_stage(
        db, now=CONTROL_NOW, evidence_loader=loader
    ).status is StageResolutionStatus.PASS


def test_postgresql_gate_and_stage_ambiguous_current_fail_closed() -> None:
    with _migrated_schema("ambiguous_control") as schema:
        engine = _schema_engine(schema)
        try:
            with Session(engine) as database:
                user = User(
                    login_id="ambiguous-control",
                    password_hash=hash_password(TEST_PASSWORD),
                )
                database.add(user)
                database.flush()
                database.execute(text('DROP INDEX "uq_configuration_active_target"'))
                rows = []
                for category, scope, target in (
                    (ACTIVATION_CATEGORY, ACTIVATION_SCOPE, ACTIVATION_TARGET),
                    (
                        EXECUTION_STAGE_CATEGORY,
                        EXECUTION_STAGE_SCOPE,
                        EXECUTION_STAGE_TARGET,
                    ),
                ):
                    for sequence in (1, 2):
                        rows.append(
                            ConfigurationVersion(
                                scope=scope,
                                target_id=target,
                                category=category,
                                sequence=sequence,
                                state="ACTIVE",
                                payload_json="{}",
                                payload_hash="a" * 64,
                                reason="intentional ambiguity acceptance",
                                created_by=user.id,
                            )
                        )
                database.add_all(rows)
                database.commit()
                assert select_current_v7_entry_activation_gate(
                    database, now=ACTIVATION_NOW, evidence_loader=None
                ).outcome is GateOutcome.INVALID
                assert resolve_current_execution_stage(
                    database, now=CONTROL_NOW, evidence_loader=None
                ).status is StageResolutionStatus.AMBIGUOUS
        finally:
            engine.dispose()


def test_postgresql_guard_typed_fk_and_invalid_matrix(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    session_factory,
) -> None:
    _, execution, _, _, _, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    position = Position(
        account_alias="KIWOOM_MOCK_PRIMARY",
        symbol="000660",
        quantity=1,
        available_quantity=1,
        managed_quantity=1,
        average_price=Decimal(100),
        managed_average_price=Decimal(100),
        state="OPEN",
        origin="CRESTA_MANAGED",
    )
    db.add(position)
    db.flush()
    trigger = StopTrigger(
        account_alias=position.account_alias,
        position_id=position.id,
        position_version=position.version,
        symbol=position.symbol,
        market="KRX",
        stop_price=Decimal(90),
        state="PENDING",
        correlation_id=str(uuid4()),
    )
    db.add(trigger)
    db.flush()
    db.add(
        GuardEvaluation(
            execution_id=None,
            stop_trigger_id=trigger.id,
            phase="PRE_ORDER",
            subject_type="STOP_TRIGGER",
            subject_id=trigger.id,
            result="PASSED",
            rule_results_json="[]",
            evaluated_at=now,
        )
    )
    db.commit()
    decision_guard = db.scalar(
        select(GuardEvaluation).where(
            GuardEvaluation.execution_id == execution.id,
            GuardEvaluation.subject_type == "DECISION_EXECUTION",
        )
    )
    assert decision_guard is not None

    invalid_rows = (
        ("DECISION_EXECUTION", str(uuid4()), None, str(uuid4())),
        ("STOP_TRIGGER", None, str(uuid4()), str(uuid4())),
        ("DECISION_EXECUTION", trigger.id, None, trigger.id),
        ("STOP_TRIGGER", None, execution.id, execution.id),
        ("DECISION_EXECUTION", None, trigger.id, trigger.id),
        ("STOP_TRIGGER", execution.id, None, execution.id),
    )

    def rejected(values: tuple[str, str | None, str | None, str]) -> bool:
        subject_type, execution_id, stop_trigger_id, subject_id = values
        with session_factory() as worker:
            worker.add(
                GuardEvaluation(
                    execution_id=execution_id,
                    stop_trigger_id=stop_trigger_id,
                    phase="BROKER_SEND",
                    subject_type=subject_type,
                    subject_id=subject_id,
                    result="PASSED",
                    rule_results_json="[]",
                    evaluated_at=now,
                )
            )
            try:
                worker.commit()
            except IntegrityError:
                worker.rollback()
                return True
            return False

    assert all(rejected(values) for values in invalid_rows)


def test_postgresql_financial_funds_selector_and_zero_null_distinction(
    db: Session,
) -> None:
    _funds_selector_case(db)


def test_postgresql_financial_capacity_exact_context_and_ordering(
    db: Session,
) -> None:
    _capacity_selector_case(db)


def test_postgresql_financial_freshness_and_future_timestamp_fail_closed(
    db: Session,
) -> None:
    _financial_freshness_case(db)


def _pending_approval_context(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
) -> tuple[DecisionExecution, Approval, datetime]:
    decision, now = _automatic_buy_ready(
        client, db, admin, monkeypatch, settings
    )
    policy = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.category == "EXECUTION_POLICY",
            ConfigurationVersion.state == "ACTIVE",
        )
    )
    assert policy is not None
    payload = SAFE_DEFAULT_POLICY.model_copy(update={"buy": "MANUAL_APPROVAL"})
    encoded = json.dumps(payload.model_dump(), separators=(",", ":"), sort_keys=True)
    policy.payload_json = encoded
    policy.payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
    db.commit()
    execution = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id="pg-pending-approval",
        settings=settings,
        now=now,
    )
    approval = db.get(Approval, execution.approval_id)
    stage = db.get(ConfigurationVersion, execution.execution_stage_version_id)
    assert approval is not None and stage is not None
    resolution = StageResolution(
        StageResolutionStatus.PASS,
        version=stage,
        payload=SimpleNamespace(stage=ExecutionStage.MOCK_AUTOMATIC),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        approvals_module,
        "resolve_current_execution_stage",
        lambda *args, **kwargs: resolution,
    )
    monkeypatch.setattr(
        decision_execution_module,
        "buy_pre_order_guard_rules",
        lambda *args, **kwargs: [{"code": "BASE", "result": "PASSED"}],
    )
    return execution, approval, now


def test_postgresql_approval_concurrent_create_exact_one(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    session_factory,
) -> None:
    decision, now = _automatic_buy_ready(
        client, db, admin, monkeypatch, settings
    )
    policy = db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.category == "EXECUTION_POLICY",
            ConfigurationVersion.state == "ACTIVE",
        )
    )
    assert policy is not None
    payload = SAFE_DEFAULT_POLICY.model_copy(update={"buy": "DISABLED"})
    encoded = json.dumps(payload.model_dump(), separators=(",", ":"), sort_keys=True)
    policy.payload_json = encoded
    policy.payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
    db.commit()
    execution = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id="pg-approval-create-parent",
        settings=settings,
        now=now,
    )
    assert execution.state == "DISABLED"
    barrier = Barrier(2)

    def create(number: int) -> str:
        with session_factory() as worker:
            barrier.wait()
            worker.add(
                Approval(
                    execution_id=execution.id,
                    decision_id=decision.id,
                    user_id=admin.id,
                    state="PENDING",
                    scope_snapshot_json="{}",
                    expires_at=now + timedelta(minutes=5),
                    result_code=f"CREATE_{number}",
                )
            )
            try:
                worker.commit()
                return "CREATED"
            except IntegrityError:
                worker.rollback()
                return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(create, (1, 2)))
    assert sorted(outcomes) == ["CONFLICT", "CREATED"]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Approval)) == 1


@pytest.mark.parametrize(
    "operations", (("APPROVE", "APPROVE"), ("APPROVE", "REJECT"))
)
def test_postgresql_approval_cas_concurrent_winner(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    session_factory,
    operations: tuple[str, str],
) -> None:
    _, approval, now = _pending_approval_context(
        client, db, admin, monkeypatch, settings
    )
    proofs: dict[int, str] = {}
    for number in (1, 2):
        raw = f"pg-approval-proof-{number}-{uuid4()}"
        proofs[number] = raw
        db.add(
            ReauthProof(
                proof_hash=token_hash(raw),
                user_id=admin.id,
                target_action="APPROVE_ORDER",
                target_id=f"{approval.id}:1",
                expires_at=now + timedelta(minutes=5),
            )
        )
    db.commit()
    barrier = Barrier(2)

    def act(item: tuple[int, str]) -> str:
        number, operation = item
        with session_factory() as worker:
            user = worker.get(User, admin.id)
            assert user is not None
            barrier.wait()
            try:
                if operation == "APPROVE":
                    result = approve(
                        worker,
                        approval_id=approval.id,
                        user=user,
                        settings=settings,
                        correlation_id=f"pg-concurrent-approve-{number}",
                        idempotency_key=f"pg-concurrent-order-{number}",
                        expected_version=1,
                        reauth_proof=proofs[number],
                        now=now,
                    )
                else:
                    result = reject(
                        worker,
                        approval_id=approval.id,
                        user=user,
                        correlation_id=f"pg-concurrent-reject-{number}",
                        expected_version=1,
                        now=now,
                    )
                return f"WIN:{result.state}"
            except ApprovalError as exc:
                worker.rollback()
                return f"LOSE:{exc.code}"
            except StaleDataError:
                worker.rollback()
                return "RAW:StaleDataError"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(act, enumerate(operations, start=1)))
    assert sum(item.startswith("WIN:") for item in outcomes) == 1
    assert outcomes.count("LOSE:APPROVAL_VERSION_CONFLICT") == 1
    assert not any(item.startswith("RAW:") for item in outcomes)
    db.expire_all()
    current = db.get(Approval, approval.id)
    assert current is not None and current.version == 2
    assert current.state in {"APPROVED", "REJECTED"}
    expected_orders = 1 if current.state == "APPROVED" else 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == expected_orders
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == expected_orders
    assert db.scalar(
        select(func.count()).select_from(ReauthProof).where(
            ReauthProof.consumed_at.is_not(None)
        )
    ) == expected_orders
    assert db.scalar(
        select(func.count()).select_from(GuardEvaluation).where(
            GuardEvaluation.phase == "APPROVAL_REVALIDATION"
        )
    ) == expected_orders
    assert db.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "SOURCED_APPROVAL_APPROVED"
        )
    ) == expected_orders
    assert db.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "APPROVAL_REJECTED"
        )
    ) == (1 - expected_orders)


def test_postgresql_reauth_concurrent_double_consume_and_rollback(
    db: Session, admin: User, session_factory
) -> None:
    now = datetime.now(UTC)
    raw = f"pg-reauth-{uuid4()}"
    proof = ReauthProof(
        proof_hash=token_hash(raw),
        user_id=admin.id,
        target_action="APPROVE_ORDER",
        target_id="pg-reauth-target",
        expires_at=now + timedelta(minutes=5),
    )
    db.add(proof)
    db.commit()
    barrier = Barrier(2)

    def consume(number: int) -> str:
        with session_factory() as worker:
            user = worker.get(User, admin.id)
            assert user is not None
            barrier.wait()
            try:
                consume_reauth_proof(
                    worker,
                    user=user,
                    raw_proof=raw,
                    target_action="APPROVE_ORDER",
                    target_id="pg-reauth-target",
                    now=now,
                )
                worker.commit()
                return f"CONSUMED:{number}"
            except ReauthProofError:
                worker.rollback()
                return f"REJECTED:{number}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(consume, (1, 2)))
    assert sum(item.startswith("CONSUMED:") for item in outcomes) == 1

    rollback_raw = f"pg-reauth-rollback-{uuid4()}"
    rollback_proof = ReauthProof(
        proof_hash=token_hash(rollback_raw),
        user_id=admin.id,
        target_action="APPROVE_ORDER",
        target_id="pg-reauth-rollback-target",
        expires_at=now + timedelta(minutes=5),
    )
    db.add(rollback_proof)
    db.commit()
    with session_factory() as worker:
        user = worker.get(User, admin.id)
        assert user is not None
        consume_reauth_proof(
            worker,
            user=user,
            raw_proof=rollback_raw,
            target_action="APPROVE_ORDER",
            target_id="pg-reauth-rollback-target",
            now=now,
        )
        worker.rollback()
    with session_factory() as worker:
        user = worker.get(User, admin.id)
        assert user is not None
        consumed = consume_reauth_proof(
            worker,
            user=user,
            raw_proof=rollback_raw,
            target_action="APPROVE_ORDER",
            target_id="pg-reauth-rollback-target",
            now=now,
        )
        worker.commit()
        assert consumed.consumed_at is not None
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_postgresql_decision_authority_concurrent_initial_create_exact_one(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    session_factory,
) -> None:
    decision, now = _automatic_buy_ready(
        client, db, admin, monkeypatch, settings
    )
    barrier = Barrier(2)

    def execute(number: int) -> str:
        with session_factory() as worker:
            current = worker.get(Decision, decision.id)
            assert current is not None
            barrier.wait()
            execution = execute_sourced_entry_decision(
                worker,
                decision=current,
                correlation_id=f"pg-authority-create-{number}",
                settings=settings,
                now=now,
            )
            return execution.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        execution_ids = list(pool.map(execute, (1, 2)))
    assert len(set(execution_ids)) == 1
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(DecisionExecution)) == 1
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 1
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1
    intent = db.scalar(select(OrderIntent))
    assert intent is not None and intent.source_type == "DECISION_EXECUTION"
    assert intent.authority_key == order_authority_key(
        source_type="DECISION_EXECUTION",
        source_id=execution_ids[0],
        approval_id=None,
    )


def test_postgresql_fixed_stop_concurrent_processing_exact_one(
    db: Session,
    admin: User,
    settings: Settings,
    session_factory,
) -> None:
    _, loader = _activate_stage(db, admin, ExecutionStage.MOCK_AUTOMATIC)
    _activate_fixed_stop_policy(db, admin)
    from tests.test_approvals_api import _activate_risk

    _activate_risk(db, admin)
    _set_gate(db, "READY")
    position = _position(db, average_price=Decimal(50000), quantity=10)
    _snapshot(db, bid_price=Decimal(49000), received_at=CONTROL_NOW)
    barrier = Barrier(2)

    def process(number: int) -> str:
        with session_factory() as worker:
            barrier.wait()
            try:
                run_fixed_stop_triggers(
                    worker,
                    settings=settings,
                    now=CONTROL_NOW,
                    correlation_id=f"pg-fixed-stop-{number}",
                    stage_evidence_loader=loader,
                )
                return "COMMITTED"
            except IntegrityError:
                worker.rollback()
                return "UNIQUE_LOSER"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(process, (1, 2)))
    assert "COMMITTED" in outcomes
    run_fixed_stop_triggers(
        db,
        settings=settings,
        now=CONTROL_NOW,
        correlation_id="pg-fixed-stop-recovery",
        stage_evidence_loader=loader,
    )
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(StopTrigger)) == 1
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 1
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1
    current_position = db.get(Position, position.id)
    order = db.scalar(select(TradingOrder))
    intent = db.scalar(select(OrderIntent))
    trigger = db.scalar(select(StopTrigger))
    assert current_position is not None and order is not None
    assert intent is not None and trigger is not None
    assert order.requested_quantity <= current_position.managed_quantity
    assert order.requested_quantity <= current_position.available_quantity
    assert intent.source_type == "STOP_TRIGGER"
    assert intent.authority_key == order_authority_key(
        source_type="STOP_TRIGGER", source_id=trigger.id, approval_id=None
    )
    guard = db.get(GuardEvaluation, trigger.guard_evaluation_id)
    assert guard is not None and guard.subject_type == "STOP_TRIGGER"


def _assert_serialized_send_or_revoke(
    db: Session, order_id: str, broker: FakeOrderClient
) -> None:
    db.expire_all()
    order = db.get(TradingOrder, order_id)
    assert order is not None
    if order.status == "INVALIDATED":
        assert broker.requests == []
        assert db.scalar(
            select(func.count()).select_from(OrderEvent).where(
                OrderEvent.order_id == order.id,
                OrderEvent.event_type == "ORDER_AUTHORITY_REVOKED_BEFORE_SEND",
            )
        ) == 1
        assert db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.target == order.id,
                AuditLog.action == "ORDER_AUTHORITY_REVOKED_BEFORE_SEND",
            )
        ) == 1
    else:
        assert order.status == "ACKNOWLEDGED"
        assert len(broker.requests) == 1


def _ready_worker_at(db: Session, now: datetime):
    identity = acquire_lease(db, "worker-a", lease_seconds=60, now=now)
    assert identity is not None
    assert update_worker_state(
        db,
        identity,
        "READY",
        websocket_connected=True,
        subscriptions_ready=True,
        gate_status="READY",
        gate_reason="WORKER_HEALTHY",
        now=now,
    )
    return identity


def _fresh_automatic_order(client, db, admin, monkeypatch, settings):
    result = _automatic_order(client, db, admin, monkeypatch, settings)
    decision, *_, now = result
    snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
    assert snapshot is not None
    snapshot.received_at = now
    db.commit()
    return result


def test_postgresql_two_senders_created_to_submitting_exact_once(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    session_factory,
) -> None:
    _, _, order, _, loader, now = _fresh_automatic_order(
        client, db, admin, monkeypatch, settings
    )
    identity = _ready_worker_at(db, now)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("pg-send-once", "KRX"))
    barrier = Barrier(2)

    def send(_number: int) -> str:
        with session_factory() as worker:
            barrier.wait()
            return send_new_order_once(
                worker,
                broker,
                identity,
                order.id,
                now=now,
                settings=settings,
                stage_evidence_loader=loader,
            ).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(send, (1, 2)))
    db.expire_all()
    revocations = db.scalars(
        select(AuditLog).where(AuditLog.target == order.id).order_by(AuditLog.created_at)
    ).all()
    assert outcomes.count("ACKNOWLEDGED") in {1, 2}, [
        (item.action, item.result) for item in revocations
    ]
    assert set(outcomes) <= {"SUBMITTING", "ACKNOWLEDGED"}
    assert len(broker.requests) == 1
    db.expire_all()
    assert db.get(TradingOrder, order.id).status == "ACKNOWLEDGED"
    status_events = db.scalars(
        select(OrderEvent).where(
            OrderEvent.order_id == order.id,
            OrderEvent.event_type == "STATUS_CHANGED",
        )
    ).all()
    assert sum(
        json.loads(event.payload_json).get("to") == "SUBMITTING"
        for event in status_events
    ) == 1


def test_postgresql_created_invalidated_vs_submit_race_is_serialized(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    session_factory,
) -> None:
    _, _, order, _, loader, now = _fresh_automatic_order(
        client, db, admin, monkeypatch, settings
    )
    identity = _ready_worker_at(db, now)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("pg-race-submit", "KRX"))
    barrier = Barrier(2)

    def sender() -> str:
        with session_factory() as worker:
            barrier.wait()
            return send_new_order_once(
                worker,
                broker,
                identity,
                order.id,
                now=now,
                settings=settings,
                stage_evidence_loader=loader,
            ).status

    def revoker() -> str:
        with session_factory() as worker:
            barrier.wait()
            current = worker.scalar(
                select(TradingOrder)
                .where(TradingOrder.id == order.id)
                .with_for_update()
            )
            assert current is not None
            intent = worker.get(OrderIntent, current.intent_id)
            result = revoke_created_order(
                worker,
                current,
                intent,
                reason="AUTOMATIC_AUTHORITY_REVOKED",
                now=now,
            )
            worker.commit()
            return result.status.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        send_future = pool.submit(sender)
        revoke_future = pool.submit(revoker)
        outcomes = (send_future.result(), revoke_future.result())
    assert set(outcomes) <= {"ACKNOWLEDGED", "INVALIDATED", "REVOKED"}
    _assert_serialized_send_or_revoke(db, order.id, broker)


@pytest.mark.parametrize(
    "downgraded_stage",
    (ExecutionStage.APPROVAL_ONLY, ExecutionStage.SHADOW),
)
def test_postgresql_stage_downgrade_vs_pre_send_race(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    session_factory,
    downgraded_stage: ExecutionStage,
) -> None:
    _, _, order, stage, loader, now = _fresh_automatic_order(
        client, db, admin, monkeypatch, settings
    )
    identity = _ready_worker_at(db, now)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("pg-stage-race", "KRX"))
    barrier = Barrier(2)

    def sender() -> str:
        with session_factory() as worker:
            barrier.wait()
            return send_new_order_once(
                worker,
                broker,
                identity,
                order.id,
                now=now,
                settings=settings,
                stage_evidence_loader=loader,
            ).status

    def downgrade() -> str:
        with session_factory() as worker:
            current_stage = worker.get(ConfigurationVersion, stage.id)
            user = worker.get(User, admin.id)
            assert current_stage is not None and user is not None
            barrier.wait()
            current_loader = _replace_stage(
                worker, current_stage, user, now, downgraded_stage
            )
            return send_new_order_once(
                worker,
                broker,
                identity,
                order.id,
                now=now,
                settings=settings,
                stage_evidence_loader=current_loader,
            ).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        send_future = pool.submit(sender)
        downgrade_future = pool.submit(downgrade)
        outcomes = (send_future.result(), downgrade_future.result())
    assert set(outcomes) <= {"ACKNOWLEDGED", "INVALIDATED"}
    _assert_serialized_send_or_revoke(db, order.id, broker)


def test_postgresql_pause_entry_vs_buy_pre_send_race(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    session_factory,
) -> None:
    _, _, order, _, loader, now = _fresh_automatic_order(
        client, db, admin, monkeypatch, settings
    )
    identity = _ready_worker_at(db, now)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("pg-pause-race", "KRX"))
    barrier = Barrier(2)

    def sender() -> str:
        with session_factory() as worker:
            barrier.wait()
            return send_new_order_once(
                worker,
                broker,
                identity,
                order.id,
                now=now,
                settings=settings,
                stage_evidence_loader=loader,
            ).status

    def pause() -> str:
        with session_factory() as worker:
            user = worker.get(User, admin.id)
            assert user is not None
            barrier.wait()
            activate_pause_entry(
                worker,
                user=user,
                reason="Phase 10G.1 PAUSE race",
                idempotency_key=f"pg-pause-race-{uuid4()}",
                correlation_id=str(uuid4()),
                request_ip="127.0.0.1",
                user_agent="pytest",
            )
            return send_new_order_once(
                worker,
                broker,
                identity,
                order.id,
                now=now,
                settings=settings,
                stage_evidence_loader=loader,
            ).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        send_future = pool.submit(sender)
        pause_future = pool.submit(pause)
        outcomes = (send_future.result(), pause_future.result())
    assert set(outcomes) <= {"ACKNOWLEDGED", "INVALIDATED"}
    _assert_serialized_send_or_revoke(db, order.id, broker)


def test_postgresql_decision_expiry_boundary_vs_pre_send_race(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    session_factory,
) -> None:
    decision, _, order, _, loader, _ = _fresh_automatic_order(
        client, db, admin, monkeypatch, settings
    )
    valid_until = decision.valid_until.replace(tzinfo=UTC)
    identity = _ready_worker_at(db, valid_until - timedelta(seconds=1))
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("pg-expiry-race", "KRX"))
    barrier = Barrier(2)

    def send(observed_at: datetime) -> str:
        with session_factory() as worker:
            barrier.wait()
            return send_new_order_once(
                worker,
                broker,
                identity,
                order.id,
                now=observed_at,
                settings=settings,
                stage_evidence_loader=loader,
            ).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                send,
                (
                    valid_until - timedelta(microseconds=1),
                    valid_until,
                ),
            )
        )
    assert set(outcomes) <= {"ACKNOWLEDGED", "INVALIDATED"}
    _assert_serialized_send_or_revoke(db, order.id, broker)


def test_postgresql_stale_lease_owner_cannot_submit(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
) -> None:
    _, _, order, _, loader, now = _fresh_automatic_order(
        client, db, admin, monkeypatch, settings
    )
    stale = _ready_worker_at(db, now)
    takeover_at = now + timedelta(minutes=2)
    successor = acquire_lease(
        db, "pg-successor", lease_seconds=60, now=takeover_at
    )
    assert successor is not None and successor.fencing_token == stale.fencing_token + 1
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    with pytest.raises(KiwoomOrderSenderError) as stale_error:
        send_new_order_once(
            db,
            broker,
            stale,
            order.id,
            now=takeover_at + timedelta(seconds=1),
            settings=settings,
            stage_evidence_loader=loader,
        )
    assert stale_error.value.code == "WORKER_LEASE_NOT_CURRENT"
    db.rollback()
    assert db.get(TradingOrder, order.id).status == "CREATED"
    assert broker.requests == []


def test_postgresql_broker_send_db_retryable_preserves_created(
    client, db: Session, admin: User, monkeypatch, settings: Settings
) -> None:
    _install_fresh_phase10f_setup(monkeypatch)
    _db_retryable_case(client, db, admin, monkeypatch, settings)


def test_postgresql_ambiguous_send_is_unknown_and_never_resent(db: Session) -> None:
    _ambiguous_send_case(db)


def test_postgresql_unknown_reconciliation_vs_worker_retry_has_no_resend(
    db: Session, session_factory
) -> None:
    identity = ready_worker(db)
    order = persisted_order(db, idempotency_key="pg-reconciliation-race")
    order.status = "UNKNOWN"
    db.commit()
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    barrier = Barrier(2)

    def retry() -> str:
        with session_factory() as worker:
            barrier.wait()
            return send_new_order_once(worker, broker, identity, order.id).status

    def reconcile() -> str:
        with session_factory() as worker:
            barrier.wait()
            return run_kiwoom_reconciliation(
                worker,
                SnapshotClient(empty_snapshot()),
                trigger="ORDER_OUTCOME_UNKNOWN",
            ).state

    with ThreadPoolExecutor(max_workers=2) as pool:
        retry_future = pool.submit(retry)
        reconcile_future = pool.submit(reconcile)
        outcomes = (retry_future.result(), reconcile_future.result())
    assert outcomes[0] == "UNKNOWN" and outcomes[1] == "MISMATCH"
    assert broker.requests == []
    db.expire_all()
    assert db.get(TradingOrder, order.id).status == "UNKNOWN"


@pytest.mark.parametrize(
    "corruption",
    (
        "MISSING_INTENT",
        "NULL_SOURCE",
        "UNKNOWN_SOURCE",
        "BROKEN_SOURCE_TARGET",
        "SOURCE_ID_MISMATCH",
        "LEGACY_EXECUTION",
        "BROKER_IMPORTED",
    ),
)
def test_postgresql_source_dispatch_invalid_matrix_is_fail_closed(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings: Settings,
    corruption: str,
) -> None:
    _source_dispatch_case(
        client, db, admin, monkeypatch, settings, corruption
    )


def test_postgresql_pause_entry_does_not_block_fixed_stop_sell(
    db: Session, admin: User, settings: Settings
) -> None:
    _fixed_stop_pause_case(db, admin, settings)
