from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.orm import Session

from app.activation_authority import (
    production_activation_evidence_loader,
    production_activation_validation_policy,
    unavailable_activation_evidence_loader,
)
from app.activation_gate import (
    ActivationGatePayload,
    ActivationStrictModel,
    ActivationValidationPolicy,
    EvidenceLoader,
    activate_activation_gate,
    activation_gate_history,
    create_activation_gate_draft,
    validate_activation_gate_draft,
)
from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.config import Settings, get_settings
from app.db import get_db
from app.models import ConfigurationVersion

router = APIRouter(prefix="/settings/v7-entry-activation", tags=["settings"])


class ActivationGateDraftRequest(ActivationStrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    gate: ActivationGatePayload
    reason: str = Field(min_length=1, max_length=500)


class ActivationGateActionRequest(ActivationStrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")


class ActivationGateVersionResponse(ActivationStrictModel):
    schema_version: str = "1.0"
    request_id: str
    version_id: str
    sequence: int
    state: str
    payload_hash: str
    gate: ActivationGatePayload
    reason: str
    created_at: datetime
    validated_at: datetime | None
    activated_at: datetime | None


class ActivationGateHistoryResponse(ActivationStrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[ActivationGateVersionResponse]


def _unavailable_evidence_loader(_reference: str) -> bytes:
    return unavailable_activation_evidence_loader(_reference)


def get_activation_evidence_loader(
    settings: Settings = Depends(get_settings),
) -> EvidenceLoader:
    """Return the deployment-owned immutable artifact resolver dependency."""

    return production_activation_evidence_loader(settings)


def get_activation_validation_policy(
    settings: Settings = Depends(get_settings),
) -> ActivationValidationPolicy:
    return production_activation_validation_policy(settings)


def _response(request_id: str, version: ConfigurationVersion) -> ActivationGateVersionResponse:
    return ActivationGateVersionResponse(
        request_id=request_id,
        version_id=version.id,
        sequence=version.sequence,
        state=version.state,
        payload_hash=version.payload_hash,
        gate=ActivationGatePayload.model_validate_json(version.payload_json),
        reason=version.reason,
        created_at=version.created_at,
        validated_at=version.validated_at,
        activated_at=version.activated_at,
    )


@router.post("/drafts", response_model=ActivationGateVersionResponse)
def post_activation_gate_draft(
    payload: ActivationGateDraftRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    evidence_loader: EvidenceLoader = Depends(get_activation_evidence_loader),
    validation_policy: ActivationValidationPolicy = Depends(
        get_activation_validation_policy
    ),
) -> ActivationGateVersionResponse:
    version = create_activation_gate_draft(
        db,
        user=context.user,
        payload=payload.gate,
        reason=payload.reason,
        now=datetime.now(UTC),
        evidence_loader=evidence_loader,
        policy=validation_policy,
    )
    return _response(request.state.request_id, version)


@router.post("/{version_id}/validate", response_model=ActivationGateVersionResponse)
def validate_activation_gate_version(
    version_id: str,
    _payload: ActivationGateActionRequest,
    request: Request,
    _: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    evidence_loader: EvidenceLoader = Depends(get_activation_evidence_loader),
    validation_policy: ActivationValidationPolicy = Depends(
        get_activation_validation_policy
    ),
) -> ActivationGateVersionResponse:
    version = validate_activation_gate_draft(
        db,
        version_id=version_id,
        now=datetime.now(UTC),
        evidence_loader=evidence_loader,
        policy=validation_policy,
    )
    return _response(request.state.request_id, version)


@router.post("/{version_id}/activate", response_model=ActivationGateVersionResponse)
def activate_activation_gate_version(
    version_id: str,
    _payload: ActivationGateActionRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    evidence_loader: EvidenceLoader = Depends(get_activation_evidence_loader),
    validation_policy: ActivationValidationPolicy = Depends(
        get_activation_validation_policy
    ),
) -> ActivationGateVersionResponse:
    version = activate_activation_gate(
        db,
        user=context.user,
        version_id=version_id,
        now=datetime.now(UTC),
        evidence_loader=evidence_loader,
        policy=validation_policy,
        correlation_id=request.state.request_id,
        request_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", "unknown"),
    )
    return _response(request.state.request_id, version)


@router.get("/history", response_model=ActivationGateHistoryResponse)
def get_activation_gate_history(
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ActivationGateHistoryResponse:
    return ActivationGateHistoryResponse(
        request_id=request.state.request_id,
        items=[_response(request.state.request_id, item) for item in activation_gate_history(db)],
    )
