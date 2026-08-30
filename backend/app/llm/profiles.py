from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import ip_address
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm.adapters.mock import MOCK_CAPABILITIES
from app.llm.contracts import ModelCapabilities, ProviderHealth
from app.llm.discovery import (
    DiscoveredModel,
    ModelDiscoveryError,
    discover_models,
    get_template,
    resolve_endpoint,
)
from app.llm.parameter_policy import (
    is_gemini_3_model,
    is_openai_reasoning_model,
    supports_service_tier,
)
from app.llm.prompts import LlmPromptError, get_prompt
from app.llm.registry import AdapterNotImplementedError, provider_registry
from app.llm.router import RouteBoundaryError, validate_foundation_route
from app.llm.secrets import LlmSecretError, LlmSecretStore
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
    "CONSERVATIVE_DECISION",
    "BALANCED_DECISION",
    "AGGRESSIVE_DECISION",
}
LEGACY_ASSIGNMENT_ROLES = (
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
    "CORE",
)
DECISION_AGENT_ROLES = (
    "CONSERVATIVE_DECISION",
    "BALANCED_DECISION",
    "AGGRESSIVE_DECISION",
)
ASSIGNMENT_ROLES = (*LEGACY_ASSIGNMENT_ROLES, *DECISION_AGENT_ROLES)


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
            .where(
                LlmProviderProfile.owner_id == owner_id,
                LlmProviderProfile.deleted_at.is_(None),
            )
            .order_by(LlmProviderProfile.created_at)
        )
    )


def registration_target_id(*, owner_id: str, name: str, template_id: str) -> str:
    value = f"{owner_id}:{name.strip()}:{template_id}"
    return hashlib.sha256(value.encode()).hexdigest()


def preview_provider_registration(
    db: Session, *, owner_id: str, name: str, template_id: str
) -> str:
    try:
        template = get_template(template_id)
    except ModelDiscoveryError as exc:
        raise LlmProfileError(exc.code, exc.status_code) from exc
    if not template.can_register:
        raise LlmProfileError("PROVIDER_REGISTRATION_UNAVAILABLE")
    normalized_name = name.strip()
    existing = db.scalar(
        select(LlmProviderProfile.id).where(
            LlmProviderProfile.owner_id == owner_id,
            LlmProviderProfile.name == normalized_name,
            LlmProviderProfile.deleted_at.is_(None),
        )
    )
    if existing:
        raise LlmProfileError("PROVIDER_NAME_CONFLICT", 409)
    return registration_target_id(
        owner_id=owner_id, name=normalized_name, template_id=template.template_id
    )


def _discovered_alias(model_id: str, used: set[str]) -> str:
    base = "".join(char if char.isalnum() or char in "_.-" else "-" for char in model_id)
    base = base.strip(".-_")[:64] or "model"
    alias = base
    suffix = 2
    while alias in used:
        tail = f"-{suffix}"
        alias = f"{base[: 64 - len(tail)]}{tail}"
        suffix += 1
    used.add(alias)
    return alias


def _discovered_capabilities(
    adapter_type: str,
    provider_template_id: str | None = None,
    model_id: str = "",
) -> ModelCapabilities:
    return ModelCapabilities(
        structured_output=True,
        web_search=(
            adapter_type in {
                "OPENAI_RESPONSES",
                "ANTHROPIC_MESSAGES",
                "GEMINI_GENERATE_CONTENT",
            }
            or provider_template_id == "llm-gateway"
        ),
        reasoning=(
            adapter_type in {"OPENAI_RESPONSES", "GEMINI_GENERATE_CONTENT"}
            or (
                adapter_type == "OPENAI_COMPATIBLE"
                and (
                    is_openai_reasoning_model(model_id)
                    or is_gemini_3_model(model_id)
                )
            )
        ),
        seed=adapter_type == "GEMINI_GENERATE_CONTENT",
        usage_reporting=True,
    )


