from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyotp
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.db import Base
from app.execution_authority import (
    ActionMode,
    ExecutionStage,
    effective_action_mode,
    effective_execution_stage,
    sourced_execution_key,
)
from app.execution_stage import (
    APPROVAL_ONLY_TEST_IDS,
    EXECUTION_STAGE_CATEGORY,
    MOCK_AUTOMATIC_TEST_IDS,
    ExecutionStageError,
    ExecutionStagePayload,
    StageResolutionStatus,
    activate_execution_stage,
    canonical_stage_json,
    create_execution_stage_draft,
    resolve_current_execution_stage,
    stage_payload_hash,
    validate_execution_stage_draft,
    validate_stage_payload,
)
from app.models import (
    ConfigurationVersion,
    DecisionExecution,
    GuardEvaluation,
    OrderIntent,
    Position,
    StopTrigger,
    TradingOrder,
    User,
)
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET

HASH = "a" * 64
NOW = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _login(client: TestClient) -> str:
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    response = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(
                datetime.now(UTC) - timedelta(seconds=30)
            ),
        },
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _evidence(test_id: str) -> tuple[dict[str, object], bytes]:
    artifact = f"immutable:{test_id}".encode()
    return (
        {
            "test_id": test_id,
            "requirement_ids": ["CFG-114"],
            "result": "PASSED",
            "code_revision": "phase-10c1-test",
            "test_plan_version": "2026-08-28",
            "executed_at": NOW - timedelta(minutes=5),
            "valid_until": NOW + timedelta(hours=1),
            "freshness_contract": None,
            "evidence_ref": f"memory://{test_id}",
            "evidence_hash": hashlib.sha256(artifact).hexdigest(),
        },
        artifact,
    )


def _stage_payload(stage: ExecutionStage) -> tuple[dict[str, object], dict[str, bytes]]:
    required = {
        ExecutionStage.SHADOW: (),
        ExecutionStage.APPROVAL_ONLY: APPROVAL_ONLY_TEST_IDS,
        ExecutionStage.MOCK_AUTOMATIC: MOCK_AUTOMATIC_TEST_IDS,
    }[stage]
    evidence: list[dict[str, object]] = []
    artifacts: dict[str, bytes] = {}
    for test_id in required:
        item, artifact = _evidence(test_id)
        evidence.append(item)
        artifacts[str(item["evidence_ref"])] = artifact
    return (
        {
            "schema_version": "execution-stage-control-v1",
            "stage": stage.value,
            "target": "MOCK",
            "validation_policy_version": "execution-stage-validation-policy-v1",
            "safety_evidence": evidence,
            "validated_at": NOW - timedelta(minutes=1),
            "valid_until": NOW + timedelta(hours=2),
        },
        artifacts,
    )


def _loader(artifacts: dict[str, bytes]):
    return artifacts.__getitem__


def test_execution_identity_and_authority_min_are_policy_independent() -> None:
    first = sourced_execution_key("decision-1")
    assert first == sourced_execution_key("decision-1")
    assert first.startswith("v7exe-") and len(first) == 70
    assert first != sourced_execution_key("decision-2")
    assert effective_execution_stage("SHADOW", "MOCK_AUTOMATIC") is ExecutionStage.SHADOW
    assert (
        effective_execution_stage("APPROVAL_ONLY", "MOCK_AUTOMATIC")
        is ExecutionStage.APPROVAL_ONLY
    )
    assert (
        effective_execution_stage("MOCK_AUTOMATIC", "APPROVAL_ONLY")
        is ExecutionStage.APPROVAL_ONLY
    )
    assert effective_action_mode("MANUAL_APPROVAL", "AUTOMATIC") is ActionMode.MANUAL_APPROVAL
    assert effective_action_mode("AUTOMATIC", "DISABLED") is ActionMode.DISABLED


@pytest.mark.parametrize("stage", list(ExecutionStage))
def test_stage_schema_accepts_exact_stage_evidence(stage: ExecutionStage) -> None:
    value, artifacts = _stage_payload(stage)
    payload = validate_stage_payload(
        value, now=NOW, evidence_loader=_loader(artifacts) if artifacts else None
    )
    canonical = canonical_stage_json(payload)
    assert payload.stage is stage
    assert len(stage_payload_hash(payload)) == 64
    assert stage_payload_hash(canonical) == stage_payload_hash(payload)


