from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import AgentCoreOutputV2
from app.config import Settings
from app.decision_execution import route_trading_decision
from app.ids import uuid7
from app.models import (
    AgentRun,
    AgentStageRun,
    Decision,
    DecisionInputSnapshot,
    Position,
    User,
)

FUSION_POLICY_VERSION = "position-agent-fusion-v1"
FUSION_MODEL_ID = "position-agent-fusion-v1"
FUSION_PROMPT_VERSION = "position-agent-fusion-policy-v1"
MINIMUM_CONFIDENCE = Decimal("0.70")
REQUIRED_STAGES = frozenset(
    {
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
        "CORE",
    }
)
ACTION_SEVERITY = {"HOLD": 0, "PARTIAL_SELL": 1, "FULL_SELL": 2}
ASSESSMENT_ACTION = {
    "HOLD_SUPPORTIVE": "HOLD",
    "NEUTRAL": "HOLD",
    "EXIT_RISK_ELEVATED": "PARTIAL_SELL",
    "EXIT_RISK_HIGH": "FULL_SELL",
}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _finish(run: AgentRun, state: str, code: str) -> None:
    run.fusion_state = state
    run.fusion_reason_code = code


def _request_id(run: AgentRun, basis: Decision) -> str:
    digest = _hash(
        {
            "agent_run_id": run.id,
            "basis_decision_id": basis.id,
            "policy_version": FUSION_POLICY_VERSION,
        }
    )
    return "fusion-" + digest[:57]


def _validate_frozen_input(
    db: Session, run: AgentRun, basis: Decision
) -> tuple[dict[str, object], DecisionInputSnapshot] | None:
    decision_input = db.get(DecisionInputSnapshot, basis.decision_input_id)
    if (
        decision_input is None
        or decision_input.user_id != run.owner_id
        or decision_input.market_snapshot_id != run.market_snapshot_id
        or basis.input_snapshot_id != run.market_snapshot_id
        or basis.symbol != run.symbol
        or basis.market != run.market
        or basis.decision_kind != "POSITION"
        or basis.purpose != "TRADING"
        or run.analysis_context != "POSITION"
    ):
        return None
    payload = json.loads(decision_input.input_json)
    position = payload.get("position")
    if not isinstance(position, dict) or _hash(position) != run.position_snapshot_hash:
        return None
    if _hash(json.loads(run.position_snapshot_json or "{}")) != run.position_snapshot_hash:
        return None
    return position, decision_input


