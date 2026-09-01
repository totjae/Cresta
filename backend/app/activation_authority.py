from __future__ import annotations

import re
from pathlib import Path

from app.activation_evidence import (
    ACTIVATION_SPEC_VERSION,
    ACTIVATION_TEST_PLAN_VERSION,
    EXPECTED_REQUIRED_ACCEPTANCE_SET_HASH,
    ActivationEvidenceStore,
    required_acceptance_set_hash,
)
from app.activation_gate import (
    ActivationGateError,
    ActivationValidationPolicy,
    EvidenceLoader,
)
from app.config import EXPECTED_MIGRATION_HEAD, Settings

DEPLOYED_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class FilesystemActivationEvidenceLoader:
    """Read-only production facade over the content-addressed evidence store."""

    __slots__ = ("_store",)

    def __init__(self, artifact_root: Path) -> None:
        self._store = ActivationEvidenceStore(artifact_root)

    def __call__(self, reference: str) -> bytes:
        return self._store.read(reference)


def unavailable_activation_evidence_loader(_reference: str) -> bytes:
    raise ActivationGateError(
        "ACTIVATION_GATE_EVIDENCE_UNAVAILABLE", 503, retryable=True
    )


def production_activation_evidence_loader(settings: Settings) -> EvidenceLoader:
    if settings.artifact_root is None:
        return unavailable_activation_evidence_loader
    return FilesystemActivationEvidenceLoader(settings.artifact_root)


def production_activation_validation_policy(
    settings: Settings,
) -> ActivationValidationPolicy:
    default = ActivationValidationPolicy()
    calculated_hash = required_acceptance_set_hash(default.required_test_ids)
    if calculated_hash != EXPECTED_REQUIRED_ACCEPTANCE_SET_HASH:
        raise ActivationGateError("ACTIVATION_GATE_INVALID")
    return ActivationValidationPolicy(
        code_revision=(
            settings.deployed_revision
            if settings.deployed_revision
            and DEPLOYED_REVISION_PATTERN.fullmatch(settings.deployed_revision)
            else None
        ),
        test_plan_version=ACTIVATION_TEST_PLAN_VERSION,
        spec_version=ACTIVATION_SPEC_VERSION,
        migration_revision=EXPECTED_MIGRATION_HEAD,
        environment=settings.environment.upper(),
        required_acceptance_set_hash=EXPECTED_REQUIRED_ACCEPTANCE_SET_HASH,
        require_artifact_v1=True,
    )


def activation_authority_is_configured(settings: Settings) -> bool:
    return (
        settings.artifact_root is not None
        and settings.deployed_revision is not None
        and DEPLOYED_REVISION_PATTERN.fullmatch(settings.deployed_revision) is not None
    )
