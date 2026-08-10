from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import (
    AgentAssessment,
    AgentCoreModelOutput,
    AgentCoreOutput,
    AgentScoutModelOutput,
)
from app.agents.runtime import (
    EVIDENCE_POLICY_VERSION,
    ROUTE_ROLES,
    AgentRuntimeError,
    RouteBinding,
    _assessment,
    _canonical,
    _complete_stage,
    _hash,
    _invoke_model,
)
from app.config import Settings
from app.db import SessionLocal
from app.ids import uuid7
from app.models import (
    AgentRun,
    AgentStageRun,
    EvidenceBundle,
    EvidenceItem,
    IndicatorSnapshot,
    LlmInvocation,
    LlmModelProfile,
    LlmPromptProfile,
    LlmProviderProfile,
    LlmRoleRoute,
    MarketSnapshot,
    Position,
)

logger = logging.getLogger("cresta.agent_worker")
DEPENDENCY_OK = {"SUCCEEDED", "INSUFFICIENT_DATA", "CONFLICTED"}
TERMINAL = DEPENDENCY_OK | {"TIMED_OUT", "FAILED", "INVALID_OUTPUT"}
DEFAULT_TIMEOUT_SECONDS = {
    "INTEL_COLLECTOR": 20,
    "EVIDENCE_VERIFIER": 15,
    "EVIDENCE_CANDIDATE_AUDITOR": 15,
    "CORE": 15,
}


@dataclass(frozen=True)
class StageClaim:
    stage_id: str
    fencing_token: int


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _finalize_run(db: Session, run: AgentRun, now: datetime) -> None:
    stages = list(
        db.scalars(
            select(AgentStageRun)
            .where(AgentStageRun.run_id == run.id)
            .order_by(AgentStageRun.sequence)
        )
    )
    if not stages or any(stage.state not in TERMINAL for stage in stages):
        return
    core = next(stage for stage in stages if stage.role == "CORE")
    run.core_action = "WAIT" if core.state == "SUCCEEDED" else None
    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run.id))
    if core.state != "SUCCEEDED" or any(
        stage.state in {"FAILED", "TIMED_OUT", "INVALID_OUTPUT"} for stage in stages
    ):
        run.state = "FAILED"
        run.error_code = core.error_code or "AGENT_STAGE_FAILED"
    else:
        run.state = (
            "PARTIAL"
            if any(stage.state != "SUCCEEDED" for stage in stages)
            or bundle is None
            or bundle.state != "VERIFIED"
            else "SUCCEEDED"
        )
    run.completed_at = now


def recover_expired_stages(db: Session, *, now: datetime) -> int:
    expired = list(
        db.scalars(
            select(AgentStageRun).where(
                AgentStageRun.state == "RUNNING",
                AgentStageRun.lease_expires_at.is_not(None),
                AgentStageRun.lease_expires_at <= now,
            )
        )
    )
    recovered = 0
    for stage in expired:
        if stage.invocation_id is None and stage.attempt_count < stage.max_attempts:
            stage.state = "PENDING"
            stage.error_code = "AGENT_LEASE_EXPIRED_RETRY"
            stage.available_at = now
            stage.started_at = None
            stage.timeout_at = None
        else:
            stage.state = "TIMED_OUT"
            stage.error_code = (
                "AGENT_INVOCATION_OUTCOME_UNKNOWN"
                if stage.invocation_id
                else "AGENT_STAGE_ATTEMPTS_EXHAUSTED"
            )
            stage.completed_at = now
            if stage.invocation_id:
                invocations = list(
                    db.scalars(
                        select(LlmInvocation).where(
                            LlmInvocation.stage_run_id == stage.id,
                            LlmInvocation.state == "RUNNING",
                        )
                    )
                )
                for invocation in invocations:
                    invocation.state = "AMBIGUOUS"
                    invocation.error_code = "AGENT_INVOCATION_OUTCOME_UNKNOWN"
                    invocation.completed_at = now
        stage.lease_owner_id = None
        stage.lease_expires_at = None
        stage.heartbeat_at = None
        recovered += 1
        run = db.get(AgentRun, stage.run_id)
        if run is not None:
            _finalize_run(db, run, now)
    if recovered:
        db.commit()
    return recovered


def _dependencies(db: Session, stage: AgentStageRun) -> list[AgentStageRun]:
    roles = json.loads(stage.dependency_roles_json)
    if not roles:
        return []
    return list(
        db.scalars(
            select(AgentStageRun).where(
                AgentStageRun.run_id == stage.run_id,
                AgentStageRun.role.in_(roles),
            )
        )
    )