def finalize_position_advisory(
    db: Session,
    *,
    run: AgentRun,
    settings: Settings,
    now: datetime,
) -> Decision | None:
    """Create a stronger server-owned POSITION decision from a verified advisory.

    The deterministic basis has already been routed independently.  The Agent
    result can only escalate risk and still passes through the ordinary Guard.
    """
    if run.purpose != "TRADING_ADVISORY":
        return None
    if run.fusion_state != "PENDING":
        return db.get(Decision, run.fusion_decision_id) if run.fusion_decision_id else None
    basis = db.get(Decision, run.basis_decision_id)
    if basis is None or run.fusion_policy_version != FUSION_POLICY_VERSION:
        _finish(run, "FAILED_SAFE", "FUSION_BASIS_NOT_FOUND")
        return None
    current = _aware(now)
    if current >= _aware(basis.valid_until):
        _finish(run, "EXPIRED", "FUSION_BASIS_EXPIRED")
        return None
    frozen = _validate_frozen_input(db, run, basis)
    if frozen is None:
        _finish(run, "FAILED_SAFE", "FUSION_INPUT_MISMATCH")
        return None
    position_payload, decision_input = frozen
    current_position = db.get(Position, str(position_payload["position_id"]))
    if (
        current_position is None
        or current_position.state != "OPEN"
        or current_position.version != int(position_payload["version"])
        or current_position.quantity <= 0
    ):
        _finish(run, "FAILED_SAFE", "FUSION_POSITION_CHANGED")
        return None

    stages = list(db.scalars(select(AgentStageRun).where(AgentStageRun.run_id == run.id)))
    by_role = {stage.role: stage for stage in stages}
    if any(by_role.get(role) is None or by_role[role].state != "SUCCEEDED" for role in REQUIRED_STAGES):
        _finish(run, "FAILED_SAFE", "FUSION_REQUIRED_STAGE_INCOMPLETE")
        return None
    core_stage = by_role["CORE"]
    if core_stage.output_json is None:
        _finish(run, "FAILED_SAFE", "FUSION_CORE_OUTPUT_MISSING")
        return None
    try:
        core = AgentCoreOutputV2.model_validate_json(core_stage.output_json)
    except ValidationError:
        _finish(run, "FAILED_SAFE", "FUSION_CORE_OUTPUT_INVALID")
        return None
    confidence = Decimal(str(core.confidence))
    if core.incomplete_roles or core.shadow_assessment == "UNKNOWN":
        _finish(run, "FAILED_SAFE", "FUSION_CORE_INCOMPLETE")
        return None
    if confidence < MINIMUM_CONFIDENCE:
        _finish(run, "NO_ESCALATION", "FUSION_CONFIDENCE_BELOW_THRESHOLD")
        return None
    advisory_action = ASSESSMENT_ACTION.get(core.shadow_assessment)
    basis_severity = ACTION_SEVERITY.get(basis.action)
    advisory_severity = ACTION_SEVERITY.get(advisory_action or "")
    if basis_severity is None or advisory_severity is None:
        _finish(run, "FAILED_SAFE", "FUSION_ACTION_NOT_SUPPORTED")
        return None
    if advisory_severity <= basis_severity:
        _finish(run, "NO_ESCALATION", "FUSION_NO_STRONGER_ACTION")
        return None

    request_id = _request_id(run, basis)
    user = db.get(User, run.owner_id)
    if user is None or user.status != "ACTIVE":
        _finish(run, "FAILED_SAFE", "FUSION_USER_NOT_ACTIVE")
        return None
    existing = db.scalar(select(Decision).where(Decision.evaluation_request_id == request_id))
    if existing is not None:
        route_trading_decision(
            db,
            decision=existing,
            user=user,
            correlation_id=uuid7(),
            settings=settings,
            now=current,
        )
        run.fusion_decision_id = existing.id
        _finish(run, "ESCALATED", "FUSION_DECISION_REUSED")
        return existing

    assert advisory_action in {"PARTIAL_SELL", "FULL_SELL"}
    reason_code = (
        "LLM_EXIT_RISK_HIGH"
        if advisory_action == "FULL_SELL"
        else "LLM_EXIT_RISK_ELEVATED"
    )
    reason_codes = list(dict.fromkeys([*core.reason_codes, reason_code]))
    fused_core = {
        "action": advisory_action,
        "confidence": str(confidence),
        "risk_level": "HIGH" if advisory_action == "FULL_SELL" else "MEDIUM",
        "sell_ratio": "0.5" if advisory_action == "PARTIAL_SELL" else None,
        "reason_codes": reason_codes,
    }
    decision = Decision(
        decision_input_id=decision_input.id,
        purpose="TRADING",
        evaluation_request_id=request_id,
        input_snapshot_id=basis.input_snapshot_id,
        symbol=basis.symbol,
        market=basis.market,
        decision_kind="POSITION",
        model_provider="CRESTA_FUSION",
        model_id=FUSION_MODEL_ID,
        prompt_version=FUSION_PROMPT_VERSION,
        schema_version=basis.schema_version,
        scout_output_json=basis.scout_output_json,
        core_output_json=_canonical(fused_core),
        action=advisory_action,
        confidence=confidence,
        risk_level=str(fused_core["risk_level"]),
        reason_codes_json=_canonical(reason_codes),
        valid_until=basis.valid_until,
        configuration_version_id=basis.configuration_version_id,
        execution_mode=None,
        execution_outcome="NO_ACTION",
        validation_status="VALID",
        latency_ms=basis.latency_ms,
    )
    db.add(decision)
    db.flush()
    route_trading_decision(
        db,
        decision=decision,
        user=user,
        correlation_id=uuid7(),
        settings=settings,
        now=current,
    )
    run.fusion_decision_id = decision.id
    _finish(run, "ESCALATED", reason_code)
    return decision
