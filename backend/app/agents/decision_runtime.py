from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activation_gate import ActivationGateError, validate_frozen_activation_provenance
from app.agents.contracts import (
    DecisionAgentInput,
    DecisionAgentModelOutput,
    DecisionAgentResult,
    validate_decision_evidence_refs,
)
from app.agents.decision_agents import (
    DECISION_AGENT_MODEL_OUTPUT_VERSION,
    DECISION_AGENT_ROLES,
    DecisionAgentFoundationError,
    build_decision_agent_input,
    build_decision_agent_stage_input,
    decision_agent_input_hash,
    decision_agent_stage_input_hash,
    resolve_frozen_decision_route,
)
from app.agents.decision_context import validate_decision_context_integrity
from app.agents.policy_profiles import (
    ROLE_AGENT_TYPES,
    PolicyProfileError,
    resolve_decision_agent_policy,
)
from app.agents.reason_codes import output_schema_for_role
from app.config import get_settings
from app.llm.contracts import LlmRequest, LlmResult
from app.llm.discovery import get_template
from app.llm.parameter_policy import supports_service_tier
from app.llm.registry import AdapterNotImplementedError, provider_registry
from app.llm.secrets import LlmSecretError, LlmSecretStore
from app.models import (
    AgentRun,
    AgentStageRun,
    DecisionContext,
    LlmInvocation,
    LlmModelProfile,
    LlmProviderProfile,
)

MAX_MODEL_OUTPUT_BYTES = 64 * 1024
SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "totp",
    "access_token",
    "refresh_token",
)


class DecisionExecutionError(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ProviderAttempt:
    model_profile_id: str
    provider_profile_id: str
    provider_model_id: str
    adapter_type: str
    endpoint: str | None
    credential_secret_ref: str | None
    provider_template_id: str | None
    fallback_used: bool


@dataclass(frozen=True)
class PreparedDecisionExecution:
    stage_id: str
    run_id: str
    role: str
    fencing_token: int
    stage_input_hash: str
    provider_input: DecisionAgentInput
    provider_input_hash: str
    prompt: str
    prompt_version: str
    route_id: str
    route_version: int
    route_version_hash: str
    timeout_ms: int
    service_tier: str
    max_output_tokens: int
    temperature: float | None
    top_p: float | None
    reasoning_effort: str | None
    seed: int | None
    fallback_policy: str
    attempts: tuple[ProviderAttempt, ...]


@dataclass(frozen=True)
class ProviderOutcome:
    invocation_id: str | None
    status: str
    reason_code: str
    model_output: DecisionAgentModelOutput | None
    actual_provider: str | None
    actual_model: str | None
    fallback_used: bool


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _contains_sensitive_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS)
            or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _attempt(
    db: Session,
    *,
    run: AgentRun,
    model_id: str,
    model_version: object,
    fallback_used: bool,
) -> ProviderAttempt:
    model = db.get(LlmModelProfile, model_id)
    provider = db.get(LlmProviderProfile, model.provider_profile_id) if model else None
    if (
        model is None
        or model.version != model_version
        or model.state != "VALIDATED"
        or provider is None
        or provider.owner_id != run.owner_id
        or provider.state != "VALIDATED"
        or provider.deleted_at is not None
        or (provider.adapter_type != "MOCK" and not provider.credential_secret_ref)
    ):
        raise DecisionExecutionError("DECISION_AGENT_ROUTE_PROVENANCE_INVALID")
    return ProviderAttempt(
        model_profile_id=model.id,
        provider_profile_id=provider.id,
        provider_model_id=model.provider_model_id,
        adapter_type=provider.adapter_type,
        endpoint=provider.endpoint,
        credential_secret_ref=provider.credential_secret_ref,
        provider_template_id=provider.provider_template_id,
        fallback_used=fallback_used,
    )