def _add_discovered_models(
    db: Session,
    *,
    provider: LlmProviderProfile,
    discovered: list[DiscoveredModel],
) -> list[LlmModelProfile]:
    existing = list(
        db.scalars(
            select(LlmModelProfile).where(LlmModelProfile.provider_profile_id == provider.id)
        )
    )
    by_provider_id = {item.provider_model_id: item for item in existing}
    used_aliases = {item.alias for item in existing}
    created: list[LlmModelProfile] = []
    for item in discovered:
        capabilities = _discovered_capabilities(
            provider.adapter_type,
            provider.provider_template_id,
            item.provider_model_id,
        )
        if item.provider_model_id in by_provider_id:
            existing_model = by_provider_id[item.provider_model_id]
            declared = ModelCapabilities.model_validate_json(
                existing_model.capabilities_json
            )
            capability_upgrades = {
                field: True
                for field in ("web_search", "reasoning", "seed")
                if getattr(capabilities, field) and not getattr(declared, field)
            }
            if capability_upgrades:
                existing_model.capabilities_json = declared.model_copy(
                    update=capability_upgrades
                ).model_dump_json()
                existing_model.version += 1
            continue
        model = LlmModelProfile(
            provider_profile_id=provider.id,
            alias=_discovered_alias(item.provider_model_id, used_aliases),
            provider_model_id=item.provider_model_id,
            capabilities_json=capabilities.model_dump_json(),
            max_context_tokens=item.max_context_tokens,
            max_output_tokens=min(item.max_output_tokens or 8192, 32768),
            temperature=None,
            top_p=None,
            reasoning_effort=None,
            seed=None,
            state="DRAFT",
        )
        db.add(model)
        created.append(model)
    return created


def register_provider_with_discovery(
    db: Session,
    *,
    user: User,
    name: str,
    template_id: str,
    configuration: dict[str, str] | None,
    credential: str,
    correlation_id: str,
    settings: Settings,
) -> tuple[LlmProviderProfile, list[LlmModelProfile]]:
    normalized_name = name.strip()
    preview_provider_registration(
        db, owner_id=user.id, name=normalized_name, template_id=template_id
    )
    db.commit()
    try:
        template = get_template(template_id)
        endpoint = resolve_endpoint(template, configuration)
        discovered = (
            discover_models(
                template.template_id, credential, configuration=configuration
            )
            if configuration
            else discover_models(template.template_id, credential)
        )
    except ModelDiscoveryError as exc:
        raise LlmProfileError(exc.code, exc.status_code) from exc

    provider = LlmProviderProfile(
        owner_id=user.id,
        name=normalized_name,
        provider_template_id=template.template_id,
        adapter_type=template.adapter_type,
        endpoint=endpoint,
        credential_secret_ref=None,
        data_policy=template.data_policy,
        state="VALIDATED",
        health_status="READY",
        last_tested_at=datetime.now(UTC),
    )
    db.add(provider)
    secret_ref: str | None = None
    try:
        db.flush()
        secret_ref = LlmSecretStore(settings.llm_secret_directory).write_provider_credential(
            provider.id, credential
        )
        provider.credential_secret_ref = secret_ref
        models = _add_discovered_models(db, provider=provider, discovered=discovered)
        _audit(
            db,
            user=user,
            action="LLM_PROVIDER_REGISTERED",
            target=provider.id,
            correlation_id=correlation_id,
            metadata={
                "template_id": template.template_id,
                "adapter_type": template.adapter_type,
                "discovered_model_count": len(models),
            },
        )
        db.commit()
    except (SQLAlchemyError, LlmSecretError) as exc:
        db.rollback()
        if secret_ref:
            try:
                LlmSecretStore(settings.llm_secret_directory).delete(secret_ref)
            except LlmSecretError:
                pass
        if isinstance(exc, IntegrityError):
            raise LlmProfileError("PROVIDER_NAME_CONFLICT", 409) from exc
        if isinstance(exc, LlmSecretError):
            raise LlmProfileError(exc.args[0]) from exc
        raise LlmProfileError("PROVIDER_PERSISTENCE_FAILED", 500) from exc
    db.refresh(provider)
    for model in models:
        db.refresh(model)
    return provider, models


