from __future__ import annotations

import json
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
    if route.execution_stage != "SHADOW" or route.fallback_policy not in {
        "FAIL_STOP",
        "FAILOVER",
    }:
        raise RouteBoundaryError("ROUTE_FOUNDATION_BOUNDARY_VIOLATION")
    try:
        fallback_ids = json.loads(route.fallback_model_profile_ids_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RouteBoundaryError("ROUTE_FOUNDATION_BOUNDARY_VIOLATION") from exc
    if (
        route.max_attempts != 1
        or not isinstance(fallback_ids, list)
        or len(fallback_ids) > 1
        or (route.fallback_policy == "FAIL_STOP" and fallback_ids)
        or (route.fallback_policy == "FAILOVER" and len(fallback_ids) != 1)
    ):
        raise RouteBoundaryError("ROUTE_FOUNDATION_BOUNDARY_VIOLATION")
    return FoundationRoutePlan(
        role=route.role,
        model_profile_id=model.id,
        execution_stage=route.execution_stage,
        fallback_policy=route.fallback_policy,
    )