def prepare_decision_execution(
    db: Session,
    *,
    stage: AgentStageRun,
    run: AgentRun,
    fencing_token: int,
    now: datetime,
) -> PreparedDecisionExecution:
    if (
        run.dag_version != "agent-dag-v7"
        or run.purpose not in {"DIAGNOSTIC", "TRADING"}
        or run.analysis_context != "ENTRY"
        or stage.role not in DECISION_AGENT_ROLES
        or stage.run_id != run.id
        or stage.state != "RUNNING"
        or stage.fencing_token != fencing_token
    ):
        raise DecisionExecutionError("DECISION_AGENT_INPUT_PROVENANCE_INVALID")
    try:
        validate_frozen_activation_provenance(db, run=run)
    except ActivationGateError as exc:
        raise DecisionExecutionError("DECISION_AGENT_INPUT_PROVENANCE_INVALID") from exc
    context = db.scalar(select(DecisionContext).where(DecisionContext.run_id == run.id))
    if context is None:
        raise DecisionExecutionError("DECISION_AGENT_INPUT_PROVENANCE_INVALID")
    if _aware(context.valid_until) <= _aware(now):
        raise DecisionExecutionError("DECISION_AGENT_CONTEXT_EXPIRED")
    try:
        validate_decision_context_integrity(db, run=run, context=context, now=now)
        stage_input = build_decision_agent_stage_input(
            db, run=run, context=context, role=stage.role
        )
        if (
            stage.route_id != stage_input.route_id
            or stage.input_hash != decision_agent_stage_input_hash(stage_input)
        ):
            raise DecisionExecutionError("DECISION_AGENT_INPUT_PROVENANCE_INVALID")
        provider_input = build_decision_agent_input(
            db, run=run, context=context, role=stage.role, now=now
        )
        binding = resolve_frozen_decision_route(db, run=run, role=stage.role)
        _, policy = resolve_decision_agent_policy(db, run_id=run.id, role=stage.role)
    except PolicyProfileError as exc:
        raise DecisionExecutionError("DECISION_AGENT_POLICY_PROVENANCE_INVALID") from exc
    except DecisionAgentFoundationError as exc:
        reason = (
            "DECISION_AGENT_CONTEXT_EXPIRED"
            if "EXPIRED" in exc.code
            else "DECISION_AGENT_ROUTE_PROVENANCE_INVALID"
            if "ROUTE" in exc.code
            else "DECISION_AGENT_INPUT_PROVENANCE_INVALID"
        )
        raise DecisionExecutionError(reason) from exc
    except Exception as exc:
        raise DecisionExecutionError("DECISION_AGENT_INPUT_PROVENANCE_INVALID") from exc

    snapshot = binding.snapshot
    route = binding.route
    prompt = binding.prompt
    if (
        policy.configuration_version_id != stage_input.policy_profile_id
        or policy.payload_hash != stage_input.policy_profile_hash
        or hashlib.sha256(prompt.system_prompt.encode("utf-8")).hexdigest()
        != snapshot.get("prompt_content_hash")
        or route.role != stage.role
        or route.primary_model_profile_id != snapshot.get("model_id")
        or route.prompt_profile_id != snapshot.get("prompt_profile_id")
        or route.prompt_version != snapshot.get("prompt_version")
        or route.fallback_policy != snapshot.get("failure_policy")
        or route.web_search_enabled
        or snapshot.get("web_search_enabled") is not False
        or route.output_schema_version != DECISION_AGENT_MODEL_OUTPUT_VERSION
    ):
        raise DecisionExecutionError("DECISION_AGENT_ROUTE_PROVENANCE_INVALID")
    generation = snapshot.get("generation_parameters")
    if not isinstance(generation, dict):
        raise DecisionExecutionError("DECISION_AGENT_ROUTE_PROVENANCE_INVALID")
    primary = _attempt(
        db,
        run=run,
        model_id=str(snapshot["model_id"]),
        model_version=snapshot.get("model_version"),
        fallback_used=False,
    )
    attempts = [primary]
    fallback_id = snapshot.get("fallback_model_id")
    if fallback_id is not None:
        if not isinstance(fallback_id, str):
            raise DecisionExecutionError("DECISION_AGENT_ROUTE_PROVENANCE_INVALID")
        attempts.append(
            _attempt(
                db,
                run=run,
                model_id=fallback_id,
                model_version=snapshot.get("fallback_model_version"),
                fallback_used=True,
            )
        )
    if route.fallback_policy == "FAILOVER" and len(attempts) != 2:
        raise DecisionExecutionError("DECISION_AGENT_ROUTE_PROVENANCE_INVALID")
    return PreparedDecisionExecution(
        stage_id=stage.id,
        run_id=run.id,
        role=stage.role,
        fencing_token=fencing_token,
        stage_input_hash=stage.input_hash,
        provider_input=provider_input,
        provider_input_hash=decision_agent_input_hash(provider_input),
        prompt=prompt.system_prompt,
        prompt_version=str(snapshot["prompt_version"]),
        route_id=route.id,
        route_version=int(snapshot["route_version"]),
        route_version_hash=str(snapshot["route_version_hash"]),
        timeout_ms=int(generation["timeout_ms"]),
        service_tier=str(generation["service_tier"]),
        max_output_tokens=int(generation["max_output_tokens"]),
        temperature=(float(generation["temperature"]) if generation.get("temperature") is not None else None),
        top_p=(float(generation["top_p"]) if generation.get("top_p") is not None else None),
        reasoning_effort=(str(generation["reasoning_effort"]) if generation.get("reasoning_effort") is not None else None),
        seed=(int(generation["seed"]) if generation.get("seed") is not None else None),
        fallback_policy=route.fallback_policy,
        attempts=tuple(attempts),
    )


