from __future__ import annotations

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.activation_evidence import (
    ARTIFACT_TYPE,
    BINDINGS_SCHEMA_VERSION,
    EXACT_REVISION_FRESHNESS,
    MAX_ARTIFACT_BYTES,
    AcceptanceBinding,
    ActivationAcceptanceBindings,
    ActivationAcceptancePublisher,
    ActivationBindingCompletenessError,
    ActivationEvidenceArtifact,
    ActivationEvidenceError,
    ActivationEvidencePublicationError,
    ActivationEvidenceReferenceError,
    ActivationEvidenceStore,
    ActivationEvidenceStoreError,
    EvidenceReference,
    EvidenceStoreFailureCategory,
    artifact_digest,
    binding_completeness,
    canonical_artifact_bytes,
    parse_activation_acceptance_bindings,
    parse_canonical_artifact,
    require_complete_bindings,
    required_acceptance_set_hash,
)
from app.activation_gate import (
    REQUIRED_ACTIVATION_TEST_IDS,
    ActivationGateError,
    SafetyEvidence,
)
from app.api.activation import _unavailable_evidence_loader

EXPECTED_SET_HASH = "d740a14dbcc471e588fc2a03776a216e7bc4c2e6053497d604f3e9804cca913e"


def _artifact_data(**overrides: object) -> dict[str, object]:
    nodeids = overrides.pop(
        "test_nodeids",
        [
            "tests/test_v7_decision_agent_execution.py::test_timeout[param-2]",
            "tests/test_v7_decision_agent_execution.py::test_timeout[param-1]",
        ],
    )
    assert isinstance(nodeids, list)
    value: dict[str, object] = {
        "schema_version": "1.0",
        "artifact_type": ARTIFACT_TYPE,
        "test_id": REQUIRED_ACTIVATION_TEST_IDS[0],
        "requirement_ids": ["CFG-106", "AI-240"],
        "result": "PASSED",
        "test_nodeids": nodeids,
        "code_revision": "5" * 40,
        "test_plan_version": "cresta-v2-activation-test-plan-2026-09-01.1",
        "spec_version": "cresta-v2-activation-spec-set-2026-09-01.1",
        "migration_revision": "20260829_0044",
        "environment": "MOCK",
        "required_acceptance_set_hash": EXPECTED_SET_HASH,
        "executed_at": datetime(2026, 9, 1, 3, 4, 5, 120_000, tzinfo=UTC),
        "freshness_contract": EXACT_REVISION_FRESHNESS,
        "runner": {"name": "pytest-검증", "version": "9.0.2"},
        "backend": "SQLITE",
        "result_summary": {
            "collected": len(nodeids),
            "passed": len(nodeids),
            "failed": 0,
            "skipped": 0,
            "xfailed": 0,
            "xpassed": 0,
            "errors": 0,
            "duration_ms": 123,
        },
    }
    value.update(overrides)
    return value


def _artifact(**overrides: object) -> ActivationEvidenceArtifact:
    return ActivationEvidenceArtifact.model_validate(_artifact_data(**overrides))


def _binding(test_id: str, nodeid: str) -> dict[str, object]:
    return {
        "test_id": test_id,
        "requirement_ids": ["AI-240"],
        "test_nodeids": [nodeid],
        "backend": "STATIC",
    }


def _manifest(*bindings: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": BINDINGS_SCHEMA_VERSION,
        "test_plan_version": "cresta-v2-activation-test-plan-2026-09-01.1",
        "required_acceptance_set_hash": EXPECTED_SET_HASH,
        "bindings": list(bindings),
    }


