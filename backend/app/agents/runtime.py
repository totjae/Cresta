from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.contracts import AgentAssessment, AgentCoreOutput
from app.llm.contracts import LlmRequest
from app.llm.registry import AdapterNotImplementedError, provider_registry
from app.models import (
    AgentRun,
    AgentStageRun,
    EvidenceBundle,
    IndicatorSnapshot,
    LlmInvocation,
    LlmModelProfile,
    LlmProviderProfile,
    LlmRoleRoute,
    MarketSnapshot,
    MarketStreamState,
    Position,
    User,
)

DAG_VERSION = "agent-dag-v1"
EVIDENCE_POLICY_VERSION = "fixture-none-v1"
ROUTE_ROLES = (
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
    "CORE",
)
STAGES = (
    ("INTEL_COLLECTOR", 10, ()),
    ("EVIDENCE_VERIFIER", 20, ("INTEL_COLLECTOR",)),
    ("TECHNICAL_SCOUT", 30, ("EVIDENCE_VERIFIER",)),
    ("NEWS_DISCLOSURE_SCOUT", 40, ("EVIDENCE_VERIFIER",)),
    ("MARKET_SECTOR_SCOUT", 50, ("EVIDENCE_VERIFIER",)),
    ("POSITION_RISK_SCOUT", 60, ("EVIDENCE_VERIFIER",)),
    (
        "CORE",
        70,
        (
            "TECHNICAL_SCOUT",
            "NEWS_DISCLOSURE_SCOUT",
            "MARKET_SECTOR_SCOUT",
            "POSITION_RISK_SCOUT",
        ),
    ),
)