def claim_next_stage(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    now: datetime,
) -> StageClaim | None:
    recover_expired_stages(db, now=now)
    statement = (
        select(AgentStageRun)
        .where(
            AgentStageRun.state == "PENDING",
            AgentStageRun.available_at <= now,
        )
        .order_by(AgentStageRun.created_at, AgentStageRun.sequence)
        .with_for_update(skip_locked=True)
    )
    for stage in db.scalars(statement):
        run = db.get(AgentRun, stage.run_id)
        if run is None or run.state in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}:
            continue
        dependencies = _dependencies(db, stage)
        if any(item.state in {"FAILED", "TIMED_OUT", "INVALID_OUTPUT"} for item in dependencies):
            stage.state = "FAILED"
            stage.error_code = "AGENT_DEPENDENCY_FAILED"
            stage.completed_at = now
            _finalize_run(db, run, now)
            db.commit()
            return None
        if any(item.state not in DEPENDENCY_OK for item in dependencies):
            continue
        if _aware(run.valid_until) <= now:
            stage.state = "TIMED_OUT"
            stage.error_code = "STALE_BEFORE_START"
            stage.completed_at = now
            _finalize_run(db, run, now)
            db.commit()
            return None
        timeout_seconds = DEFAULT_TIMEOUT_SECONDS.get(stage.role, 10)
        if stage.route_id:
            route = db.get(LlmRoleRoute, stage.route_id)
            timeout_seconds = max(1, (route.timeout_ms + 999) // 1000) if route else 10
        stage.state = "RUNNING"
        stage.lease_owner_id = worker_id
        stage.lease_expires_at = now + timedelta(seconds=lease_seconds)
        stage.fencing_token += 1
        stage.attempt_count += 1
        stage.started_at = now
        stage.timeout_at = now + timedelta(seconds=timeout_seconds)
        stage.heartbeat_at = now
        if run.state == "CREATED":
            run.state = "RUNNING"
            run.started_at = now
        claim = StageClaim(stage.id, stage.fencing_token)
        db.commit()
        return claim
    db.commit()
    return None


def _binding(db: Session, run: AgentRun, stage: AgentStageRun) -> RouteBinding:
    if stage.route_id is None:
        raise AgentRuntimeError("AGENT_ROUTE_NOT_READY")
    route = db.get(LlmRoleRoute, stage.route_id)
    model = db.get(LlmModelProfile, route.primary_model_profile_id) if route else None
    provider = db.get(LlmProviderProfile, model.provider_profile_id) if model else None
    versions = json.loads(run.route_versions_json).get(stage.role, {})
    fallback_model = (
        db.get(LlmModelProfile, versions.get("fallback_model_id"))
        if versions.get("fallback_model_id")
        else None
    )
    fallback_provider = (
        db.get(LlmProviderProfile, fallback_model.provider_profile_id)
        if fallback_model
        else None
    )
    prompt = (
        db.get(LlmPromptProfile, route.prompt_profile_id)
        if route and route.prompt_profile_id
        else None
    )
    if (
        route is None
        or model is None
        or provider is None
        or route.id != versions.get("route_id")
        or route.version != versions.get("route_version")
        or model.id != versions.get("model_id")
        or model.version != versions.get("model_version")
        or route.fallback_policy != versions.get("failure_policy")
        or (fallback_model.id if fallback_model else None)
        != versions.get("fallback_model_id")
        or (fallback_model.version if fallback_model else None)
        != versions.get("fallback_model_version")
        or provider.state != "VALIDATED"
        or provider.deleted_at is not None
        or (provider.adapter_type != "MOCK" and not provider.credential_secret_ref)
        or (
            fallback_model is not None
            and (
                fallback_provider is None
                or fallback_provider.owner_id != run.owner_id
                or fallback_provider.state != "VALIDATED"
                or fallback_provider.deleted_at is not None
                or fallback_model.state != "VALIDATED"
                or (
                    fallback_provider.adapter_type != "MOCK"
                    and not fallback_provider.credential_secret_ref
                )
            )
        )
        or route.prompt_profile_id != versions.get("prompt_profile_id")
        or (
            route.prompt_profile_id is not None
            and (
                prompt is None
                or prompt.state != "VALIDATED"
                or prompt.content_hash != versions.get("prompt_content_hash")
            )
        )
    ):
        raise AgentRuntimeError("AGENT_ROUTE_SNAPSHOT_MISMATCH")
    return RouteBinding(route, model, provider, fallback_model, fallback_provider)


def _decimal_text(value: object | None) -> str | None:
    return str(value) if value is not None else None


def _scout_role_input(
    *,
    run: AgentRun,
    snapshot: MarketSnapshot,
    bundle: EvidenceBundle,
    indicator: IndicatorSnapshot | None,
    position: Position | None,
) -> tuple[dict[str, object], list[str]]:
    input_refs = [
        snapshot.id,
        bundle.id,
        *([indicator.id] if indicator else []),
        *([position.id] if position else []),
    ]
    allowed_evidence_refs = [str(item) for item in json.loads(bundle.evidence_ids_json)]
    role_input: dict[str, object] = {
        "market": run.market,
        "symbol": run.symbol,
        "observed_at": snapshot.event_at.isoformat(),
        "valid_until": run.valid_until.isoformat(),
        "market_snapshot": {
            "ref": snapshot.id,
            "last_price": _decimal_text(snapshot.last_price),
            "open_price": _decimal_text(snapshot.open_price),
            "high_price": _decimal_text(snapshot.high_price),
            "low_price": _decimal_text(snapshot.low_price),
            "cumulative_volume": snapshot.cumulative_volume,
            "best_bid_price": _decimal_text(snapshot.best_bid_price),
            "best_ask_price": _decimal_text(snapshot.best_ask_price),
            "trading_status": snapshot.trading_status,
            "quality": snapshot.quality,
        },
        "evidence_bundle": {
            "ref": bundle.id,
            "state": bundle.state,
            "evidence_refs": json.loads(bundle.evidence_ids_json),
            "reason_codes": json.loads(bundle.reason_codes_json),
        },
        "indicator_snapshot": (
            {
                "ref": indicator.id,
                "calculator_version": indicator.calculator_version,
                "vwap": _decimal_text(indicator.vwap),
                "sma5": _decimal_text(indicator.sma5),
                "drawdown_from_high_pct": _decimal_text(
                    indicator.drawdown_from_high_pct
                ),
                "spread_pct": _decimal_text(indicator.spread_pct),
                "price_vs_vwap_pct": _decimal_text(indicator.price_vs_vwap_pct),
                "sma5_slope_pct": _decimal_text(indicator.sma5_slope_pct),
                "relative_volume_5": _decimal_text(indicator.relative_volume_5),
                "realized_volatility_pct": _decimal_text(
                    indicator.realized_volatility_pct
                ),
                "minute_bar_count": indicator.minute_bar_count,
            }
            if indicator
            else None
        ),
        "position": (
            {
                "ref": position.id,
                "quantity": position.quantity,
                "average_price": _decimal_text(position.average_price),
                "state": position.state,
                "version": position.version,
            }
            if position
            else None
        ),
        "allowed_input_refs": input_refs,
        "allowed_evidence_refs": allowed_evidence_refs,
    }
    return role_input, input_refs


def _execute_stage(db: Session, stage: AgentStageRun, run: AgentRun, now: datetime) -> None:
    snapshot = db.get(MarketSnapshot, run.market_snapshot_id)
    if snapshot is None:
        raise AgentRuntimeError("AGENT_MARKET_SNAPSHOT_NOT_FOUND")
    if stage.role == "INTEL_COLLECTOR":
        _complete_stage(
            stage,
            state="SUCCEEDED",
            output={
                "schema_version": "intel-fixture-v1",
                "status": "SUCCEEDED",
                "source_mode": "FIXTURE_NONE",
                "evidence_count": 0,
            },
            now=now,
        )
        return
    if stage.role == "EVIDENCE_VERIFIER":
        record = {
            "schema_version": "evidence-bundle-v1",
            "market": run.market,
            "symbol": run.symbol,
            "market_snapshot_id": snapshot.id,
            "policy_version": EVIDENCE_POLICY_VERSION,
            "state": "PARTIAL",
            "evidence_ids": [],
            "reason_codes": ["NO_EXTERNAL_EVIDENCE_FIXTURE"],
        }
        bundle = EvidenceBundle(
            owner_id=run.owner_id,
            run_id=run.id,
            market=run.market,
            symbol=run.symbol,
            as_of=now,
            policy_version=EVIDENCE_POLICY_VERSION,
            state="PARTIAL",
            evidence_ids_json="[]",
            contradiction_groups_json="[]",
            stale_evidence_ids_json="[]",
            reason_codes_json=_canonical(["NO_EXTERNAL_EVIDENCE_FIXTURE"]),
            bundle_hash=_hash(record),
        )
        db.add(bundle)
        db.flush()
        _complete_stage(
            stage,
            state="SUCCEEDED",
            output={**record, "bundle_id": bundle.id, "bundle_hash": bundle.bundle_hash},
            now=now,
        )
        return

    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run.id))
    if bundle is None:
        raise AgentRuntimeError("AGENT_EVIDENCE_BUNDLE_NOT_FOUND")
    if stage.role == "EVIDENCE_CANDIDATE_AUDITOR":
        candidates = list(
            db.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.run_id == run.id,
                    EvidenceItem.source_tier == "UNRATED",
                )
                .order_by(EvidenceItem.created_at, EvidenceItem.id)
            )
        )
        provider_counts: dict[str, int] = {}
        for candidate in candidates:
            provider_counts[candidate.source_name] = (
                provider_counts.get(candidate.source_name, 0) + 1
            )
        reason_codes = [
            "UNRATED_SOURCE_CANDIDATES_PRESENT"
            if candidates
            else "NO_PROVIDER_SOURCE_CANDIDATES"
        ]
        _complete_stage(
            stage,
            state="SUCCEEDED",
            output={
                "schema_version": "evidence-candidate-audit-v1",
                "status": "SUCCEEDED",
                "candidate_count": len(candidates),
                "candidate_ids": [candidate.id for candidate in candidates],
                "provider_counts": {
                    provider: provider_counts[provider]
                    for provider in sorted(provider_counts)
                },
                "reason_codes": reason_codes,
                "evidence_bundle_ref": bundle.id,
                "evidence_bundle_hash": bundle.bundle_hash,
                "bundle_mutated": False,
            },
            now=now,
        )
        return
    binding = _binding(db, run, stage)
    if stage.role in ROUTE_ROLES[:-1]:
        indicator = db.scalar(
            select(IndicatorSnapshot).where(
                IndicatorSnapshot.market_snapshot_id == snapshot.id
            )
        )
        position = db.scalar(
            select(Position).where(
                Position.symbol == run.symbol,
                Position.state == "OPEN",
                Position.quantity > 0,
            )
        )
        role_input, input_refs = _scout_role_input(
            run=run,
            snapshot=snapshot,
            bundle=bundle,
            indicator=indicator,
            position=position,
        )
        outcome = _invoke_model(
            db,
            stage=stage,
            binding=binding,
            role_input=role_input,
            now=now,
        )
        if outcome.provider.adapter_type == "MOCK":
            assessment = _assessment(
                stage.role,
                stage_run_id=stage.id,
                symbol=run.symbol,
                input_refs=input_refs,
                indicator=indicator,
                snapshot=snapshot,
                position=position,
                observed_at=now,
                valid_until=run.valid_until,
            )
        else:
            model_output = AgentScoutModelOutput.model_validate(outcome.output_json)
            assessment = AgentAssessment(
                stage_run_id=stage.id,
                role=stage.role,
                symbol=run.symbol,
                input_refs=input_refs,
                observed_at=now,
                valid_until=run.valid_until,
                **model_output.model_dump(),
            )
        _complete_stage(
            stage,
            state=assessment.status,
            output=assessment.model_dump(mode="json"),
            now=now,
        )
        return

    scout_stages = list(
        db.scalars(
            select(AgentStageRun).where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role.in_(ROUTE_ROLES[:-1]),
            )
        )
    )
    assessments = {
        item.role: AgentAssessment.model_validate(json.loads(item.output_json or "{}"))
        for item in scout_stages
    }
    incomplete = sorted(
        role for role, assessment in assessments.items() if assessment.status != "SUCCEEDED"
    )
    candidate_audit_stage = db.scalar(
        select(AgentStageRun).where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == "EVIDENCE_CANDIDATE_AUDITOR",
        )
    )
    if candidate_audit_stage is None:
        if run.dag_version != "agent-dag-v1":
            raise AgentRuntimeError("AGENT_EVIDENCE_CANDIDATE_AUDIT_NOT_FOUND")
        candidate_audit = {
            "candidate_count": 0,
            "reason_codes": ["LEGACY_DAG_NO_CANDIDATE_AUDIT"],
        }
        candidate_audit_ref = None
    else:
        if candidate_audit_stage.output_json is None:
            raise AgentRuntimeError("AGENT_EVIDENCE_CANDIDATE_AUDIT_NOT_FOUND")
        candidate_audit = json.loads(candidate_audit_stage.output_json)
        candidate_audit_ref = candidate_audit_stage.id
    outcome = _invoke_model(
        db,
        stage=stage,
        binding=binding,
        role_input={
            "market": run.market,
            "symbol": run.symbol,
            "market_snapshot_ref": snapshot.id,
            "evidence_bundle_ref": bundle.id,
            "assessments": {
                item.role: json.loads(item.output_json or "{}") for item in scout_stages
            },
            "assessment_hashes": {
                item.role: item.output_hash for item in scout_stages
            },
            "required_incomplete_roles": incomplete,
            "evidence_candidate_audit": {
                "ref": candidate_audit_ref,
                "candidate_count": candidate_audit["candidate_count"],
                "reason_codes": candidate_audit["reason_codes"],
                "verified_evidence_count": len(json.loads(bundle.evidence_ids_json)),
            },
        },
        now=now,
    )
    core = (
        AgentCoreOutput(
            confidence=0 if incomplete else 0.5,
            risk_level="HIGH" if incomplete else "MEDIUM",
            reason_codes=[
                "AGENT_RUNTIME_SHADOW_ONLY",
                "REQUIRED_SCOUT_INCOMPLETE" if incomplete else "DIAGNOSTIC_WAIT_ONLY",
            ],
            incomplete_roles=incomplete,
        )
        if outcome.provider.adapter_type == "MOCK"
        else AgentCoreOutput(
            **AgentCoreModelOutput.model_validate(outcome.output_json).model_dump()
        )
    )
    _complete_stage(stage, state="SUCCEEDED", output=core.model_dump(mode="json"), now=now)


