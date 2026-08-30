from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.activation_gate import (
    ActivationGateError,
    ActivationValidationPolicy,
    EvidenceLoader,
    GateOutcome,
    GateResolution,
    build_actual_version_snapshot,
    canonical_activation_json,
    select_current_v7_entry_activation_gate,
    version_snapshot_hash,
)
from app.agents.contracts import (
    AgentAssessment,
    AgentAssessmentV2,
    AgentCoreModelOutput,
    AgentCoreModelOutputV2,
    AgentScoutModelOutput,
)
from app.agents.decision_agents import (
    DECISION_AGENT_MODEL_OUTPUT_VERSION,
    DECISION_AGENT_ROLES,
    V7_LLM_ROUTE_ROLES,
)
from app.agents.policy_profiles import PolicyProfileError, select_active_policy_profiles
from app.agents.reason_codes import (
    REASON_CODE_POLICY_VERSION,
    invalid_reason_codes,
    output_schema_for_role,
    reason_code_context,
)
from app.agents.server_inputs import (
    SERVER_INPUT_POLICY_VERSION,
    build_position_snapshot,
)
from app.config import get_settings
from app.decision_inputs import build_v7_scout_input
from app.llm.contracts import LlmRequest
from app.llm.discovery import get_template
from app.llm.parameter_policy import supports_service_tier
from app.llm.registry import AdapterNotImplementedError, provider_registry
from app.llm.secrets import LlmSecretError, LlmSecretStore
from app.market_context import select_market_context
from app.models import (
    AgentRun,
    AgentStageRun,
    AuditLog,
    Decision,
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
from app.position_agent_fusion import FUSION_POLICY_VERSION

DAG_VERSION = "agent-dag-v6"
V7_DAG_VERSION = "agent-dag-v7"
V2_DAG_VERSIONS = frozenset({"agent-dag-v4", "agent-dag-v5", DAG_VERSION, V7_DAG_VERSION})
SERVER_INPUT_DAG_VERSIONS = frozenset({"agent-dag-v5", DAG_VERSION, V7_DAG_VERSION})
ASSESSMENT_SCHEMA_VERSION = "agent-assessment-v2"
CORE_SCHEMA_VERSION = "agent-core-v2"
SCORE_POLICY_VERSION = "score-policy-v1"
EVIDENCE_POLICY_VERSION = "official-primary-secondary-v3"
ROUTE_ROLES = (
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
    "CORE",
)
SCOUT_ROUTE_ROLES = ROUTE_ROLES[:-1]
MAX_MODEL_OUTPUT_BYTES = 64 * 1024
SENSITIVE_MODEL_OUTPUT_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "totp",
    "access_token",
    "refresh_token",
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
V7_UPSTREAM_STAGES = STAGES[:-1]
V7_DECISION_STAGES = (
    ("CONSERVATIVE_DECISION", 70, ("EVIDENCE_CANDIDATE_AUDITOR",)),
    ("BALANCED_DECISION", 71, ("EVIDENCE_CANDIDATE_AUDITOR",)),
    ("AGGRESSIVE_DECISION", 72, ("EVIDENCE_CANDIDATE_AUDITOR",)),
)
V7_ARBITER_STAGE = (
    "ENTRY_ARBITER",
    80,
    tuple(role for role, _, _ in V7_DECISION_STAGES),
)
V7_LOGICAL_STAGES = (*V7_UPSTREAM_STAGES, *V7_DECISION_STAGES, V7_ARBITER_STAGE)


def stage_plan(dag_version: str) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    return V7_UPSTREAM_STAGES if dag_version == V7_DAG_VERSION else STAGES


def route_roles(dag_version: str) -> tuple[str, ...]:
    return SCOUT_ROUTE_ROLES if dag_version == V7_DAG_VERSION else ROUTE_ROLES


def allowed_roles(dag_version: str) -> frozenset[str]:
    return frozenset(role for role, _, _ in stage_plan(dag_version))


def logical_roles(dag_version: str) -> frozenset[str]:
    plan = V7_LOGICAL_STAGES if dag_version == V7_DAG_VERSION else STAGES
    return frozenset(role for role, _, _ in plan)


def materializable_roles(dag_version: str) -> frozenset[str]:
    plan = (
        V7_LOGICAL_STAGES
        if dag_version == V7_DAG_VERSION
        else STAGES
    )
    return frozenset(role for role, _, _ in plan)


def executable_roles(dag_version: str) -> frozenset[str]:
    return (
        materializable_roles(dag_version)
        if dag_version == V7_DAG_VERSION
        else allowed_roles(dag_version)
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


def uses_v2_contract(dag_version: str) -> bool:
    return dag_version in V2_DAG_VERSIONS


def uses_server_inputs(dag_version: str) -> bool:
    return dag_version in SERVER_INPUT_DAG_VERSIONS


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _contains_sensitive_output_key(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_MODEL_OUTPUT_KEY_PARTS):
                return True
            if _contains_sensitive_output_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_output_key(item) for item in value)
    return False


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
    db: Session,
    *,
    owner_id: str,
    route_ids: dict[str, str],
    required_roles: tuple[str, ...] = ROUTE_ROLES,
) -> dict[str, RouteBinding]:
    if set(route_ids) != set(required_roles):
        raise AgentRuntimeError("AGENT_ROUTE_SET_INCOMPLETE")
    bindings: dict[str, RouteBinding] = {}
    for role in required_roles:
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
            or (
                role not in {"NEWS_DISCLOSURE_SCOUT", "MARKET_SECTOR_SCOUT"}
                and route.web_search_enabled
            )
            or not isinstance(fallback_ids, list)
            or len(fallback_ids) > 1
            or (route.fallback_policy == "FAIL_STOP" and fallback_ids)
            or (route.fallback_policy == "FAILOVER" and len(fallback_ids) != 1)
            or route.output_schema_version
            not in (
                {"agent-core-v1", "agent-core-v2"}
                if role == "CORE"
                else {DECISION_AGENT_MODEL_OUTPUT_VERSION}
                if role in DECISION_AGENT_ROLES
                else {"agent-assessment-v1", "agent-assessment-v2"}
            )
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
            db.get(LlmPromptProfile, route.prompt_profile_id) if route.prompt_profile_id else None
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
                role in DECISION_AGENT_ROLES and route.prompt_profile_id is None
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
                    credential=("route-check" if checked_provider.adapter_type != "MOCK" else None),
                )
        except AdapterNotImplementedError as exc:
            raise AgentRuntimeError("AGENT_ADAPTER_NOT_ALLOWED") from exc
        bindings[role] = RouteBinding(route, model, provider, fallback_model, fallback_provider)
    return bindings