def _request(
    prepared: PreparedDecisionExecution,
    *,
    invocation_id: str,
    attempt: ProviderAttempt,
    now: datetime,
) -> LlmRequest:
    runtime_utc = _aware(now)
    runtime_kst = runtime_utc.astimezone(ZoneInfo("Asia/Seoul"))
    return LlmRequest(
        invocation_id=invocation_id,
        role=prepared.role,
        model_profile_id=attempt.model_profile_id,
        prompt_version=prepared.prompt_version,
        input_schema_version="decision-agent-input-v1",
        input_hash=prepared.provider_input_hash,
        messages=[
            {"role": "system", "content": prepared.prompt},
            {
                "role": "system",
                "content": "\n".join(
                    (
                        "[Cresta Decision Agent runtime v1]",
                        f"Current time: {runtime_utc.isoformat()} (UTC).",
                        f"Current time: {runtime_kst.isoformat()} (Asia/Seoul).",
                        "Use only the immutable user payload. External tools and web search are disabled.",
                        "Copy evidence IDs only from allowed_evidence_refs and use only schema-listed reason codes.",
                        "Return only decision-agent-model-output-v1; provenance is server-owned.",
                    )
                ),
            },
            {
                "role": "user",
                "content": _canonical(prepared.provider_input.model_dump(mode="json")),
            },
        ],
        output_json_schema=output_schema_for_role(prepared.role),
        timeout_ms=prepared.timeout_ms,
        service_tier=prepared.service_tier,
        max_output_tokens=prepared.max_output_tokens,
        temperature=prepared.temperature,
        top_p=prepared.top_p,
        reasoning_effort=prepared.reasoning_effort,
        seed=prepared.seed,
        tool_policy="NONE",
        allowed_tools=[],
    )


def _create_invocation(
    db: Session,
    *,
    prepared: PreparedDecisionExecution,
    attempt: ProviderAttempt,
    worker_id: str,
    now: datetime,
) -> tuple[str, LlmRequest] | None:
    stage = db.scalar(
        select(AgentStageRun)
        .where(AgentStageRun.id == prepared.stage_id)
        .with_for_update()
    )
    if (
        stage is None
        or stage.state != "RUNNING"
        or stage.lease_owner_id != worker_id
        or stage.fencing_token != prepared.fencing_token
        or stage.lease_expires_at is None
        or _aware(stage.lease_expires_at) <= _aware(now)
    ):
        db.rollback()
        return None
    invocation = LlmInvocation(
        stage_run_id=stage.id,
        requested_provider_profile_id=attempt.provider_profile_id,
        requested_model_profile_id=attempt.model_profile_id,
        state="RUNNING",
        input_hash=prepared.provider_input_hash,
        runtime_context_at=now,
        web_search_enabled=False,
    )
    db.add(invocation)
    db.flush()
    if stage.invocation_id is None:
        stage.invocation_id = invocation.id
    request = _request(prepared, invocation_id=invocation.id, attempt=attempt, now=now)
    db.commit()
    return invocation.id, request