class AgentRuntimeError(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class RouteBinding:
    route: LlmRoleRoute
    model: LlmModelProfile
    provider: LlmProviderProfile


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _load_routes(
    db: Session, *, owner_id: str, route_ids: dict[str, str]
) -> dict[str, RouteBinding]:
    if set(route_ids) != set(ROUTE_ROLES):
        raise AgentRuntimeError("AGENT_ROUTE_SET_INCOMPLETE")
    bindings: dict[str, RouteBinding] = {}
    for role in ROUTE_ROLES:
        route = db.get(LlmRoleRoute, route_ids[role])
        if (
            route is None
            or route.owner_id != owner_id
            or route.role != role
            or route.state not in {"VALIDATED", "ACTIVE"}
            or route.execution_stage != "SHADOW"
            or route.fallback_policy != "NONE"
            or route.max_attempts != 1
            or route.output_schema_version
            != ("agent-core-v1" if role == "CORE" else "agent-assessment-v1")
        ):
            raise AgentRuntimeError("AGENT_ROUTE_NOT_READY")
        model = db.get(LlmModelProfile, route.primary_model_profile_id)
        provider = db.get(LlmProviderProfile, model.provider_profile_id) if model else None
        if (
            model is None
            or model.state != "VALIDATED"
            or provider is None
            or provider.owner_id != owner_id
            or provider.adapter_type != "MOCK"
            or provider.state != "VALIDATED"
        ):
            raise AgentRuntimeError("AGENT_ROUTE_NOT_READY")
        try:
            provider_registry.resolve(provider.adapter_type)
        except AdapterNotImplementedError as exc:
            raise AgentRuntimeError("AGENT_ADAPTER_NOT_ALLOWED") from exc
        bindings[role] = RouteBinding(route, model, provider)
    return bindings


def _snapshot(db: Session, market: str, symbol: str) -> MarketSnapshot:
    stream = db.get(MarketStreamState, (market, symbol))
    snapshot = (
        db.get(MarketSnapshot, stream.current_snapshot_id)
        if stream and stream.current_snapshot_id
        else None
    )
    if snapshot is None:
        raise AgentRuntimeError("AGENT_MARKET_SNAPSHOT_NOT_FOUND", 409)
    return snapshot


def _stage(
    db: Session,
    *,
    run: AgentRun,
    role: str,
    sequence: int,
    dependencies: tuple[str, ...],
    route_id: str | None,
    input_hash: str,
) -> AgentStageRun:
    stage = AgentStageRun(
        run_id=run.id,
        role=role,
        sequence=sequence,
        dependency_roles_json=_canonical(list(dependencies)),
        route_id=route_id,
        input_hash=input_hash,
    )
    db.add(stage)
    db.flush()
    return stage


def _complete_stage(
    stage: AgentStageRun, *, state: str, output: dict[str, object], now: datetime
) -> None:
    stage.state = state
    stage.output_json = _canonical(output)
    stage.output_hash = _hash(output)
    stage.completed_at = now


def _invoke_mock(
    db: Session,
    *,
    stage: AgentStageRun,
    binding: RouteBinding,
    role_input: dict[str, object],
    now: datetime,
) -> None:
    stage.state = "RUNNING"
    stage.started_at = now
    invocation = LlmInvocation(
        stage_run_id=stage.id,
        requested_provider_profile_id=binding.provider.id,
        requested_model_profile_id=binding.model.id,
        state="RUNNING",
        input_hash=stage.input_hash,
    )
    db.add(invocation)
    db.flush()
    stage.invocation_id = invocation.id
    request = LlmRequest(
        invocation_id=invocation.id,
        role=stage.role,
        model_profile_id=binding.model.id,
        prompt_version=binding.route.prompt_version,
        input_schema_version="agent-runtime-input-v1",
        input_hash=stage.input_hash,
        messages=[{"role": "user", "content": _canonical(role_input)}],
        output_json_schema={"type": "object"},
        timeout_ms=binding.route.timeout_ms,
        max_output_tokens=binding.route.max_output_tokens_override
        or binding.model.max_output_tokens,
        temperature=float(
            binding.route.temperature_override
            if binding.route.temperature_override is not None
            else binding.model.temperature
        ),
        top_p=float(
            binding.route.top_p_override
            if binding.route.top_p_override is not None
            else binding.model.top_p
        )
        if (binding.route.top_p_override is not None or binding.model.top_p is not None)
        else None,
        reasoning_effort=binding.route.reasoning_effort_override
        or binding.model.reasoning_effort,
        seed=binding.route.seed_override
        if binding.route.seed_override is not None
        else binding.model.seed,
    )
    result = provider_registry.resolve(binding.provider.adapter_type).generate_structured(
        request, binding.model.provider_model_id
    )
    invocation.state = result.status
    invocation.actual_provider = result.actual_provider
    invocation.actual_model = result.actual_model
    invocation.raw_response_hash = result.raw_response_hash
    invocation.latency_ms = result.latency_ms
    invocation.usage_json = _canonical(
        {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens}
    )
    invocation.retry_count = result.retry_count
    invocation.fallback_path_json = _canonical(result.fallback_path)
    invocation.validation_status = result.schema_validation
    invocation.completed_at = now
    if result.status != "SUCCEEDED" or result.schema_validation != "PASSED":
        raise AgentRuntimeError("AGENT_MOCK_INVOCATION_FAILED")


def _assessment(
    role: str,
    *,
    stage_run_id: str,
    symbol: str,
    input_refs: list[str],
    indicator: IndicatorSnapshot | None,
    snapshot: MarketSnapshot,
    position: Position | None,
    observed_at: datetime,
    valid_until: datetime,
) -> AgentAssessment:
    common = {
        "stage_run_id": stage_run_id,
        "role": role,
        "symbol": symbol,
        "input_refs": input_refs,
        "observed_at": observed_at,
        "valid_until": valid_until,
    }
    if role == "TECHNICAL_SCOUT":
        if indicator is None:
            return AgentAssessment(
                **common,
                status="INSUFFICIENT_DATA",
                stance="UNKNOWN",
                confidence=0,
                uncertainty=1,
                reason_codes=["INDICATOR_SNAPSHOT_MISSING"],
                evidence_refs=[],
            )
        supportive = indicator.price_vs_vwap_pct is not None and indicator.price_vs_vwap_pct >= 0
        return AgentAssessment(
            **common,
            status="SUCCEEDED",
            stance="SUPPORTIVE" if supportive else "CAUTION",
            entry_score=60 if supportive else 40,
            exit_risk_score=30 if supportive else 60,
            confidence=0.7,
            uncertainty=0.3,
            reason_codes=["PRICE_AT_OR_ABOVE_VWAP" if supportive else "PRICE_BELOW_VWAP"],
            evidence_refs=[],
        )
    if role == "NEWS_DISCLOSURE_SCOUT":
        return AgentAssessment(
            **common,
            status="INSUFFICIENT_DATA",
            stance="UNKNOWN",
            confidence=0,
            uncertainty=1,
            reason_codes=["NO_EXTERNAL_EVIDENCE_FIXTURE"],
            evidence_refs=[],
        )
    if role == "MARKET_SECTOR_SCOUT":
        normal = snapshot.quality == "NORMAL"
        return AgentAssessment(
            **common,
            status="SUCCEEDED" if normal else "INSUFFICIENT_DATA",
            stance="NEUTRAL" if normal else "UNKNOWN",
            entry_score=50 if normal else None,
            exit_risk_score=50 if normal else None,
            confidence=0.5 if normal else 0,
            uncertainty=0.5 if normal else 1,
            reason_codes=["MARKET_SNAPSHOT_NORMAL" if normal else "MARKET_SNAPSHOT_DEGRADED"],
            evidence_refs=[],
        )
    if position is None:
        return AgentAssessment(
            **common,
            status="INSUFFICIENT_DATA",
            stance="UNKNOWN",
            confidence=0,
            uncertainty=1,
            reason_codes=["OPEN_POSITION_NOT_FOUND"],
            evidence_refs=[],
        )
    return AgentAssessment(
        **common,
        status="SUCCEEDED",
        stance="NEUTRAL",
        exit_risk_score=50,
        confidence=0.5,
        uncertainty=0.5,
        reason_codes=["OPEN_POSITION_PRESENT"],
        evidence_refs=[],
    )


def create_diagnostic_run(
    db: Session,
    *,
    user: User,
    market: str,
    symbol: str,
    route_ids: dict[str, str],
    now: datetime | None = None,
) -> tuple[AgentRun, bool]:
    observed = now or datetime.now(UTC)
    snapshot = _snapshot(db, market, symbol)
    bindings = _load_routes(db, owner_id=user.id, route_ids=route_ids)
    route_versions = {
        role: {
            "route_id": binding.route.id,
            "route_version": binding.route.version,
            "model_id": binding.model.id,
            "model_version": binding.model.version,
        }
        for role, binding in sorted(bindings.items())
    }
    input_record = {
        "market": market,
        "symbol": symbol,
        "market_snapshot_id": snapshot.id,
        "payload_hash": snapshot.payload_hash,
        "dag_version": DAG_VERSION,
        "route_versions": route_versions,
    }
    input_hash = _hash(input_record)
    idempotency_key = _hash(
        {"owner_id": user.id, "purpose": "DIAGNOSTIC", "input_hash": input_hash}
    )
    existing = db.scalar(select(AgentRun).where(AgentRun.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.owner_id != user.id:
            raise AgentRuntimeError("AGENT_IDEMPOTENCY_CONFLICT", 409)
        return existing, False

    run = AgentRun(
        owner_id=user.id,
        market=market,
        symbol=symbol,
        market_snapshot_id=snapshot.id,
        input_hash=input_hash,
        dag_version=DAG_VERSION,
        route_versions_json=_canonical(route_versions),
        idempotency_key=idempotency_key,
        state="RUNNING",
        valid_until=observed + timedelta(minutes=1),
        started_at=observed,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(AgentRun).where(AgentRun.idempotency_key == idempotency_key))
        if existing is None or existing.owner_id != user.id:
            raise AgentRuntimeError("AGENT_IDEMPOTENCY_CONFLICT", 409)
        return existing, False

    stages = {
        role: _stage(
            db,
            run=run,
            role=role,
            sequence=sequence,
            dependencies=dependencies,
            route_id=bindings[role].route.id if role in bindings else None,
            input_hash=_hash({"run_input_hash": input_hash, "role": role}),
        )
        for role, sequence, dependencies in STAGES
    }

    intel_output = {
        "schema_version": "intel-fixture-v1",
        "status": "SUCCEEDED",
        "source_mode": "FIXTURE_NONE",
        "evidence_count": 0,
    }
    _complete_stage(stages["INTEL_COLLECTOR"], state="SUCCEEDED", output=intel_output, now=observed)

    bundle_record = {
        "schema_version": "evidence-bundle-v1",
        "market": market,
        "symbol": symbol,
        "market_snapshot_id": snapshot.id,
        "policy_version": EVIDENCE_POLICY_VERSION,
        "state": "PARTIAL",
        "evidence_ids": [],
        "reason_codes": ["NO_EXTERNAL_EVIDENCE_FIXTURE"],
    }
    bundle = EvidenceBundle(
        owner_id=user.id,
        run_id=run.id,
        market=market,
        symbol=symbol,
        as_of=observed,
        policy_version=EVIDENCE_POLICY_VERSION,
        state="PARTIAL",
        evidence_ids_json="[]",
        contradiction_groups_json="[]",
        stale_evidence_ids_json="[]",
        reason_codes_json=_canonical(["NO_EXTERNAL_EVIDENCE_FIXTURE"]),
        bundle_hash=_hash(bundle_record),
    )
    db.add(bundle)
    db.flush()
    _complete_stage(
        stages["EVIDENCE_VERIFIER"],
        state="SUCCEEDED",
        output={**bundle_record, "bundle_id": bundle.id, "bundle_hash": bundle.bundle_hash},
        now=observed,
    )

    indicator = db.scalar(
        select(IndicatorSnapshot).where(IndicatorSnapshot.market_snapshot_id == snapshot.id)
    )
    position = db.scalar(
        select(Position).where(
            Position.symbol == symbol, Position.state == "OPEN", Position.quantity > 0
        )
    )
    assessments: dict[str, AgentAssessment] = {}
    for role in ROUTE_ROLES[:-1]:
        stage = stages[role]
        _invoke_mock(
            db,
            stage=stage,
            binding=bindings[role],
            role_input={
                "market_snapshot_id": snapshot.id,
                "evidence_bundle_id": bundle.id,
                "indicator_snapshot_id": indicator.id if indicator else None,
                "position_id": position.id if position else None,
            },
            now=observed,
        )
        assessment = _assessment(
            role,
            stage_run_id=stage.id,
            symbol=symbol,
            input_refs=[
                snapshot.id,
                bundle.id,
                *([indicator.id] if indicator else []),
                *([position.id] if position else []),
            ],
            indicator=indicator,
            snapshot=snapshot,
            position=position,
            observed_at=observed,
            valid_until=run.valid_until,
        )
        assessments[role] = assessment
        _complete_stage(
            stage,
            state=assessment.status,
            output=assessment.model_dump(mode="json"),
            now=observed,
        )

    core_stage = stages["CORE"]
    _invoke_mock(
        db,
        stage=core_stage,
        binding=bindings["CORE"],
        role_input={
            "market_snapshot_id": snapshot.id,
            "evidence_bundle_id": bundle.id,
            "assessment_hashes": {role: stages[role].output_hash for role in assessments},
        },
        now=observed,
    )
    incomplete = sorted(
        role for role, assessment in assessments.items() if assessment.status != "SUCCEEDED"
    )
    core = AgentCoreOutput(
        confidence=0 if incomplete else 0.5,
        risk_level="HIGH" if incomplete else "MEDIUM",
        reason_codes=[
            "AGENT_RUNTIME_SHADOW_ONLY",
            "REQUIRED_SCOUT_INCOMPLETE" if incomplete else "DIAGNOSTIC_WAIT_ONLY",
        ],
        incomplete_roles=incomplete,
    )
    _complete_stage(
        core_stage,
        state="SUCCEEDED",
        output=core.model_dump(mode="json"),
        now=observed,
    )
    run.core_action = "WAIT"
    run.state = "PARTIAL" if incomplete else "SUCCEEDED"
    run.completed_at = observed
    db.commit()
    db.refresh(run)
    return run, True


def list_agent_runs(db: Session, owner_id: str, limit: int = 50) -> list[AgentRun]:
    return list(
        db.scalars(
            select(AgentRun)
            .where(AgentRun.owner_id == owner_id)
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
        )
    )


def get_agent_run(db: Session, owner_id: str, run_id: str) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if run is None or run.owner_id != owner_id:
        raise AgentRuntimeError("AGENT_RUN_NOT_FOUND", 404)
    return run
