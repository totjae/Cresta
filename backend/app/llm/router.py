from __future__ import annotations

from dataclasses import dataclass

from app.models import LlmModelProfile, LlmRoleRoute


class RouteBoundaryError(Exception):
    pass


@dataclass(frozen=True)
class FoundationRoutePlan:
    role: str
    model_profile_id: str
    execution_stage: str
    fallback_policy: str
    external_execution_enabled: bool = False


def validate_foundation_route(route: LlmRoleRoute, model: LlmModelProfile) -> FoundationRoutePlan:
    if model.state != "VALIDATED":
        raise RouteBoundaryError("ROUTE_MODEL_NOT_VALIDATED")
    if route.execution_stage != "SHADOW" or route.fallback_policy != "NONE":
        raise RouteBoundaryError("ROUTE_FOUNDATION_BOUNDARY_VIOLATION")
    if route.max_attempts != 1 or route.fallback_model_profile_ids_json != "[]":
        raise RouteBoundaryError("ROUTE_FOUNDATION_BOUNDARY_VIOLATION")
    return FoundationRoutePlan(
        role=route.role,
        model_profile_id=model.id,
        execution_stage=route.execution_stage,
        fallback_policy=route.fallback_policy,
    )