def _call_adapter(attempt: ProviderAttempt, request: LlmRequest) -> LlmResult:
    credential = None
    if attempt.adapter_type != "MOCK":
        if not attempt.credential_secret_ref:
            raise LlmSecretError("credential required")
        credential = LlmSecretStore(get_settings().llm_secret_directory).read(
            attempt.credential_secret_ref
        )
    adapter = provider_registry.resolve(
        attempt.adapter_type,
        endpoint=attempt.endpoint,
        credential=credential,
        chat_path=(
            get_template(attempt.provider_template_id).chat_path
            if attempt.provider_template_id
            else None
        ),
    )
    return adapter.generate_structured(request, attempt.provider_model_id)


def _record_invocation(
    db: Session,
    *,
    prepared: PreparedDecisionExecution,
    attempt: ProviderAttempt,
    invocation_id: str,
    worker_id: str,
    result: LlmResult | None,
    error_state: str | None,
    error_code: str | None,
    now: datetime,
) -> bool:
    stage = db.scalar(
        select(AgentStageRun)
        .where(AgentStageRun.id == prepared.stage_id)
        .with_for_update()
    )
    invocation = db.get(LlmInvocation, invocation_id)
    if (
        stage is None
        or invocation is None
        or stage.state != "RUNNING"
        or stage.lease_owner_id != worker_id
        or stage.fencing_token != prepared.fencing_token
        or stage.lease_expires_at is None
        or _aware(stage.lease_expires_at) <= _aware(now)
    ):
        db.rollback()
        return False
    if result is None:
        invocation.state = error_state or "PROVIDER_ERROR"
        invocation.error_code = error_code
        invocation.completed_at = now
    else:
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
            [prepared.attempts[0].model_profile_id, attempt.model_profile_id]
            if attempt.fallback_used
            else result.fallback_path
        )
        invocation.validation_status = result.schema_validation
        invocation.completed_at = now
        if result.output_json is not None:
            encoded = _canonical(result.output_json)
            if _contains_sensitive_key(result.output_json):
                invocation.state = "INVALID_OUTPUT"
                invocation.validation_status = "FAILED"
                invocation.error_code = "LLM_MODEL_OUTPUT_SENSITIVE_FIELD"
            elif len(encoded.encode("utf-8")) > MAX_MODEL_OUTPUT_BYTES:
                invocation.state = "INVALID_OUTPUT"
                invocation.validation_status = "FAILED"
                invocation.error_code = "LLM_MODEL_OUTPUT_TOO_LARGE"
            else:
                invocation.model_output_json = encoded
                invocation.model_output_hash = hashlib.sha256(encoded.encode()).hexdigest()
                invocation.model_output_captured_at = now
        if invocation.error_code is None and (
            result.status != "SUCCEEDED" or result.schema_validation != "PASSED"
        ):
            invocation.error_code = f"LLM_{result.status}"
    db.commit()
    return True


def _validate_model_output(result: LlmResult) -> tuple[DecisionAgentModelOutput | None, str]:
    if result.status == "TIMED_OUT":
        return None, "DECISION_AGENT_PROVIDER_TIMEOUT"
    if result.status == "INVALID_OUTPUT":
        return None, "DECISION_AGENT_OUTPUT_SCHEMA_INVALID"
    if result.status != "SUCCEEDED":
        return None, "DECISION_AGENT_PROVIDER_ERROR"
    if result.schema_validation != "PASSED":
        return None, "DECISION_AGENT_OUTPUT_SCHEMA_INVALID"
    try:
        output = DecisionAgentModelOutput.model_validate(result.output_json)
    except (ValidationError, TypeError, ValueError) as exc:
        message = str(exc).lower()
        if "reason" in message:
            return None, "DECISION_AGENT_REASON_NOT_ALLOWED"
        if "evidence" in message or "disjoint" in message:
            return None, "DECISION_AGENT_EVIDENCE_NOT_ALLOWED"
        return None, "DECISION_AGENT_OUTPUT_SCHEMA_INVALID"
    return output, ""