def sync_provider_models(
    db: Session,
    *,
    user: User,
    provider_id: str,
    correlation_id: str,
    settings: Settings,
) -> tuple[LlmProviderProfile, list[LlmModelProfile]]:
    provider = get_provider(db, user.id, provider_id)
    if not provider.provider_template_id or not provider.credential_secret_ref:
        raise LlmProfileError("PROVIDER_CREDENTIAL_REQUIRED")
    try:
        credential = LlmSecretStore(settings.llm_secret_directory).read(
            provider.credential_secret_ref
        )
        discovered = discover_models(
            provider.provider_template_id,
            credential,
            endpoint_override=provider.endpoint,
        )
    except LlmSecretError as exc:
        raise LlmProfileError(exc.args[0]) from exc
    except ModelDiscoveryError as exc:
        raise LlmProfileError(exc.code, exc.status_code) from exc
    created = _add_discovered_models(db, provider=provider, discovered=discovered)
    provider.health_status = "READY"
    provider.state = "VALIDATED"
    provider.last_tested_at = datetime.now(UTC)
    provider.version += 1
    _audit(
        db,
        user=user,
        action="LLM_PROVIDER_MODELS_SYNCED",
        target=provider.id,
        correlation_id=correlation_id,
        metadata={"discovered_model_count": len(discovered), "new_model_count": len(created)},
    )
    db.commit()
    db.refresh(provider)
    for model in created:
        db.refresh(model)
    return provider, created


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
    if profile is None or profile.owner_id != owner_id or profile.deleted_at is not None:
        raise LlmProfileError("PROVIDER_NOT_FOUND", 404)
    return profile


def provider_deletion_target(provider: LlmProviderProfile) -> str:
    return hashlib.sha256(f"{provider.id}:{provider.version}".encode()).hexdigest()


def _route_uses_models(route: LlmRoleRoute, model_ids: set[str]) -> bool:
    if route.primary_model_profile_id in model_ids:
        return True
    try:
        fallback_ids = json.loads(route.fallback_model_profile_ids_json)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(fallback_ids, list) and any(item in model_ids for item in fallback_ids)


def preview_provider_deletion(
    db: Session, *, owner_id: str, provider_id: str
) -> tuple[LlmProviderProfile, str]:
    provider = get_provider(db, owner_id, provider_id)
    model_ids = set(
        db.scalars(
            select(LlmModelProfile.id).where(
                LlmModelProfile.provider_profile_id == provider.id
            )
        )
    )
    active_routes = db.scalars(
        select(LlmRoleRoute).where(
            LlmRoleRoute.owner_id == owner_id,
            LlmRoleRoute.state == "ACTIVE",
        )
    )
    if any(_route_uses_models(route, model_ids) for route in active_routes):
        raise LlmProfileError("PROVIDER_IN_ACTIVE_ROUTE", 409)
    return provider, provider_deletion_target(provider)


def delete_provider(
    db: Session,
    *,
    user: User,
    provider_id: str,
    correlation_id: str,
    settings: Settings,
) -> None:
    provider, _ = preview_provider_deletion(
        db, owner_id=user.id, provider_id=provider_id
    )
    models = list(
        db.scalars(
            select(LlmModelProfile).where(
                LlmModelProfile.provider_profile_id == provider.id
            )
        )
    )
    model_ids = {model.id for model in models}
    routes = [
        route
        for route in db.scalars(
            select(LlmRoleRoute).where(
                LlmRoleRoute.owner_id == user.id,
                LlmRoleRoute.state.in_(("DRAFT", "VALIDATED")),
            )
        )
        if _route_uses_models(route, model_ids)
    ]
    secret_ref = provider.credential_secret_ref
    if secret_ref:
        try:
            LlmSecretStore(settings.llm_secret_directory).delete(secret_ref)
        except LlmSecretError as exc:
            raise LlmProfileError(exc.args[0], 500) from exc
    provider.credential_secret_ref = None
    provider.state = "DISABLED"
    provider.health_status = "DISABLED"
    provider.deleted_at = datetime.now(UTC)
    provider.version += 1
    for model in models:
        model.state = "DISABLED"
        model.version += 1
    for route in routes:
        route.state = "SUPERSEDED"
        route.version += 1
    _audit(
        db,
        user=user,
        action="LLM_PROVIDER_DELETED",
        target=provider.id,
        correlation_id=correlation_id,
        metadata={"template_id": provider.provider_template_id},
    )
    db.commit()