def test_artifact_model_normalizes_normative_arrays_and_serializes_exact_bytes() -> None:
    artifact = _artifact()
    assert artifact.requirement_ids == ["AI-240", "CFG-106"]
    assert artifact.test_nodeids == sorted(artifact.test_nodeids)

    canonical = canonical_artifact_bytes(artifact)
    assert canonical == canonical_artifact_bytes(
        ActivationEvidenceArtifact.model_validate(dict(reversed(list(_artifact_data().items()))))
    )
    assert not canonical.startswith(b"\xef\xbb\xbf")
    assert not canonical.endswith(b"\n")
    assert b" " not in canonical
    assert "pytest-검증".encode() in canonical
    assert b'"executed_at":"2026-09-01T03:04:05.12Z"' in canonical
    assert parse_canonical_artifact(canonical) == artifact


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda data: b"\xef\xbb\xbf" + data, "ACTIVATION_EVIDENCE_BOM_FORBIDDEN"),
        (lambda data: b"\xff" + data, "ACTIVATION_EVIDENCE_UTF8_INVALID"),
        (lambda data: data + b"\n", "ACTIVATION_EVIDENCE_NON_CANONICAL"),
        (
            lambda data: data.replace(b'"artifact_type":', b' "artifact_type":', 1),
            "ACTIVATION_EVIDENCE_NON_CANONICAL",
        ),
        (
            lambda data: b'{"artifact_type":"activation-evidence-artifact-v1",'
            + data[1:],
            "ACTIVATION_EVIDENCE_DUPLICATE_KEY",
        ),
    ],
)
def test_canonical_parser_rejects_bom_utf8_duplicates_and_noncanonical_bytes(
    mutation, code: str
) -> None:
    canonical = canonical_artifact_bytes(_artifact())
    with pytest.raises(ActivationEvidenceError) as caught:
        parse_canonical_artifact(mutation(canonical))
    assert caught.value.code == code


@pytest.mark.parametrize("number", [1.5, float("nan"), float("inf"), float("-inf")])
def test_floats_and_non_finite_values_are_rejected_recursively(number: float) -> None:
    data = _artifact_data()
    summary = dict(data["result_summary"])  # type: ignore[arg-type]
    summary["duration_ms"] = number
    data["result_summary"] = summary
    with pytest.raises(ValidationError):
        ActivationEvidenceArtifact.model_validate(data)

    raw = canonical_artifact_bytes(_artifact()).replace(b'"duration_ms":123', b'"duration_ms":1.5')
    with pytest.raises(ActivationEvidenceError) as caught:
        parse_canonical_artifact(raw)
    assert caught.value.code == "ACTIVATION_EVIDENCE_FLOAT_FORBIDDEN"


def test_parser_rejects_unknown_schema_field() -> None:
    material = _artifact().model_dump(mode="json")
    material["unknown"] = True
    raw = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ActivationEvidenceError) as caught:
        parse_canonical_artifact(raw)
    assert caught.value.code == "ACTIVATION_EVIDENCE_SCHEMA_INVALID"


def test_artifact_size_accepts_exact_limit_and_rejects_one_byte_over() -> None:
    base_node = "tests/test_activation_evidence.py::test_size_"
    base = _artifact(test_nodeids=[base_node])
    base_size = len(canonical_artifact_bytes(base))
    exact = _artifact(test_nodeids=[base_node + "x" * (MAX_ARTIFACT_BYTES - base_size)])
    exact_bytes = canonical_artifact_bytes(exact)
    assert len(exact_bytes) == MAX_ARTIFACT_BYTES
    assert parse_canonical_artifact(exact_bytes) == exact

    oversized = _artifact(
        test_nodeids=[base_node + "x" * (MAX_ARTIFACT_BYTES - base_size + 1)]
    )
    with pytest.raises(ActivationEvidenceError) as caught:
        canonical_artifact_bytes(oversized)
    assert caught.value.code == "ACTIVATION_EVIDENCE_TOO_LARGE"


def test_evidence_reference_is_typed_and_canonical() -> None:
    digest = "a" * 64
    reference = EvidenceReference.parse(f"sha256:{digest}")
    assert (reference.algorithm, reference.digest, reference.canonical) == (
        "sha256",
        digest,
        f"sha256:{digest}",
    )
    assert str(reference) == reference.canonical


