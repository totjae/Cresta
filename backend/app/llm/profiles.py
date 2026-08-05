from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import ip_address
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