def test_provider(
    db: Session,
    *,
    user: User,
    provider_id: str,
    correlation_id: str,
    settings: Settings | None = None,
) -> tuple[LlmProviderProfile, ProviderHealth]:
    profile = get_provider(db, user.id, provider_id)
    try:
        credential = None
        if profile.adapter_type != "MOCK":
            if profile.credential_secret_ref is None or settings is None:
                raise LlmProfileError("PROVIDER_CREDENTIAL_REQUIRED")
            credential = LlmSecretStore(settings.llm_secret_directory).read(
                profile.credential_secret_ref
            )
        adapter = provider_registry.resolve(
            profile.adapter_type,
            endpoint=profile.endpoint,
            credential=credential,
        )
    except LlmSecretError as exc:
        raise LlmProfileError(exc.args[0]) from exc
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


def credential_target_id(profile: LlmProviderProfile) -> str:
    value = f"{profile.id}:{profile.version}:{profile.adapter_type}"
    return hashlib.sha256(value.encode()).hexdigest()


def preview_provider_credential(
    db: Session, *, owner_id: str, provider_id: str
) -> tuple[LlmProviderProfile, str]:
    profile = get_provider(db, owner_id, provider_id)
    if profile.adapter_type == "MOCK":
        raise LlmProfileError("MOCK_CREDENTIAL_FORBIDDEN")
    if profile.adapter_type not in {
        "OPENAI_RESPONSES",
        "ANTHROPIC_MESSAGES",
        "GEMINI_GENERATE_CONTENT",
        "OPENAI_COMPATIBLE",
    }:
        raise LlmProfileError("ADAPTER_NOT_IMPLEMENTED")
    return profile, credential_target_id(profile)


def set_provider_credential(
    db: Session,
    *,
    user: User,
    provider_id: str,
    credential: str,
    correlation_id: str,
    settings: Settings,
) -> LlmProviderProfile:
    profile = get_provider(db, user.id, provider_id)
    preview_provider_credential(db, owner_id=user.id, provider_id=provider_id)
    try:
        secret_ref = LlmSecretStore(settings.llm_secret_directory).write_provider_credential(
            profile.id, credential
        )
    except LlmSecretError as exc:
        db.rollback()
        raise LlmProfileError(exc.args[0]) from exc
    profile.credential_secret_ref = secret_ref
    profile.state = "DRAFT"
    profile.health_status = "UNKNOWN"
    profile.last_tested_at = None
    profile.version += 1
    _audit(
        db,
        user=user,
        action="LLM_PROVIDER_CREDENTIAL_SET",
        target=profile.id,
        correlation_id=correlation_id,
        metadata={"adapter_type": profile.adapter_type, "credential_configured": True},
    )
    db.commit()
    db.refresh(profile)
    return profile


