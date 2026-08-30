from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.db import Base
from app.models import (
    AgentRun,
    AgentStageRun,
    Decision,
    DecisionContext,
    DecisionInputSnapshot,
    EvidenceBundle,
    LlmModelProfile,
    LlmPromptProfile,
    LlmProviderProfile,
    LlmRoleRoute,
    MarketSnapshot,
    User,
)

HASH = "a" * 64
NOW = datetime.now(UTC)
BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def v7_db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def migration_db_path() -> Path:
    database = BACKEND_ROOT / f".phase3a-test-{uuid4().hex}.db"
    yield database
    for artifact in BACKEND_ROOT.glob(f"{database.name}*"):
        artifact.unlink(missing_ok=True)


def _market_snapshot(db: Session, suffix: str = "base") -> MarketSnapshot:
    snapshot = MarketSnapshot(
        symbol="005930",
        market="KRX",
        source="TEST",
        sequence_or_hash=suffix,
        payload_hash=HASH,
        last_price=Decimal(70000),
        open_price=Decimal(70000),
        high_price=Decimal(70000),
        low_price=Decimal(70000),
        cumulative_volume=1,
        trading_status="OPEN",
        quality="NORMAL",
        event_at=NOW,
        received_at=NOW,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def _agent_run(
    db: Session,
    owner: User,
    snapshot: MarketSnapshot,
    *,
    purpose: str,
    suffix: str,
    analysis_context: str = "ENTRY",
) -> AgentRun:
    run = AgentRun(
        owner_id=owner.id,
        purpose=purpose,
        execution_stage="SHADOW",
        market="KRX",
        symbol="005930",
        market_snapshot_id=snapshot.id,
        input_hash=HASH,
        dag_version="agent-dag-v7" if purpose == "TRADING" else "agent-dag-v6",
        route_versions_json="{}",
        idempotency_key=f"run-{suffix}",
        state="CREATED",
        analysis_context=analysis_context,
        valid_until=NOW + timedelta(minutes=5),
    )
    db.add(run)
    db.flush()
    return run


def _stage(db: Session, run: AgentRun, role: str, sequence: int) -> AgentStageRun:
    stage = AgentStageRun(
        run_id=run.id,
        role=role,
        sequence=sequence,
        dependency_roles_json="[]",
        state="SUCCEEDED",
        input_hash=HASH,
        output_json="{}",
        output_hash=HASH,
    )
    db.add(stage)
    db.flush()
    return stage


def _decision(
    db: Session,
    snapshot: MarketSnapshot,
    suffix: str,
    *,
    source_run: AgentRun | None = None,
    source_stage: AgentStageRun | None = None,
    source_hash: str | None = None,
) -> Decision:
    sourced = source_run is not None and source_stage is not None and source_hash is not None
    decision = Decision(
        purpose="TRADING",
        evaluation_request_id=f"decision-{suffix}",
        input_snapshot_id=snapshot.id,
        symbol="005930",
        market="KRX",
        decision_kind="ENTRY",
        model_provider=None if sourced else "SERVER",
        model_id=None if sourced else "test",
        prompt_version=None if sourced else "test-v1",
        schema_version="sourced-entry-decision-v1" if sourced else "test-v1",
        scout_output_json=None if sourced else "{}",
        core_output_json=None if sourced else "{}",
        action="WAIT",
        confidence=None if sourced else Decimal("0.50000"),
        risk_level=None if sourced else "LOW",
        reason_codes_json="[]",
        valid_until=NOW + timedelta(minutes=5),
        execution_mode=None,
        execution_outcome=None if sourced else "NO_ACTION",
        validation_status="VALID",
        latency_ms=None if sourced else 0,
        source_agent_run_id=source_run.id if source_run else None,
        source_stage_run_id=source_stage.id if source_stage else None,
        source_stage_output_hash=source_hash,
    )
    db.add(decision)
    return decision


def _owner_and_snapshot(db: Session) -> tuple[User, MarketSnapshot]:
    owner = User(login_id="v7-owner", password_hash="unused")
    db.add(owner)
    db.flush()
    return owner, _market_snapshot(db)


def test_agent_run_accepts_legacy_and_trading_purposes(v7_db: Session) -> None:
    owner, snapshot = _owner_and_snapshot(v7_db)
    _agent_run(v7_db, owner, snapshot, purpose="DIAGNOSTIC", suffix="diagnostic")
    trading = _agent_run(v7_db, owner, snapshot, purpose="TRADING", suffix="trading")
    v7_db.commit()

    assert trading.purpose == "TRADING"
    assert trading.policy_profile_version_map_json is None
    assert trading.activation_gate_version_id is None


def test_agent_stage_accepts_all_v7_roles(v7_db: Session) -> None:
    owner, snapshot = _owner_and_snapshot(v7_db)
    run = _agent_run(v7_db, owner, snapshot, purpose="TRADING", suffix="roles")
    roles = (
        "CONSERVATIVE_DECISION",
        "BALANCED_DECISION",
        "AGGRESSIVE_DECISION",
        "ENTRY_ARBITER",
    )
    for sequence, role in enumerate(roles, start=1):
        _stage(v7_db, run, role, sequence)
    v7_db.commit()

    assert {stage.role for stage in v7_db.query(AgentStageRun).all()} == set(roles)


def test_route_and_prompt_accept_decision_roles_but_reject_arbiter(v7_db: Session) -> None:
    owner, _snapshot = _owner_and_snapshot(v7_db)
    provider = LlmProviderProfile(
        owner_id=owner.id,
        name="mock",
        adapter_type="MOCK",
        data_policy="NONE",
        state="VALIDATED",
        health_status="HEALTHY",
    )
    v7_db.add(provider)
    v7_db.flush()
    model = LlmModelProfile(
        provider_profile_id=provider.id,
        alias="mock-model",
        provider_model_id="mock-model",
        capabilities_json="{}",
        max_output_tokens=1024,
        temperature=Decimal(0),
        state="VALIDATED",
    )
    v7_db.add(model)
    v7_db.flush()

    for sequence, role in enumerate(
        ("CONSERVATIVE_DECISION", "BALANCED_DECISION", "AGGRESSIVE_DECISION"), start=1
    ):
        prompt = LlmPromptProfile(
            owner_id=owner.id,
            role=role,
            version_number=sequence,
            version_label=f"{role}-v1",
            system_prompt="test",
            content_hash=HASH,
            state="VALIDATED",
            reason="test",
        )
        v7_db.add(prompt)
        v7_db.flush()
        v7_db.add(
            LlmRoleRoute(
                owner_id=owner.id,
                role=role,
                primary_model_profile_id=model.id,
                fallback_model_profile_ids_json="[]",
                fallback_policy="FAIL_STOP",
                execution_stage="SHADOW",
                timeout_ms=120000,
                service_tier="DEFAULT",
                max_attempts=1,
                daily_call_limit=10,
                daily_cost_limit_krw=Decimal(0),
                prompt_version="v1",
                prompt_profile_id=prompt.id,
                output_schema_version="decision-agent-result-v1",
                state="VALIDATED",
                reason="test",
            )
        )
    v7_db.commit()

    v7_db.add(
        LlmPromptProfile(
            owner_id=owner.id,
            role="ENTRY_ARBITER",
            version_number=99,
            version_label="arbiter-v1",
            system_prompt="test",
            content_hash=HASH,
            state="VALIDATED",
            reason="test",
        )
    )
    with pytest.raises(IntegrityError):
        v7_db.commit()
    v7_db.rollback()

    v7_db.add(
        LlmRoleRoute(
            owner_id=owner.id,
            role="ENTRY_ARBITER",
            primary_model_profile_id=model.id,
            fallback_model_profile_ids_json="[]",
            fallback_policy="FAIL_STOP",
            execution_stage="SHADOW",
            timeout_ms=120000,
            service_tier="DEFAULT",
            max_attempts=1,
            daily_call_limit=10,
            daily_cost_limit_krw=Decimal(0),
            prompt_version="v1",
            output_schema_version="entry-consensus-v1",
            state="VALIDATED",
            reason="test",
        )
    )
    with pytest.raises(IntegrityError):
        v7_db.commit()


def test_decision_context_run_is_unique_and_relationship_is_mapped(v7_db: Session) -> None:
    owner, snapshot = _owner_and_snapshot(v7_db)
    run = _agent_run(v7_db, owner, snapshot, purpose="TRADING", suffix="context")
    roles = (
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
        "EVIDENCE_CANDIDATE_AUDITOR",
    )
    stages = [_stage(v7_db, run, role, sequence) for sequence, role in enumerate(roles, 1)]
    decision_input = DecisionInputSnapshot(
        user_id=owner.id,
        purpose="TRADING",
        schema_version="scout-input-v2",
        market="KRX",
        symbol="005930",
        market_snapshot_id=snapshot.id,
        observed_at=NOW,
        data_quality="NORMAL",
        session_state="CONTINUOUS",
        input_json="{}",
        input_hash=HASH,
    )
    bundle = EvidenceBundle(
        owner_id=owner.id,
        run_id=run.id,
        market="KRX",
        symbol="005930",
        as_of=NOW,
        policy_version="test-v1",
        state="VERIFIED",
        evidence_ids_json="[]",
        contradiction_groups_json="[]",
        stale_evidence_ids_json="[]",
        reason_codes_json="[]",
        bundle_hash=HASH,
    )
    v7_db.add_all([decision_input, bundle])
    v7_db.flush()

    def context(suffix: str) -> DecisionContext:
        return DecisionContext(
            id=f"context-{suffix}",
            run_id=run.id,
            schema_version="decision-context-v1",
            decision_input_snapshot_id=decision_input.id,
            evidence_bundle_id=bundle.id,
            technical_scout_stage_id=stages[0].id,
            news_disclosure_scout_stage_id=stages[1].id,
            market_sector_scout_stage_id=stages[2].id,
            position_risk_scout_stage_id=stages[3].id,
            candidate_audit_stage_id=stages[4].id,
            configuration_provenance_json="{}",
            configuration_provenance_hash=HASH,
            version_manifest_json="{}",
            version_manifest_hash=HASH,
            manifest_json="{}",
            context_hash=HASH,
            frozen_at=NOW,
            valid_until=NOW + timedelta(minutes=5),
        )

    first = context("first")
    v7_db.add(first)
    v7_db.commit()
    assert run.decision_context is first
    assert first.run is run

    v7_db.add(context("second"))
    with pytest.raises(IntegrityError):
        v7_db.commit()


def test_decision_source_lineage_constraints_and_legacy_nulls(v7_db: Session) -> None:
    owner, snapshot = _owner_and_snapshot(v7_db)
    run = _agent_run(v7_db, owner, snapshot, purpose="TRADING", suffix="source")
    stage = _stage(v7_db, run, "ENTRY_ARBITER", 1)

    legacy = _decision(v7_db, snapshot, "legacy")
    v7_db.commit()
    assert legacy.source_agent_run_id is None
    assert legacy.source_stage_run_id is None
    assert legacy.source_stage_output_hash is None

    _decision(v7_db, snapshot, "partial", source_run=run)
    with pytest.raises(IntegrityError):
        v7_db.commit()
    v7_db.rollback()

    sourced = _decision(
        v7_db,
        snapshot,
        "sourced",
        source_run=run,
        source_stage=stage,
        source_hash=HASH,
    )
    v7_db.commit()
    assert sourced.source_agent_run is run
    assert sourced.source_stage_run is stage

    alternate_stage = _stage(v7_db, run, "BALANCED_DECISION", 2)
    _decision(
        v7_db,
        snapshot,
        "duplicate-run",
        source_run=run,
        source_stage=alternate_stage,
        source_hash=HASH,
    )
    with pytest.raises(IntegrityError):
        v7_db.commit()
    v7_db.rollback()

    other_run = _agent_run(
        v7_db, owner, snapshot, purpose="TRADING", suffix="other-source"
    )
    _decision(
        v7_db,
        snapshot,
        "duplicate-stage",
        source_run=other_run,
        source_stage=stage,
        source_hash=HASH,
    )
    with pytest.raises(IntegrityError):
        v7_db.commit()
    v7_db.rollback()

    invalid_fk = _decision(v7_db, snapshot, "invalid-fk")
    invalid_fk.source_agent_run_id = "missing-run"
    invalid_fk.source_stage_run_id = "missing-stage"
    invalid_fk.source_stage_output_hash = HASH
    with pytest.raises(IntegrityError):
        v7_db.commit()


def _alembic(db_path: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["CRESTA_DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return result


def test_migration_empty_database_round_trip(migration_db_path: Path) -> None:
    # T-V2-MIG-001 / T-V2-MIG-002
    database = migration_db_path
    _alembic(database, "upgrade", "head")
    _alembic(database, "downgrade", "20260817_0038")
    _alembic(database, "upgrade", "head")

    engine = create_engine(f"sqlite:///{database.as_posix()}", poolclass=NullPool)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260829_0044"
        )
        assert connection.scalar(
            text("SELECT COUNT(*) FROM pragma_table_info('decision_contexts')")
        ) == 20


def test_migration_preserves_legacy_rows_and_null_lineage(migration_db_path: Path) -> None:
    # T-V2-MIG-003 / T-V2-MIG-004 / T-V2-MIG-005
    database = migration_db_path
    _alembic(database, "upgrade", "20260817_0038")
    engine = create_engine(f"sqlite:///{database.as_posix()}", poolclass=NullPool)
    valid_until = "2026-08-25 10:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id,login_id,password_hash,password_params,status,created_at,updated_at) "
                "VALUES ('u1','legacy','x','test','ACTIVE',:at,:at)"
            ),
            {"at": valid_until},
        )
        connection.execute(
            text(
                "INSERT INTO market_snapshots "
                "(id,symbol,market,source,sequence_or_hash,payload_hash,last_price,open_price,"
                "high_price,low_price,cumulative_volume,trading_status,quality,recovery_snapshot,"
                "event_at,received_at,created_at) VALUES "
                "('m1','005930','KRX','TEST','legacy','h',1,1,1,1,1,'OPEN','NORMAL',0,"
                ":at,:at,:at)"
            ),
            {"at": valid_until},
        )
        connection.execute(
            text(
                "INSERT INTO decisions "
                "(id,purpose,evaluation_request_id,input_snapshot_id,symbol,market,decision_kind,"
                "model_provider,model_id,prompt_version,schema_version,scout_output_json,"
                "core_output_json,action,confidence,risk_level,reason_codes_json,valid_until,"
                "execution_outcome,validation_status,latency_ms,created_at) VALUES "
                "('d1','TRADING','legacy-decision','m1','005930','KRX','ENTRY','SERVER','test',"
                "'v1','v1','{}','{}','WAIT',0.5,'LOW','[]',:until,'NO_ACTION','VALID',0,:until)"
            ),
            {"until": valid_until},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id,owner_id,purpose,execution_stage,market,symbol,market_snapshot_id,input_hash,"
                "dag_version,route_versions_json,idempotency_key,state,analysis_context,"
                "basis_decision_id,fusion_policy_version,fusion_state,valid_until,created_at) VALUES "
                "('r1','u1','TRADING_ADVISORY','SHADOW','KRX','005930','m1','h','agent-dag-v6',"
                "'{}','legacy-run','CREATED','POSITION','d1','fusion-v1','PENDING',:until,:until)"
            ),
            {"until": valid_until},
        )
        connection.execute(
            text(
                "INSERT INTO agent_stage_runs "
                "(id,run_id,role,sequence,dependency_roles_json,state,input_hash,fencing_token,"
                "attempt_count,max_attempts,available_at,created_at) VALUES "
                "('s1','r1','CORE',1,'[]','SUCCEEDED','h',0,0,1,:at,:at)"
            ),
            {"at": valid_until},
        )

    _alembic(database, "upgrade", "head")
    with engine.connect() as connection:
        run = connection.execute(
            text(
                "SELECT purpose,basis_decision_id,fusion_policy_version,fusion_state,"
                "policy_profile_version_map_json,activation_gate_version_id "
                "FROM agent_runs WHERE id='r1'"
            )
        ).one()
        decision = connection.execute(
            text(
                "SELECT source_agent_run_id,source_stage_run_id,source_stage_output_hash "
                "FROM decisions WHERE id='d1'"
            )
        ).one()
        stage_role = connection.scalar(text("SELECT role FROM agent_stage_runs WHERE id='s1'"))

    assert run == ("TRADING_ADVISORY", "d1", "fusion-v1", "PENDING", None, None)
    assert decision == (None, None, None)
    assert stage_role == "CORE"