def invoke_decision_provider(
    db: Session,
    *,
    prepared: PreparedDecisionExecution,
    worker_id: str,
) -> ProviderOutcome | None:
    for index, attempt in enumerate(prepared.attempts):
        started = datetime.now(UTC)
        created = _create_invocation(
            db,
            prepared=prepared,
            attempt=attempt,
            worker_id=worker_id,
            now=started,
        )
        if created is None:
            return None
        invocation_id, request = created
        result: LlmResult | None = None
        error_state = None
        error_code = None
        try:
            if prepared.service_tier != "DEFAULT" and not supports_service_tier(
                attempt.provider_template_id
            ):
                raise AdapterNotImplementedError("service tier unsupported")
            result = _call_adapter(attempt, request)
        except TimeoutError:
            error_state = "TIMED_OUT"
            error_code = "LLM_TIMED_OUT"
        except (AdapterNotImplementedError, LlmSecretError):
            error_state = "PROVIDER_ERROR"
            error_code = "LLM_PROVIDER_ERROR"
        except Exception:  # noqa: BLE001 - adapter failures must become an ambiguous fail-stop
            error_state = "AMBIGUOUS"
            error_code = "AGENT_INVOCATION_OUTCOME_UNKNOWN"
        completed = datetime.now(UTC)
        if not _record_invocation(
            db,
            prepared=prepared,
            attempt=attempt,
            invocation_id=invocation_id,
            worker_id=worker_id,
            result=result,
            error_state=error_state,
            error_code=error_code,
            now=completed,
        ):
            return None
        if result is not None and result.invocation_id != invocation_id:
            return ProviderOutcome(
                invocation_id,
                "CONFLICTED",
                "DECISION_AGENT_ROUTE_PROVENANCE_INVALID",
                None,
                result.actual_provider,
                result.actual_model,
                attempt.fallback_used,
            )
        if (
            result is not None
            and result.status == "SUCCEEDED"
            and (
                result.actual_provider is None
                or result.actual_model != attempt.provider_model_id
            )
        ):
            return ProviderOutcome(
                invocation_id,
                "CONFLICTED",
                "DECISION_AGENT_ROUTE_PROVENANCE_INVALID",
                None,
                result.actual_provider,
                result.actual_model,
                attempt.fallback_used,
            )
        if result is None:
            reason = (
                "DECISION_AGENT_PROVIDER_TIMEOUT"
                if error_state == "TIMED_OUT"
                else "DECISION_AGENT_CLAIM_OUTCOME_UNKNOWN"
                if error_state == "AMBIGUOUS"
                else "DECISION_AGENT_PROVIDER_ERROR"
            )
            status = "TIMED_OUT" if error_state == "TIMED_OUT" else "FAILED"
            return ProviderOutcome(
                invocation_id, status, reason, None, None, None, attempt.fallback_used
            )
        output, reason = _validate_model_output(result)
        if output is not None:
            try:
                validate_decision_evidence_refs(
                    output,
                    allowed_evidence_refs=set(prepared.provider_input.allowed_evidence_refs),
                )
            except ValueError:
                output = None
                reason = "DECISION_AGENT_EVIDENCE_NOT_ALLOWED"
        if output is not None:
            return ProviderOutcome(
                invocation_id,
                output.status,
                "",
                output,
                result.actual_provider,
                result.actual_model,
                attempt.fallback_used,
            )
        may_failover = (
            prepared.fallback_policy == "FAILOVER"
            and index == 0
            and result.status in {"REFUSED", "RATE_LIMITED", "PROVIDER_ERROR"}
        )
        if may_failover:
            continue
        status = (
            "TIMED_OUT"
            if reason == "DECISION_AGENT_PROVIDER_TIMEOUT"
            else "INVALID_OUTPUT"
            if reason
            in {
                "DECISION_AGENT_OUTPUT_SCHEMA_INVALID",
                "DECISION_AGENT_REASON_NOT_ALLOWED",
                "DECISION_AGENT_EVIDENCE_NOT_ALLOWED",
            }
            else "FAILED"
        )
        return ProviderOutcome(
            invocation_id,
            status,
            reason,
            None,
            result.actual_provider,
            result.actual_model,
            attempt.fallback_used,
        )
    raise AssertionError("decision provider attempt list must not be empty")


