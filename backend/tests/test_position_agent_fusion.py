from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.contracts import AgentCoreOutputV2
from app.agents.worker import _finalize_run
from app.config import Settings
from app.mock_ai import create_mock_position_trading_decision
from app.models import (
    AgentRun,
    AgentStageRun,
    Decision,
    DecisionExecution,
    DecisionInputSnapshot,
    Position,
    User,
)
from app.position_agent_fusion import (
    FUSION_MODEL_ID,
    FUSION_POLICY_VERSION,
    finalize_position_advisory,
)
from tests.test_analysis_scheduler import _at_kst, _watch_with_snapshot


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _basis(
    db: Session, admin: User, settings: Settings, now: datetime
) -> tuple[Decision, dict[str, object], Position]:
    _watch_with_snapshot(db, admin, now)
    position = Position(
        account_alias="KIWOOM_MOCK_PRIMARY",
        symbol="005930",
        quantity=10,
        available_quantity=10,
        managed_quantity=10,
        average_price=Decimal(100),
        managed_average_price=Decimal(100),
        state="OPEN",
        origin="CRESTA_MANAGED",
    )
    db.add(position)
    db.commit()
    decision, created = create_mock_position_trading_decision(
        db,
        user=admin,
        position=position,
        evaluation_request_id="position-fusion-basis",
        symbol="005930",
        market="KRX",
        settings=settings,
        now=now,
    )
    assert created and decision.action == "HOLD"
    decision_input = db.get(DecisionInputSnapshot, decision.decision_input_id)
    assert decision_input is not None
    position_payload = json.loads(decision_input.input_json)["position"]
    db.commit()
    return decision, position_payload, position


def _advisory(
    db: Session,
    *,
    admin: User,
    basis: Decision,
    position_payload: dict[str, object],
    assessment: str,
    confidence: float = 0.9,
) -> AgentRun:
    run = AgentRun(
        owner_id=admin.id,
        purpose="TRADING_ADVISORY",
        execution_stage="SHADOW",
        market=basis.market,
        symbol=basis.symbol,
        market_snapshot_id=basis.input_snapshot_id,
        input_hash="a" * 64,
        dag_version="agent-dag-v6",
        route_versions_json="{}",
        idempotency_key=_hash({"basis": basis.id, "assessment": assessment}),
        state="SUCCEEDED",
        core_action="WAIT",
        analysis_context="POSITION",
        position_snapshot_json=_canonical(position_payload),
        position_snapshot_hash=_hash(position_payload),
        shadow_assessment=assessment,
        basis_decision_id=basis.id,
        fusion_policy_version=FUSION_POLICY_VERSION,
        fusion_state="PENDING",
        valid_until=basis.valid_until,
        completed_at=basis.created_at,
    )
    db.add(run)
    db.flush()
    core = AgentCoreOutputV2(
        shadow_assessment=assessment,
        confidence=confidence,
        risk_level="HIGH" if assessment == "EXIT_RISK_HIGH" else "MEDIUM",
        reason_codes=["AGENT_RUNTIME_SHADOW_ONLY"],
        incomplete_roles=[],
    )
    for sequence, role in enumerate(
        (
            "TECHNICAL_SCOUT",
            "NEWS_DISCLOSURE_SCOUT",
            "MARKET_SECTOR_SCOUT",
            "POSITION_RISK_SCOUT",
            "CORE",
        ),
        start=1,
    ):
        db.add(
            AgentStageRun(
                run_id=run.id,
                role=role,
                sequence=sequence,
                dependency_roles_json="[]",
                state="SUCCEEDED",
                input_hash=str(sequence) * 64,
                output_json=core.model_dump_json() if role == "CORE" else "{}",
                max_attempts=1,
            )
        )
    db.commit()
    return run