def execute_claimed_stage(
    db: Session,
    *,
    claim: StageClaim,
    worker_id: str,
    now: datetime,
) -> bool:
    stage = db.scalar(
        select(AgentStageRun)
        .where(AgentStageRun.id == claim.stage_id)
        .with_for_update()
    )
    if (
        stage is None
        or stage.state != "RUNNING"
        or stage.lease_owner_id != worker_id
        or stage.fencing_token != claim.fencing_token
    ):
        db.rollback()
        return False
    run = db.get(AgentRun, stage.run_id)
    if run is None:
        db.rollback()
        return False
    try:
        if stage.timeout_at is not None and _aware(stage.timeout_at) <= now:
            raise TimeoutError("AGENT_STAGE_TIMEOUT")
        _execute_stage(db, stage, run, now)
    except Exception as exc:
        logger.exception("Agent stage failed role=%s stage=%s", stage.role, stage.id)
        if stage.invocation_id and not isinstance(exc, AgentRuntimeError):
            invocations = list(
                db.scalars(
                    select(LlmInvocation).where(LlmInvocation.stage_run_id == stage.id)
                )
            )
            for invocation in invocations:
                if invocation.state == "RUNNING" and invocation.completed_at is None:
                    invocation.state = "AMBIGUOUS"
                    invocation.error_code = "AGENT_INVOCATION_OUTCOME_UNKNOWN"
                    invocation.completed_at = now
        stage.state = "TIMED_OUT" if isinstance(exc, TimeoutError) else "FAILED"
        stage.error_code = getattr(exc, "code", None) or str(exc)[:64] or "AGENT_STAGE_FAILED"
        stage.completed_at = now
    stage.lease_owner_id = None
    stage.lease_expires_at = None
    stage.heartbeat_at = now
    _finalize_run(db, run, now)
    db.commit()
    return True


def process_agent_work_once(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    observed = now or datetime.now(UTC)
    claim = claim_next_stage(
        db,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        now=observed,
    )
    if claim is None:
        return False
    return execute_claimed_stage(db, claim=claim, worker_id=worker_id, now=datetime.now(UTC))


class AgentWorker:
    def __init__(self, settings: Settings, *, worker_id: str | None = None) -> None:
        self.settings = settings
        self.worker_id = worker_id or uuid7()
        self.stop_event = asyncio.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def _process_once(self) -> bool:
        with SessionLocal() as db:
            return process_agent_work_once(
                db,
                worker_id=self.worker_id,
                lease_seconds=self.settings.agent_worker_lease_seconds,
            )

    async def run(self) -> int:
        while not self.stop_event.is_set():
            try:
                processed = await asyncio.to_thread(self._process_once)
            except Exception:
                processed = False
                logger.exception("Agent worker tick failed")
            if not processed:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self.stop_event.wait(),
                        timeout=self.settings.agent_worker_poll_seconds,
                    )
        return 0