def test_stage_schema_rejects_unknown_live_stale_and_bad_evidence() -> None:
    value, _ = _stage_payload(ExecutionStage.SHADOW)
    with pytest.raises(ValidationError):
        ExecutionStagePayload.model_validate({**value, "unknown": True})
    with pytest.raises(ValidationError):
        ExecutionStagePayload.model_validate({**value, "target": "LIVE"})
    with pytest.raises(ExecutionStageError, match="EXECUTION_STAGE_EXPIRED"):
        validate_stage_payload(
            {**value, "valid_until": NOW}, now=NOW, evidence_loader=None
        )
    automatic, artifacts = _stage_payload(ExecutionStage.MOCK_AUTOMATIC)
    automatic["safety_evidence"] = list(automatic["safety_evidence"])[1:]
    with pytest.raises(ExecutionStageError, match="EXECUTION_STAGE_INVALID"):
        validate_stage_payload(
            automatic, now=NOW, evidence_loader=_loader(artifacts)
        )
    automatic, artifacts = _stage_payload(ExecutionStage.MOCK_AUTOMATIC)
    first = next(iter(automatic["safety_evidence"]))
    assert isinstance(first, dict)
    first["evidence_hash"] = "0" * 64
    with pytest.raises(ExecutionStageError, match="EXECUTION_STAGE_EVIDENCE_INVALID"):
        validate_stage_payload(
            automatic, now=NOW, evidence_loader=_loader(artifacts)
        )


def test_stage_lifecycle_selector_and_no_production_seed(db: Session, admin: User) -> None:
    assert db.scalar(
        select(ConfigurationVersion).where(
            ConfigurationVersion.category == EXECUTION_STAGE_CATEGORY
        )
    ) is None
    assert resolve_current_execution_stage(
        db, now=NOW, evidence_loader=None
    ).status is StageResolutionStatus.ABSENT
    value, _ = _stage_payload(ExecutionStage.SHADOW)
    version = create_execution_stage_draft(
        db,
        user=admin,
        payload=value,
        reason="phase 10C.1 test",
        now=NOW,
        evidence_loader=None,
    )
    assert version.state == "DRAFT"
    validate_execution_stage_draft(
        db, version_id=version.id, now=NOW, evidence_loader=None
    )
    activate_execution_stage(
        db,
        user=admin,
        version_id=version.id,
        now=NOW,
        evidence_loader=None,
        correlation_id="correlation-stage",
        request_ip="127.0.0.1",
        user_agent="pytest",
    )
    resolved = resolve_current_execution_stage(db, now=NOW, evidence_loader=None)
    assert resolved.status is StageResolutionStatus.PASS
    assert resolved.version is not None and resolved.version.id == version.id
    assert resolved.payload is not None and resolved.payload.stage is ExecutionStage.SHADOW


