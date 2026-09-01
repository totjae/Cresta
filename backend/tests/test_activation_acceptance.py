from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.activation_acceptance import (
    AcceptanceOutcome,
    GateAcceptanceResult,
    NodeResult,
    collect_bound_nodeids,
    load_authoritative_manifest,
    publish_passed_results,
)
from app.activation_authority import (
    FilesystemActivationEvidenceLoader,
    production_activation_evidence_loader,
    production_activation_validation_policy,
)
from app.activation_evidence import (
    ACTIVATION_SPEC_VERSION,
    ACTIVATION_TEST_PLAN_VERSION,
    EXPECTED_REQUIRED_ACCEPTANCE_SET_HASH,
    ActivationEvidenceStore,
    ActivationEvidenceStoreError,
    EvidenceStoreFailureCategory,
    parse_canonical_artifact,
)
from app.activation_gate import (
    REQUIRED_ACTIVATION_TEST_IDS,
    ActivationGateError,
    ActivationGatePayload,
    SafetyEvidence,
    validate_activation_payload,
    version_snapshot_hash,
)
from app.config import EXPECTED_MIGRATION_HEAD, Settings
from app.models import AuditLog, ConfigurationVersion
from tests.test_phase_9c1_foundation import _snapshot_payload
from tests.test_phase_9c2_trading_runtime import _setup

MANIFEST_PATH = Path(__file__).with_name("activation_acceptance_bindings.json")
REVISION = "5" * 40


def _all_pass_results():
    manifest = load_authoritative_manifest(MANIFEST_PATH)
    return manifest, tuple(
        GateAcceptanceResult(
            test_id=binding.test_id,
            nodeids=tuple(binding.test_nodeids),
            outcome=AcceptanceOutcome.PASSED,
            duration_ms=1,
        )
        for binding in manifest.bindings
    )


def _strict_gate(
    artifact_root: Path,
    *,
    snapshot: dict[str, object] | object | None = None,
) -> ActivationGatePayload:
    now = datetime.now(UTC)
    manifest, results = _all_pass_results()
    published = publish_passed_results(
        manifest,
        results,
        artifact_root=artifact_root,
        code_revision=REVISION,
        runner_name="pytest",
        runner_version="9.0.2",
        executed_at=now - timedelta(minutes=1),
    )
    store = ActivationEvidenceStore(artifact_root)
    evidence: list[SafetyEvidence] = []
    for item in published:
        body = parse_canonical_artifact(store.read(item.evidence_ref))
        evidence.append(
            SafetyEvidence(
                test_id=body.test_id,
                requirement_ids=body.requirement_ids,
                result="PASSED",
                code_revision=body.code_revision,
                test_plan_version=body.test_plan_version,
                spec_version=body.spec_version,
                executed_at=body.executed_at,
                valid_until=None,
                freshness_contract=body.freshness_contract,
                evidence_ref=item.evidence_ref,
                evidence_hash=item.evidence_hash,
            )
        )
    snapshot_value = snapshot or _snapshot_payload()
    snapshot_dict = (
        snapshot_value.model_dump()
        if hasattr(snapshot_value, "model_dump")
        else snapshot_value
    )
    return ActivationGatePayload.model_validate(
        {
            "schema_version": "activation-gate-v1",
            "gate_state": "OPEN",
            "target": "MOCK",
            "version_snapshot": snapshot_dict,
            "version_snapshot_hash": version_snapshot_hash(snapshot_dict),
            "safety_evidence": evidence,
            "validation_policy_version": "activation-validation-policy-v1",
            "validated_at": now - timedelta(seconds=30),
            "valid_until": now + timedelta(hours=1),
        }
    )


def _policy(root: Path):
    return production_activation_validation_policy(
        Settings(artifact_root=root, deployed_revision=REVISION)
    )


def _report(
    *,
    when: str = "call",
    passed: bool = False,
    failed: bool = False,
    skipped: bool = False,
    wasxfail: str | None = None,
):
    return SimpleNamespace(
        when=when,
        passed=passed,
        failed=failed,
        skipped=skipped,
        wasxfail=wasxfail,
        duration=0.001,
    )


def test_authoritative_manifest_is_exact_complete_and_deterministic() -> None:
    manifest = load_authoritative_manifest(MANIFEST_PATH)
    assert len(manifest.bindings) == len(REQUIRED_ACTIVATION_TEST_IDS) == 118
    assert tuple(item.test_id for item in manifest.bindings) == REQUIRED_ACTIVATION_TEST_IDS
    assert manifest.required_acceptance_set_hash == EXPECTED_REQUIRED_ACCEPTANCE_SET_HASH
    assert manifest.test_plan_version == ACTIVATION_TEST_PLAN_VERSION
    assert all(item.test_nodeids == sorted(item.test_nodeids) for item in manifest.bindings)