def _frozen_result_fields(
    db: Session, *, stage: AgentStageRun, run: AgentRun
) -> dict[str, Any]:
    context = db.scalar(select(DecisionContext).where(DecisionContext.run_id == run.id))
    routes = json.loads(run.route_versions_json)
    route = routes[stage.role]
    policies = json.loads(run.policy_profile_version_map_json or "{}")["profiles"]
    agent_type = ROLE_AGENT_TYPES[stage.role]
    policy = next(item for item in policies if item["agent_type"] == agent_type)
    if context is None:
        raise DecisionExecutionError("DECISION_AGENT_INPUT_PROVENANCE_INVALID")
    return {
        "stage_run_id": stage.id,
        "role": stage.role,
        "decision_context_id": context.id,
        "decision_context_hash": context.context_hash,
        "agent_type": agent_type,
        "policy_profile_id": policy["configuration_version_id"],
        "policy_profile_hash": policy["payload_hash"],
        "policy_profile_version": policy["sequence"],
        "policy_profile_category": policy["category"],
        "route_id": route["route_id"],
        "route_version": route["route_version"],
        "route_version_hash": route["route_version_hash"],
        "prompt_profile_id": route["prompt_profile_id"],
        "prompt_version": route["prompt_version"],
        "prompt_hash": route["prompt_content_hash"],
        "model_id": route["model_id"],
        "requested_model_profile_id": route["model_id"],
        "valid_until": _aware(context.valid_until).isoformat(),
    }


def _result(
    db: Session,
    *,
    stage: AgentStageRun,
    run: AgentRun,
    status: str,
    reason_code: str,
    output: DecisionAgentModelOutput | None,
    actual_provider: str | None,
    actual_model: str | None,
    fallback_used: bool,
) -> DecisionAgentResult:
    provenance = _frozen_result_fields(db, stage=stage, run=run)
    if (actual_provider is None) != (actual_model is None):
        actual_provider = actual_model = None
    semantic = (
        output.model_dump(exclude={"schema_version"})
        if output is not None
        else {
            "status": status,
            "action": "UNKNOWN",
            "confidence": 0.0,
            "entry_score": None,
            "risk_score": None,
            "reason_codes": [reason_code],
            "positive_evidence_refs": [],
            "negative_evidence_refs": [],
        }
    )
    return DecisionAgentResult(
        **provenance,
        **semantic,
        actual_provider=actual_provider,
        actual_model=actual_model,
        fallback_used=fallback_used,
    )


def complete_decision_execution(
    db: Session,
    *,
    prepared: PreparedDecisionExecution | None,
    claim_stage_id: str,
    fencing_token: int,
    worker_id: str,
    outcome: ProviderOutcome | None,
    preparation_error: str | None,
    now: datetime,
) -> bool:
    stage = db.scalar(
        select(AgentStageRun).where(AgentStageRun.id == claim_stage_id).with_for_update()
    )
    if (
        stage is None
        or stage.state != "RUNNING"
        or stage.lease_owner_id != worker_id
        or stage.fencing_token != fencing_token
        or stage.lease_expires_at is None
        or _aware(stage.lease_expires_at) <= _aware(now)
    ):
        db.rollback()
        return False
    run = db.scalar(select(AgentRun).where(AgentRun.id == stage.run_id).with_for_update())
    if run is None:
        db.rollback()
        return False
    final_outcome = outcome
    conflict_reason = preparation_error
    if stage.timeout_at is not None and _aware(stage.timeout_at) <= _aware(now):
        conflict_reason = "DECISION_AGENT_PROVIDER_TIMEOUT"
    if conflict_reason is None:
        try:
            fresh = prepare_decision_execution(
                db,
                stage=stage,
                run=run,
                fencing_token=fencing_token,
                now=now,
            )
            if prepared is None or fresh != prepared:
                conflict_reason = "DECISION_AGENT_INPUT_PROVENANCE_INVALID"
        except DecisionExecutionError as exc:
            conflict_reason = exc.reason_code
    if conflict_reason is not None:
        status = (
            "TIMED_OUT"
            if conflict_reason
            in {"DECISION_AGENT_CONTEXT_EXPIRED", "DECISION_AGENT_PROVIDER_TIMEOUT"}
            else "CONFLICTED"
        )
        final_outcome = ProviderOutcome(
            outcome.invocation_id if outcome else None,
            status,
            conflict_reason,
            None,
            outcome.actual_provider if outcome else None,
            outcome.actual_model if outcome else None,
            outcome.fallback_used if outcome else False,
        )
    if final_outcome is None:
        db.rollback()
        return False
    result = _result(
        db,
        stage=stage,
        run=run,
        status=final_outcome.status,
        reason_code=final_outcome.reason_code,
        output=final_outcome.model_output,
        actual_provider=final_outcome.actual_provider,
        actual_model=final_outcome.actual_model,
        fallback_used=final_outcome.fallback_used,
    )
    encoded = _canonical(result.model_dump(mode="json"))
    stage.state = result.status
    stage.output_json = encoded
    stage.output_hash = hashlib.sha256(encoded.encode()).hexdigest()
    stage.error_code = None if result.status == "SUCCEEDED" else result.reason_codes[0]
    stage.completed_at = now
    stage.lease_owner_id = None
    stage.lease_expires_at = None
    stage.heartbeat_at = now
    db.commit()
    return True