@pytest.mark.parametrize(
    "value",
    [
        "../sha256:" + "a" * 64,
        "..",
        "/sha256:" + "a" * 64,
        "sha256\\" + "a" * 64,
        "/tmp/evidence.json",
        r"C:\evidence.json",
        r"\\server\share\evidence.json",
        "file:" + "a" * 64,
        "http://example.test/evidence",
        "https://example.test/evidence",
        "%2e%2e%2f" + "a" * 64,
        "sha256:" + "a" * 64 + "?query=1",
        "sha256:" + "a" * 64 + "#fragment",
        "sha256:extra:" + "a" * 64,
        "sha256:" + "a" * 63,
        "sha256:" + "a" * 65,
        "sha256:" + "A" * 64,
        "sha256:" + "g" * 64,
        " sha256:" + "a" * 64,
        "sha256:" + "a" * 64 + " ",
        "sha256:" + "a" * 64 + "\n",
        "sha256:" + "a" * 32 + "\0" + "a" * 31,
    ],
)
def test_malicious_or_noncanonical_evidence_references_are_rejected(value: str) -> None:
    with pytest.raises(ActivationEvidenceReferenceError):
        EvidenceReference.parse(value)


def test_store_publish_layout_read_and_idempotence(tmp_path: Path) -> None:
    store = ActivationEvidenceStore(tmp_path)
    canonical = canonical_artifact_bytes(_artifact())
    first = store.publish(canonical)
    second = store.publish(canonical)
    expected = (
        tmp_path
        / "activation-evidence"
        / "sha256"
        / first.evidence_hash[:2]
        / f"{first.evidence_hash}.json"
    )
    assert expected.is_file()
    assert first.created and not second.created
    assert first.reference == second.reference
    assert store.read(first.reference) == canonical
    assert first.evidence_hash == artifact_digest(canonical)
    assert list(expected.parent.glob("*.json")) == [expected]
    assert list(expected.parent.glob("*.tmp")) == []


def test_store_missing_oversized_and_corrupt_files_fail_closed(tmp_path: Path) -> None:
    store = ActivationEvidenceStore(tmp_path)
    with pytest.raises(ActivationEvidenceStoreError) as invalid:
        store.read("../evidence.json")
    assert invalid.value.category == EvidenceStoreFailureCategory.INVALID_REFERENCE

    missing = EvidenceReference.from_digest("a" * 64)
    with pytest.raises(ActivationEvidenceStoreError) as caught:
        store.read(missing)
    assert caught.value.category == EvidenceStoreFailureCategory.NOT_FOUND
    assert str(tmp_path) not in str(caught.value)

    canonical = canonical_artifact_bytes(_artifact())
    stored = store.publish(canonical)
    target = store.path_for(stored.reference)
    target.write_bytes(b"corrupt")
    with pytest.raises(ActivationEvidenceStoreError) as caught:
        store.read(stored.reference)
    assert caught.value.category == EvidenceStoreFailureCategory.CORRUPT_OR_HASH_MISMATCH
    with pytest.raises(ActivationEvidenceStoreError) as republish:
        store.publish(canonical)
    assert republish.value.category == EvidenceStoreFailureCategory.CORRUPT_OR_HASH_MISMATCH
    assert target.read_bytes() == b"corrupt"

    oversized_data = b"x" * (MAX_ARTIFACT_BYTES + 1)
    oversized_ref = EvidenceReference.from_digest(artifact_digest(oversized_data))
    oversized_path = (
        tmp_path
        / "activation-evidence"
        / "sha256"
        / oversized_ref.digest[:2]
        / f"{oversized_ref.digest}.json"
    )
    oversized_path.parent.mkdir(parents=True, exist_ok=True)
    oversized_path.write_bytes(oversized_data)
    with pytest.raises(ActivationEvidenceStoreError) as oversized:
        store.read(oversized_ref)
    assert oversized.value.category == EvidenceStoreFailureCategory.UNREADABLE


def test_store_requires_an_existing_directory_root(tmp_path: Path) -> None:
    reference = EvidenceReference.from_digest("a" * 64)
    missing_root = tmp_path / "missing-root"
    with pytest.raises(ActivationEvidenceStoreError) as missing:
        ActivationEvidenceStore(missing_root).read(reference)
    assert missing.value.category == EvidenceStoreFailureCategory.STORE_UNAVAILABLE

    file_root = tmp_path / "file-root"
    file_root.write_bytes(b"not a directory")
    with pytest.raises(ActivationEvidenceStoreError) as non_directory:
        ActivationEvidenceStore(file_root).read(reference)
    assert non_directory.value.category == EvidenceStoreFailureCategory.STORE_UNAVAILABLE