def test_node_collection_fails_closed_when_pytest_does_not_collect(monkeypatch) -> None:
    manifest = load_authoritative_manifest(MANIFEST_PATH)
    monkeypatch.setattr("pytest.main", lambda *args, **kwargs: 0)
    with pytest.raises(ValueError, match="unresolved pytest node IDs"):
        collect_bound_nodeids(
            manifest,
            backend_root=MANIFEST_PATH.parents[1],
            test_ids=(REQUIRED_ACTIVATION_TEST_IDS[0],),
        )


@pytest.mark.parametrize(
    ("reports", "expected"),
    [
        ([_report(passed=True)], AcceptanceOutcome.PASSED),
        ([_report(failed=True)], AcceptanceOutcome.FAILED),
        ([_report(when="setup", failed=True)], AcceptanceOutcome.ERROR),
        ([_report(skipped=True)], AcceptanceOutcome.SKIPPED),
        ([_report(skipped=True, wasxfail="reason")], AcceptanceOutcome.XFAIL),
        ([_report(passed=True, wasxfail="reason")], AcceptanceOutcome.XPASS),
        ([], AcceptanceOutcome.NOT_RUN),
    ],
)
def test_structured_runner_classifies_every_normative_outcome(
    reports, expected: AcceptanceOutcome
) -> None:
    assert NodeResult("tests/test_sample.py::test_case", reports).outcome == expected


def test_publisher_refuses_partial_or_nonpass_sets(tmp_path: Path) -> None:
    manifest, results = _all_pass_results()
    failed = replace(results[0], outcome=AcceptanceOutcome.SKIPPED)
    with pytest.raises(ValueError, match="all selected IDs PASSED"):
        publish_passed_results(
            manifest,
            (failed, *results[1:]),
            artifact_root=tmp_path,
            code_revision=REVISION,
            runner_name="pytest",
            runner_version="9.0.2",
            executed_at=datetime.now(UTC),
        )
    assert not (tmp_path / "activation-evidence").exists()


def test_production_loader_is_read_only_and_resolves_exact_bytes(tmp_path: Path) -> None:
    gate = _strict_gate(tmp_path)
    loader = production_activation_evidence_loader(
        Settings(artifact_root=tmp_path, deployed_revision=REVISION)
    )
    assert isinstance(loader, FilesystemActivationEvidenceLoader)
    assert not hasattr(loader, "publish")
    item = gate.safety_evidence[0]
    assert loader(item.evidence_ref)


def test_missing_deployment_authority_and_store_fail_closed(tmp_path: Path) -> None:
    gate = _strict_gate(tmp_path)
    for revision in (None, "not-a-full-lowercase-sha"):
        unavailable_policy = production_activation_validation_policy(
            Settings(artifact_root=tmp_path, deployed_revision=revision)
        )
        with pytest.raises(ActivationGateError) as authority:
            validate_activation_payload(
                gate,
                now=datetime.now(UTC),
                evidence_loader=ActivationEvidenceStore(tmp_path),
                policy=unavailable_policy,
            )
        assert (authority.value.status_code, authority.value.retryable) == (503, True)

    loader = production_activation_evidence_loader(Settings(deployed_revision=REVISION))
    with pytest.raises(ActivationGateError) as unavailable:
        loader(gate.safety_evidence[0].evidence_ref)
    assert (unavailable.value.status_code, unavailable.value.retryable) == (503, True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code_revision", "6" * 40),
        ("test_plan_version", "other-test-plan-v1"),
        ("spec_version", "other-spec-v1"),
        ("migration_revision", "20260829_9999"),
        ("environment", "OTHER"),
        ("required_acceptance_set_hash", "6" * 64),
    ],
)
def test_every_deployment_authority_mismatch_is_invalid(
    tmp_path: Path, field: str, value: str
) -> None:
    gate = _strict_gate(tmp_path)
    policy = replace(_policy(tmp_path), **{field: value})
    with pytest.raises(ActivationGateError) as caught:
        validate_activation_payload(
            gate,
            now=datetime.now(UTC),
            evidence_loader=production_activation_evidence_loader(
                Settings(artifact_root=tmp_path, deployed_revision=REVISION)
            ),
            policy=policy,
        )
    assert (caught.value.code, caught.value.status_code) == (
        "ACTIVATION_GATE_INVALID",
        422,
    )


def test_descriptor_cannot_reuse_another_tests_valid_artifact(tmp_path: Path) -> None:
    gate = _strict_gate(tmp_path)
    first, second = gate.safety_evidence[:2]
    first.evidence_ref = second.evidence_ref
    first.evidence_hash = second.evidence_hash
    with pytest.raises(ActivationGateError) as caught:
        validate_activation_payload(
            gate,
            now=datetime.now(UTC),
            evidence_loader=production_activation_evidence_loader(
                Settings(artifact_root=tmp_path, deployed_revision=REVISION)
            ),
            policy=_policy(tmp_path),
        )
    assert caught.value.code == "ACTIVATION_GATE_INVALID"