def _route_version_snapshot(
    db: Session,
    bindings: dict[str, RouteBinding],
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        role: {
            "route_id": binding.route.id,
            "route_version": binding.route.version,
            "route_version_hash": _hash(
                {
                    "fallback_model_profile_ids_json": binding.route.fallback_model_profile_ids_json,
                    "fallback_policy": binding.route.fallback_policy,
                    "max_attempts": binding.route.max_attempts,
                    "output_schema_version": binding.route.output_schema_version,
                    "primary_model_profile_id": binding.route.primary_model_profile_id,
                    "prompt_profile_id": binding.route.prompt_profile_id,
                    "prompt_version": binding.route.prompt_version,
                    "role": binding.route.role,
                    "version": binding.route.version,
                }
            ),
            "declared_output_schema_version": binding.route.output_schema_version,
            "effective_output_schema_version": (
                CORE_SCHEMA_VERSION
                if role == "CORE"
                else DECISION_AGENT_MODEL_OUTPUT_VERSION
                if role in DECISION_AGENT_ROLES
                else ASSESSMENT_SCHEMA_VERSION
            ),
            "model_id": binding.model.id,
            "model_version": binding.model.version,
            "failure_policy": binding.route.fallback_policy,
            "web_search_enabled": binding.route.web_search_enabled,
            "fallback_model_id": binding.fallback_model.id if binding.fallback_model else None,
            "fallback_model_version": (
                binding.fallback_model.version if binding.fallback_model else None
            ),
            "prompt_profile_id": binding.route.prompt_profile_id,
            "prompt_version": binding.route.prompt_version,
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
                )
                if (
                    binding.route.temperature_override is not None
                    or binding.model.temperature is not None
                )
                else None,
                "top_p": str(
                    binding.route.top_p_override
                    if binding.route.top_p_override is not None
                    else binding.model.top_p
                )
                if binding.route.top_p_override is not None or binding.model.top_p is not None
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
    for value in snapshot.values():
        if not isinstance(value, dict):
            raise AgentRuntimeError("AGENT_ROUTE_SNAPSHOT_INVALID")
        value["route_version_hash"] = _hash(
            {key: item for key, item in value.items() if key != "route_version_hash"}
        )
    return snapshot


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
    run = db.get(AgentRun, stage.run_id)
    if run is None:
        raise AgentRuntimeError("AGENT_RUN_NOT_FOUND")
    core_schema_version = (
        CORE_SCHEMA_VERSION if uses_v2_contract(run.dag_version) else "agent-core-v1"
    )
    effective_role_input = {**role_input, **reason_code_context(stage.role)}
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
    if binding.route.service_tier != "DEFAULT" and not supports_service_tier(
        provider.provider_template_id
    ):
        invocation.state = "PROVIDER_ERROR"
        invocation.error_code = "AGENT_SERVICE_TIER_UNSUPPORTED"
        invocation.completed_at = now
        return None
    prompt = (
        db.get(LlmPromptProfile, binding.route.prompt_profile_id)
        if binding.route.prompt_profile_id
        else None
    )
    if prompt is not None and (
        prompt.owner_id != binding.route.owner_id
        or prompt.role != stage.role
        or prompt.state != "VALIDATED"
        or hashlib.sha256(prompt.system_prompt.encode("utf-8")).hexdigest() != prompt.content_hash
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
                    "Use only reason codes listed in allowed_reason_codes.",
                ]
            ),
        }
    )
    messages.append({"role": "user", "content": _canonical(effective_role_input)})
    request = LlmRequest(
        invocation_id=invocation.id,
        role=stage.role,
        model_profile_id=model.id,
        prompt_version=binding.route.prompt_version,
        input_schema_version="agent-runtime-input-v1",
        input_hash=stage.input_hash,
        messages=messages,
        output_json_schema=output_schema_for_role(
            stage.role, core_schema_version=core_schema_version
        ),
        timeout_ms=binding.route.timeout_ms,
        service_tier=binding.route.service_tier,
        max_output_tokens=binding.route.max_output_tokens_override or model.max_output_tokens,
        temperature=float(
            binding.route.temperature_override
            if binding.route.temperature_override is not None
            else model.temperature
        )
        if (binding.route.temperature_override is not None or model.temperature is not None)
        else None,
        top_p=float(
            binding.route.top_p_override
            if binding.route.top_p_override is not None
            else model.top_p
        )
        if (binding.route.top_p_override is not None or model.top_p is not None)
        else None,
        reasoning_effort=binding.route.reasoning_effort_override or model.reasoning_effort,
        seed=binding.route.seed_override if binding.route.seed_override is not None else model.seed,
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
            credential = LlmSecretStore(get_settings().llm_secret_directory).read(
                provider.credential_secret_ref
            )
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
    if result.output_json is not None:
        if _contains_sensitive_output_key(result.output_json):
            invocation.state = "INVALID_OUTPUT"
            invocation.validation_status = "FAILED"
            invocation.error_code = "LLM_MODEL_OUTPUT_SENSITIVE_FIELD"
            return None
        model_output_json = _canonical(result.output_json)
        if len(model_output_json.encode("utf-8")) > MAX_MODEL_OUTPUT_BYTES:
            invocation.state = "INVALID_OUTPUT"
            invocation.validation_status = "FAILED"
            invocation.error_code = "LLM_MODEL_OUTPUT_TOO_LARGE"
            return None
        invocation.model_output_json = model_output_json
        invocation.model_output_hash = hashlib.sha256(model_output_json.encode()).hexdigest()
        invocation.model_output_captured_at = invocation.completed_at
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
                output = (
                    AgentCoreModelOutputV2.model_validate(result.output_json)
                    if uses_v2_contract(run.dag_version)
                    else AgentCoreModelOutput.model_validate(result.output_json)
                )
                if invalid_reason_codes(stage.role, output.reason_codes):
                    invocation.state = "INVALID_OUTPUT"
                    invocation.validation_status = "FAILED"
                    invocation.error_code = "LLM_REASON_CODE_NOT_ALLOWED"
                    return None
                expected_incomplete = sorted(
                    str(item) for item in role_input.get("required_incomplete_roles", [])
                )
                if sorted(output.incomplete_roles) != expected_incomplete:
                    invocation.state = "INVALID_OUTPUT"
                    invocation.validation_status = "FAILED"
                    invocation.error_code = "LLM_CORE_INCOMPLETE_ROLES_MISMATCH"
                    return None
                if uses_v2_contract(run.dag_version):
                    context = str(role_input.get("analysis_context"))
                    allowed_assessments = (
                        {
                            "ENTRY_STRONG",
                            "ENTRY_SUPPORTIVE",
                            "NEUTRAL",
                            "ENTRY_ADVERSE",
                            "UNKNOWN",
                        }
                        if context == "ENTRY"
                        else {
                            "HOLD_SUPPORTIVE",
                            "NEUTRAL",
                            "EXIT_RISK_ELEVATED",
                            "EXIT_RISK_HIGH",
                            "UNKNOWN",
                        }
                    )
                    if (
                        output.shadow_assessment not in allowed_assessments
                        or expected_incomplete
                        and output.shadow_assessment != "UNKNOWN"
                    ):
                        invocation.state = "INVALID_OUTPUT"
                        invocation.validation_status = "FAILED"
                        invocation.error_code = "LLM_CORE_SHADOW_ASSESSMENT_MISMATCH"
                        return None
            else:
                output = AgentScoutModelOutput.model_validate(result.output_json)
                if invalid_reason_codes(stage.role, output.reason_codes):
                    invocation.state = "INVALID_OUTPUT"
                    invocation.validation_status = "FAILED"
                    invocation.error_code = "LLM_REASON_CODE_NOT_ALLOWED"
                    return None
                allowed_refs = {str(item) for item in role_input.get("allowed_evidence_refs", [])}
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
    day_start_kst = now.astimezone(ZoneInfo("Asia/Seoul")).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    day_start_utc = day_start_kst.astimezone(UTC)
    daily_calls = (
        db.scalar(
            select(func.count(LlmInvocation.id))
            .join(AgentStageRun, AgentStageRun.id == LlmInvocation.stage_run_id)
            .where(
                AgentStageRun.route_id == binding.route.id,
                LlmInvocation.created_at >= day_start_utc,
                or_(
                    LlmInvocation.error_code.is_(None),
                    LlmInvocation.error_code != "LOCAL_DAILY_CALL_LIMIT",
                ),
            )
        )
        or 0
    )
    if daily_calls >= binding.route.daily_call_limit:
        stage.state = "RUNNING"
        stage.started_at = now
        invocation = LlmInvocation(
            stage_run_id=stage.id,
            requested_provider_profile_id=binding.provider.id,
            requested_model_profile_id=binding.model.id,
            state="RATE_LIMITED",
            input_hash=stage.input_hash,
            runtime_context_at=now,
            web_search_enabled=binding.route.web_search_enabled,
            error_code="LOCAL_DAILY_CALL_LIMIT",
            completed_at=now,
        )
        db.add(invocation)
        db.flush()
        if stage.invocation_id is None:
            stage.invocation_id = invocation.id
        raise AgentRuntimeError("AGENT_DAILY_CALL_LIMIT")
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
                reason_codes=["INDICATOR_DATA_MISSING"],
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
            reason_codes=["PRICE_ABOVE_VWAP" if supportive else "PRICE_BELOW_VWAP"],
            evidence_refs=[],
        )
    if role == "NEWS_DISCLOSURE_SCOUT":
        return AgentAssessment(
            **common,
            status="INSUFFICIENT_DATA",
            stance="UNKNOWN",
            confidence=0,
            uncertainty=1,
            reason_codes=["NO_VERIFIED_EVIDENCE"],
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
            reason_codes=["MARKET_TREND_NEUTRAL" if normal else "MARKET_DATA_QUALITY_DEGRADED"],
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
        reason_codes=["POSITION_RISK_NORMAL"],
        evidence_refs=[],
    )


