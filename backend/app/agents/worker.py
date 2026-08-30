from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activation_gate import ActivationValidationPolicy, EvidenceLoader
from app.agents.contracts import (
    AgentAssessment,
    AgentAssessmentV2,
    AgentCoreModelOutput,
    AgentCoreModelOutputV2,
    AgentCoreOutput,
    AgentCoreOutputV2,
    AgentScoutModelOutput,
)
from app.agents.dart import (
    DART_SOURCE_POLICY_VERSION,
    collect_dart_disclosures,
    receipt_date_as_utc,
)
from app.agents.decision_agents import (
    DECISION_AGENT_ROLES as FOUNDATION_DECISION_AGENT_ROLES,
)
from app.agents.decision_agents import (
    V7_LLM_ROUTE_ROLES,
    DecisionAgentFoundationError,
    materialize_decision_agent_stages,
)
from app.agents.decision_context import (
    DECISION_AGENT_ROLES,
    V7_DAG_VERSION,
    DecisionContextFreezeError,
    freeze_decision_context,
)
from app.agents.decision_finalizer import (
    reconcile_v7_diagnostic_lifecycle,
    reconcile_v7_entry_finalizations,
)
from app.agents.decision_runtime import (
    execute_decision_agent_stage,
    terminalize_recovered_decision_stage,
)
from app.agents.entry_arbiter import (
    ENTRY_ARBITER_ROLE,
    EntryArbiterError,
    execute_entry_arbiter_stage,
    materialize_entry_arbiter_stage,
    validate_entry_arbiter_stage,
)
from app.agents.krx import (
    KRX_SOURCE_POLICY_VERSION,
    base_date_as_utc,
    collect_krx_daily_market,
)
from app.agents.naver_news import (
    NAVER_NEWS_SOURCE_POLICY_VERSION,
    collect_naver_news,
)
from app.agents.runtime import (
    ASSESSMENT_SCHEMA_VERSION,
    CORE_SCHEMA_VERSION,
    DAG_VERSION,
    EVIDENCE_POLICY_VERSION,
    ROUTE_ROLES,
    SCORE_POLICY_VERSION,
    SCOUT_ROUTE_ROLES,
    AgentRuntimeError,
    RouteBinding,
    _assessment,
    _assessment_v2,
    _canonical,
    _complete_stage,
    _hash,
    _invoke_model,
    allowed_roles,
    executable_roles,
    materializable_roles,
    uses_server_inputs,
    uses_v2_contract,
)
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.decision_inputs import _indicator_payload, canonical_input_json, input_digest
from app.ids import uuid7
from app.models import (
    AgentRun,
    AgentStageRun,
    DecisionContext,
    DecisionInputSnapshot,
    EvidenceBundle,
    EvidenceItem,
    IndicatorSnapshot,
    LlmInvocation,
    LlmModelProfile,
    LlmPromptProfile,
    LlmProviderProfile,
    LlmRoleRoute,
    MarketContextSnapshot,
    MarketSnapshot,
    Position,
)

logger = logging.getLogger("cresta.agent_worker")
DEPENDENCY_OK = {"SUCCEEDED", "NOT_APPLICABLE", "INSUFFICIENT_DATA", "CONFLICTED"}
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


def _is_v7_upstream_run(run: AgentRun) -> bool:
    if run.dag_version != V7_DAG_VERSION:
        return False
    try:
        versions = json.loads(run.route_versions_json)
    except (TypeError, ValueError):
        return False
    return bool(
        isinstance(versions, dict)
        and frozenset(versions)
        in {frozenset(SCOUT_ROUTE_ROLES), frozenset(V7_LLM_ROUTE_ROLES)}
        and all(
            isinstance(value, dict) and isinstance(value.get("route_version_hash"), str)
            for value in versions.values()
        )
    )


