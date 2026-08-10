from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.contracts import (
    AgentAssessment,
    AgentCoreModelOutput,
    AgentScoutModelOutput,
)
from app.config import get_settings
from app.llm.contracts import LlmRequest
from app.llm.discovery import get_template
from app.llm.registry import AdapterNotImplementedError, provider_registry
from app.llm.secrets import LlmSecretError, LlmSecretStore
from app.models import (
    AgentRun,
    AgentStageRun,
    EvidenceItem,
    IndicatorSnapshot,
    LlmInvocation,
    LlmModelProfile,
    LlmPromptProfile,
    LlmProviderProfile,
    LlmRoleRoute,
    MarketSnapshot,
    MarketStreamState,
    Position,
    User,
)

DAG_VERSION = "agent-dag-v2"
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
        "EVIDENCE_CANDIDATE_AUDITOR",
        65,
        (
            "TECHNICAL_SCOUT",
            "NEWS_DISCLOSURE_SCOUT",
            "MARKET_SECTOR_SCOUT",
            "POSITION_RISK_SCOUT",
        ),
    ),
    (
        "CORE",
        70,
        (
            "TECHNICAL_SCOUT",
            "NEWS_DISCLOSURE_SCOUT",
            "MARKET_SECTOR_SCOUT",
            "POSITION_RISK_SCOUT",
            "EVIDENCE_CANDIDATE_AUDITOR",
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
    fallback_model: LlmModelProfile | None = None
    fallback_provider: LlmProviderProfile | None = None


@dataclass(frozen=True)
class InvocationOutcome:
    model: LlmModelProfile
    provider: LlmProviderProfile
    output_json: dict[str, object] | None


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _candidate_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _persist_source_candidates(
    db: Session,
    *,
    stage: AgentStageRun,
    provider_name: str,
    candidates: list[object],
    now: datetime,
) -> None:
    run = db.get(AgentRun, stage.run_id)
    if run is None or not candidates:
        return
    existing_urls = set(
        db.scalars(select(EvidenceItem.source_url).where(EvidenceItem.run_id == run.id))
    )
    for candidate in candidates:
        url = str(getattr(candidate, "url", ""))
        if not url or url in existing_urls:
            continue
        title = str(getattr(candidate, "title", ""))[:500]
        published_at = _candidate_timestamp(getattr(candidate, "published_at", None))
        record = {
            "url": url,
            "title": title,
            "published_at": published_at.isoformat() if published_at else None,
            "provider": provider_name,
        }
        db.add(
            EvidenceItem(
                run_id=run.id,
                market=run.market,
                symbol=run.symbol,
                source_type="WEB",
                source_tier="UNRATED",
                source_name=provider_name[:128],
                source_url=url,
                title=title,
                facts_json="[]",
                content_hash=_hash(record),
                extraction_method="RULE",
                published_at=published_at,
                event_at=published_at,
                received_at=now,
            )
        )
        existing_urls.add(url)


def _load_routes(
    db: Session, *, owner_id: str, route_ids: dict[str, str]
) -> dict[str, RouteBinding]:
    if set(route_ids) != set(ROUTE_ROLES):
        raise AgentRuntimeError("AGENT_ROUTE_SET_INCOMPLETE")
    bindings: dict[str, RouteBinding] = {}
    for role in ROUTE_ROLES:
        route = db.get(LlmRoleRoute, route_ids[role])
        try:
            fallback_ids = json.loads(route.fallback_model_profile_ids_json) if route else []
        except (TypeError, json.JSONDecodeError):
            fallback_ids = None
        if (
            route is None
            or route.owner_id != owner_id
            or route.role != role
            or route.state not in {"VALIDATED", "ACTIVE"}
            or route.execution_stage != "SHADOW"
            or route.fallback_policy not in {"FAIL_STOP", "FAILOVER"}
            or route.max_attempts != 1
            or not isinstance(fallback_ids, list)
            or len(fallback_ids) > 1
            or (route.fallback_policy == "FAIL_STOP" and fallback_ids)
            or (route.fallback_policy == "FAILOVER" and len(fallback_ids) != 1)
            or route.output_schema_version
            != ("agent-core-v1" if role == "CORE" else "agent-assessment-v1")
        ):
            raise AgentRuntimeError("AGENT_ROUTE_NOT_READY")
        model = db.get(LlmModelProfile, route.primary_model_profile_id)
        provider = db.get(LlmProviderProfile, model.provider_profile_id) if model else None
        fallback_model = db.get(LlmModelProfile, fallback_ids[0]) if fallback_ids else None
        fallback_provider = (
            db.get(LlmProviderProfile, fallback_model.provider_profile_id)
            if fallback_model
            else None
        )
        prompt = (
            db.get(LlmPromptProfile, route.prompt_profile_id)
            if route.prompt_profile_id
            else None
        )
        if (
            model is None
            or model.state != "VALIDATED"
            or provider is None
            or provider.owner_id != owner_id
            or provider.state != "VALIDATED"
            or provider.deleted_at is not None
            or (provider.adapter_type != "MOCK" and not provider.credential_secret_ref)
            or (
                fallback_ids
                and (
                    fallback_model is None
                    or fallback_model.state != "VALIDATED"
                    or fallback_provider is None
                    or fallback_provider.owner_id != owner_id
                    or fallback_provider.state != "VALIDATED"
                    or fallback_provider.deleted_at is not None
                    or (
                        fallback_provider.adapter_type != "MOCK"
                        and not fallback_provider.credential_secret_ref
                    )
                )
            )
            or (
                route.prompt_profile_id is not None
                and (
                    prompt is None
                    or prompt.owner_id != owner_id
                    or prompt.role != role
                    or prompt.state != "VALIDATED"
                )
            )
        ):
            raise AgentRuntimeError("AGENT_ROUTE_NOT_READY")
        try:
            for checked_provider in (provider, fallback_provider):
                if checked_provider is None:
                    continue
                provider_registry.resolve(
                    checked_provider.adapter_type,
                    endpoint=checked_provider.endpoint,
                    credential=(
                        "route-check" if checked_provider.adapter_type != "MOCK" else None
                    ),
                )
        except AdapterNotImplementedError as exc:
            raise AgentRuntimeError("AGENT_ADAPTER_NOT_ALLOWED") from exc
        bindings[role] = RouteBinding(
            route, model, provider, fallback_model, fallback_provider
        )
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
    max_attempts: int = 1,
    available_at: datetime,
) -> AgentStageRun:
    stage = AgentStageRun(
        run_id=run.id,
        role=role,
        sequence=sequence,
        dependency_roles_json=_canonical(list(dependencies)),
        route_id=route_id,
        input_hash=input_hash,
        max_attempts=max_attempts,
        available_at=available_at,
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


def _invoke_once(
    db: Session,
    *,
    stage: AgentStageRun,
    binding: RouteBinding,
    model: LlmModelProfile,
    provider: LlmProviderProfile,
    role_input: dict[str, object],
    now: datetime,
) -> InvocationOutcome | None:
    stage.state = "RUNNING"
    stage.started_at = now
    invocation = LlmInvocation(
        stage_run_id=stage.id,
        requested_provider_profile_id=provider.id,
        requested_model_profile_id=model.id,
        state="RUNNING",
        input_hash=stage.input_hash,
        runtime_context_at=now,
        web_search_enabled=binding.route.web_search_enabled,
    )
    db.add(invocation)
    db.flush()
    if stage.invocation_id is None:
        stage.invocation_id = invocation.id
    prompt = (
        db.get(LlmPromptProfile, binding.route.prompt_profile_id)
        if binding.route.prompt_profile_id
        else None
    )
    if prompt is not None and (
        prompt.owner_id != binding.route.owner_id
        or prompt.role != stage.role
        or prompt.state != "VALIDATED"
        or hashlib.sha256(prompt.system_prompt.encode("utf-8")).hexdigest()
        != prompt.content_hash
    ):
        raise AgentRuntimeError("AGENT_PROMPT_SNAPSHOT_MISMATCH")
    messages = []
    if prompt is not None:
        messages.append({"role": "system", "content": prompt.system_prompt})
    runtime_utc = now.astimezone(UTC)
    runtime_kst = runtime_utc.astimezone(ZoneInfo("Asia/Seoul"))
    messages.append(
        {
            "role": "system",
            "content": "\n".join(
                [
                    "[Cresta runtime context v1]",
                    f"Current time: {runtime_utc.isoformat()} (UTC).",
                    f"Current time: {runtime_kst.isoformat()} (Asia/Seoul).",
                    "Use these timestamps as the current date and time for this request.",
                    "Do not treat model memory as current market, news, or disclosure evidence.",
                    (
                        "Web search is enabled. Prefer recent sources, check each source date, "
                        "and never use information published after the current timestamp."
                        if binding.route.web_search_enabled
                        else "Web search is disabled. Report missing current evidence instead of guessing."
                    ),
                    (
                        "Only copy evidence IDs from allowed_evidence_refs into evidence_refs. "
                        "Provider citations and URLs are unverified source candidates, not evidence IDs. "
                        "If allowed_evidence_refs is empty, return evidence_refs as an empty array."
                        if stage.role != "CORE"
                        else "Use only the supplied Scout results and verified evidence references."
                    ),
                ]
            ),
        }
    )
    messages.append({"role": "user", "content": _canonical(role_input)})
    request = LlmRequest(
        invocation_id=invocation.id,
        role=stage.role,
        model_profile_id=model.id,
        prompt_version=binding.route.prompt_version,
        input_schema_version="agent-runtime-input-v1",
        input_hash=stage.input_hash,
        messages=messages,
        output_json_schema=(
            AgentCoreModelOutput.model_json_schema()
            if stage.role == "CORE"
            else AgentScoutModelOutput.model_json_schema()
        ),
        timeout_ms=binding.route.timeout_ms,
        service_tier=binding.route.service_tier,
        max_output_tokens=binding.route.max_output_tokens_override or model.max_output_tokens,
        temperature=float(
            binding.route.temperature_override
            if binding.route.temperature_override is not None
            else model.temperature
        ),
        top_p=float(
            binding.route.top_p_override
            if binding.route.top_p_override is not None
            else model.top_p
        )
        if (binding.route.top_p_override is not None or model.top_p is not None)
        else None,
        reasoning_effort=binding.route.reasoning_effort_override or model.reasoning_effort,
        seed=binding.route.seed_override
        if binding.route.seed_override is not None
        else model.seed,
        tool_policy="ALLOWLIST" if binding.route.web_search_enabled else "NONE",
        allowed_tools=["WEB_SEARCH"] if binding.route.web_search_enabled else [],
    )
    credential = None
    if provider.adapter_type != "MOCK":
        if not provider.credential_secret_ref:
            invocation.state = "PROVIDER_ERROR"
            invocation.error_code = "AGENT_PROVIDER_CREDENTIAL_REQUIRED"
            invocation.completed_at = now
            return None
        try:
            credential = LlmSecretStore(
                get_settings().llm_secret_directory
            ).read(provider.credential_secret_ref)
        except LlmSecretError:
            invocation.state = "PROVIDER_ERROR"
            invocation.error_code = "AGENT_PROVIDER_CREDENTIAL_UNREADABLE"
            invocation.completed_at = now
            return None
    try:
        result = provider_registry.resolve(
            provider.adapter_type,
            endpoint=provider.endpoint,
            credential=credential,
            chat_path=(
                get_template(provider.provider_template_id).chat_path
                if provider.provider_template_id
                else None
            ),
        ).generate_structured(request, model.provider_model_id)
    except AdapterNotImplementedError:
        invocation.state = "PROVIDER_ERROR"
        invocation.error_code = "AGENT_ADAPTER_NOT_ALLOWED"
        invocation.completed_at = now
        return None
    invocation.state = result.status
    invocation.actual_provider = result.actual_provider
    invocation.actual_model = result.actual_model
    invocation.provider_request_id = result.provider_request_id
    invocation.gateway_request_id = result.gateway_request_id
    invocation.raw_response_hash = result.raw_response_hash
    invocation.latency_ms = result.latency_ms
    invocation.usage_json = _canonical(
        {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens}
    )
    invocation.retry_count = result.retry_count
    invocation.fallback_path_json = _canonical(
        [binding.model.id, model.id] if model.id != binding.model.id else result.fallback_path
    )
    invocation.validation_status = result.schema_validation
    invocation.completed_at = datetime.now(UTC)
    if binding.route.web_search_enabled:
        _persist_source_candidates(
            db,
            stage=stage,
            provider_name=result.actual_provider or provider.name,
            candidates=list(result.source_candidates),
            now=invocation.completed_at,
        )
    if result.status != "SUCCEEDED" or result.schema_validation != "PASSED":
        invocation.error_code = f"LLM_{result.status}"
        return None
    if provider.adapter_type != "MOCK":
        try:
            if stage.role == "CORE":
                output = AgentCoreModelOutput.model_validate(result.output_json)
                expected_incomplete = sorted(
                    str(item)
                    for item in role_input.get("required_incomplete_roles", [])
                )
                if sorted(output.incomplete_roles) != expected_incomplete:
                    invocation.state = "INVALID_OUTPUT"
                    invocation.validation_status = "FAILED"
                    invocation.error_code = "LLM_CORE_INCOMPLETE_ROLES_MISMATCH"
                    return None
            else:
                output = AgentScoutModelOutput.model_validate(result.output_json)
                allowed_refs = {
                    str(item) for item in role_input.get("allowed_evidence_refs", [])
                }
                if not set(output.evidence_refs).issubset(allowed_refs):
                    invocation.state = "INVALID_OUTPUT"
                    invocation.validation_status = "FAILED"
                    invocation.error_code = "LLM_EVIDENCE_REF_NOT_ALLOWED"
                    return None
        except (ValidationError, TypeError):
            invocation.state = "INVALID_OUTPUT"
            invocation.validation_status = "FAILED"
            invocation.error_code = "LLM_SCHEMA_VALIDATION_FAILED"
            return None
    invocation.validation_status = "PASSED"
    return InvocationOutcome(model=model, provider=provider, output_json=result.output_json)


def _invoke_model(
    db: Session,
    *,
    stage: AgentStageRun,
    binding: RouteBinding,
    role_input: dict[str, object],
    now: datetime,
) -> InvocationOutcome:
    outcome = _invoke_once(
        db,
        stage=stage,
        binding=binding,
        model=binding.model,
        provider=binding.provider,
        role_input=role_input,
        now=now,
    )
    if outcome is not None:
        return outcome
    if binding.route.fallback_policy == "FAILOVER":
        if binding.fallback_model is None or binding.fallback_provider is None:
            raise AgentRuntimeError("AGENT_ROUTE_SNAPSHOT_MISMATCH")
        outcome = _invoke_once(
            db,
            stage=stage,
            binding=binding,
            model=binding.fallback_model,
            provider=binding.fallback_provider,
            role_input=role_input,
            now=now,
        )
        if outcome is not None:
            return outcome
    raise AgentRuntimeError("AGENT_LLM_FAIL_STOP")


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
            "failure_policy": binding.route.fallback_policy,
            "fallback_model_id": (
                binding.fallback_model.id if binding.fallback_model else None
            ),
            "fallback_model_version": (
                binding.fallback_model.version if binding.fallback_model else None
            ),
            "prompt_profile_id": binding.route.prompt_profile_id,
            "prompt_content_hash": (
                db.get(LlmPromptProfile, binding.route.prompt_profile_id).content_hash
                if binding.route.prompt_profile_id
                else None
            ),
            "generation_parameters": {
                "temperature": str(
                    binding.route.temperature_override
                    if binding.route.temperature_override is not None
                    else binding.model.temperature
                ),
                "top_p": str(
                    binding.route.top_p_override
                    if binding.route.top_p_override is not None
                    else binding.model.top_p
                )
                if (binding.route.top_p_override is not None or binding.model.top_p is not None)
                else None,
                "max_output_tokens": binding.route.max_output_tokens_override
                or binding.model.max_output_tokens,
                "reasoning_effort": binding.route.reasoning_effort_override
                or binding.model.reasoning_effort,
                "seed": binding.route.seed_override
                if binding.route.seed_override is not None
                else binding.model.seed,
                "timeout_ms": binding.route.timeout_ms,
                "service_tier": binding.route.service_tier,
            },
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
        state="CREATED",
        valid_until=observed
        + timedelta(
            milliseconds=max(
                60000,
                sum(binding.route.timeout_ms for binding in bindings.values()) + 30000,
            )
        ),
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

    for role, sequence, dependencies in STAGES:
        _stage(
            db,
            run=run,
            role=role,
            sequence=sequence,
            dependencies=dependencies,
            route_id=bindings[role].route.id if role in bindings else None,
            input_hash=_hash({"run_input_hash": input_hash, "role": role}),
            max_attempts=bindings[role].route.max_attempts if role in bindings else 2,
            available_at=observed,
        )
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