def test_migration_refuses_destructive_v7_downgrade(migration_db_path: Path) -> None:
    # T-V2-MIG-006
    database = migration_db_path
    _alembic(database, "upgrade", "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}", poolclass=NullPool)
    valid_until = "2026-08-25 10:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id,login_id,password_hash,password_params,status,created_at,updated_at) "
                "VALUES ('u1','v7','x','test','ACTIVE',:at,:at)"
            ),
            {"at": valid_until},
        )
        connection.execute(
            text(
                "INSERT INTO market_snapshots "
                "(id,symbol,market,source,sequence_or_hash,payload_hash,last_price,open_price,"
                "high_price,low_price,cumulative_volume,trading_status,quality,recovery_snapshot,"
                "event_at,received_at,created_at) VALUES "
                "('m1','005930','KRX','TEST','v7','h',1,1,1,1,1,'OPEN','NORMAL',0,"
                ":at,:at,:at)"
            ),
            {"at": valid_until},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id,owner_id,purpose,execution_stage,market,symbol,market_snapshot_id,input_hash,"
                "dag_version,route_versions_json,idempotency_key,state,analysis_context,"
                "valid_until,created_at) "
                "VALUES ('r1','u1','TRADING','SHADOW','KRX','005930','m1','h','agent-dag-v7',"
                "'{}','v7-run','CREATED','ENTRY',:until,:until)"
            ),
            {"until": valid_until},
        )

    result = _alembic(database, "downgrade", "20260817_0038", check=False)
    assert result.returncode != 0
    assert "Refusing downgrade of 20260825_0039" in result.stderr
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260825_0039"
        )