def test_store_rejects_file_and_intermediate_directory_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ActivationEvidenceStore(tmp_path)
    canonical = canonical_artifact_bytes(_artifact())
    reference = EvidenceReference.from_digest(artifact_digest(canonical))
    prefix = tmp_path / "activation-evidence" / "sha256" / reference.digest[:2]
    prefix.mkdir(parents=True)
    artifact_path = prefix / f"{reference.digest}.json"
    artifact_path.write_bytes(canonical)

    linked_root = tmp_path / "linked-root"
    linked_root.mkdir()
    original_lstat = Path.lstat
    symlink_paths = {artifact_path, linked_root}

    def fake_lstat(path: Path) -> object:
        if path in symlink_paths:
            return SimpleNamespace(st_mode=stat.S_IFLNK)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ActivationEvidenceStoreError) as file_link:
        store.read(reference)
    assert file_link.value.category == EvidenceStoreFailureCategory.UNREADABLE

    symlink_paths.clear()
    symlink_paths.add(tmp_path / "activation-evidence")
    with pytest.raises(ActivationEvidenceStoreError) as intermediate_link:
        store.read(reference)
    assert intermediate_link.value.category == EvidenceStoreFailureCategory.STORE_UNAVAILABLE

    symlink_paths.clear()
    symlink_paths.add(linked_root)
    with pytest.raises(ActivationEvidenceStoreError) as root_link:
        ActivationEvidenceStore(linked_root).read(reference)
    assert root_link.value.category == EvidenceStoreFailureCategory.STORE_UNAVAILABLE


def test_two_concurrent_identical_publishers_create_one_complete_artifact(
    tmp_path: Path,
) -> None:
    store = ActivationEvidenceStore(tmp_path)
    publisher = ActivationAcceptancePublisher(store)
    artifact = _artifact()
    barrier = Barrier(2)

    def publish() -> object:
        barrier.wait()
        return publisher.publish(artifact)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))

    assert results[0] == results[1]
    reference = EvidenceReference.parse(results[0].evidence_ref)  # type: ignore[union-attr]
    target = store.path_for(reference)
    assert target.read_bytes() == canonical_artifact_bytes(artifact)
    assert list(target.parent.glob("*.json")) == [target]
    assert list(target.parent.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "result",
    ["FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS", "PARTIAL", "PLANNED", "NOT_RUN", ""],
)
def test_publisher_rejects_every_non_pass_result(tmp_path: Path, result: str) -> None:
    publisher = ActivationAcceptancePublisher(ActivationEvidenceStore(tmp_path))
    data = _artifact_data(result=result)
    with pytest.raises(ActivationEvidencePublicationError) as caught:
        publisher.publish(data)
    assert caught.value.code == "ACTIVATION_EVIDENCE_NOT_PUBLISHABLE"
    assert list(tmp_path.rglob("*.json")) == []


def test_publisher_returns_only_typed_non_secret_metadata(tmp_path: Path) -> None:
    published = ActivationAcceptancePublisher(ActivationEvidenceStore(tmp_path)).publish(
        _artifact()
    )
    assert published.test_id == REQUIRED_ACTIVATION_TEST_IDS[0]
    assert published.evidence_ref == f"sha256:{published.evidence_hash}"
    assert published.canonical_byte_size > 0
    assert not hasattr(published, "artifact") and not hasattr(published, "logs")