def terminalize_recovered_decision_stage(
    db: Session,
    *,
    stage: AgentStageRun,
    run: AgentRun,
    now: datetime,
) -> bool:
    """Persist the authoritative UNKNOWN result for an expired Decision Agent lease."""
    if stage.role not in DECISION_AGENT_ROLES:
        return False
    invocation = db.scalar(
        select(LlmInvocation)
        .where(LlmInvocation.stage_run_id == stage.id)
        .order_by(LlmInvocation.created_at.desc())
        .limit(1)
    )
    actual_provider = invocation.actual_provider if invocation is not None else None
    actual_model = invocation.actual_model if invocation is not None else None
    if (actual_provider is None) != (actual_model is None):
        actual_provider = actual_model = None
    frozen_model_id = json.loads(run.route_versions_json)[stage.role]["model_id"]
    result = _result(
        db,
        stage=stage,
        run=run,
        status="TIMED_OUT",
        reason_code="DECISION_AGENT_CLAIM_OUTCOME_UNKNOWN",
        output=None,
        actual_provider=actual_provider,
        actual_model=actual_model,
        fallback_used=bool(
            invocation is not None
            and invocation.requested_model_profile_id is not None
            and invocation.requested_model_profile_id != frozen_model_id
        ),
    )
    encoded = _canonical(result.model_dump(mode="json"))
    stage.state = result.status
    stage.output_json = encoded
    stage.output_hash = hashlib.sha256(encoded.encode()).hexdigest()
    stage.error_code = result.reason_codes[0]
    stage.completed_at = now
    return True


def execute_decision_agent_stage(
    db: Session,
    *,
    claim_stage_id: str,
    fencing_token: int,
    worker_id: str,
) -> bool:
    stage = db.scalar(
        select(AgentStageRun).where(AgentStageRun.id == claim_stage_id).with_for_update()
    )
    if (
        stage is None
        or stage.state != "RUNNING"
        or stage.lease_owner_id != worker_id
        or stage.fencing_token != fencing_token
    ):
        db.rollback()
        return False
    run = db.get(AgentRun, stage.run_id)
    if run is None:
        db.rollback()
        return False
    prepared = None
    preparation_error = None
    try:
        prepared = prepare_decision_execution(
            db,
            stage=stage,
            run=run,
            fencing_token=fencing_token,
            now=datetime.now(UTC),
        )
    except DecisionExecutionError as exc:
        preparation_error = exc.reason_code
    db.commit()
    outcome = (
        invoke_decision_provider(db, prepared=prepared, worker_id=worker_id)
        if prepared is not None
        else None
    )
    return complete_decision_execution(
        db,
        prepared=prepared,
        claim_stage_id=claim_stage_id,
        fencing_token=fencing_token,
        worker_id=worker_id,
        outcome=outcome,
        preparation_error=preparation_error,
        now=datetime.now(UTC),
    )
