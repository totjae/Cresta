from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.config import Settings, get_settings
from app.db import get_db
from app.llm.contracts import ModelCapabilities
from app.llm.discovery import catalog_items
from app.llm.profiles import (
    activate_assignments,
    create_model,
    create_provider,
    create_route,
    delete_provider,
    disable_model,
    effective_generation_parameters,
    get_model_for_history,
    list_models,
    list_providers,
    list_routes,
    preview_assignment_activation,
    preview_provider_credential,
    preview_provider_deletion,
    preview_provider_registration,
    register_provider_with_discovery,
    route_dependencies_available,
    set_provider_credential,
    sync_provider_models,
    test_provider,
    validate_model,
    validate_route,
)
from app.llm.prompts import create_prompt, get_prompt, list_prompts, validate_prompt
from app.models import LlmModelProfile, LlmPromptProfile, LlmProviderProfile, LlmRoleRoute
from app.schemas import (
    LlmAssignmentActivateRequest,
    LlmAssignmentActivationRequest,
    LlmAssignmentActivationResponse,
    LlmAssignmentPreviewResponse,
    LlmCapabilitiesPayload,
    LlmCredentialPreviewResponse,
    LlmCredentialSetRequest,
    LlmModelCreateRequest,
    LlmModelListResponse,
    LlmModelResponse,
    LlmPromptCreateRequest,
    LlmPromptListResponse,
    LlmPromptResponse,
    LlmProviderCatalogItem,
    LlmProviderCatalogResponse,
    LlmProviderCreateRequest,
    LlmProviderDeletionPreviewResponse,
    LlmProviderDeletionRequest,
    LlmProviderListResponse,
    LlmProviderRegistrationPreviewRequest,
    LlmProviderRegistrationPreviewResponse,
    LlmProviderRegistrationRequest,
    LlmProviderRegistrationResponse,
    LlmProviderResponse,
    LlmProviderTestResponse,
    LlmRoleAssignmentItem,
    LlmRoleAssignmentListResponse,
    LlmRouteCreateRequest,
    LlmRouteListResponse,
    LlmRouteResponse,
)

router = APIRouter(prefix="/ai", tags=["ai-provider-foundation"])


def _provider_response(profile: LlmProviderProfile) -> LlmProviderResponse:
    return LlmProviderResponse(
        id=profile.id,
        name=profile.name,
        provider_template_id=profile.provider_template_id,
        adapter_type=profile.adapter_type,
        endpoint=profile.endpoint,
        credential_configured=profile.credential_secret_ref is not None,
        data_policy=profile.data_policy,
        state=profile.state,
        health_status=profile.health_status,
        last_tested_at=profile.last_tested_at,
        version=profile.version,
        created_at=profile.created_at,
    )


def _model_response(model: LlmModelProfile) -> LlmModelResponse:
    return LlmModelResponse(
        id=model.id,
        provider_profile_id=model.provider_profile_id,
        alias=model.alias,
        provider_model_id=model.provider_model_id,
        capabilities=LlmCapabilitiesPayload.model_validate_json(model.capabilities_json),
        max_context_tokens=model.max_context_tokens,
        max_output_tokens=model.max_output_tokens,
        temperature=model.temperature,
        top_p=model.top_p,
        reasoning_effort=model.reasoning_effort,
        seed=model.seed,
        state=model.state,
        validated_at=model.validated_at,
        version=model.version,
        created_at=model.created_at,
    )


def _route_response(db: Session, owner_id: str, route: LlmRoleRoute) -> LlmRouteResponse:
    model = get_model_for_history(db, owner_id, route.primary_model_profile_id)
    fallback_ids = json.loads(route.fallback_model_profile_ids_json)
    fallback_model = (
        get_model_for_history(db, owner_id, fallback_ids[0]) if fallback_ids else None
    )
    prompt = (
        get_prompt(db, owner_id=owner_id, prompt_id=route.prompt_profile_id)
        if route.prompt_profile_id
        else None
    )
    return LlmRouteResponse(
        id=route.id,
        role=route.role,
        primary_model_profile_id=route.primary_model_profile_id,
        primary_model_alias=model.alias,
        failure_policy=route.fallback_policy,
        fallback_model_profile_id=fallback_model.id if fallback_model else None,
        fallback_model_alias=fallback_model.alias if fallback_model else None,
        execution_stage=route.execution_stage,
        timeout_ms=route.timeout_ms,
        max_attempts=route.max_attempts,
        daily_call_limit=route.daily_call_limit,
        daily_cost_limit_krw=route.daily_cost_limit_krw,
        prompt_version=route.prompt_version,
        prompt_profile_id=route.prompt_profile_id,
        prompt_content_hash=prompt.content_hash if prompt else None,
        output_schema_version=route.output_schema_version,
        temperature_override=route.temperature_override,
        top_p_override=route.top_p_override,
        max_output_tokens_override=route.max_output_tokens_override,
        reasoning_effort_override=route.reasoning_effort_override,
        seed_override=route.seed_override,
        effective_parameters=effective_generation_parameters(route, model),
        state=route.state,
        reason=route.reason,
        validated_at=route.validated_at,
        version=route.version,
        created_at=route.created_at,
    )