def _assessment_v2(
    role: str,
    *,
    stage_run_id: str,
    symbol: str,
    input_refs: list[str],
    indicator: IndicatorSnapshot | None,
    snapshot: MarketSnapshot,
    position: Position | dict[str, object] | None,
    market_context: dict[str, object] | None,
    server_input_policy_version: str | None,
    analysis_context: str,
    observed_at: datetime,
    valid_until: datetime,
) -> AgentAssessmentV2:
    common = {
        "stage_run_id": stage_run_id,
        "role": role,
        "symbol": symbol,
        "input_refs": input_refs,
        "observed_at": observed_at,
        "valid_until": valid_until,
    }
    if role == "POSITION_RISK_SCOUT" and analysis_context == "ENTRY":
        return AgentAssessmentV2(
            **common,
            status="NOT_APPLICABLE",
            stance="UNKNOWN",
            confidence=0,
            uncertainty=1,
            reason_codes=["OPEN_POSITION_NOT_FOUND"],
            evidence_refs=[],
        )
    if role == "MARKET_SECTOR_SCOUT" and server_input_policy_version == SERVER_INPUT_POLICY_VERSION:
        if market_context is None:
            return AgentAssessmentV2(
                **common,
                status="INSUFFICIENT_DATA",
                stance="UNKNOWN",
                confidence=0,
                uncertainty=1,
                reason_codes=["MARKET_DATA_INSUFFICIENT"],
                evidence_refs=[],
            )
        index = market_context.get("index")
        sector = market_context.get("sector")
        breadth = market_context.get("breadth")
        index_change = (
            Decimal(str(index.get("change_pct")))
            if isinstance(index, dict) and index.get("change_pct") is not None
            else None
        )
        sector_change = (
            Decimal(str(sector.get("change_pct")))
            if isinstance(sector, dict) and sector.get("change_pct") is not None
            else None
        )
        breadth_ratio = (
            Decimal(str(breadth.get("advancer_ratio_pct")))
            if isinstance(breadth, dict) and breadth.get("advancer_ratio_pct") is not None
            else None
        )
        if index_change is None or sector_change is None or breadth_ratio is None:
            return AgentAssessmentV2(
                **common,
                status="INSUFFICIENT_DATA",
                stance="UNKNOWN",
                confidence=0,
                uncertainty=1,
                reason_codes=["MARKET_DATA_INSUFFICIENT"],
                evidence_refs=[],
            )
        supportive = index_change >= 0 and sector_change >= 0 and breadth_ratio >= 50
        adverse = index_change < 0 and sector_change < 0 and breadth_ratio < 40
        return AgentAssessmentV2(
            **common,
            status="SUCCEEDED",
            stance="SUPPORTIVE" if supportive else "CAUTION" if adverse else "NEUTRAL",
            entry_score=65 if supportive else 35 if adverse else 50,
            exit_risk_score=30 if supportive else 70 if adverse else 50,
            confidence=0.75,
            uncertainty=0.25,
            reason_codes=[
                "MARKET_TREND_SUPPORTIVE"
                if supportive
                else "MARKET_TREND_WEAK"
                if adverse
                else "MARKET_SECTOR_SIGNALS_MIXED"
            ],
            evidence_refs=[],
        )
    if (
        role == "POSITION_RISK_SCOUT"
        and isinstance(position, dict)
        and server_input_policy_version == SERVER_INPUT_POLICY_VERSION
    ):
        freshness = position.get("freshness")
        if not isinstance(freshness, dict) or freshness.get("status") != "FRESH":
            return AgentAssessmentV2(
                **common,
                status="INSUFFICIENT_DATA",
                stance="UNKNOWN",
                confidence=0,
                uncertainty=1,
                reason_codes=["POSITION_DATA_STALE"],
                evidence_refs=[],
            )
        stop_distance = Decimal(str(position["distance_to_fixed_stop_pct"]))
        unrealized_return = Decimal(str(position["unrealized_return_pct"]))
        critical = stop_distance <= 0
        elevated = not critical and stop_distance <= Decimal("0.5")
        losing = unrealized_return < 0
        return AgentAssessmentV2(
            **common,
            status="SUCCEEDED",
            stance="RISK" if critical or elevated else "CAUTION" if losing else "NEUTRAL",
            exit_risk_score=90 if critical else 75 if elevated else 60 if losing else 35,
            confidence=0.85,
            uncertainty=0.15,
            reason_codes=[
                "FIXED_STOP_TRIGGERED"
                if critical
                else "FIXED_STOP_NEAR"
                if elevated
                else "POSITION_LOSING"
                if losing
                else "POSITION_RISK_NORMAL"
            ],
            evidence_refs=[],
        )
    legacy = _assessment(
        role,
        stage_run_id=stage_run_id,
        symbol=symbol,
        input_refs=input_refs,
        indicator=indicator,
        snapshot=snapshot,
        position=position,  # type: ignore[arg-type]
        observed_at=observed_at,
        valid_until=valid_until,
    )
    return AgentAssessmentV2(**legacy.model_dump(exclude={"schema_version"}))