def test_exit_risk_elevated_escalates_hold_to_guarded_partial_sell_once(
    db: Session, admin: User, settings: Settings
) -> None:
    now = _at_kst(2026, 8, 5, 10, 5)
    basis, position_payload, _ = _basis(db, admin, settings, now)
    run = _advisory(
        db,
        admin=admin,
        basis=basis,
        position_payload=position_payload,
        assessment="EXIT_RISK_ELEVATED",
    )

    first = finalize_position_advisory(db, run=run, settings=settings, now=now)
    second = finalize_position_advisory(db, run=run, settings=settings, now=now)
    db.commit()

    assert first is not None and second is not None and first.id == second.id
    assert first.action == "PARTIAL_SELL"
    assert first.model_id == FUSION_MODEL_ID
    assert json.loads(first.core_output_json)["sell_ratio"] == "0.5"
    assert run.fusion_state == "ESCALATED"
    assert run.fusion_decision_id == first.id
    assert db.scalar(select(func.count()).select_from(Decision)) == 2
    execution = db.scalar(
        select(DecisionExecution).where(DecisionExecution.decision_id == first.id)
    )
    assert execution is not None


def test_neutral_and_low_confidence_do_not_create_fusion_decision(
    db: Session, admin: User, settings: Settings
) -> None:
    now = _at_kst(2026, 8, 5, 10, 5)
    basis, position_payload, _ = _basis(db, admin, settings, now)
    run = _advisory(
        db,
        admin=admin,
        basis=basis,
        position_payload=position_payload,
        assessment="NEUTRAL",
    )

    assert finalize_position_advisory(db, run=run, settings=settings, now=now) is None
    assert run.fusion_state == "NO_ESCALATION"
    assert run.fusion_reason_code == "FUSION_NO_STRONGER_ACTION"
    assert db.scalar(select(func.count()).select_from(Decision)) == 1


def test_changed_position_fails_safe_before_creating_fusion_decision(
    db: Session, admin: User, settings: Settings
) -> None:
    now = _at_kst(2026, 8, 5, 10, 5)
    basis, position_payload, position = _basis(db, admin, settings, now)
    run = _advisory(
        db,
        admin=admin,
        basis=basis,
        position_payload=position_payload,
        assessment="EXIT_RISK_HIGH",
    )
    position.version += 1
    db.commit()

    assert finalize_position_advisory(db, run=run, settings=settings, now=now) is None
    assert run.fusion_state == "FAILED_SAFE"
    assert run.fusion_reason_code == "FUSION_POSITION_CHANGED"
    assert db.scalar(select(func.count()).select_from(Decision)) == 1


def test_expired_basis_never_creates_fusion_decision(
    db: Session, admin: User, settings: Settings
) -> None:
    now = _at_kst(2026, 8, 5, 10, 5)
    basis, position_payload, _ = _basis(db, admin, settings, now)
    run = _advisory(
        db,
        admin=admin,
        basis=basis,
        position_payload=position_payload,
        assessment="EXIT_RISK_HIGH",
    )

    assert (
        finalize_position_advisory(
            db, run=run, settings=settings, now=now + timedelta(minutes=5)
        )
        is None
    )
    assert run.fusion_state == "EXPIRED"
    assert db.scalar(select(func.count()).select_from(Decision)) == 1


def test_agent_worker_completion_invokes_server_owned_fusion(
    db: Session, admin: User, settings: Settings
) -> None:
    now = _at_kst(2026, 8, 5, 10, 5)
    basis, position_payload, _ = _basis(db, admin, settings, now)
    run = _advisory(
        db,
        admin=admin,
        basis=basis,
        position_payload=position_payload,
        assessment="EXIT_RISK_HIGH",
    )
    run.state = "RUNNING"
    run.completed_at = None
    db.commit()

    _finalize_run(db, run, now)
    db.commit()

    assert run.state == "PARTIAL"
    assert run.fusion_state == "ESCALATED"
    fused = db.get(Decision, run.fusion_decision_id)
    assert fused is not None and fused.action == "FULL_SELL"