def _prompt_response(prompt: LlmPromptProfile) -> LlmPromptResponse:
    return LlmPromptResponse(
        id=prompt.id,
        role=prompt.role,
        version_number=prompt.version_number,
        version_label=prompt.version_label,
        system_prompt=prompt.system_prompt,
        content_hash=prompt.content_hash,
        state=prompt.state,
        reason=prompt.reason,
        validated_at=prompt.validated_at,
        version=prompt.version,
        created_at=prompt.created_at,
    )


@router.get("/provider-catalog", response_model=LlmProviderCatalogResponse)
def get_provider_catalog(
    request: Request,
    _: AuthContext = Depends(get_auth_context),
) -> LlmProviderCatalogResponse:
    return LlmProviderCatalogResponse(
        request_id=request.state.request_id,
        items=[
            LlmProviderCatalogItem(
                template_id=item.template_id,
                adapter_type=item.adapter_type,
                label=item.label,
                can_register=item.can_register,
                support_level=item.support_level,
                configuration_fields=[
                    {
                        "key": field.key,
                        "label": field.label,
                        "minimum_length": field.minimum_length,
                        "maximum_length": field.maximum_length,
                    }
                    for field in item.configuration_fields
                ],
            )
            for item in catalog_items()
        ],
    )


@router.post(
    "/provider-registrations/preview",
    response_model=LlmProviderRegistrationPreviewResponse,
)
def post_provider_registration_preview(
    payload: LlmProviderRegistrationPreviewRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmProviderRegistrationPreviewResponse:
    target_id = preview_provider_registration(
        db,
        owner_id=context.user.id,
        name=payload.name,
        template_id=payload.template_id or payload.adapter_type or "",
    )
    return LlmProviderRegistrationPreviewResponse(
        request_id=request.state.request_id, target_id=target_id
    )


@router.post(
    "/provider-registrations",
    response_model=LlmProviderRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_provider_registration(
    payload: LlmProviderRegistrationRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LlmProviderRegistrationResponse:
    provider, models = register_provider_with_discovery(
        db,
        user=context.user,
        name=payload.name,
        template_id=payload.template_id or payload.adapter_type or "",
        configuration=payload.configuration,
        credential=payload.credential,
        correlation_id=request.state.request_id,
        settings=settings,
    )
    return LlmProviderRegistrationResponse(
        request_id=request.state.request_id,
        provider=_provider_response(provider),
        models=[_model_response(model) for model in models],
    )


@router.get("/providers", response_model=LlmProviderListResponse)
def get_providers(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> LlmProviderListResponse:
    return LlmProviderListResponse(
        request_id=request.state.request_id,
        items=[_provider_response(item) for item in list_providers(db, context.user.id)],
    )


@router.post(
    "/providers/{provider_id}/delete-preview",
    response_model=LlmProviderDeletionPreviewResponse,
)
def post_provider_delete_preview(
    provider_id: str,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmProviderDeletionPreviewResponse:
    provider, target_id = preview_provider_deletion(
        db, owner_id=context.user.id, provider_id=provider_id
    )
    return LlmProviderDeletionPreviewResponse(
        request_id=request.state.request_id,
        target_id=target_id,
        provider_id=provider.id,
    )


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider_profile(
    provider_id: str,
    payload: LlmProviderDeletionRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    delete_provider(
        db,
        user=context.user,
        provider_id=provider_id,
        correlation_id=request.state.request_id,
        settings=settings,
    )


@router.post("/providers", response_model=LlmProviderResponse, status_code=status.HTTP_201_CREATED)
def post_provider(
    payload: LlmProviderCreateRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmProviderResponse:
    profile = create_provider(
        db,
        user=context.user,
        name=payload.name,
        adapter_type=payload.adapter_type,
        endpoint=payload.endpoint,
        credential_secret_ref=payload.credential_secret_ref,
        data_policy=payload.data_policy,
        correlation_id=request.state.request_id,
    )
    return _provider_response(profile)


@router.post("/providers/{provider_id}/test", response_model=LlmProviderTestResponse)
def post_provider_test(
    provider_id: str,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LlmProviderTestResponse:
    profile, health = test_provider(
        db,
        user=context.user,
        provider_id=provider_id,
        correlation_id=request.state.request_id,
        settings=settings,
    )
    return LlmProviderTestResponse(
        request_id=request.state.request_id,
        provider=_provider_response(profile),
        external_network_used=health.external_network_used,
        capabilities=LlmCapabilitiesPayload.model_validate(health.capabilities.model_dump()),
        message_code=health.message_code,
    )


@router.post(
    "/providers/{provider_id}/models/sync",
    response_model=LlmProviderRegistrationResponse,
)
def post_provider_models_sync(
    provider_id: str,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LlmProviderRegistrationResponse:
    provider, models = sync_provider_models(
        db,
        user=context.user,
        provider_id=provider_id,
        correlation_id=request.state.request_id,
        settings=settings,
    )
    return LlmProviderRegistrationResponse(
        request_id=request.state.request_id,
        provider=_provider_response(provider),
        models=[_model_response(model) for model in models],
    )


@router.post(
    "/providers/{provider_id}/credential-preview",
    response_model=LlmCredentialPreviewResponse,
)
def post_provider_credential_preview(
    provider_id: str,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmCredentialPreviewResponse:
    profile, target_id = preview_provider_credential(
        db, owner_id=context.user.id, provider_id=provider_id
    )
    return LlmCredentialPreviewResponse(
        request_id=request.state.request_id,
        target_id=target_id,
        provider_id=profile.id,
    )


@router.post("/providers/{provider_id}/credential", response_model=LlmProviderResponse)
def post_provider_credential(
    provider_id: str,
    payload: LlmCredentialSetRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LlmProviderResponse:
    profile = set_provider_credential(
        db,
        user=context.user,
        provider_id=provider_id,
        credential=payload.credential,
        correlation_id=request.state.request_id,
        settings=settings,
    )
    return _provider_response(profile)


@router.get("/models", response_model=LlmModelListResponse)
def get_models(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> LlmModelListResponse:
    return LlmModelListResponse(
        request_id=request.state.request_id,
        items=[_model_response(item) for item in list_models(db, context.user.id)],
    )


@router.post("/models", response_model=LlmModelResponse, status_code=status.HTTP_201_CREATED)
def post_model(
    payload: LlmModelCreateRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmModelResponse:
    model = create_model(
        db,
        user=context.user,
        provider_id=payload.provider_profile_id,
        alias=payload.alias,
        provider_model_id=payload.provider_model_id,
        capabilities=ModelCapabilities.model_validate(payload.capabilities.model_dump()),
        max_context_tokens=payload.max_context_tokens,
        max_output_tokens=payload.max_output_tokens,
        temperature=payload.temperature,
        top_p=payload.top_p,
        reasoning_effort=payload.reasoning_effort,
        seed=payload.seed,
        correlation_id=request.state.request_id,
    )
    return _model_response(model)


@router.post("/models/{model_id}/validate", response_model=LlmModelResponse)
def post_model_validate(
    model_id: str,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmModelResponse:
    model = validate_model(
        db,
        user=context.user,
        model_id=model_id,
        correlation_id=request.state.request_id,
    )
    return _model_response(model)


@router.post("/models/{model_id}/disable", response_model=LlmModelResponse)
def post_model_disable(
    model_id: str,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmModelResponse:
    model = disable_model(
        db,
        user=context.user,
        model_id=model_id,
        correlation_id=request.state.request_id,
    )
    return _model_response(model)


@router.get("/prompts", response_model=LlmPromptListResponse)
def get_prompt_profiles(
    request: Request,
    role: str | None = None,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> LlmPromptListResponse:
    return LlmPromptListResponse(
        request_id=request.state.request_id,
        items=[
            _prompt_response(item)
            for item in list_prompts(db, owner_id=context.user.id, role=role)
        ],
    )


@router.post("/prompts", response_model=LlmPromptResponse, status_code=status.HTTP_201_CREATED)
def post_prompt_profile(
    payload: LlmPromptCreateRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmPromptResponse:
    prompt = create_prompt(
        db,
        user=context.user,
        role=payload.role,
        system_prompt=payload.system_prompt,
        reason=payload.reason,
        correlation_id=request.state.request_id,
    )
    return _prompt_response(prompt)


@router.post("/prompts/{prompt_id}/validate", response_model=LlmPromptResponse)
def post_prompt_validate(
    prompt_id: str,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmPromptResponse:
    prompt = validate_prompt(
        db,
        user=context.user,
        prompt_id=prompt_id,
        correlation_id=request.state.request_id,
    )
    return _prompt_response(prompt)


@router.get("/routes", response_model=LlmRouteListResponse)
def get_routes(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> LlmRouteListResponse:
    return LlmRouteListResponse(
        request_id=request.state.request_id,
        items=[
            _route_response(db, context.user.id, item) for item in list_routes(db, context.user.id)
        ],
    )


@router.get("/role-assignments", response_model=LlmRoleAssignmentListResponse)
def get_role_assignments(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> LlmRoleAssignmentListResponse:
    routes = list_routes(db, context.user.id)
    roles = (
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
        "CORE",
    )
    items: list[LlmRoleAssignmentItem] = []
    for role in roles:
        role_routes = [route for route in routes if route.role == role]
        active = next((route for route in role_routes if route.state == "ACTIVE"), None)
        candidates = [
            route
            for route in role_routes
            if route.state == "VALIDATED"
            and route_dependencies_available(db, context.user.id, route)
        ]
        status_value = (
            "ACTIVE"
            if active
            else "AMBIGUOUS"
            if len(candidates) > 1
            else "CANDIDATE"
            if len(candidates) == 1
            else "UNASSIGNED"
        )
        items.append(
            LlmRoleAssignmentItem(
                role=role,
                current=_route_response(db, context.user.id, active) if active else None,
                candidates=[_route_response(db, context.user.id, route) for route in candidates],
                history_count=len(role_routes),
                status=status_value,
            )
        )
    return LlmRoleAssignmentListResponse(request_id=request.state.request_id, items=items)


@router.post("/routes", response_model=LlmRouteResponse, status_code=status.HTTP_201_CREATED)
def post_route(
    payload: LlmRouteCreateRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmRouteResponse:
    route = create_route(
        db,
        user=context.user,
        role=payload.role,
        primary_model_profile_id=payload.primary_model_profile_id,
        failure_policy=payload.failure_policy,
        fallback_model_profile_id=payload.fallback_model_profile_id,
        timeout_ms=payload.timeout_ms,
        daily_call_limit=payload.daily_call_limit,
        daily_cost_limit_krw=payload.daily_cost_limit_krw,
        prompt_version=payload.prompt_version,
        prompt_profile_id=payload.prompt_profile_id,
        output_schema_version=payload.output_schema_version,
        temperature_override=payload.temperature_override,
        top_p_override=payload.top_p_override,
        max_output_tokens_override=payload.max_output_tokens_override,
        reasoning_effort_override=payload.reasoning_effort_override,
        seed_override=payload.seed_override,
        reason=payload.reason,
        correlation_id=request.state.request_id,
    )
    return _route_response(db, context.user.id, route)


@router.post("/routes/{route_id}/validate", response_model=LlmRouteResponse)
def post_route_validate(
    route_id: str,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmRouteResponse:
    route = validate_route(
        db,
        user=context.user,
        route_id=route_id,
        correlation_id=request.state.request_id,
    )
    return _route_response(db, context.user.id, route)


@router.post(
    "/role-assignments/activation-preview",
    response_model=LlmAssignmentPreviewResponse,
)
def post_assignment_activation_preview(
    payload: LlmAssignmentActivationRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmAssignmentPreviewResponse:
    target_id, routes = preview_assignment_activation(
        db, owner_id=context.user.id, route_ids=payload.route_ids
    )
    return LlmAssignmentPreviewResponse(
        request_id=request.state.request_id,
        target_id=target_id,
        routes=[_route_response(db, context.user.id, route) for route in routes],
    )


@router.post(
    "/role-assignments/activate",
    response_model=LlmAssignmentActivationResponse,
)
def post_assignment_activate(
    payload: LlmAssignmentActivateRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LlmAssignmentActivationResponse:
    routes = activate_assignments(
        db,
        user=context.user,
        route_ids=payload.route_ids,
        correlation_id=request.state.request_id,
    )
    return LlmAssignmentActivationResponse(
        request_id=request.state.request_id,
        routes=[_route_response(db, context.user.id, route) for route in routes],
    )