def _create_run(
    db: Session,
    *,
    user: User,
    market: str,
    symbol: str,
    route_ids: dict[str, str],
    purpose: str,
    basis_decision: Decision | None,
    now: datetime | None = None,
) -> tuple[AgentRun, bool]:
    if purpose not in {"DIAGNOSTIC", "TRADING_ADVISORY"}:
        raise AgentRuntimeError("AGENT_PURPOSE_NOT_ALLOWED", 409)
    if purpose == "DIAGNOSTIC" and basis_decision is not None:
        raise AgentRuntimeError("AGENT_DIAGNOSTIC_BASIS_NOT_ALLOWED", 409)
    if purpose == "TRADING_ADVISORY" and (
        basis_decision is None
        or basis_decision.purpose != "TRADING"
        or basis_decision.decision_kind != "POSITION"
        or basis_decision.symbol != symbol
        or basis_decision.market != market
    ):
        raise AgentRuntimeError("AGENT_ADVISORY_BASIS_INVALID", 409)
    observed = now or datetime.now(UTC)
    settings = get_settings()
    if settings.dart_enabled and settings.dart_configuration_status() != "CONFIGURED":
        raise AgentRuntimeError("AGENT_DART_NOT_CONFIGURED", 409)
    if settings.krx_enabled and settings.krx_configuration_status() != "CONFIGURED":
        raise AgentRuntimeError("AGENT_KRX_NOT_CONFIGURED", 409)
    if settings.naver_news_enabled and settings.naver_news_configuration_status() != "CONFIGURED":
        raise AgentRuntimeError("AGENT_NAVER_NEWS_NOT_CONFIGURED", 409)
    snapshot = _snapshot(db, market, symbol)
    position = db.scalar(
        select(Position)
        .where(
            Position.symbol == symbol,
            Position.state == "OPEN",
            Position.quantity > 0,
        )
        .order_by(Position.updated_at.desc(), Position.id)
    )
    analysis_context = "POSITION" if position is not None else "ENTRY"
    if purpose == "TRADING_ADVISORY" and analysis_context != "POSITION":
        raise AgentRuntimeError("AGENT_ADVISORY_POSITION_NOT_FOUND", 409)
    position_snapshot: dict[str, object] = (
        build_position_snapshot(
            db,
            user=user,
            position=position,
            market_snapshot=snapshot,
            settings=settings,
        )
        if position is not None
        else {
            "marker": "NO_OPEN_POSITION",
            "calculation_version": "position-risk-input-v1",
            "symbol": symbol,
            "market_observed_at": snapshot.event_at.isoformat(),
            "source_refs": [snapshot.id],
        }
    )
    position_snapshot_hash = _hash(position_snapshot)
    market_context = select_market_context(db, market=market, symbol=symbol, now=observed)
    bindings = _load_routes(db, owner_id=user.id, route_ids=route_ids)
    route_versions = {
        role: {
            "route_id": binding.route.id,
            "route_version": binding.route.version,
            "declared_output_schema_version": binding.route.output_schema_version,
            "effective_output_schema_version": (
                CORE_SCHEMA_VERSION if role == "CORE" else ASSESSMENT_SCHEMA_VERSION
            ),
            "model_id": binding.model.id,
            "model_version": binding.model.version,
            "failure_policy": binding.route.fallback_policy,
            "fallback_model_id": (binding.fallback_model.id if binding.fallback_model else None),
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
                )
                if (
                    binding.route.temperature_override is not None
                    or binding.model.temperature is not None
                )
                else None,
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
        "purpose": purpose,
        "basis_decision_id": basis_decision.id if basis_decision else None,
        "market": market,
        "symbol": symbol,
        "market_snapshot_id": snapshot.id,
        "payload_hash": snapshot.payload_hash,
        "dag_version": DAG_VERSION,
        "analysis_context": analysis_context,
        "position_snapshot_hash": position_snapshot_hash,
        "server_input_policy_version": SERVER_INPUT_POLICY_VERSION,
        "market_context_snapshot_id": market_context.id if market_context else None,
        "market_context_snapshot_hash": (market_context.payload_hash if market_context else None),
        "assessment_schema_version": ASSESSMENT_SCHEMA_VERSION,
        "core_schema_version": CORE_SCHEMA_VERSION,
        "score_policy_version": SCORE_POLICY_VERSION,
        "reason_code_policy_version": REASON_CODE_POLICY_VERSION,
        "evidence_source_policy": {
            "version": EVIDENCE_POLICY_VERSION,
            "dart_status": settings.dart_configuration_status(),
            "krx_status": settings.krx_configuration_status(),
            "naver_news_status": settings.naver_news_configuration_status(),
        },
        "route_versions": route_versions,
    }
    input_hash = _hash(input_record)
    idempotency_key = _hash({"owner_id": user.id, "purpose": purpose, "input_hash": input_hash})
    existing = db.scalar(select(AgentRun).where(AgentRun.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.owner_id != user.id:
            raise AgentRuntimeError("AGENT_IDEMPOTENCY_CONFLICT", 409)
        return existing, False

    run = AgentRun(
        owner_id=user.id,
        purpose=purpose,
        market=market,
        symbol=symbol,
        market_snapshot_id=snapshot.id,
        input_hash=input_hash,
        dag_version=DAG_VERSION,
        analysis_context=analysis_context,
        position_snapshot_json=_canonical(position_snapshot),
        position_snapshot_hash=position_snapshot_hash,
        server_input_policy_version=SERVER_INPUT_POLICY_VERSION,
        market_context_snapshot_id=market_context.id if market_context else None,
        market_context_snapshot_hash=market_context.payload_hash if market_context else None,
        route_versions_json=_canonical(route_versions),
        idempotency_key=idempotency_key,
        state="CREATED",
        basis_decision_id=basis_decision.id if basis_decision else None,
        fusion_policy_version=(FUSION_POLICY_VERSION if purpose == "TRADING_ADVISORY" else None),
        fusion_state="PENDING" if purpose == "TRADING_ADVISORY" else None,
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


def create_diagnostic_run(
    db: Session,
    *,
    user: User,
    market: str,
    symbol: str,
    route_ids: dict[str, str],
    now: datetime | None = None,
) -> tuple[AgentRun, bool]:
    return _create_run(
        db,
        user=user,
        market=market,
        symbol=symbol,
        route_ids=route_ids,
        purpose="DIAGNOSTIC",
        basis_decision=None,
        now=now,
    )


def create_v7_upstream_diagnostic_run(
    db: Session,
    *,
    user: User,
    market: str,
    symbol: str,
    route_ids: dict[str, str],
    now: datetime | None = None,
) -> tuple[AgentRun, bool]:
    """Atomically admit the Phase 4 v7 DIAGNOSTIC upstream execution slice."""
    observed = now or datetime.now(UTC)
    settings = get_settings()
    try:
        snapshot = _snapshot(db, market, symbol)
        state = db.get(MarketStreamState, (market, symbol))
        if state is None:
            raise AgentRuntimeError("AGENT_MARKET_STREAM_NOT_FOUND", 409)
        market_context = select_market_context(db, market=market, symbol=symbol, now=observed)
        decision_input, input_payload = build_v7_scout_input(
            db,
            user_id=user.id,
            snapshot=snapshot,
            state=state,
            observed_at=observed,
            quote_stale_seconds=settings.quote_stale_seconds,
            dart_lookback_days=settings.dart_lookback_days,
            krx_lookback_days=settings.krx_lookback_days,
            naver_news_lookback_hours=settings.naver_news_lookback_hours,
            market_context=market_context,
        )
        bindings = _load_routes(
            db,
            owner_id=user.id,
            route_ids=route_ids,
            required_roles=V7_LLM_ROUTE_ROLES,
        )
        route_versions = _route_version_snapshot(db, bindings)
        route_versions_json = _canonical(route_versions)
        frozen = select_active_policy_profiles(db)
        idempotency_key = _hash(
            {
                "analysis_context": "ENTRY",
                "dag_version": V7_DAG_VERSION,
                "input_hash": decision_input.input_hash,
                "owner_id": user.id,
                "purpose": "DIAGNOSTIC",
            }
        )
        existing = db.scalar(select(AgentRun).where(AgentRun.idempotency_key == idempotency_key))
        if existing is not None:
            existing_roles = {
                item.role
                for item in db.scalars(
                    select(AgentStageRun).where(AgentStageRun.run_id == existing.id)
                )
            }
            if (
                existing.owner_id != user.id
                or existing.dag_version != V7_DAG_VERSION
                or existing.purpose != "DIAGNOSTIC"
                or existing.analysis_context != "ENTRY"
                or existing.input_hash != decision_input.input_hash
                or existing.route_versions_json != route_versions_json
                or existing.policy_profile_version_map_json != frozen.manifest_json
                or existing.policy_profile_version_map_hash != frozen.manifest_hash
                or existing_roles != allowed_roles(V7_DAG_VERSION)
            ):
                raise AgentRuntimeError("AGENT_IDEMPOTENCY_CONFLICT", 409)
            db.commit()
            return existing, False

        valid_until_value = input_payload.get("valid_until")
        if not isinstance(valid_until_value, str):
            raise AgentRuntimeError("AGENT_INPUT_VALIDITY_INVALID")
        valid_until = datetime.fromisoformat(valid_until_value)
        position_snapshot = {
            "calculation_version": "position-risk-input-v1",
            "market_observed_at": snapshot.event_at.isoformat(),
            "marker": "NO_OPEN_POSITION",
            "source_refs": [snapshot.id],
            "symbol": symbol,
        }
        run = AgentRun(
            owner_id=user.id,
            purpose="DIAGNOSTIC",
            execution_stage="SHADOW",
            market=market,
            symbol=symbol,
            market_snapshot_id=snapshot.id,
            input_hash=decision_input.input_hash,
            dag_version=V7_DAG_VERSION,
            route_versions_json=route_versions_json,
            policy_profile_version_map_json=frozen.manifest_json,
            policy_profile_version_map_hash=frozen.manifest_hash,
            idempotency_key=idempotency_key,
            state="CREATED",
            analysis_context="ENTRY",
            position_snapshot_json=_canonical(position_snapshot),
            position_snapshot_hash=_hash(position_snapshot),
            server_input_policy_version=SERVER_INPUT_POLICY_VERSION,
            market_context_snapshot_id=market_context.id if market_context else None,
            market_context_snapshot_hash=(market_context.payload_hash if market_context else None),
            valid_until=valid_until,
        )
        db.add(run)
        db.flush()
        for role, sequence, dependencies in stage_plan(V7_DAG_VERSION):
            _stage(
                db,
                run=run,
                role=role,
                sequence=sequence,
                dependencies=dependencies,
                route_id=bindings[role].route.id if role in bindings else None,
                input_hash=_hash(
                    {
                        "run_input_hash": run.input_hash,
                        "role": role,
                        "state": "AWAITING_EVIDENCE" if role in SCOUT_ROUTE_ROLES else "READY",
                    }
                ),
                max_attempts=bindings[role].route.max_attempts if role in bindings else 2,
                available_at=observed,
            )
        db.flush()
        db.commit()
        db.refresh(run)
        return run, True
    except (AgentRuntimeError, PolicyProfileError, ValueError):
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise AgentRuntimeError("AGENT_IDEMPOTENCY_CONFLICT", 409) from exc


_GATE_ADMISSION_RESULT = {
    GateOutcome.CLOSED: ("ACTIVATION_GATE_CLOSED", "BLOCKED", False, 409),
    GateOutcome.SUPERSEDED: ("ACTIVATION_GATE_SUPERSEDED", "BLOCKED", False, 409),
    GateOutcome.INVALID: ("ACTIVATION_GATE_INVALID", "INVALID", False, 409),
    GateOutcome.DB_RETRYABLE_FAILURE: (
        "ACTIVATION_GATE_DB_RETRYABLE_FAILURE",
        "RETRYABLE_FAILURE",
        True,
        503,
    ),
}


def _persist_trading_admission_denial(
    db: Session,
    *,
    user: User,
    correlation_id: str,
    resolution: GateResolution,
) -> AgentRuntimeError:
    action, result, retryable, status_code = _GATE_ADMISSION_RESULT[resolution.outcome]
    version = resolution.version
    metadata = {
        "schema_version": "finalization-audit-v1",
        "agent_run_id": None,
        "decision_id": None,
        "evaluation_request_id": correlation_id,
        "decision_context_id": None,
        "source_stage_run_id": None,
        "source_stage_output_hash": None,
        "activation_gate_version_id": version.id if version is not None else None,
        "activation_gate_version_hash": (
            version.payload_hash if version is not None else None
        ),
        "retryable": retryable,
    }
    db.add(
        AuditLog(
            actor_type="SYSTEM",
            actor_id=user.id,
            action=action,
            target="V7_ENTRY_ACTIVATION:MOCK",
            result=result,
            correlation_id=correlation_id,
            metadata_json=_canonical(metadata),
        )
    )
    db.commit()
    return AgentRuntimeError(action, status_code)


def create_v7_upstream_trading_run(
    db: Session,
    *,
    user: User,
    market: str,
    symbol: str,
    route_ids: dict[str, str],
    correlation_id: str,
    evidence_loader: EvidenceLoader | None,
    validation_policy: ActivationValidationPolicy | None = None,
    now: datetime | None = None,
) -> tuple[AgentRun, bool]:
    """Atomically admit a server-owned v7 TRADING run behind the Activation Gate."""
    observed = now or datetime.now(UTC)
    settings = get_settings()
    denial: GateResolution | None = None
    try:
        snapshot = _snapshot(db, market, symbol)
        state = db.get(MarketStreamState, (market, symbol))
        if state is None:
            raise AgentRuntimeError("AGENT_MARKET_STREAM_NOT_FOUND", 409)
        market_context = select_market_context(db, market=market, symbol=symbol, now=observed)
        decision_input, input_payload = build_v7_scout_input(
            db,
            user_id=user.id,
            snapshot=snapshot,
            state=state,
            observed_at=observed,
            quote_stale_seconds=settings.quote_stale_seconds,
            dart_lookback_days=settings.dart_lookback_days,
            krx_lookback_days=settings.krx_lookback_days,
            naver_news_lookback_hours=settings.naver_news_lookback_hours,
            market_context=market_context,
            purpose="TRADING",
        )
        bindings = _load_routes(
            db,
            owner_id=user.id,
            route_ids=route_ids,
            required_roles=V7_LLM_ROUTE_ROLES,
        )
        route_versions = _route_version_snapshot(db, bindings)
        route_versions_json = _canonical(route_versions)
        frozen = select_active_policy_profiles(db)
        actual_snapshot = build_actual_version_snapshot(
            policy_version_map=json.loads(frozen.manifest_json),
            route_versions=route_versions,
        )
        gate = select_current_v7_entry_activation_gate(
            db,
            now=observed,
            evidence_loader=evidence_loader,
            policy=validation_policy,
            lock=True,
        )
        if gate.outcome != GateOutcome.PASS:
            denial = gate
            raise ActivationGateError(gate.outcome.value)
        assert gate.version is not None and gate.payload is not None
        if (
            canonical_activation_json(actual_snapshot)
            != canonical_activation_json(gate.payload.version_snapshot)
            or version_snapshot_hash(actual_snapshot) != gate.payload.version_snapshot_hash
        ):
            denial = GateResolution(
                GateOutcome.INVALID, version=gate.version, payload=gate.payload
            )
            raise ActivationGateError("ACTIVATION_GATE_SNAPSHOT_MISMATCH")

        idempotency_key = _hash(
            {
                "analysis_context": "ENTRY",
                "dag_version": V7_DAG_VERSION,
                "input_hash": decision_input.input_hash,
                "owner_id": user.id,
                "purpose": "TRADING",
            }
        )
        existing = db.scalar(select(AgentRun).where(AgentRun.idempotency_key == idempotency_key))
        if existing is not None:
            existing_roles = {
                item.role
                for item in db.scalars(
                    select(AgentStageRun).where(AgentStageRun.run_id == existing.id)
                )
            }
            if (
                existing.owner_id != user.id
                or existing.dag_version != V7_DAG_VERSION
                or existing.purpose != "TRADING"
                or existing.analysis_context != "ENTRY"
                or existing.input_hash != decision_input.input_hash
                or existing.route_versions_json != route_versions_json
                or existing.policy_profile_version_map_json != frozen.manifest_json
                or existing.policy_profile_version_map_hash != frozen.manifest_hash
                or existing_roles != allowed_roles(V7_DAG_VERSION)
            ):
                raise AgentRuntimeError("AGENT_IDEMPOTENCY_CONFLICT", 409)
            if (
                existing.activation_gate_version_id != gate.version.id
                or existing.activation_gate_version_hash != gate.version.payload_hash
            ):
                denial = GateResolution(
                    GateOutcome.SUPERSEDED,
                    version=gate.version,
                    payload=gate.payload,
                )
                raise ActivationGateError("ACTIVATION_GATE_SUPERSEDED")
            db.commit()
            return existing, False

        valid_until_value = input_payload.get("valid_until")
        if not isinstance(valid_until_value, str):
            raise AgentRuntimeError("AGENT_INPUT_VALIDITY_INVALID")
        valid_until = datetime.fromisoformat(valid_until_value)
        position_snapshot = {
            "calculation_version": "position-risk-input-v1",
            "market_observed_at": snapshot.event_at.isoformat(),
            "marker": "NO_OPEN_POSITION",
            "source_refs": [snapshot.id],
            "symbol": symbol,
        }
        run = AgentRun(
            owner_id=user.id,
            purpose="TRADING",
            execution_stage="SHADOW",
            market=market,
            symbol=symbol,
            market_snapshot_id=snapshot.id,
            input_hash=decision_input.input_hash,
            dag_version=V7_DAG_VERSION,
            route_versions_json=route_versions_json,
            policy_profile_version_map_json=frozen.manifest_json,
            policy_profile_version_map_hash=frozen.manifest_hash,
            activation_gate_version_id=gate.version.id,
            activation_gate_version_hash=gate.version.payload_hash,
            idempotency_key=idempotency_key,
            state="CREATED",
            analysis_context="ENTRY",
            position_snapshot_json=_canonical(position_snapshot),
            position_snapshot_hash=_hash(position_snapshot),
            server_input_policy_version=SERVER_INPUT_POLICY_VERSION,
            market_context_snapshot_id=market_context.id if market_context else None,
            market_context_snapshot_hash=(market_context.payload_hash if market_context else None),
            valid_until=valid_until,
        )
        db.add(run)
        db.flush()
        for role, sequence, dependencies in stage_plan(V7_DAG_VERSION):
            _stage(
                db,
                run=run,
                role=role,
                sequence=sequence,
                dependencies=dependencies,
                route_id=bindings[role].route.id if role in bindings else None,
                input_hash=_hash(
                    {
                        "run_input_hash": run.input_hash,
                        "role": role,
                        "state": "AWAITING_EVIDENCE" if role in SCOUT_ROUTE_ROLES else "READY",
                    }
                ),
                max_attempts=bindings[role].route.max_attempts if role in bindings else 2,
                available_at=observed,
            )
        final_gate = select_current_v7_entry_activation_gate(
            db,
            now=observed,
            evidence_loader=evidence_loader,
            policy=validation_policy,
            lock=True,
        )
        if final_gate.outcome != GateOutcome.PASS:
            denial = final_gate
            raise ActivationGateError(final_gate.outcome.value)
        assert final_gate.version is not None
        if (
            final_gate.version.id != gate.version.id
            or final_gate.version.payload_hash != gate.version.payload_hash
        ):
            denial = GateResolution(
                GateOutcome.SUPERSEDED,
                version=final_gate.version,
                payload=final_gate.payload,
            )
            raise ActivationGateError("ACTIVATION_GATE_SUPERSEDED")
        db.commit()
        db.refresh(run)
        return run, True
    except ActivationGateError:
        db.rollback()
        if denial is None:
            denial = GateResolution(GateOutcome.INVALID)
        raise _persist_trading_admission_denial(
            db,
            user=user,
            correlation_id=correlation_id,
            resolution=denial,
        )
    except (AgentRuntimeError, PolicyProfileError, ValueError):
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise AgentRuntimeError("AGENT_IDEMPOTENCY_CONFLICT", 409) from exc


def create_position_advisory_run(
    db: Session,
    *,
    user: User,
    basis_decision: Decision,
    route_ids: dict[str, str],
    now: datetime | None = None,
) -> tuple[AgentRun, bool]:
    return _create_run(
        db,
        user=user,
        market=basis_decision.market,
        symbol=basis_decision.symbol,
        route_ids=route_ids,
        purpose="TRADING_ADVISORY",
        basis_decision=basis_decision,
        now=now,
    )


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