def test_binding_parser_normalizes_exact_bindings_and_completeness() -> None:
    data = _manifest(
        _binding("T-SYN-002", "static::ruff::check"),
        _binding("T-SYN-001", "tests/test_example.py::test_exact[param]"),
    )
    manifest = parse_activation_acceptance_bindings(json.dumps(data).encode())
    assert [item.test_id for item in manifest.bindings] == ["T-SYN-001", "T-SYN-002"]
    result = binding_completeness(manifest, ["T-SYN-001", "T-SYN-002"])
    assert result.complete and result.missing_ids == () and result.unexpected_ids == ()
    require_complete_bindings(manifest, ["T-SYN-001", "T-SYN-002"])

    missing = binding_completeness(manifest, ["T-SYN-001", "T-SYN-002", "T-SYN-003"])
    assert missing.missing_ids == ("T-SYN-003",) and not missing.complete
    with pytest.raises(ActivationBindingCompletenessError) as caught:
        require_complete_bindings(manifest, ["T-SYN-001", "T-SYN-002", "T-SYN-003"])
    assert caught.value.result == missing

    extra = binding_completeness(manifest, ["T-SYN-001"])
    assert extra.unexpected_ids == ("T-SYN-002",)


@pytest.mark.parametrize(
    "bindings",
    [
        [
            _binding("T-SYN-001", "static::ruff::check"),
            _binding("T-SYN-001", "static::ruff::other"),
        ],
        [{**_binding("T-SYN-001", "static::ruff::check"), "test_nodeids": []}],
        [
            {
                **_binding("T-SYN-001", "static::ruff::check"),
                "test_nodeids": ["static::ruff::check", "static::ruff::check"],
            }
        ],
        [{**_binding("T-SYN-001", "static::ruff::check"), "test_nodeids": ["tests/*"]}],
        [
            {
                **_binding("T-SYN-001", "static::ruff::check"),
                "test_nodeids": ["daily acceptance proof"],
            }
        ],
    ],
)
def test_binding_manifest_rejects_duplicate_empty_and_fuzzy_bindings(
    bindings: list[dict[str, object]],
) -> None:
    with pytest.raises(ActivationEvidenceError) as caught:
        parse_activation_acceptance_bindings(json.dumps(_manifest(*bindings)))
    assert caught.value.code == "ACTIVATION_BINDINGS_SCHEMA_INVALID"


def test_binding_models_are_strict_and_do_not_require_authoritative_manifest() -> None:
    binding = AcceptanceBinding.model_validate(
        _binding("T-SYN-001", "deployment::gate-api-transition")
    )
    manifest = ActivationAcceptanceBindings(
        schema_version=BINDINGS_SCHEMA_VERSION,
        test_plan_version="synthetic-v1",
        required_acceptance_set_hash="a" * 64,
        bindings=[binding],
    )
    assert manifest.bindings == [binding]
    assert manifest.test_plan_version == "synthetic-v1"


def test_current_required_acceptance_set_hash_matches_normative_value() -> None:
    calculated = required_acceptance_set_hash(REQUIRED_ACTIVATION_TEST_IDS)
    assert len(REQUIRED_ACTIVATION_TEST_IDS) == 118
    assert calculated == EXPECTED_SET_HASH


def test_foundation_publication_is_safety_evidence_shape_compatible(tmp_path: Path) -> None:
    artifact = _artifact()
    published = ActivationAcceptancePublisher(ActivationEvidenceStore(tmp_path)).publish(
        artifact
    )
    descriptor = SafetyEvidence(
        test_id=artifact.test_id,
        requirement_ids=artifact.requirement_ids,
        result="PASSED",
        code_revision=artifact.code_revision,
        test_plan_version=artifact.test_plan_version,
        spec_version=artifact.spec_version,
        executed_at=artifact.executed_at,
        valid_until=None,
        freshness_contract=artifact.freshness_contract,
        evidence_ref=published.evidence_ref,
        evidence_hash=published.evidence_hash,
    )
    assert descriptor.evidence_ref == published.evidence_ref
    assert descriptor.evidence_hash == published.evidence_hash


def test_production_unavailable_loader_remains_fail_closed() -> None:
    with pytest.raises(ActivationGateError) as caught:
        _unavailable_evidence_loader("sha256:" + "a" * 64)
    assert (caught.value.code, caught.value.status_code, caught.value.retryable) == (
        "ACTIVATION_GATE_EVIDENCE_UNAVAILABLE",
        503,
        True,
    )