def test_stage_configuration_api_lifecycle_has_no_execution_handoff(
    client: TestClient, db: Session
) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    current_time = datetime.now(UTC)
    control = {
        "schema_version": "execution-stage-control-v1",
        "stage": "SHADOW",
        "target": "MOCK",
        "validation_policy_version": "execution-stage-validation-policy-v1",
        "safety_evidence": [],
        "validated_at": (current_time - timedelta(minutes=1)).isoformat(),
        "valid_until": (current_time + timedelta(hours=1)).isoformat(),
    }
    draft = client.post(
        "/api/v1/settings/v7-entry-execution-stage/drafts",
        headers=headers,
        json={"schema_version": "1.0", "control": control, "reason": "SHADOW foundation"},
    )
    assert draft.status_code == 200, draft.text
    version_id = draft.json()["version_id"]
    assert draft.json()["state"] == "DRAFT"
    validated = client.post(
        f"/api/v1/settings/v7-entry-execution-stage/{version_id}/validate",
        headers=headers,
        json={"schema_version": "1.0"},
    )
    assert validated.status_code == 200, validated.text
    activated = client.post(
        f"/api/v1/settings/v7-entry-execution-stage/{version_id}/activate",
        headers=headers,
        json={"schema_version": "1.0"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["state"] == "ACTIVE"
    current = client.get("/api/v1/settings/v7-entry-execution-stage/current")
    assert current.status_code == 200
    assert current.json()["status"] == "PASS"
    assert current.json()["item"]["version_id"] == version_id
    history = client.get("/api/v1/settings/v7-entry-execution-stage/history")
    assert history.status_code == 200
    assert [item["version_id"] for item in history.json()["items"]] == [version_id]
    assert db.query(DecisionExecution).count() == 0


def test_stage_selector_classifies_ambiguous_invalid_and_db_failure(db: Session, admin: User) -> None:
    db.execute(text("DROP INDEX uq_configuration_active_target"))
    valid, _ = _stage_payload(ExecutionStage.SHADOW)
    canonical = canonical_stage_json(ExecutionStagePayload.model_validate(valid))
    db.add_all(
        [
            ConfigurationVersion(
                scope="SYSTEM",
                target_id="MOCK",
                category=EXECUTION_STAGE_CATEGORY,
                sequence=sequence,
                state="ACTIVE",
                payload_json=canonical,
                payload_hash=stage_payload_hash(canonical),
                reason="ambiguous fixture",
                created_by=admin.id,
            )
            for sequence in (1, 2)
        ]
    )
    db.commit()
    assert resolve_current_execution_stage(
        db, now=NOW, evidence_loader=None
    ).status is StageResolutionStatus.AMBIGUOUS

    class BrokenSession:
        def scalars(self, _statement: object) -> object:
            raise SQLAlchemyError("database unavailable")

    assert resolve_current_execution_stage(  # type: ignore[arg-type]
        BrokenSession(), now=NOW, evidence_loader=None
    ).status is StageResolutionStatus.DB_RETRYABLE_FAILURE


def _execution(**overrides: object) -> DecisionExecution:
    values: dict[str, object] = {
        "execution_key": sourced_execution_key("decision-1"),
        "decision_id": "decision-1",
        "user_id": "user-1",
        "account_alias": "mock-primary",
        "symbol": "005930",
        "market": "KRX",
        "action": "WAIT",
        "mode": None,
        "stage": None,
        "state": "NO_ACTION",
        "result_code": "WAIT",
        "contract_version": "sourced-entry-execution-v1",
        "correlation_id": "correlation-execution",
    }
    values.update(overrides)
    return DecisionExecution(**values)


def test_sourced_execution_exact_one_and_conditional_representation(db: Session) -> None:
    db.add(_execution())
    db.commit()
    db.add(_execution(execution_key=sourced_execution_key("retry-material-ignored")))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(
        _execution(
            execution_key=sourced_execution_key("decision-2"),
            decision_id="decision-2",
            action="BUY",
            state="FAILED_SAFE",
            result_code="DECISION_EXPIRED",
        )
    )
    db.commit()
    db.add(
        _execution(
            execution_key=sourced_execution_key("decision-3"),
            decision_id="decision-3",
            action="BUY",
            state="ROUTING",
            result_code=None,
            mode="AUTOMATIC",
            stage="MOCK_AUTOMATIC",
            execution_stage_version_id=None,
            execution_stage_payload_hash=HASH,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_order_intent_typed_source_shape_and_authority_key_unique(db: Session) -> None:
    db.add(
        OrderIntent(
            account_alias="mock-primary",
            symbol="005930",
            market="KRX",
            side="BUY",
            action="BUY",
            requested_quantity=1,
            source_type="DECISION_EXECUTION",
            source_id="execution-1",
            decision_execution_id="execution-1",
            guard_evaluation_id="guard-1",
            execution_stage_version_id="stage-1",
            execution_stage_payload_hash=HASH,
            authority_key="authority-1",
            correlation_id="correlation-typed-intent",
        )
    )
    db.commit()
    db.add(
        OrderIntent(
            account_alias="mock-primary",
            symbol="000660",
            market="KRX",
            side="BUY",
            action="BUY",
            requested_quantity=1,
            source_type="STOP_TRIGGER",
            source_id="trigger-1",
            stop_trigger_id="trigger-1",
            decision_execution_id="mixed-execution",
            guard_evaluation_id="guard-2",
            execution_stage_version_id="stage-1",
            execution_stage_payload_hash=HASH,
            authority_key="authority-2",
            correlation_id="correlation-mixed-intent",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(
        OrderIntent(
            account_alias="mock-primary",
            symbol="000660",
            market="KRX",
            side="BUY",
            action="BUY",
            requested_quantity=1,
            source_type="BROKER_DIAGNOSTIC",
            source_id="diagnostic-1",
            authority_key="authority-1",
            correlation_id="correlation-duplicate-authority",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_typed_guard_subject_and_fixed_stop_fk_with_foreign_keys() -> None:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record) -> None:  # type: ignore[no-untyped-def]
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        position = Position(
            account_alias="mock-primary",
            symbol="005930",
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
            account_alias="mock-primary",
            position_id=position.id,
            position_version=position.version,
            symbol=position.symbol,
            market="KRX",
            stop_price=Decimal(90),
            state="PENDING",
            correlation_id="correlation-trigger",
        )
        db.add(trigger)
        db.flush()
        guard = GuardEvaluation(
            execution_id=None,
            stop_trigger_id=trigger.id,
            phase="PRE_ORDER",
            subject_type="STOP_TRIGGER",
            subject_id=trigger.id,
            result="PASSED",
            rule_results_json="[]",
            evaluated_at=NOW,
        )
        db.add(guard)
        db.commit()
        assert guard.stop_trigger_id == trigger.id and guard.execution_id is None
        db.add(
            GuardEvaluation(
                execution_id=trigger.id,
                stop_trigger_id=None,
                phase="PRE_ORDER",
                subject_type="STOP_TRIGGER",
                subject_id=trigger.id,
                result="PASSED",
                rule_results_json="[]",
                evaluated_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_order_intent_legacy_and_invalidated_order_foundation(db: Session) -> None:
    intent = OrderIntent(
        account_alias="mock-primary",
        symbol="005930",
        market="KRX",
        side="BUY",
        action="BUY",
        requested_quantity=1,
        correlation_id="correlation-order",
    )
    db.add(intent)
    db.flush()
    order = TradingOrder(
        intent_id=intent.id,
        order_group_id=intent.order_group_id,
        account_alias="mock-primary",
        symbol="005930",
        market="KRX",
        side="BUY",
        order_type="LIMIT",
        limit_price=Decimal(100),
        requested_quantity=1,
        remaining_quantity=1,
        status="INVALIDATED",
        idempotency_key="phase10-invalidated",
        request_hash=HASH,
        trading_date=date(2026, 8, 28),
        correlation_id="correlation-order",
    )
    db.add(order)
    db.commit()
    assert intent.source_type is None and order.status == "INVALIDATED"

    db.add(
        OrderIntent(
            account_alias="mock-primary",
            symbol="000660",
            market="KRX",
            side="BUY",
            action="BUY",
            requested_quantity=1,
            source_type="UNKNOWN",
            source_id="unknown",
            authority_key="authority-unknown",
            correlation_id="correlation-invalid-source",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def _alembic(db_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["CRESTA_DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_0040_to_0041_migration_preserves_legacy_and_corrects_stop_fk(tmp_path: Path) -> None:
    database = tmp_path / "phase10c1.db"
    result = _alembic(database, "upgrade", "20260827_0040")
    assert result.returncode == 0, result.stdout + result.stderr
    engine = create_engine(f"sqlite:///{database.as_posix()}", poolclass=NullPool)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO stop_triggers "
                "(id,account_alias,position_id,position_version,symbol,market,stop_price,state,"
                "correlation_id,version,created_at,updated_at) VALUES "
                "('trigger-1','mock-primary','position-1',1,'005930','KRX',90,'PENDING',"
                "'correlation',1,:now,:now)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO guard_evaluations "
                "(id,execution_id,phase,subject_type,subject_id,result,rule_results_json,"
                "evaluated_at) VALUES "
                "('guard-1','trigger-1','PRE_ORDER','STOP_TRIGGER','trigger-1','PASSED','[]',:now)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO order_intents "
                "(id,order_group_id,account_alias,environment,symbol,market,side,action,"
                "requested_quantity,correlation_id,version,created_at,updated_at) VALUES "
                "('intent-legacy','group-legacy','mock-primary','MOCK','005930','KRX','BUY',"
                "'BUY',1,'correlation',1,:now,:now)"
            ),
            {"now": NOW},
        )
    result = _alembic(database, "upgrade", "20260828_0041")
    assert result.returncode == 0, result.stdout + result.stderr
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260828_0041"
        )
        corrected = connection.execute(
            text(
                "SELECT execution_id, stop_trigger_id FROM guard_evaluations "
                "WHERE id='guard-1'"
            )
        ).one()
        assert corrected == (None, "trigger-1")
        legacy = connection.execute(
            text(
                "SELECT source_type, authority_key FROM order_intents "
                "WHERE id='intent-legacy'"
            )
        ).one()
        assert legacy == (None, None)
        assert "stop_trigger_id" in {
            column["name"] for column in inspect(engine).get_columns("guard_evaluations")
        }
        assert any(
            fk["referred_table"] == "stop_triggers"
            for fk in inspect(engine).get_foreign_keys("guard_evaluations")
        )
        order_intent_fks = {
            fk["referred_table"] for fk in inspect(engine).get_foreign_keys("order_intents")
        }
        assert {
            "decision_executions",
            "stop_triggers",
            "guard_evaluations",
            "approvals",
            "configuration_versions",
        } <= order_intent_fks
        approval_fks = {
            fk["referred_table"] for fk in inspect(engine).get_foreign_keys("approvals")
        }
        assert {"reauth_proofs", "orders"} <= approval_fks


def test_0041_downgrade_refuses_new_execution_semantics(tmp_path: Path) -> None:
    database = tmp_path / "phase10c1-downgrade.db"
    result = _alembic(database, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr
    engine = create_engine(f"sqlite:///{database.as_posix()}", poolclass=NullPool)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO decision_executions "
                "(id,execution_key,decision_id,user_id,account_alias,symbol,market,action,mode,"
                "stage,state,result_code,contract_version,correlation_id,version,created_at,"
                "updated_at) VALUES "
                "('execution-new','v7exe-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                "aaaaaaaaaa','decision-new','user-new','mock-primary','005930','KRX','WAIT',NULL,"
                "NULL,'NO_ACTION','WAIT','sourced-entry-execution-v1','correlation',1,:now,:now)"
            ),
            {"now": NOW},
        )
    result = _alembic(database, "downgrade", "20260827_0040")
    assert result.returncode != 0
    assert "Refusing downgrade of 20260828_0041" in result.stderr