def test_phase_9c1_migration_refuses_unknown_downgrade(migration_db_path: Path) -> None:
    database = migration_db_path
    _alembic(database, "upgrade", "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}", poolclass=NullPool)
    at = "2026-08-27 12:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO market_snapshots "
                "(id,symbol,market,source,sequence_or_hash,payload_hash,last_price,open_price,"
                "high_price,low_price,cumulative_volume,trading_status,quality,recovery_snapshot,"
                "event_at,received_at,created_at) VALUES "
                "('m1','005930','KRX','TEST','unknown','h',1,1,1,1,1,'OPEN','NORMAL',0,"
                ":at,:at,:at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO decisions "
                "(id,purpose,evaluation_request_id,input_snapshot_id,symbol,market,decision_kind,"
                "model_provider,model_id,prompt_version,schema_version,scout_output_json,"
                "core_output_json,action,confidence,risk_level,reason_codes_json,valid_until,"
                "execution_outcome,validation_status,latency_ms,created_at) VALUES "
                "('d1','TRADING','unknown-decision','m1','005930','KRX','ENTRY','SERVER','test',"
                "'v1','1.0','{}','{}','UNKNOWN',0.5,'LOW','[]',:at,'NO_ACTION','VALID',1,:at)"
            ),
            {"at": at},
        )
    result = _alembic(database, "downgrade", "20260825_0039", check=False)
    assert result.returncode != 0
    assert "Refusing downgrade of 20260827_0040" in result.stderr