@pytest.mark.parametrize(
    ("category", "status", "retryable"),
    [
        (EvidenceStoreFailureCategory.INVALID_REFERENCE, 422, False),
        (EvidenceStoreFailureCategory.NOT_FOUND, 422, False),
        (EvidenceStoreFailureCategory.CORRUPT_OR_HASH_MISMATCH, 422, False),
        (EvidenceStoreFailureCategory.UNREADABLE, 503, True),
        (EvidenceStoreFailureCategory.STORE_UNAVAILABLE, 503, True),
    ],
)
def test_store_failure_taxonomy_maps_without_path_leakage(
    tmp_path: Path,
    category: EvidenceStoreFailureCategory,
    status: int,
    retryable: bool,
) -> None:
    gate = _strict_gate(tmp_path)

    def failed_loader(_reference: str) -> bytes:
        raise ActivationEvidenceStoreError(category)

    with pytest.raises(ActivationGateError) as caught:
        validate_activation_payload(
            gate,
            now=datetime.now(UTC),
            evidence_loader=failed_loader,
            policy=_policy(tmp_path),
        )
    assert (caught.value.status_code, caught.value.retryable) == (status, retryable)
    assert str(tmp_path) not in str(caught.value)


def test_activation_http_errors_and_valid_recreated_session_lifecycle(
    client, db, admin, settings: Settings, monkeypatch, tmp_path: Path
) -> None:
    _, _, actual_snapshot = _setup(client, db, admin, monkeypatch)
    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200
    csrf = session.json()["csrf_token"]
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    gate_without_authority = _strict_gate(tmp_path)
    request = {
        "schema_version": "1.0",
        "gate": gate_without_authority.model_dump(mode="json"),
        "reason": "Phase 11B.0B2 production resolver acceptance",
    }
    no_csrf = client.post("/api/v1/settings/v7-entry-activation/drafts", json=request)
    assert no_csrf.status_code == 403
    unavailable = client.post(
        "/api/v1/settings/v7-entry-activation/drafts", json=request, headers=headers
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["retryable"] is True

    settings.artifact_root = tmp_path
    settings.deployed_revision = REVISION
    invalid = gate_without_authority.model_copy(deep=True)
    invalid.safety_evidence[0].evidence_ref = "sha256:" + "a" * 64
    invalid.safety_evidence[0].evidence_hash = "a" * 64
    request["gate"] = invalid.model_dump(mode="json")
    invalid_response = client.post(
        "/api/v1/settings/v7-entry-activation/drafts", json=request, headers=headers
    )
    assert invalid_response.status_code == 422
    assert invalid_response.json()["error"]["code"] == "ACTIVATION_GATE_INVALID"

    request["gate"] = gate_without_authority.model_dump(mode="json")
    snapshot_mismatch = client.post(
        "/api/v1/settings/v7-entry-activation/drafts", json=request, headers=headers
    )
    assert snapshot_mismatch.status_code == 422

    valid_gate = _strict_gate(tmp_path, snapshot=actual_snapshot)
    request["gate"] = valid_gate.model_dump(mode="json")
    draft = client.post(
        "/api/v1/settings/v7-entry-activation/drafts", json=request, headers=headers
    )
    assert draft.status_code == 200
    assert draft.json()["state"] == "DRAFT"
    version_id = draft.json()["version_id"]

    action = {"schema_version": "1.0"}
    validated = client.post(
        f"/api/v1/settings/v7-entry-activation/{version_id}/validate",
        json=action,
        headers=headers,
    )
    assert validated.status_code == 200
    assert validated.json()["state"] == "VALIDATED"
    active = client.post(
        f"/api/v1/settings/v7-entry-activation/{version_id}/activate",
        json=action,
        headers=headers,
    )
    assert active.status_code == 200
    assert active.json()["state"] == "ACTIVE"
    assert db.scalar(
        select(func.count()).select_from(ConfigurationVersion).where(
            ConfigurationVersion.category == "V7_ENTRY_ACTIVATION",
            ConfigurationVersion.state == "ACTIVE",
        )
    ) == 1
    assert db.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "V7_ENTRY_ACTIVATION_ACTIVATED",
            AuditLog.target == version_id,
        )
    ) == 1


def test_normative_authority_constants_remain_exact() -> None:
    assert ACTIVATION_TEST_PLAN_VERSION == "cresta-v2-activation-test-plan-2026-09-01.1"
    assert ACTIVATION_SPEC_VERSION == "cresta-v2-activation-spec-set-2026-09-01.1"
    assert EXPECTED_MIGRATION_HEAD == "20260829_0044"
    assert EXPECTED_REQUIRED_ACCEPTANCE_SET_HASH == (
        "d740a14dbcc471e588fc2a03776a216e7bc4c2e6053497d604f3e9804cca913e"
    )
