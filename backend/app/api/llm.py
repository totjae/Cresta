from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.db import get_db
from app.llm.contracts import ModelCapabilities
from app.llm.profiles import (
    create_model,
    create_provider,
    create_route,
    get_model,
    list_models,
    list_providers,
    list_routes,
    test_provider,
    validate_model,
    validate_route,
)
from app.models import LlmModelProfile, LlmProviderProfile, LlmRoleRoute
from app.schemas import (
    LlmCapabilitiesPayload,
    LlmModelCreateRequest,
    LlmModelListResponse,
    LlmModelResponse,
    LlmProviderCreateRequest,
    LlmProviderListResponse,
    LlmProviderResponse,
    LlmProviderTestResponse,
    LlmRouteCreateRequest,
    LlmRouteListResponse,
    LlmRouteResponse,
)

router = APIRouter(prefix="/ai", tags=["ai-provider-foundation"])


def _provider_response(profile: LlmProviderProfile) -> LlmProviderResponse:
    return LlmProviderResponse(
        id=profile.id,
        name=profile.name,
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
        state=model.state,
        validated_at=model.validated_at,
        version=model.version,
        created_at=model.created_at,
    )


def _route_response(db: Session, owner_id: str, route: LlmRoleRoute) -> LlmRouteResponse:
    model = get_model(db, owner_id, route.primary_model_profile_id)
    return LlmRouteResponse(
        id=route.id,
        role=route.role,
        primary_model_profile_id=route.primary_model_profile_id,
        primary_model_alias=model.alias,
        fallback_policy=route.fallback_policy,
        execution_stage=route.execution_stage,
        timeout_ms=route.timeout_ms,
        max_attempts=route.max_attempts,
        daily_call_limit=route.daily_call_limit,
        daily_cost_limit_krw=route.daily_cost_limit_krw,
        prompt_version=route.prompt_version,
        output_schema_version=route.output_schema_version,
        state=route.state,
        reason=route.reason,
        validated_at=route.validated_at,
        version=route.version,
        created_at=route.created_at,
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
) -> LlmProviderTestResponse:
    profile, health = test_provider(
        db,
        user=context.user,
        provider_id=provider_id,
        correlation_id=request.state.request_id,
    )
    return LlmProviderTestResponse(
        request_id=request.state.request_id,
        provider=_provider_response(profile),
        external_network_used=health.external_network_used,
        capabilities=LlmCapabilitiesPayload.model_validate(health.capabilities.model_dump()),
        message_code=health.message_code,
    )


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
        timeout_ms=payload.timeout_ms,
        daily_call_limit=payload.daily_call_limit,
        daily_cost_limit_krw=payload.daily_cost_limit_krw,
        prompt_version=payload.prompt_version,
        output_schema_version=payload.output_schema_version,
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