def _finalize_run(db: Session, run: AgentRun, now: datetime) -> None:
    if run.dag_version == V7_DAG_VERSION:
        if run.state == "CREATED":
            run.state = "RUNNING"
            run.started_at = run.started_at or now
        return
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
    if core.state == "SUCCEEDED" and core.output_json:
        core_output = json.loads(core.output_json)
        run.shadow_assessment = core_output.get("shadow_assessment")
    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run.id))
    if core.state != "SUCCEEDED" or any(
        stage.state in {"FAILED", "TIMED_OUT", "INVALID_OUTPUT"} for stage in stages
    ):
        run.state = "FAILED"
        run.error_code = core.error_code or "AGENT_STAGE_FAILED"
    else:
        run.state = (
            "PARTIAL"
            if any(stage.state not in {"SUCCEEDED", "NOT_APPLICABLE"} for stage in stages)
            or bundle is None
            or bundle.state != "VERIFIED"
            else "SUCCEEDED"
        )
    run.completed_at = now
    if run.purpose == "TRADING_ADVISORY":
        try:
            from app.position_agent_fusion import finalize_position_advisory

            finalize_position_advisory(
                db,
                run=run,
                settings=get_settings(),
                now=now,
            )
        except Exception:
            logger.exception("POSITION Agent advisory fusion failed run=%s", run.id)
            run.fusion_state = "FAILED_SAFE"
            run.fusion_reason_code = "FUSION_INTERNAL_ERROR"


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
            run = db.get(AgentRun, stage.run_id)
            structured_recovery = bool(
                run is not None
                and terminalize_recovered_decision_stage(
                    db,
                    stage=stage,
                    run=run,
                    now=now,
                )
            )
            if not structured_recovery:
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
        if _is_v7_upstream_run(run):
            if stage.role not in materializable_roles(run.dag_version):
                stage.state = "FAILED"
                stage.error_code = "AGENT_ROLE_NOT_ALLOWED_FOR_DAG"
                stage.completed_at = now
                _finalize_run(db, run, now)
                db.commit()
                return None
            if stage.role not in executable_roles(run.dag_version):
                continue
        if run.dag_version == V7_DAG_VERSION and stage.role in DECISION_AGENT_ROLES:
            context = db.scalar(
                select(DecisionContext).where(DecisionContext.run_id == run.id)
            )
            if context is None:
                continue
        dependencies = _dependencies(db, stage)
        if stage.role == ENTRY_ARBITER_ROLE:
            try:
                validate_entry_arbiter_stage(
                    db,
                    stage=stage,
                    run=run,
                    now=now,
                    lock_inputs=True,
                )
            except EntryArbiterError as exc:
                stage.state = exc.failure_state
                stage.output_json = None
                stage.output_hash = None
                stage.error_code = exc.code
                stage.completed_at = now
                _finalize_run(db, run, now)
                db.commit()
                return None
            if len(dependencies) != 3 or any(
                item.state not in TERMINAL for item in dependencies
            ):
                stage.state = "CONFLICTED"
                stage.output_json = None
                stage.output_hash = None
                stage.error_code = "ENTRY_ARBITER_DEPENDENCY_INVALID"
                stage.completed_at = now
                _finalize_run(db, run, now)
                db.commit()
                return None
        else:
            dependency_failed = any(
                item.state in {"FAILED", "TIMED_OUT", "INVALID_OUTPUT"}
                for item in dependencies
            )
            if dependency_failed:
                may_reduce_v4_result = uses_v2_contract(run.dag_version) and stage.role in {
                    "EVIDENCE_CANDIDATE_AUDITOR",
                    "CORE",
                }
                if not may_reduce_v4_result:
                    stage.state = "FAILED"
                    stage.error_code = "AGENT_DEPENDENCY_FAILED"
                    stage.completed_at = now
                    _finalize_run(db, run, now)
                    db.commit()
                    return None
                if any(item.state not in TERMINAL for item in dependencies):
                    continue
            elif any(item.state not in DEPENDENCY_OK for item in dependencies):
                continue
        if run.dag_version == V7_DAG_VERSION and stage.role in SCOUT_ROUTE_ROLES:
            expected_hash = _v7_scout_role_input_hash(db, run=run, stage=stage)
            if stage.attempt_count == 0:
                stage.input_hash = expected_hash
            elif stage.input_hash != expected_hash:
                stage.state = "FAILED"
                stage.error_code = "AGENT_STAGE_INPUT_HASH_MISMATCH"
                stage.completed_at = now
                _finalize_run(db, run, now)
                db.commit()
                return None
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
        db.get(LlmProviderProfile, fallback_model.provider_profile_id) if fallback_model else None
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
        or (
            "web_search_enabled" in versions
            and route.web_search_enabled != versions.get("web_search_enabled")
        )
        or (fallback_model.id if fallback_model else None) != versions.get("fallback_model_id")
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
    position: Position | dict[str, object] | None,
    market_context: MarketContextSnapshot | None = None,
    v7_role: str | None = None,
) -> tuple[dict[str, object], list[str]]:
    position_ref = (
        str(position.get("position_id"))
        if isinstance(position, dict) and position.get("position_id")
        else position.id
        if isinstance(position, Position)
        else None
    )
    input_refs = [
        snapshot.id,
        bundle.id,
        *([indicator.id] if indicator else []),
        *([position_ref] if position_ref else []),
        *([market_context.id] if market_context else []),
    ]
    allowed_evidence_refs = [str(item) for item in json.loads(bundle.evidence_ids_json)]
    role_input: dict[str, object] = {
        "market": run.market,
        "symbol": run.symbol,
        "analysis_context": run.analysis_context,
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
                "drawdown_from_high_pct": _decimal_text(indicator.drawdown_from_high_pct),
                "spread_pct": _decimal_text(indicator.spread_pct),
                "price_vs_vwap_pct": _decimal_text(indicator.price_vs_vwap_pct),
                "sma5_slope_pct": _decimal_text(indicator.sma5_slope_pct),
                "relative_volume_5": _decimal_text(indicator.relative_volume_5),
                "realized_volatility_pct": _decimal_text(indicator.realized_volatility_pct),
                "minute_bar_count": indicator.minute_bar_count,
            }
            if indicator
            else None
        ),
        "position": (
            {
                "ref": position_ref,
                "quantity": position.get("quantity"),
                "average_price": position.get("average_price"),
                "state": position.get("state"),
                "version": position.get("version"),
            }
            if isinstance(position, dict)
            else {
                "ref": position.id,
                "quantity": position.quantity,
                "average_price": _decimal_text(position.average_price),
                "state": position.state,
                "version": position.version,
            }
            if position
            else None
        ),
        "server_input_policy_version": run.server_input_policy_version,
        "market_context_snapshot": (
            json.loads(market_context.payload_json) if market_context else None
        ),
        "allowed_input_refs": input_refs,
        "allowed_evidence_refs": allowed_evidence_refs,
    }
    if v7_role is not None:
        scoped_refs = [snapshot.id, bundle.id]
        if v7_role == "TECHNICAL_SCOUT" and indicator is not None:
            scoped_refs.append(indicator.id)
        elif v7_role == "MARKET_SECTOR_SCOUT" and market_context is not None:
            scoped_refs.append(market_context.id)
        elif v7_role == "POSITION_RISK_SCOUT" and position_ref is not None:
            scoped_refs.append(position_ref)
        role_input["allowed_input_refs"] = scoped_refs
        if v7_role != "TECHNICAL_SCOUT":
            role_input.pop("indicator_snapshot")
        if v7_role != "MARKET_SECTOR_SCOUT":
            role_input.pop("market_context_snapshot")
        if v7_role not in {"TECHNICAL_SCOUT", "POSITION_RISK_SCOUT"}:
            role_input.pop("position")
        input_refs = scoped_refs
    return role_input, input_refs


def _v7_scout_role_input_material(
    db: Session, *, run: AgentRun, stage: AgentStageRun
) -> dict[str, object]:
    if run.dag_version != V7_DAG_VERSION or stage.role not in SCOUT_ROUTE_ROLES:
        raise AgentRuntimeError("AGENT_ROLE_NOT_ALLOWED_FOR_DAG")
    decision_input = db.scalar(
        select(DecisionInputSnapshot).where(
            DecisionInputSnapshot.user_id == run.owner_id,
            DecisionInputSnapshot.market_snapshot_id == run.market_snapshot_id,
            DecisionInputSnapshot.input_hash == run.input_hash,
            DecisionInputSnapshot.schema_version == "scout-input-v2",
        )
    )
    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run.id))
    if decision_input is None or bundle is None:
        raise AgentRuntimeError("AGENT_STAGE_INPUT_PROVENANCE_MISSING")
    try:
        input_payload = json.loads(decision_input.input_json)
        versions = json.loads(run.route_versions_json)
    except (TypeError, ValueError) as exc:
        raise AgentRuntimeError("AGENT_STAGE_INPUT_PROVENANCE_INVALID") from exc
    route_version = versions.get(stage.role) if isinstance(versions, dict) else None
    if not isinstance(input_payload, dict) or not isinstance(route_version, dict):
        raise AgentRuntimeError("AGENT_STAGE_INPUT_PROVENANCE_INVALID")
    if (
        input_payload.get("schema_version") != "scout-input-v2"
        or _canonical(input_payload) != decision_input.input_json
        or _hash(input_payload) != decision_input.input_hash
    ):
        raise AgentRuntimeError("AGENT_STAGE_INPUT_PROVENANCE_INVALID")
    if stage.route_id != route_version.get("route_id") or not isinstance(
        route_version.get("route_version_hash"), str
    ):
        raise AgentRuntimeError("AGENT_ROUTE_SNAPSHOT_MISMATCH")
    indicator = input_payload.get("indicator_provenance")
    market_context = input_payload.get("market_context_provenance")
    position = json.loads(run.position_snapshot_json) if run.position_snapshot_json else None
    return {
        "schema_version": "scout-role-input-v1",
        "role": stage.role,
        "scout_input_snapshot_id": decision_input.id,
        "scout_input_hash": decision_input.input_hash,
        "evidence_bundle_id": bundle.id,
        "evidence_bundle_hash": bundle.bundle_hash,
        "route_id": stage.route_id,
        "route_version_hash": route_version["route_version_hash"],
        "input_contract_version": "agent-runtime-input-v1",
        "indicator_provenance": indicator if stage.role == "TECHNICAL_SCOUT" else None,
        "market_context_provenance": (
            market_context if stage.role == "MARKET_SECTOR_SCOUT" else None
        ),
        "position_provenance": (
            {
                "position_snapshot_hash": run.position_snapshot_hash,
                "position_snapshot": position,
            }
            if stage.role == "POSITION_RISK_SCOUT"
            else None
        ),
    }


