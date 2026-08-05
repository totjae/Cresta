from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import ip_address
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.service import consume_reauth_proof
from app.llm.adapters.mock import MOCK_CAPABILITIES
from app.llm.contracts import ModelCapabilities, ProviderHealth
from app.llm.registry import AdapterNotImplementedError, provider_registry
from app.llm.router import RouteBoundaryError, validate_foundation_route
from app.models import (
    AuditLog,
    LlmModelProfile,
    LlmProviderProfile,
    LlmRoleRoute,
    User,
)

ADAPTER_TYPES = {
    "MOCK",
    "OPENAI_RESPONSES",
    "ANTHROPIC_MESSAGES",
    "GEMINI_GENERATE_CONTENT",
    "VERCEL_AI_GATEWAY",
    "OPENAI_COMPATIBLE",
    "OLLAMA_NATIVE",
    "OLLAMA_OPENAI_COMPATIBLE",
}
ROLES = {
    "INTEL_COLLECTOR",
    "EVIDENCE_VERIFIER",
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
    "CORE",
}
ASSIGNMENT_ROLES = (
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
    "CORE",
)
ASSIGNMENT_REAUTH_ACTION = "LLM_ROLE_ASSIGNMENT_ACTIVATE"


class LlmProfileError(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _audit(
    db: Session,
    *,
    user: User,
    action: str,
    target: str,
    correlation_id: str,
    metadata: dict[str, object],
) -> None:
    db.add(
        AuditLog(
            actor_type="USER",
            actor_id=user.id,
            action=action,
            target=target,
            result="SUCCESS",
            correlation_id=correlation_id,
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        )
    )


def _safe_endpoint(adapter_type: str, endpoint: str | None) -> str | None:
    normalized = endpoint.strip().rstrip("/") if endpoint else None
    if adapter_type == "MOCK":
        if normalized:
            raise LlmProfileError("MOCK_ENDPOINT_FORBIDDEN")
        return None
    if not normalized:
        raise LlmProfileError("PROVIDER_ENDPOINT_REQUIRED")
    parsed = urlparse(normalized)
    if not parsed.hostname:
        raise LlmProfileError("PROVIDER_ENDPOINT_NOT_ALLOWED")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LlmProfileError("PROVIDER_ENDPOINT_NOT_ALLOWED")
    if any(segment in {".", ".."} for segment in parsed.path.split("/")):
        raise LlmProfileError("PROVIDER_ENDPOINT_NOT_ALLOWED")
    hostname = parsed.hostname.lower()
    try:
        address = ip_address(hostname)
    except ValueError:
        address = None
    local_host = hostname == "localhost" or hostname.endswith(".localhost")
    local_address = bool(address and address.is_loopback)
    if adapter_type.startswith("OLLAMA_"):
        if parsed.scheme not in {"http", "https"} or not (local_host or local_address):
            raise LlmProfileError("PROVIDER_ENDPOINT_NOT_ALLOWED")
        return normalized
    private_address = bool(
        address
        and (
            address.is_private
            or address.is_link_local
            or address.is_unspecified
            or address.is_reserved
        )
    )
    if parsed.scheme != "https" or local_host or private_address or hostname.endswith(".local"):
        raise LlmProfileError("PROVIDER_ENDPOINT_NOT_ALLOWED")
    return normalized


def _validate_data_policy(adapter_type: str, data_policy: str) -> None:
    allowed = {
        "MOCK": {"NONE"},
        "OPENAI_RESPONSES": {"EXTERNAL_CLOUD"},
        "ANTHROPIC_MESSAGES": {"EXTERNAL_CLOUD"},
        "GEMINI_GENERATE_CONTENT": {"EXTERNAL_CLOUD"},
        "VERCEL_AI_GATEWAY": {"GATEWAY"},
        "OPENAI_COMPATIBLE": {"EXTERNAL_CLOUD", "GATEWAY"},
        "OLLAMA_NATIVE": {"LOCAL"},
        "OLLAMA_OPENAI_COMPATIBLE": {"LOCAL"},
    }
    if data_policy not in allowed[adapter_type]:
        raise LlmProfileError("PROVIDER_DATA_POLICY_MISMATCH")


def list_providers(db: Session, owner_id: str) -> list[LlmProviderProfile]:
    return list(
        db.scalars(
            select(LlmProviderProfile)
            .where(LlmProviderProfile.owner_id == owner_id)
            .order_by(LlmProviderProfile.created_at)
        )
    )


def create_provider(
    db: Session,
    *,
    user: User,
    name: str,
    adapter_type: str,
    endpoint: str | None,
    credential_secret_ref: str | None,
    data_policy: str,
    correlation_id: str,
) -> LlmProviderProfile:
    if adapter_type not in ADAPTER_TYPES:
        raise LlmProfileError("PROVIDER_ADAPTER_UNSUPPORTED")
    if adapter_type == "MOCK" and credential_secret_ref:
        raise LlmProfileError("MOCK_CREDENTIAL_FORBIDDEN")
    if credential_secret_ref:
        raise LlmProfileError("FOUNDATION_CREDENTIAL_FORBIDDEN")
    _validate_data_policy(adapter_type, data_policy)
    profile = LlmProviderProfile(
        owner_id=user.id,
        name=name.strip(),
        adapter_type=adapter_type,
        endpoint=_safe_endpoint(adapter_type, endpoint),
        credential_secret_ref=credential_secret_ref.strip() if credential_secret_ref else None,
        data_policy=data_policy,
    )
    db.add(profile)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise LlmProfileError("PROVIDER_NAME_CONFLICT", 409) from exc
    _audit(
        db,
        user=user,
        action="LLM_PROVIDER_CREATED",
        target=profile.id,
        correlation_id=correlation_id,
        metadata={"adapter_type": adapter_type, "has_secret_ref": bool(credential_secret_ref)},
    )
    db.commit()
    db.refresh(profile)
    return profile


def get_provider(db: Session, owner_id: str, provider_id: str) -> LlmProviderProfile:
    profile = db.get(LlmProviderProfile, provider_id)
    if profile is None or profile.owner_id != owner_id:
        raise LlmProfileError("PROVIDER_NOT_FOUND", 404)
    return profile


def test_provider(
    db: Session,
    *,
    user: User,
    provider_id: str,
    correlation_id: str,
) -> tuple[LlmProviderProfile, ProviderHealth]:
    profile = get_provider(db, user.id, provider_id)
    try:
        adapter = provider_registry.resolve(profile.adapter_type)
    except AdapterNotImplementedError as exc:
        raise LlmProfileError("ADAPTER_NOT_IMPLEMENTED", 422) from exc
    health = adapter.healthcheck()
    profile.health_status = health.status
    profile.last_tested_at = datetime.now(UTC)
    profile.state = "VALIDATED"
    profile.version += 1
    _audit(
        db,
        user=user,
        action="LLM_PROVIDER_TESTED",
        target=profile.id,
        correlation_id=correlation_id,
        metadata={"adapter_type": profile.adapter_type, "status": health.status},
    )
    db.commit()
    db.refresh(profile)
    return profile, health


def list_models(db: Session, owner_id: str) -> list[LlmModelProfile]:
    return list(
        db.scalars(
            select(LlmModelProfile)
            .join(LlmProviderProfile)
            .where(LlmProviderProfile.owner_id == owner_id)
            .order_by(LlmModelProfile.created_at)
        )
    )


def create_model(
    db: Session,
    *,
    user: User,
    provider_id: str,
    alias: str,
    provider_model_id: str,
    capabilities: ModelCapabilities,
    max_context_tokens: int | None,
    max_output_tokens: int,
    temperature: Decimal,
    top_p: Decimal | None,
    reasoning_effort: str | None,
    seed: int | None,
    correlation_id: str,
) -> LlmModelProfile:
    provider = get_provider(db, user.id, provider_id)
    model = LlmModelProfile(
        provider_profile_id=provider.id,
        alias=alias.strip(),
        provider_model_id=provider_model_id.strip(),
        capabilities_json=capabilities.model_dump_json(),
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        top_p=top_p,
        reasoning_effort=reasoning_effort,
        seed=seed,
    )
    db.add(model)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise LlmProfileError("MODEL_ALIAS_CONFLICT", 409) from exc
    _audit(
        db,
        user=user,
        action="LLM_MODEL_CREATED",
        target=model.id,
        correlation_id=correlation_id,
        metadata={"provider_id": provider.id, "alias": model.alias},
    )
    db.commit()
    db.refresh(model)
    return model


def get_model(db: Session, owner_id: str, model_id: str) -> LlmModelProfile:
    model = db.get(LlmModelProfile, model_id)
    if model is None:
        raise LlmProfileError("MODEL_NOT_FOUND", 404)
    provider = get_provider(db, owner_id, model.provider_profile_id)
    if provider.id != model.provider_profile_id:
        raise LlmProfileError("MODEL_NOT_FOUND", 404)
    return model


def validate_model(
    db: Session,
    *,
    user: User,
    model_id: str,
    correlation_id: str,
) -> LlmModelProfile:
    model = get_model(db, user.id, model_id)
    provider = get_provider(db, user.id, model.provider_profile_id)
    if provider.adapter_type != "MOCK" or provider.state != "VALIDATED":
        raise LlmProfileError("ADAPTER_NOT_IMPLEMENTED")
    declared = ModelCapabilities.model_validate_json(model.capabilities_json)
    if model.reasoning_effort is not None and not declared.reasoning:
        raise LlmProfileError("MODEL_PARAMETER_UNSUPPORTED_REASONING")
    if model.seed is not None and not declared.seed:
        raise LlmProfileError("MODEL_PARAMETER_UNSUPPORTED_SEED")
    unsupported = [
        key
        for key, enabled in declared.model_dump().items()
        if enabled and not getattr(MOCK_CAPABILITIES, key)
    ]
    if unsupported:
        raise LlmProfileError("MODEL_CAPABILITY_UNVERIFIED")
    model.state = "VALIDATED"
    model.validated_at = datetime.now(UTC)
    model.version += 1
    _audit(
        db,
        user=user,
        action="LLM_MODEL_VALIDATED",
        target=model.id,
        correlation_id=correlation_id,
        metadata={"provider_id": provider.id},
    )
    db.commit()
    db.refresh(model)
    return model


def list_routes(db: Session, owner_id: str) -> list[LlmRoleRoute]:
    return list(
        db.scalars(
            select(LlmRoleRoute)
            .where(LlmRoleRoute.owner_id == owner_id)
            .order_by(LlmRoleRoute.created_at)
        )
    )


def create_route(
    db: Session,
    *,
    user: User,
    role: str,
    primary_model_profile_id: str,
    timeout_ms: int,
    daily_call_limit: int,
    daily_cost_limit_krw: Decimal,
    prompt_version: str,
    output_schema_version: str,
    temperature_override: Decimal | None,
    top_p_override: Decimal | None,
    max_output_tokens_override: int | None,
    reasoning_effort_override: str | None,
    seed_override: int | None,
    reason: str,
    correlation_id: str,
) -> LlmRoleRoute:
    if role not in ROLES:
        raise LlmProfileError("ROLE_UNSUPPORTED")
    get_model(db, user.id, primary_model_profile_id)
    route = LlmRoleRoute(
        owner_id=user.id,
        role=role,
        primary_model_profile_id=primary_model_profile_id,
        timeout_ms=timeout_ms,
        daily_call_limit=daily_call_limit,
        daily_cost_limit_krw=daily_cost_limit_krw,
        prompt_version=prompt_version.strip(),
        output_schema_version=output_schema_version.strip(),
        temperature_override=temperature_override,
        top_p_override=top_p_override,
        max_output_tokens_override=max_output_tokens_override,
        reasoning_effort_override=reasoning_effort_override,
        seed_override=seed_override,
        reason=reason.strip(),
    )
    db.add(route)
    db.flush()
    _audit(
        db,
        user=user,
        action="LLM_ROUTE_CREATED",
        target=route.id,
        correlation_id=correlation_id,
        metadata={"role": role, "execution_stage": "SHADOW"},
    )
    db.commit()
    db.refresh(route)
    return route


def get_route(db: Session, owner_id: str, route_id: str) -> LlmRoleRoute:
    route = db.get(LlmRoleRoute, route_id)
    if route is None or route.owner_id != owner_id:
        raise LlmProfileError("ROUTE_NOT_FOUND", 404)
    return route


def validate_route(
    db: Session,
    *,
    user: User,
    route_id: str,
    correlation_id: str,
) -> LlmRoleRoute:
    route = get_route(db, user.id, route_id)
    if route.state != "DRAFT":
        raise LlmProfileError("ROUTE_STATE_CONFLICT", 409)
    model = get_model(db, user.id, route.primary_model_profile_id)
    capabilities = ModelCapabilities.model_validate_json(model.capabilities_json)
    if route.reasoning_effort_override is not None and not capabilities.reasoning:
        raise LlmProfileError("MODEL_PARAMETER_UNSUPPORTED_REASONING")
    if route.seed_override is not None and not capabilities.seed:
        raise LlmProfileError("MODEL_PARAMETER_UNSUPPORTED_SEED")
    try:
        validate_foundation_route(route, model)
    except RouteBoundaryError as exc:
        raise LlmProfileError(str(exc)) from exc
    route.state = "VALIDATED"
    route.validated_at = datetime.now(UTC)
    route.version += 1
    _audit(
        db,
        user=user,
        action="LLM_ROUTE_VALIDATED",
        target=route.id,
        correlation_id=correlation_id,
        metadata={"role": route.role, "execution_stage": route.execution_stage},
    )
    db.commit()
    db.refresh(route)
    return route


def effective_generation_parameters(
    route: LlmRoleRoute, model: LlmModelProfile
) -> dict[str, object]:
    return {
        "temperature": route.temperature_override
        if route.temperature_override is not None
        else model.temperature,
        "temperature_source": "ROLE_OVERRIDE"
        if route.temperature_override is not None
        else "MODEL_DEFAULT",
        "top_p": route.top_p_override if route.top_p_override is not None else model.top_p,
        "top_p_source": "ROLE_OVERRIDE"
        if route.top_p_override is not None
        else ("MODEL_DEFAULT" if model.top_p is not None else "ADAPTER_DEFAULT"),
        "max_output_tokens": route.max_output_tokens_override
        if route.max_output_tokens_override is not None
        else model.max_output_tokens,
        "max_output_tokens_source": "ROLE_OVERRIDE"
        if route.max_output_tokens_override is not None
        else "MODEL_DEFAULT",
        "reasoning_effort": route.reasoning_effort_override
        if route.reasoning_effort_override is not None
        else model.reasoning_effort,
        "reasoning_effort_source": "ROLE_OVERRIDE"
        if route.reasoning_effort_override is not None
        else (
            "MODEL_DEFAULT" if model.reasoning_effort is not None else "ADAPTER_DEFAULT"
        ),
        "seed": route.seed_override if route.seed_override is not None else model.seed,
        "seed_source": "ROLE_OVERRIDE"
        if route.seed_override is not None
        else ("MODEL_DEFAULT" if model.seed is not None else "ADAPTER_DEFAULT"),
    }


def _assignment_routes(
    db: Session, *, owner_id: str, route_ids: dict[str, str], lock: bool = False
) -> list[LlmRoleRoute]:
    if set(route_ids) != set(ASSIGNMENT_ROLES):
        raise LlmProfileError("ROLE_ASSIGNMENT_SET_INCOMPLETE")
    query = select(LlmRoleRoute).where(LlmRoleRoute.id.in_(route_ids.values()))
    if lock:
        query = query.with_for_update()
    routes = list(db.scalars(query))
    by_id = {route.id: route for route in routes}
    selected: list[LlmRoleRoute] = []
    for role in ASSIGNMENT_ROLES:
        route = by_id.get(route_ids[role])
        if (
            route is None
            or route.owner_id != owner_id
            or route.role != role
            or route.state not in {"VALIDATED", "ACTIVE"}
            or route.execution_stage != "SHADOW"
        ):
            raise LlmProfileError("ROLE_ASSIGNMENT_NOT_READY")
        model = get_model(db, owner_id, route.primary_model_profile_id)
        if model.state != "VALIDATED":
            raise LlmProfileError("ROLE_ASSIGNMENT_NOT_READY")
        selected.append(route)
    return selected


def assignment_target_id(route_ids: dict[str, str]) -> str:
    canonical = json.dumps(route_ids, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def preview_assignment_activation(
    db: Session, *, owner_id: str, route_ids: dict[str, str]
) -> tuple[str, list[LlmRoleRoute]]:
    routes = _assignment_routes(db, owner_id=owner_id, route_ids=route_ids)
    return assignment_target_id(route_ids), routes


def activate_assignments(
    db: Session,
    *,
    user: User,
    route_ids: dict[str, str],
    reauth_proof: str,
    correlation_id: str,
) -> list[LlmRoleRoute]:
    selected = _assignment_routes(db, owner_id=user.id, route_ids=route_ids, lock=True)
    target_id = assignment_target_id(route_ids)
    consume_reauth_proof(
        db,
        user=user,
        raw_proof=reauth_proof,
        target_action=ASSIGNMENT_REAUTH_ACTION,
        target_id=target_id,
    )
    current = list(
        db.scalars(
            select(LlmRoleRoute)
            .where(
                LlmRoleRoute.owner_id == user.id,
                LlmRoleRoute.role.in_(ASSIGNMENT_ROLES),
                LlmRoleRoute.state == "ACTIVE",
            )
            .with_for_update()
        )
    )
    selected_ids = {route.id for route in selected}
    now = datetime.now(UTC)
    for route in current:
        if route.id not in selected_ids:
            route.state = "SUPERSEDED"
            route.version += 1
    db.flush()
    for route in selected:
        if route.state != "ACTIVE":
            route.state = "ACTIVE"
            route.activated_at = now
            route.version += 1
    _audit(
        db,
        user=user,
        action="LLM_ROLE_ASSIGNMENTS_ACTIVATED",
        target=target_id,
        correlation_id=correlation_id,
        metadata={"route_ids": route_ids, "roles": list(ASSIGNMENT_ROLES)},
    )
    db.commit()
    for route in selected:
        db.refresh(route)
    return selected