def list_models(db: Session, owner_id: str) -> list[LlmModelProfile]:
    return list(
        db.scalars(
            select(LlmModelProfile)
            .join(LlmProviderProfile)
            .where(
                LlmProviderProfile.owner_id == owner_id,
                LlmProviderProfile.deleted_at.is_(None),
            )
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
    temperature: Decimal | None,
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


def get_model_for_history(
    db: Session, owner_id: str, model_id: str
) -> LlmModelProfile:
    model = db.get(LlmModelProfile, model_id)
    if model is None:
        raise LlmProfileError("MODEL_NOT_FOUND", 404)
    provider = db.get(LlmProviderProfile, model.provider_profile_id)
    if provider is None or provider.owner_id != owner_id:
        raise LlmProfileError("MODEL_NOT_FOUND", 404)
    return model


def route_dependencies_available(
    db: Session, owner_id: str, route: LlmRoleRoute
) -> bool:
    try:
        fallback_ids = _fallback_model_ids(route)
    except LlmProfileError:
        return False
    model_ids = [route.primary_model_profile_id, *fallback_ids]
    for model_id in model_ids:
        model = db.get(LlmModelProfile, model_id)
        if model is None or model.state != "VALIDATED":
            return False
        provider = db.get(LlmProviderProfile, model.provider_profile_id)
        if not (
            provider is not None
            and provider.owner_id == owner_id
            and provider.deleted_at is None
            and provider.state == "VALIDATED"
        ):
            return False
    return True


def _fallback_model_ids(route: LlmRoleRoute) -> list[str]:
    try:
        value = json.loads(route.fallback_model_profile_ids_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise LlmProfileError("ROUTE_FALLBACK_INVALID") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise LlmProfileError("ROUTE_FALLBACK_INVALID")
    return value


def validate_model(
    db: Session,
    *,
    user: User,
    model_id: str,
    correlation_id: str,
) -> LlmModelProfile:
    model = get_model(db, user.id, model_id)
    provider = get_provider(db, user.id, model.provider_profile_id)
    if provider.state != "VALIDATED":
        raise LlmProfileError("PROVIDER_NOT_VALIDATED")
    declared = ModelCapabilities.model_validate_json(model.capabilities_json)
    if model.reasoning_effort is not None and not declared.reasoning:
        raise LlmProfileError("MODEL_PARAMETER_UNSUPPORTED_REASONING")
    if model.seed is not None and not declared.seed:
        raise LlmProfileError("MODEL_PARAMETER_UNSUPPORTED_SEED")
    if provider.adapter_type == "MOCK":
        available = MOCK_CAPABILITIES
    else:
        try:
            available = (
                provider_registry.resolve(
                    provider.adapter_type,
                    endpoint=provider.endpoint,
                    credential="capability-check-only",
                )
                .healthcheck()
                .capabilities
            )
        except AdapterNotImplementedError as exc:
            raise LlmProfileError("ADAPTER_NOT_IMPLEMENTED") from exc
    unsupported = [
        key
        for key, enabled in declared.model_dump().items()
        if enabled and not getattr(available, key)
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


def disable_model(
    db: Session,
    *,
    user: User,
    model_id: str,
    correlation_id: str,
) -> LlmModelProfile:
    model = get_model(db, user.id, model_id)
    active_routes = db.scalars(
        select(LlmRoleRoute).where(
            LlmRoleRoute.owner_id == user.id,
            LlmRoleRoute.state == "ACTIVE",
        )
    )
    if any(_route_uses_models(route, {model.id}) for route in active_routes):
        raise LlmProfileError("MODEL_IN_ACTIVE_ROUTE", 409)
    model.state = "DISABLED"
    model.version += 1
    _audit(
        db,
        user=user,
        action="LLM_MODEL_DISABLED",
        target=model.id,
        correlation_id=correlation_id,
        metadata={"provider_id": model.provider_profile_id},
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
    failure_policy: str,
    fallback_model_profile_id: str | None,
    timeout_ms: int,
    daily_call_limit: int,
    daily_cost_limit_krw: Decimal,
    prompt_version: str | None,
    prompt_profile_id: str | None,
    output_schema_version: str,
    temperature_override: Decimal | None,
    top_p_override: Decimal | None,
    max_output_tokens_override: int | None,
    reasoning_effort_override: str | None,
    seed_override: int | None,
    reason: str,
    correlation_id: str,
    service_tier: str = "DEFAULT",
    web_search_enabled: bool = False,
) -> LlmRoleRoute:
    if role not in ROLES:
        raise LlmProfileError("ROLE_UNSUPPORTED")
    if role in DECISION_AGENT_ROLES and web_search_enabled:
        raise LlmProfileError("WEB_SEARCH_ROLE_UNSUPPORTED")
    if role in DECISION_AGENT_ROLES and output_schema_version.strip() != (
        "decision-agent-model-output-v1"
    ):
        raise LlmProfileError("ROUTE_OUTPUT_SCHEMA_INVALID")
    if role in DECISION_AGENT_ROLES and prompt_profile_id is None:
        raise LlmProfileError("PROMPT_REQUIRED")
    get_model(db, user.id, primary_model_profile_id)
    if failure_policy not in {"FAIL_STOP", "FAILOVER"}:
        raise LlmProfileError("ROUTE_FAILURE_POLICY_INVALID")
    if failure_policy == "FAIL_STOP" and fallback_model_profile_id is not None:
        raise LlmProfileError("ROUTE_FALLBACK_NOT_ALLOWED")
    if failure_policy == "FAILOVER" and fallback_model_profile_id is None:
        raise LlmProfileError("ROUTE_FALLBACK_REQUIRED")
    if fallback_model_profile_id == primary_model_profile_id:
        raise LlmProfileError("ROUTE_FALLBACK_EQUALS_PRIMARY")
    if fallback_model_profile_id is not None:
        get_model(db, user.id, fallback_model_profile_id)
    resolved_prompt_version = prompt_version.strip() if prompt_version else None
    if prompt_profile_id:
        try:
            prompt = get_prompt(db, owner_id=user.id, prompt_id=prompt_profile_id)
        except LlmPromptError as exc:
            raise LlmProfileError(exc.code, exc.status_code) from exc
        if prompt.role != role:
            raise LlmProfileError("PROMPT_ROLE_MISMATCH")
        if prompt.state != "VALIDATED":
            raise LlmProfileError("PROMPT_NOT_VALIDATED")
        resolved_prompt_version = prompt.version_label
    if not resolved_prompt_version:
        raise LlmProfileError("PROMPT_REQUIRED")
    route = LlmRoleRoute(
        owner_id=user.id,
        role=role,
        primary_model_profile_id=primary_model_profile_id,
        fallback_policy=failure_policy,
        fallback_model_profile_ids_json=json.dumps(
            [fallback_model_profile_id] if fallback_model_profile_id else [],
            separators=(",", ":"),
        ),
        timeout_ms=timeout_ms,
        service_tier=service_tier,
        web_search_enabled=web_search_enabled,
        daily_call_limit=daily_call_limit,
        daily_cost_limit_krw=daily_cost_limit_krw,
        prompt_version=resolved_prompt_version,
        prompt_profile_id=prompt_profile_id,
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
        metadata={
            "role": role,
            "execution_stage": "SHADOW",
            "failure_policy": failure_policy,
            "fallback_configured": fallback_model_profile_id is not None,
            "service_tier": service_tier,
            "web_search_enabled": web_search_enabled,
        },
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
    provider = get_provider(db, user.id, model.provider_profile_id)
    fallback_models = [get_model(db, user.id, item) for item in _fallback_model_ids(route)]
    if route.role in DECISION_AGENT_ROLES and (
        route.web_search_enabled
        or route.prompt_profile_id is None
        or route.output_schema_version != "decision-agent-model-output-v1"
    ):
        raise LlmProfileError("ROLE_ASSIGNMENT_NOT_READY")
    if route.prompt_profile_id:
        try:
            prompt = get_prompt(db, owner_id=user.id, prompt_id=route.prompt_profile_id)
        except LlmPromptError as exc:
            raise LlmProfileError(exc.code, exc.status_code) from exc
        if prompt.role != route.role or prompt.state != "VALIDATED":
            raise LlmProfileError("PROMPT_ROUTE_NOT_READY")
    if provider.state != "VALIDATED" or model.state != "VALIDATED":
        raise LlmProfileError("ROUTE_DEPENDENCY_NOT_VALIDATED")
    for fallback_model in fallback_models:
        fallback_provider = get_provider(db, user.id, fallback_model.provider_profile_id)
        if fallback_model.state != "VALIDATED" or fallback_provider.state != "VALIDATED":
            raise LlmProfileError("ROUTE_DEPENDENCY_NOT_VALIDATED")
        if fallback_provider.adapter_type != "MOCK" and not fallback_provider.credential_secret_ref:
            raise LlmProfileError("PROVIDER_CREDENTIAL_REQUIRED")
        try:
            provider_registry.resolve(
                fallback_provider.adapter_type,
                endpoint=fallback_provider.endpoint,
                credential=(
                    "route-validation" if fallback_provider.adapter_type != "MOCK" else None
                ),
            )
        except AdapterNotImplementedError as exc:
            raise LlmProfileError("ADAPTER_NOT_IMPLEMENTED") from exc
    if provider.adapter_type != "MOCK" and not provider.credential_secret_ref:
        raise LlmProfileError("PROVIDER_CREDENTIAL_REQUIRED")
    tier_providers = [
        provider,
        *(get_provider(db, user.id, item.provider_profile_id) for item in fallback_models),
    ]
    if route.service_tier != "DEFAULT" and any(
        not supports_service_tier(item.provider_template_id) for item in tier_providers
    ):
        raise LlmProfileError("SERVICE_TIER_UNSUPPORTED")
    if route.daily_cost_limit_krw > 0:
        raise LlmProfileError("DAILY_COST_LIMIT_UNAVAILABLE")
    try:
        provider_registry.resolve(
            provider.adapter_type,
            endpoint=provider.endpoint,
            credential=("route-validation" if provider.adapter_type != "MOCK" else None),
        )
    except AdapterNotImplementedError as exc:
        raise LlmProfileError("ADAPTER_NOT_IMPLEMENTED") from exc
    capabilities = ModelCapabilities.model_validate_json(model.capabilities_json)
    model_capabilities = [
        capabilities,
        *(ModelCapabilities.model_validate_json(item.capabilities_json) for item in fallback_models),
    ]
    effective_temperatures = [
        route.temperature_override
        if route.temperature_override is not None
        else item.temperature
        for item in [model, *fallback_models]
    ]
    provider_model_pairs = list(zip(tier_providers, [model, *fallback_models], strict=True))
    if any(
        item is not None and item > 1
        for item, (item_provider, _) in zip(
            effective_temperatures, provider_model_pairs, strict=True
        )
        if item_provider.adapter_type == "ANTHROPIC_MESSAGES"
    ):
        raise LlmProfileError("MODEL_PARAMETER_UNSUPPORTED_TEMPERATURE")
    if (
        route.temperature_override is not None or route.top_p_override is not None
    ) and any(
            item_provider.adapter_type == "GEMINI_GENERATE_CONTENT"
            and is_gemini_3_model(item_model.provider_model_id)
            for item_provider, item_model in provider_model_pairs
    ):
        raise LlmProfileError("MODEL_PARAMETER_USE_ADAPTER_DEFAULT")
    if route.reasoning_effort_override is not None and any(
        not item.reasoning for item in model_capabilities
    ):
        raise LlmProfileError("MODEL_PARAMETER_UNSUPPORTED_REASONING")
    if route.seed_override is not None and any(not item.seed for item in model_capabilities):
        raise LlmProfileError("MODEL_PARAMETER_UNSUPPORTED_SEED")
    if route.web_search_enabled:
        if route.role not in {"NEWS_DISCLOSURE_SCOUT", "MARKET_SECTOR_SCOUT"}:
            raise LlmProfileError("WEB_SEARCH_ROLE_UNSUPPORTED")
        if any(not item.web_search for item in model_capabilities):
            raise LlmProfileError("MODEL_CAPABILITY_UNSUPPORTED_WEB_SEARCH")
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
        else ("MODEL_DEFAULT" if model.temperature is not None else "ADAPTER_DEFAULT"),
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
        else ("MODEL_DEFAULT" if model.reasoning_effort is not None else "ADAPTER_DEFAULT"),
        "seed": route.seed_override if route.seed_override is not None else model.seed,
        "seed_source": "ROLE_OVERRIDE"
        if route.seed_override is not None
        else ("MODEL_DEFAULT" if model.seed is not None else "ADAPTER_DEFAULT"),
    }


def _assignment_routes(
    db: Session, *, owner_id: str, route_ids: dict[str, str], lock: bool = False
) -> list[LlmRoleRoute]:
    selected_roles = tuple(route_ids)
    if frozenset(selected_roles) not in {
        frozenset(LEGACY_ASSIGNMENT_ROLES),
        frozenset(ASSIGNMENT_ROLES),
    }:
        raise LlmProfileError("ROLE_ASSIGNMENT_SET_INCOMPLETE")
    query = select(LlmRoleRoute).where(LlmRoleRoute.id.in_(route_ids.values()))
    if lock:
        query = query.with_for_update()
    routes = list(db.scalars(query))
    by_id = {route.id: route for route in routes}
    selected: list[LlmRoleRoute] = []
    ordered_roles = tuple(role for role in ASSIGNMENT_ROLES if role in route_ids)
    for role in ordered_roles:
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
        if model.state != "VALIDATED" or not route_dependencies_available(
            db, owner_id, route
        ):
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
    correlation_id: str,
) -> list[LlmRoleRoute]:
    selected = _assignment_routes(db, owner_id=user.id, route_ids=route_ids, lock=True)
    target_id = assignment_target_id(route_ids)
    current = list(
        db.scalars(
            select(LlmRoleRoute)
            .where(
                LlmRoleRoute.owner_id == user.id,
                LlmRoleRoute.role.in_(route_ids),
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
        metadata={"route_ids": route_ids, "roles": list(route_ids)},
    )
    db.commit()
    for route in selected:
        db.refresh(route)
    return selected