def _v7_scout_role_input_hash(db: Session, *, run: AgentRun, stage: AgentStageRun) -> str:
    return _hash(_v7_scout_role_input_material(db, run=run, stage=stage))


def _execute_stage(db: Session, stage: AgentStageRun, run: AgentRun, now: datetime) -> None:
    if _is_v7_upstream_run(run) and stage.role not in allowed_roles(run.dag_version):
        raise AgentRuntimeError("AGENT_ROLE_NOT_ALLOWED_FOR_DAG")
    snapshot = db.get(MarketSnapshot, run.market_snapshot_id)
    if snapshot is None:
        raise AgentRuntimeError("AGENT_MARKET_SNAPSHOT_NOT_FOUND")
    if stage.role == "INTEL_COLLECTOR":
        settings = get_settings()
        evidence_ids: list[str] = []
        source_results: dict[str, dict[str, object]] = {}
        company_name: str | None = None
        if settings.dart_enabled:
            collection = collect_dart_disclosures(
                settings,
                symbol=run.symbol,
                now=now,
            )
            for disclosure in collection.disclosures:
                facts = disclosure.facts()
                record = {
                    "source_policy_version": DART_SOURCE_POLICY_VERSION,
                    "receipt_number": disclosure.receipt_number,
                    "facts": facts,
                }
                evidence = EvidenceItem(
                    run_id=run.id,
                    market=run.market,
                    symbol=run.symbol,
                    source_type="DART_DISCLOSURE",
                    source_tier="PRIMARY",
                    source_name="OPENDART",
                    source_url=disclosure.source_url,
                    title=disclosure.report_name or disclosure.corporation_name,
                    facts_json=_canonical(facts),
                    content_hash=_hash(record),
                    extraction_method="RULE",
                    published_at=receipt_date_as_utc(disclosure.receipt_date),
                    event_at=receipt_date_as_utc(disclosure.receipt_date),
                    received_at=now,
                )
                db.add(evidence)
                db.flush()
                evidence_ids.append(evidence.id)
                if disclosure.corporation_name:
                    company_name = disclosure.corporation_name
            source_results["OPENDART"] = {
                "source_policy_version": DART_SOURCE_POLICY_VERSION,
                "query_start_date": collection.start_date,
                "query_end_date": collection.end_date,
                "pages_fetched": collection.pages_fetched,
                "evidence_count": len(collection.disclosures),
            }
        if settings.krx_enabled:
            collection = collect_krx_daily_market(
                settings,
                symbol=run.symbol,
                now=now,
            )
            if collection.item is not None:
                item = collection.item
                company_name = item.name or company_name
                facts = item.facts()
                record = {
                    "source_policy_version": KRX_SOURCE_POLICY_VERSION,
                    "base_date": item.base_date,
                    "symbol": item.symbol,
                    "facts": facts,
                }
                evidence = EvidenceItem(
                    run_id=run.id,
                    market=run.market,
                    symbol=run.symbol,
                    source_type="KRX_DAILY_MARKET",
                    source_tier="PRIMARY",
                    source_name="KRX_OPEN_API",
                    source_url=item.source_url,
                    title=f"{item.name} {item.market_name} {item.base_date} 일별매매정보",
                    facts_json=_canonical(facts),
                    content_hash=_hash(record),
                    extraction_method="RULE",
                    published_at=base_date_as_utc(item.base_date),
                    event_at=base_date_as_utc(item.base_date),
                    received_at=now,
                )
                db.add(evidence)
                db.flush()
                evidence_ids.append(evidence.id)
            source_results["KRX"] = {
                "source_policy_version": KRX_SOURCE_POLICY_VERSION,
                "dates_queried": list(collection.dates_queried),
                "requests_made": collection.requests_made,
                "evidence_count": int(collection.item is not None),
            }
        if settings.naver_news_enabled:
            collection = collect_naver_news(
                settings,
                symbol=run.symbol,
                company_name=company_name,
                now=now,
            )
            fresh_ids: list[str] = []
            stale_ids: list[str] = []
            for item in collection.items:
                facts = item.facts()
                record = {
                    "source_policy_version": NAVER_NEWS_SOURCE_POLICY_VERSION,
                    "source_url": item.source_url,
                    "published_at": item.published_at.isoformat(),
                    "facts": facts,
                }
                evidence = EvidenceItem(
                    run_id=run.id,
                    market=run.market,
                    symbol=run.symbol,
                    source_type="NEWS",
                    source_tier="SECONDARY",
                    source_name="NAVER_API_HUB_NEWS",
                    source_url=item.source_url,
                    title=item.title,
                    facts_json=_canonical(facts),
                    content_hash=_hash(record),
                    extraction_method="RULE",
                    published_at=item.published_at,
                    event_at=item.published_at,
                    received_at=now,
                )
                db.add(evidence)
                db.flush()
                evidence_ids.append(evidence.id)
                (stale_ids if item.stale else fresh_ids).append(evidence.id)
            source_results["NAVER_NEWS"] = {
                "source_policy_version": NAVER_NEWS_SOURCE_POLICY_VERSION,
                "query_identity": collection.query_identity,
                "returned_count": collection.returned_count,
                "evidence_count": len(fresh_ids),
                "stale_count": len(stale_ids),
                "irrelevant_count": collection.irrelevant_count,
                "unsafe_url_count": collection.unsafe_url_count,
                "cache_hit": collection.cache_hit,
                "fresh_evidence_ids": fresh_ids,
                "stale_evidence_ids": stale_ids,
            }
        if source_results:
            source_mode = (
                "MULTI_OFFICIAL"
                if len(source_results) > 1
                else "OPENDART_PRIMARY"
                if "OPENDART" in source_results
                else "KRX_DAILY_PRIMARY"
                if "KRX" in source_results
                else "NAVER_NEWS_SECONDARY"
            )
            _complete_stage(
                stage,
                state="SUCCEEDED",
                output={
                    "schema_version": "intel-official-primary-v2",
                    "status": "SUCCEEDED",
                    "source_mode": source_mode,
                    "source_policy_version": (
                        next(iter(source_results.values()))["source_policy_version"]
                        if len(source_results) == 1
                        else EVIDENCE_POLICY_VERSION
                    ),
                    "source_results": source_results,
                    "evidence_count": len(evidence_ids),
                    "evidence_ids": evidence_ids,
                },
                now=now,
            )
            return
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
        intel = db.scalar(
            select(AgentStageRun).where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role == "INTEL_COLLECTOR",
            )
        )
        intel_output = json.loads(intel.output_json or "{}") if intel else {}
        candidate_items = list(
            db.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.run_id == run.id,
                    EvidenceItem.source_type.in_(("DART_DISCLOSURE", "KRX_DAILY_MARKET", "NEWS")),
                    EvidenceItem.source_tier.in_(("PRIMARY", "SECONDARY")),
                )
                .order_by(EvidenceItem.created_at, EvidenceItem.id)
            )
        )
        source_results = intel_output.get("source_results", {})
        news_result = (
            source_results.get("NAVER_NEWS", {}) if isinstance(source_results, dict) else {}
        )
        stale_ids = set(news_result.get("stale_evidence_ids", []))
        verified_item_validity: list[dict[str, object]] = []
        freshness_snapshot_hash: str | None = None
        freshness_error = False
        if run.dag_version == V7_DAG_VERSION:
            decision_input = db.scalar(
                select(DecisionInputSnapshot).where(
                    DecisionInputSnapshot.user_id == run.owner_id,
                    DecisionInputSnapshot.input_hash == run.input_hash,
                    DecisionInputSnapshot.schema_version == "scout-input-v2",
                )
            )
            try:
                input_payload = json.loads(decision_input.input_json) if decision_input else {}
                configuration = input_payload["configuration_version"]
                values = configuration["values"]
                freshness_snapshot = {
                    "configuration_payload_hash": configuration["payload_hash"],
                    "dart_lookback_days": values["dart_lookback_days"],
                    "krx_lookback_days": values["krx_lookback_days"],
                    "naver_news_lookback_hours": values["naver_news_lookback_hours"],
                    "schema_version": "evidence-freshness-policy-v1",
                }
                freshness_snapshot_hash = _hash(freshness_snapshot)
                for item in candidate_items:
                    anchor: datetime | None
                    duration: timedelta | None
                    if item.source_type == "DART_DISCLOSURE" and item.source_tier == "PRIMARY":
                        anchor = item.event_at
                        duration = timedelta(days=int(values["dart_lookback_days"]))
                    elif item.source_type == "KRX_DAILY_MARKET" and item.source_tier == "PRIMARY":
                        anchor = item.event_at
                        duration = timedelta(days=int(values["krx_lookback_days"]))
                    elif item.source_type == "NEWS" and item.source_tier == "SECONDARY":
                        anchor = item.published_at
                        duration = timedelta(hours=int(values["naver_news_lookback_hours"]))
                    else:
                        anchor = None
                        duration = None
                    if anchor is None or duration is None:
                        stale_ids.add(item.id)
                        freshness_error = True
                        continue
                    item_valid_until = _aware(anchor) + duration
                    if item_valid_until <= now:
                        stale_ids.add(item.id)
                        continue
                    verified_item_validity.append(
                        {
                            "evidence_item_id": item.id,
                            "freshness_anchor": _aware(anchor).isoformat(),
                            "item_valid_until": item_valid_until.isoformat(),
                            "source_tier": item.source_tier,
                            "source_type": item.source_type,
                        }
                    )
                verified_item_validity.sort(key=lambda item: str(item["evidence_item_id"]))
            except (KeyError, TypeError, ValueError):
                freshness_error = True
                stale_ids.update(item.id for item in candidate_items)
        verified_items = [item for item in candidate_items if item.id not in stale_ids]
        evidence_ids = [item.id for item in verified_items]
        stale_evidence_ids = [item.id for item in candidate_items if item.id in stale_ids]
        reason_codes: list[str] = []
        if any(item.source_type == "DART_DISCLOSURE" for item in verified_items):
            reason_codes.append("DART_PRIMARY_EVIDENCE_VERIFIED")
        elif "OPENDART" in source_results or intel_output.get("source_mode") == "OPENDART_PRIMARY":
            reason_codes.append("DART_QUERY_COMPLETE_NO_MATCHES")
        if any(item.source_type == "KRX_DAILY_MARKET" for item in verified_items):
            reason_codes.append("KRX_PRIMARY_EVIDENCE_VERIFIED")
        elif "KRX" in source_results:
            reason_codes.append("KRX_QUERY_COMPLETE_NO_MATCH")
        fresh_news = [item for item in verified_items if item.source_type == "NEWS"]
        if fresh_news:
            reason_codes.append("NAVER_NEWS_SECONDARY_EVIDENCE_VERIFIED")
        elif "NAVER_NEWS" in source_results:
            if int(news_result.get("stale_count", 0)) > 0:
                reason_codes.append("NAVER_NEWS_STALE_ONLY")
            elif int(news_result.get("returned_count", 0)) == 0:
                reason_codes.append("NAVER_NEWS_QUERY_COMPLETE_NO_MATCHES")
            else:
                reason_codes.append("NAVER_NEWS_NO_RELEVANT_RESULTS")
        if not reason_codes:
            reason_codes.append("NO_EXTERNAL_EVIDENCE_FIXTURE")
        if run.dag_version == V7_DAG_VERSION and freshness_error:
            reason_codes.append("EVIDENCE_FRESHNESS_PROVENANCE_INVALID")
        if run.dag_version == V7_DAG_VERSION and not verified_items:
            reason_codes.append("EVIDENCE_USABLE_ITEMS_EMPTY")
        reason_codes = sorted(set(reason_codes))
        full_coverage = (
            isinstance(source_results, dict)
            and {"OPENDART", "KRX", "NAVER_NEWS"}.issubset(source_results)
            and any(item.source_type == "KRX_DAILY_MARKET" for item in verified_items)
        )
        bundle_state = "VERIFIED" if full_coverage else "PARTIAL"
        record = {
            "schema_version": "evidence-bundle-v1",
            "market": run.market,
            "symbol": run.symbol,
            "market_snapshot_id": snapshot.id,
            "policy_version": EVIDENCE_POLICY_VERSION,
            "state": bundle_state,
            "evidence_ids": evidence_ids,
            "stale_evidence_ids": stale_evidence_ids,
            "reason_codes": reason_codes,
        }
        bundle = EvidenceBundle(
            owner_id=run.owner_id,
            run_id=run.id,
            market=run.market,
            symbol=run.symbol,
            as_of=now,
            policy_version=EVIDENCE_POLICY_VERSION,
            state=bundle_state,
            evidence_ids_json=_canonical(evidence_ids),
            contradiction_groups_json="[]",
            stale_evidence_ids_json=_canonical(stale_evidence_ids),
            reason_codes_json=_canonical(reason_codes),
            bundle_hash=_hash(record),
        )
        db.add(bundle)
        db.flush()
        if run.dag_version == V7_DAG_VERSION:
            verifier_state = "SUCCEEDED" if verified_items and not freshness_error else "FAILED"
            verifier_valid_until = (
                min(
                    _aware(run.valid_until),
                    *(
                        datetime.fromisoformat(str(item["item_valid_until"]))
                        for item in verified_item_validity
                    ),
                )
                if verified_item_validity
                else _aware(now)
            )
            _complete_stage(
                stage,
                state=verifier_state,
                output={
                    "schema_version": "evidence-verifier-v2",
                    "stage_run_id": stage.id,
                    "role": "EVIDENCE_VERIFIER",
                    "status": verifier_state,
                    "evidence_bundle_id": bundle.id,
                    "evidence_bundle_hash": bundle.bundle_hash,
                    "observed_at": now.isoformat(),
                    "valid_until": verifier_valid_until.isoformat(),
                    "evidence_policy_version": EVIDENCE_POLICY_VERSION,
                    "freshness_policy_version": "evidence-freshness-policy-v1",
                    "freshness_policy_snapshot_hash": freshness_snapshot_hash,
                    "verified_item_validity": verified_item_validity,
                    "reason_codes": reason_codes,
                },
                now=now,
            )
        else:
            _complete_stage(
                stage,
                state="SUCCEEDED",
                output={**record, "bundle_id": bundle.id, "bundle_hash": bundle.bundle_hash},
                now=now,
            )
        return

    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run.id))
    if bundle is None:
        if uses_v2_contract(run.dag_version) and stage.role == "CORE":
            core = AgentCoreOutputV2(
                shadow_assessment="UNKNOWN",
                confidence=0,
                risk_level="HIGH",
                reason_codes=["AGENT_RUNTIME_SHADOW_ONLY", "REQUIRED_SCOUT_INCOMPLETE"],
                incomplete_roles=list(ROUTE_ROLES[:-1]),
            )
            _complete_stage(
                stage,
                state="SUCCEEDED",
                output=core.model_dump(mode="json"),
                now=now,
            )
            return
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
            "UNRATED_SOURCE_CANDIDATES_PRESENT" if candidates else "NO_PROVIDER_SOURCE_CANDIDATES"
        ]
        source_counts: dict[str, int] = {}
        for candidate in candidates:
            source_counts[candidate.source_type] = source_counts.get(candidate.source_type, 0) + 1
        candidate_ids = sorted(candidate.id for candidate in candidates)
        if run.dag_version == V7_DAG_VERSION:
            output = {
                "schema_version": "evidence-candidate-audit-v2",
                "stage_run_id": stage.id,
                "role": "EVIDENCE_CANDIDATE_AUDITOR",
                "status": "SUCCEEDED",
                "observed_at": now.isoformat(),
                "evidence_bundle_id": bundle.id,
                "evidence_bundle_hash": bundle.bundle_hash,
                "candidate_ids": candidate_ids,
                "candidate_count": len(candidate_ids),
                "provider_counts": {
                    provider: provider_counts[provider] for provider in sorted(provider_counts)
                },
                "source_counts": {
                    source: source_counts[source] for source in sorted(source_counts)
                },
                "reason_codes": sorted(set(reason_codes)),
                "audit_policy_version": "evidence-candidate-audit-policy-v1",
                "candidate_set_hash": _hash(candidate_ids),
            }
        else:
            output = {
                "schema_version": "evidence-candidate-audit-v1",
                "status": "SUCCEEDED",
                "candidate_count": len(candidates),
                "candidate_ids": candidate_ids,
                "provider_counts": {
                    provider: provider_counts[provider] for provider in sorted(provider_counts)
                },
                "reason_codes": reason_codes,
                "evidence_bundle_ref": bundle.id,
                "evidence_bundle_hash": bundle.bundle_hash,
                "bundle_mutated": False,
            }
        _complete_stage(stage, state="SUCCEEDED", output=output, now=now)
        return
    binding = _binding(db, run, stage)
    if stage.role in ROUTE_ROLES[:-1]:
        if run.dag_version == V7_DAG_VERSION and stage.input_hash != _v7_scout_role_input_hash(
            db, run=run, stage=stage
        ):
            raise AgentRuntimeError("AGENT_STAGE_INPUT_HASH_MISMATCH")
        indicator = db.scalar(
            select(IndicatorSnapshot).where(IndicatorSnapshot.market_snapshot_id == snapshot.id)
        )
        market_context: MarketContextSnapshot | None = None
        market_context_payload: dict[str, object] | None = None
        market_context_conflicted = False
        if uses_server_inputs(run.dag_version) and run.market_context_snapshot_id:
            market_context = db.get(MarketContextSnapshot, run.market_context_snapshot_id)
            if market_context is None:
                market_context_conflicted = True
            else:
                try:
                    payload = json.loads(market_context.payload_json)
                except (TypeError, ValueError):
                    payload = None
                if (
                    not isinstance(payload, dict)
                    or market_context.payload_hash != run.market_context_snapshot_hash
                    or _hash(payload) != run.market_context_snapshot_hash
                ):
                    market_context_conflicted = True
                    market_context = None
                else:
                    market_context_payload = payload
        if uses_v2_contract(run.dag_version):
            if not run.position_snapshot_json or not run.position_snapshot_hash:
                _, input_refs = _scout_role_input(
                    run=run,
                    snapshot=snapshot,
                    bundle=bundle,
                    indicator=indicator,
                    position=None,
                    v7_role=stage.role if run.dag_version == V7_DAG_VERSION else None,
                )
                assessment = AgentAssessmentV2(
                    stage_run_id=stage.id,
                    role=stage.role,
                    symbol=run.symbol,
                    status="INSUFFICIENT_DATA",
                    stance="UNKNOWN",
                    confidence=0,
                    entry_score=None,
                    exit_risk_score=None,
                    reason_codes=["INPUT_DATA_MISSING"],
                    uncertainty=1,
                    evidence_refs=[],
                    input_refs=input_refs,
                    observed_at=now,
                    valid_until=run.valid_until,
                )
                _complete_stage(
                    stage,
                    state=assessment.status,
                    output=assessment.model_dump(mode="json"),
                    now=now,
                )
                return
            try:
                frozen_position = json.loads(run.position_snapshot_json)
            except (TypeError, ValueError):
                frozen_position = {}
            if _hash(frozen_position) != run.position_snapshot_hash:
                _, input_refs = _scout_role_input(
                    run=run,
                    snapshot=snapshot,
                    bundle=bundle,
                    indicator=indicator,
                    position=None,
                    v7_role=stage.role if run.dag_version == V7_DAG_VERSION else None,
                )
                reason_codes = ["INPUT_DATA_CONFLICTED"]
                if stage.role == "POSITION_RISK_SCOUT":
                    reason_codes.append("POSITION_DATA_CONFLICTED")
                assessment = AgentAssessmentV2(
                    stage_run_id=stage.id,
                    role=stage.role,
                    symbol=run.symbol,
                    status="CONFLICTED",
                    stance="UNKNOWN",
                    confidence=0,
                    entry_score=None,
                    exit_risk_score=None,
                    reason_codes=reason_codes,
                    uncertainty=1,
                    evidence_refs=[],
                    input_refs=input_refs,
                    observed_at=now,
                    valid_until=run.valid_until,
                )
                _complete_stage(
                    stage,
                    state=assessment.status,
                    output=assessment.model_dump(mode="json"),
                    now=now,
                )
                return
            position = frozen_position if frozen_position.get("marker") == "OPEN_POSITION" else None
        else:
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
            market_context=(market_context if stage.role == "MARKET_SECTOR_SCOUT" else None),
            v7_role=stage.role if run.dag_version == V7_DAG_VERSION else None,
        )
        role_material: dict[str, object] | None = None
        if run.dag_version == V7_DAG_VERSION:
            role_material = _v7_scout_role_input_material(db, run=run, stage=stage)
            role_input["scout_role_input"] = role_material
        if run.dag_version == V7_DAG_VERSION and stage.role == "TECHNICAL_SCOUT":
            assert role_material is not None
            frozen_indicator = role_material.get("indicator_provenance")
            decision_input = db.scalar(
                select(DecisionInputSnapshot).where(
                    DecisionInputSnapshot.user_id == run.owner_id,
                    DecisionInputSnapshot.input_hash == run.input_hash,
                    DecisionInputSnapshot.schema_version == "scout-input-v2",
                )
            )
            try:
                frozen_input = json.loads(decision_input.input_json) if decision_input else {}
                frozen_market = frozen_input["market_snapshot_provenance"]
            except (KeyError, TypeError, ValueError):
                frozen_market = None
            if (
                not isinstance(frozen_market, dict)
                or frozen_market.get("snapshot_id") != snapshot.id
                or frozen_market.get("payload_hash") != snapshot.payload_hash
                or frozen_market.get("source") != snapshot.source
            ):
                assessment_v2 = AgentAssessmentV2(
                    stage_run_id=stage.id,
                    role=stage.role,
                    symbol=run.symbol,
                    status="CONFLICTED",
                    stance="UNKNOWN",
                    confidence=0,
                    uncertainty=1,
                    reason_codes=["INPUT_DATA_CONFLICTED"],
                    evidence_refs=[],
                    input_refs=input_refs,
                    observed_at=now,
                    valid_until=run.valid_until,
                )
                _complete_stage(
                    stage,
                    state=assessment_v2.status,
                    output=assessment_v2.model_dump(mode="json"),
                    now=now,
                )
                return
            if indicator is None:
                assessment_v2 = AgentAssessmentV2(
                    stage_run_id=stage.id,
                    role=stage.role,
                    symbol=run.symbol,
                    status="INSUFFICIENT_DATA",
                    stance="UNKNOWN",
                    confidence=0,
                    uncertainty=1,
                    reason_codes=["INDICATOR_DATA_MISSING"],
                    evidence_refs=[],
                    input_refs=input_refs,
                    observed_at=now,
                    valid_until=run.valid_until,
                )
                _complete_stage(
                    stage,
                    state=assessment_v2.status,
                    output=assessment_v2.model_dump(mode="json"),
                    now=now,
                )
                return
            current_indicator_hash = input_digest(
                canonical_input_json(_indicator_payload(indicator))
            )
            if (
                not isinstance(frozen_indicator, dict)
                or frozen_indicator.get("snapshot_id") != indicator.id
                or frozen_indicator.get("calculator_version") != indicator.calculator_version
                or frozen_indicator.get("payload_hash") != current_indicator_hash
            ):
                assessment_v2 = AgentAssessmentV2(
                    stage_run_id=stage.id,
                    role=stage.role,
                    symbol=run.symbol,
                    status="CONFLICTED",
                    stance="UNKNOWN",
                    confidence=0,
                    uncertainty=1,
                    reason_codes=["INPUT_DATA_CONFLICTED"],
                    evidence_refs=[],
                    input_refs=input_refs,
                    observed_at=now,
                    valid_until=run.valid_until,
                )
                _complete_stage(
                    stage,
                    state=assessment_v2.status,
                    output=assessment_v2.model_dump(mode="json"),
                    now=now,
                )
                return
        if uses_server_inputs(run.dag_version) and stage.role == "MARKET_SECTOR_SCOUT":
            if run.dag_version == V7_DAG_VERSION:
                assert role_material is not None
                frozen_context = role_material.get("market_context_provenance")
                if market_context is not None and (
                    not isinstance(frozen_context, dict)
                    or frozen_context.get("snapshot_id") != market_context.id
                    or frozen_context.get("payload_hash") != market_context.payload_hash
                    or frozen_context.get("quality") != market_context.quality
                    or frozen_context.get("valid_until")
                    != _aware(market_context.valid_until).isoformat()
                    or market_context.quality != "NORMAL"
                    or _aware(market_context.valid_until) <= now
                ):
                    market_context_conflicted = True
            if market_context_conflicted:
                assessment_v2 = AgentAssessmentV2(
                    stage_run_id=stage.id,
                    role=stage.role,
                    symbol=run.symbol,
                    status="CONFLICTED",
                    stance="UNKNOWN",
                    confidence=0,
                    uncertainty=1,
                    reason_codes=["INPUT_DATA_CONFLICTED"],
                    evidence_refs=[],
                    input_refs=input_refs,
                    observed_at=now,
                    valid_until=run.valid_until,
                )
                _complete_stage(
                    stage,
                    state=assessment_v2.status,
                    output=assessment_v2.model_dump(mode="json"),
                    now=now,
                )
                return
            if market_context is None:
                assessment_v2 = _assessment_v2(
                    stage.role,
                    stage_run_id=stage.id,
                    symbol=run.symbol,
                    input_refs=input_refs,
                    indicator=indicator,
                    snapshot=snapshot,
                    position=position,
                    market_context=None,
                    server_input_policy_version=run.server_input_policy_version,
                    analysis_context=run.analysis_context or "ENTRY",
                    observed_at=now,
                    valid_until=run.valid_until,
                )
                _complete_stage(
                    stage,
                    state=assessment_v2.status,
                    output=assessment_v2.model_dump(mode="json"),
                    now=now,
                )
                return
        if (
            uses_v2_contract(run.dag_version)
            and stage.role == "POSITION_RISK_SCOUT"
            and run.analysis_context == "ENTRY"
        ):
            assessment_v2 = _assessment_v2(
                stage.role,
                stage_run_id=stage.id,
                symbol=run.symbol,
                input_refs=input_refs,
                indicator=indicator,
                snapshot=snapshot,
                position=None,
                market_context=market_context_payload,
                server_input_policy_version=run.server_input_policy_version,
                analysis_context="ENTRY",
                observed_at=now,
                valid_until=run.valid_until,
            )
            _complete_stage(
                stage,
                state=assessment_v2.status,
                output=assessment_v2.model_dump(mode="json"),
                now=now,
            )
            return
        if (
            uses_server_inputs(run.dag_version)
            and stage.role == "POSITION_RISK_SCOUT"
            and isinstance(position, dict)
            and (
                not isinstance(position.get("freshness"), dict)
                or position["freshness"].get("status") != "FRESH"
            )
        ):
            assessment_v2 = _assessment_v2(
                stage.role,
                stage_run_id=stage.id,
                symbol=run.symbol,
                input_refs=input_refs,
                indicator=indicator,
                snapshot=snapshot,
                position=position,
                market_context=market_context_payload,
                server_input_policy_version=run.server_input_policy_version,
                analysis_context=run.analysis_context or "POSITION",
                observed_at=now,
                valid_until=run.valid_until,
            )
            _complete_stage(
                stage,
                state=assessment_v2.status,
                output=assessment_v2.model_dump(mode="json"),
                now=now,
            )
            return
        outcome = _invoke_model(
            db,
            stage=stage,
            binding=binding,
            role_input=role_input,
            now=now,
        )
        if outcome.provider.adapter_type == "MOCK":
            assessment = (
                _assessment_v2(
                    stage.role,
                    stage_run_id=stage.id,
                    symbol=run.symbol,
                    input_refs=input_refs,
                    indicator=indicator,
                    snapshot=snapshot,
                    position=position,
                    market_context=market_context_payload,
                    server_input_policy_version=run.server_input_policy_version,
                    analysis_context=run.analysis_context or "ENTRY",
                    observed_at=now,
                    valid_until=run.valid_until,
                )
                if uses_v2_contract(run.dag_version)
                else _assessment(
                    stage.role,
                    stage_run_id=stage.id,
                    symbol=run.symbol,
                    input_refs=input_refs,
                    indicator=indicator,
                    snapshot=snapshot,
                    position=position if isinstance(position, Position) else None,
                    observed_at=now,
                    valid_until=run.valid_until,
                )
            )
        else:
            model_output = AgentScoutModelOutput.model_validate(outcome.output_json)
            assessment = (
                AgentAssessmentV2(
                    stage_run_id=stage.id,
                    role=stage.role,
                    symbol=run.symbol,
                    input_refs=input_refs,
                    observed_at=now,
                    valid_until=run.valid_until,
                    **model_output.model_dump(),
                )
                if uses_v2_contract(run.dag_version)
                else AgentAssessment(
                    stage_run_id=stage.id,
                    role=stage.role,
                    symbol=run.symbol,
                    input_refs=input_refs,
                    observed_at=now,
                    valid_until=run.valid_until,
                    **model_output.model_dump(),
                )
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
    assessment_contract = (
        AgentAssessmentV2 if uses_v2_contract(run.dag_version) else AgentAssessment
    )
    assessments: dict[str, AgentAssessmentV2 | AgentAssessment] = {}
    invalid_assessment_roles: list[str] = []
    assessment_payloads: dict[str, object] = {}
    for item in scout_stages:
        try:
            payload = json.loads(item.output_json) if item.output_json else None
            if not isinstance(payload, dict):
                raise TypeError("Agent assessment output is unavailable")
            assessments[item.role] = assessment_contract.model_validate(payload)
            assessment_payloads[item.role] = payload
        except (TypeError, ValueError):
            if not uses_v2_contract(run.dag_version):
                raise
            invalid_assessment_roles.append(item.role)
            assessment_payloads[item.role] = {
                "status": item.state,
                "error_code": item.error_code or "AGENT_ASSESSMENT_OUTPUT_UNAVAILABLE",
            }
    incomplete = sorted(
        set(invalid_assessment_roles)
        | {
            role
            for role, assessment in assessments.items()
            if assessment.status
            not in (
                {"SUCCEEDED", "NOT_APPLICABLE"}
                if uses_v2_contract(run.dag_version) and run.analysis_context == "ENTRY"
                else {"SUCCEEDED"}
            )
        }
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
    if uses_v2_contract(run.dag_version) and (
        invalid_assessment_roles or (run.dag_version == DAG_VERSION and incomplete)
    ):
        core = AgentCoreOutputV2(
            shadow_assessment="UNKNOWN",
            confidence=0,
            risk_level="HIGH",
            reason_codes=["AGENT_RUNTIME_SHADOW_ONLY", "REQUIRED_SCOUT_INCOMPLETE"],
            incomplete_roles=incomplete,
        )
        _complete_stage(stage, state="SUCCEEDED", output=core.model_dump(mode="json"), now=now)
        return

    outcome = _invoke_model(
        db,
        stage=stage,
        binding=binding,
        role_input={
            "market": run.market,
            "symbol": run.symbol,
            "analysis_context": run.analysis_context,
            "assessment_schema_version": (
                ASSESSMENT_SCHEMA_VERSION
                if uses_v2_contract(run.dag_version)
                else "agent-assessment-v1"
            ),
            "core_schema_version": (
                CORE_SCHEMA_VERSION if uses_v2_contract(run.dag_version) else "agent-core-v1"
            ),
            "score_policy_version": (
                SCORE_POLICY_VERSION if uses_v2_contract(run.dag_version) else None
            ),
            "market_snapshot_ref": snapshot.id,
            "evidence_bundle_ref": bundle.id,
            "assessments": assessment_payloads,
            "assessment_hashes": {item.role: item.output_hash for item in scout_stages},
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
    if uses_v2_contract(run.dag_version):
        core = (
            AgentCoreOutputV2(
                shadow_assessment="UNKNOWN" if incomplete else "NEUTRAL",
                confidence=0 if incomplete else 0.5,
                risk_level="HIGH" if incomplete else "MEDIUM",
                reason_codes=[
                    "AGENT_RUNTIME_SHADOW_ONLY",
                    "REQUIRED_SCOUT_INCOMPLETE" if incomplete else "DIAGNOSTIC_WAIT_ONLY",
                ],
                incomplete_roles=incomplete,
            )
            if outcome.provider.adapter_type == "MOCK"
            else AgentCoreOutputV2(
                **AgentCoreModelOutputV2.model_validate(outcome.output_json).model_dump()
            )
        )
    else:
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
        select(AgentStageRun).where(AgentStageRun.id == claim.stage_id).with_for_update()
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
                db.scalars(select(LlmInvocation).where(LlmInvocation.stage_run_id == stage.id))
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
    completed_role = stage.role
    completed_run_id = run.id
    _finalize_run(db, run, now)
    db.commit()
    if completed_role == "EVIDENCE_CANDIDATE_AUDITOR" and run.dag_version == V7_DAG_VERSION:
        try:
            reconcile_v7_upstream_contexts(db, now=now, run_id=completed_run_id, limit=1)
        except DecisionContextFreezeError:
            logger.exception("v7 DecisionContext reconciliation failed run=%s", completed_run_id)
    return True


def reconcile_v7_upstream_contexts(
    db: Session,
    *,
    now: datetime,
    run_id: str | None = None,
    limit: int = 10,
) -> int:
    statement = select(AgentRun).where(
        AgentRun.dag_version == V7_DAG_VERSION,
        AgentRun.purpose.in_(("DIAGNOSTIC", "TRADING")),
        AgentRun.analysis_context == "ENTRY",
        AgentRun.state.in_(("CREATED", "RUNNING")),
        AgentRun.valid_until > now,
    )
    if run_id is not None:
        statement = statement.where(AgentRun.id == run_id)
    runs = list(db.scalars(statement.order_by(AgentRun.created_at).limit(limit)))
    reconciled = 0
    for candidate in runs:
        existing_context = db.scalar(
            select(DecisionContext).where(DecisionContext.run_id == candidate.id)
        )
        stages = list(
            db.scalars(
                select(AgentStageRun)
                .where(AgentStageRun.run_id == candidate.id)
                .order_by(AgentStageRun.sequence)
            )
        )
        stage_roles = {stage.role for stage in stages}
        if not allowed_roles(V7_DAG_VERSION) <= stage_roles or not stage_roles <= materializable_roles(
            V7_DAG_VERSION
        ):
            continue
        by_role = {stage.role: stage for stage in stages}
        if (
            by_role["INTEL_COLLECTOR"].state != "SUCCEEDED"
            or by_role["EVIDENCE_VERIFIER"].state != "SUCCEEDED"
            or by_role["EVIDENCE_CANDIDATE_AUDITOR"].state != "SUCCEEDED"
            or any(by_role[role].state not in DEPENDENCY_OK for role in SCOUT_ROUTE_ROLES)
        ):
            continue
        freeze_decision_context(db, run_id=candidate.id, now=now)
        persisted = db.get(AgentRun, candidate.id)
        if persisted is not None:
            persisted.state = "RUNNING"
            persisted.completed_at = None
            db.commit()
        if existing_context is None:
            reconciled += 1
    return reconciled


def reconcile_v7_decision_stages(
    db: Session,
    *,
    now: datetime,
    run_id: str | None = None,
    limit: int = 10,
) -> int:
    statement = (
        select(AgentRun)
        .join(DecisionContext, DecisionContext.run_id == AgentRun.id)
        .where(
            AgentRun.dag_version == V7_DAG_VERSION,
            AgentRun.purpose.in_(("DIAGNOSTIC", "TRADING")),
            AgentRun.analysis_context == "ENTRY",
            AgentRun.state.in_(("CREATED", "RUNNING")),
            AgentRun.valid_until > now,
        )
    )
    if run_id is not None:
        statement = statement.where(AgentRun.id == run_id)
    run_ids = list(db.scalars(statement.order_by(AgentRun.created_at).limit(limit)))
    reconciled = 0
    for candidate in run_ids:
        try:
            before = set(
                db.scalars(
                    select(AgentStageRun.role).where(
                        AgentStageRun.run_id == candidate.id,
                        AgentStageRun.role.in_(FOUNDATION_DECISION_AGENT_ROLES),
                    )
                )
            )
            materialize_decision_agent_stages(db, run_id=candidate.id, now=now)
            if before != set(FOUNDATION_DECISION_AGENT_ROLES):
                reconciled += 1
        except DecisionAgentFoundationError as exc:
            if exc.code != "DECISION_AGENT_ROUTE_SET_INCOMPLETE":
                raise
    return reconciled


def reconcile_v7_arbiter_stages(
    db: Session,
    *,
    now: datetime,
    run_id: str | None = None,
    limit: int = 10,
) -> int:
    statement = (
        select(AgentRun)
        .join(DecisionContext, DecisionContext.run_id == AgentRun.id)
        .where(
            AgentRun.dag_version == V7_DAG_VERSION,
            AgentRun.purpose.in_(("DIAGNOSTIC", "TRADING")),
            AgentRun.analysis_context == "ENTRY",
            AgentRun.state.in_(("CREATED", "RUNNING")),
            AgentRun.valid_until > now,
        )
    )
    if run_id is not None:
        statement = statement.where(AgentRun.id == run_id)
    candidates = list(db.scalars(statement.order_by(AgentRun.created_at).limit(limit)))
    reconciled = 0
    for candidate in candidates:
        existing = db.scalar(
            select(AgentStageRun).where(
                AgentStageRun.run_id == candidate.id,
                AgentStageRun.role == ENTRY_ARBITER_ROLE,
            )
        )
        try:
            materialize_entry_arbiter_stage(db, run_id=candidate.id, now=now)
            if existing is None:
                reconciled += 1
        except EntryArbiterError:
            db.rollback()
    return reconciled


def process_agent_work_once(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
    finalization_evidence_loader: EvidenceLoader | None = None,
    finalization_validation_policy: ActivationValidationPolicy | None = None,
) -> bool:
    observed = now or datetime.now(UTC)
    claim = claim_next_stage(
        db,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        now=observed,
    )
    if claim is None:
        upstream = reconcile_v7_upstream_contexts(db, now=observed, limit=1)
        decisions = reconcile_v7_decision_stages(db, now=observed, limit=1)
        arbiters = reconcile_v7_arbiter_stages(db, now=observed, limit=1)
        diagnostic_closures = reconcile_v7_diagnostic_lifecycle(db, limit=1)
        finalizations = (
            reconcile_v7_entry_finalizations(
                db,
                evidence_loader=finalization_evidence_loader,
                validation_policy=finalization_validation_policy,
                limit=1,
            )
            if finalization_evidence_loader is not None
            else 0
        )
        return bool(
            upstream
            or decisions
            or arbiters
            or diagnostic_closures
            or finalizations
        )
    claimed_stage = db.get(AgentStageRun, claim.stage_id)
    if claimed_stage is not None and claimed_stage.role == ENTRY_ARBITER_ROLE:
        completed = execute_entry_arbiter_stage(
            db,
            claim_stage_id=claim.stage_id,
            fencing_token=claim.fencing_token,
            worker_id=worker_id,
        )
        if completed:
            reconcile_v7_diagnostic_lifecycle(
                db,
                run_id=claimed_stage.run_id,
                limit=1,
            )
            if finalization_evidence_loader is not None:
                reconcile_v7_entry_finalizations(
                    db,
                    evidence_loader=finalization_evidence_loader,
                    validation_policy=finalization_validation_policy,
                    run_id=claimed_stage.run_id,
                    limit=1,
                )
        return completed
    if claimed_stage is not None and claimed_stage.role in FOUNDATION_DECISION_AGENT_ROLES:
        return execute_decision_agent_stage(
            db,
            claim_stage_id=claim.stage_id,
            fencing_token=claim.fencing_token,
            worker_id=worker_id,
        )
    return execute_claimed_stage(db, claim=claim, worker_id=worker_id, now=datetime.now(UTC))


class AgentWorker:
    def __init__(
        self,
        settings: Settings,
        *,
        worker_id: str | None = None,
        finalization_evidence_loader: EvidenceLoader | None = None,
        finalization_validation_policy: ActivationValidationPolicy | None = None,
    ) -> None:
        self.settings = settings
        self.worker_id = worker_id or uuid7()
        self.finalization_evidence_loader = finalization_evidence_loader
        self.finalization_validation_policy = finalization_validation_policy
        self.stop_event = asyncio.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def _process_once(self) -> bool:
        with SessionLocal() as db:
            return process_agent_work_once(
                db,
                worker_id=self.worker_id,
                lease_seconds=self.settings.agent_worker_lease_seconds,
                finalization_evidence_loader=self.finalization_evidence_loader,
                finalization_validation_policy=self.finalization_validation_policy,
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
