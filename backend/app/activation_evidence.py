from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

ARTIFACT_SCHEMA_VERSION = "1.0"
ARTIFACT_TYPE = "activation-evidence-artifact-v1"
BINDINGS_SCHEMA_VERSION = "activation-acceptance-bindings-v1"
EXACT_REVISION_FRESHNESS = "EXACT_REVISION"
ACTIVATION_TEST_PLAN_VERSION = "cresta-v2-activation-test-plan-2026-09-01.1"
ACTIVATION_SPEC_VERSION = "cresta-v2-activation-spec-set-2026-09-01.1"
EXPECTED_REQUIRED_ACCEPTANCE_SET_HASH = (
    "d740a14dbcc471e588fc2a03776a216e7bc4c2e6053497d604f3e9804cca913e"
)
MAX_ARTIFACT_BYTES = 65_536
HASH_PATTERN = r"^[0-9a-f]{64}$"
REFERENCE_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")
COMMIT_PATTERN = r"^[0-9a-f]{40}$"
VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
MIGRATION_PATTERN = r"^[0-9a-z][0-9a-z_]*$"
TEST_ID_PATTERN = re.compile(r"^T-[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}$")
PYTEST_NODE_ID_PATTERN = re.compile(
    r"^(?![A-Za-z]:)(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\\\x00\r\n]+\.py::.+$"
)
STATIC_NODE_ID_PATTERN = re.compile(
    r"^static::[A-Za-z0-9][A-Za-z0-9._-]*::[A-Za-z0-9][A-Za-z0-9._:-]*$"
)
DEPLOYMENT_NODE_ID_PATTERN = re.compile(
    r"^deployment::[A-Za-z0-9][A-Za-z0-9._:-]*$"
)
BACKENDS = Literal["POSTGRESQL", "SQLITE", "STATIC", "DEPLOYMENT", "MIXED"]


class ActivationEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _validate_plain_string(value: str, *, label: str) -> str:
    if not value or value != value.strip() or any(ord(character) < 32 for character in value):
        raise ValueError(f"{label} must be a non-empty plain string")
    return value


def _validate_test_id(value: str, *, required: set[str] | None = None) -> str:
    _validate_plain_string(value, label="test ID")
    if TEST_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("test ID is invalid")
    if required is not None and value not in required:
        raise ValueError("test ID is not in the required activation set")
    return value


def _normalize_string_list(
    value: object,
    *,
    label: str,
    reject_globs: bool = False,
) -> object:
    if not isinstance(value, list):
        return value
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{label} must contain strings")
        _validate_plain_string(item, label=label)
        if reject_globs and any(character in item for character in "*?"):
            raise ValueError(f"{label} must not contain glob syntax")
        normalized.append(item)
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must be unique")
    return sorted(normalized)


def _validate_node_ids(value: object) -> object:
    normalized = _normalize_string_list(
        value, label="test node IDs", reject_globs=True
    )
    if isinstance(normalized, list) and any(
        PYTEST_NODE_ID_PATTERN.fullmatch(item) is None
        and STATIC_NODE_ID_PATTERN.fullmatch(item) is None
        and DEPLOYMENT_NODE_ID_PATTERN.fullmatch(item) is None
        for item in normalized
    ):
        raise ValueError("test node ID must be an exact authoritative identifier")
    return normalized


def _utc_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        if not value.endswith("Z"):
            raise ValueError("timestamp must use UTC RFC3339 Z form")
        try:
            value = datetime.fromisoformat(f"{value[:-1]}+00:00")
        except ValueError as exc:
            raise ValueError("timestamp must be valid UTC RFC3339") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must use UTC")
    return value.astimezone(UTC)


def _format_utc_timestamp(value: datetime) -> str:
    result = value.strftime("%Y-%m-%dT%H:%M:%S")
    if value.microsecond:
        result += f".{value.microsecond:06d}".rstrip("0")
    return f"{result}Z"


class EvidenceRunner(ActivationEvidenceModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)

    @field_validator("name", "version")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _validate_plain_string(value, label="runner identity")


NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class EvidenceResultSummary(ActivationEvidenceModel):
    collected: NonNegativeInt
    passed: NonNegativeInt
    failed: NonNegativeInt
    skipped: NonNegativeInt
    xfailed: NonNegativeInt
    xpassed: NonNegativeInt
    errors: NonNegativeInt
    duration_ms: NonNegativeInt


class ActivationEvidenceArtifact(ActivationEvidenceModel):
    schema_version: Literal["1.0"]
    artifact_type: Literal["activation-evidence-artifact-v1"]
    test_id: str
    requirement_ids: list[str]
    result: Literal["PASSED"]
    test_nodeids: list[str]
    code_revision: str = Field(pattern=COMMIT_PATTERN)
    test_plan_version: str = Field(min_length=1, pattern=VERSION_PATTERN)
    spec_version: str = Field(min_length=1, pattern=VERSION_PATTERN)
    migration_revision: str = Field(min_length=1, pattern=MIGRATION_PATTERN)
    environment: Literal["MOCK"]
    required_acceptance_set_hash: str = Field(pattern=HASH_PATTERN)
    executed_at: datetime
    freshness_contract: Literal["EXACT_REVISION"]
    runner: EvidenceRunner
    backend: BACKENDS
    result_summary: EvidenceResultSummary

    @field_validator("test_id")
    @classmethod
    def validate_required_test_id(cls, value: str) -> str:
        from app.activation_gate import REQUIRED_ACTIVATION_TEST_IDS

        return _validate_test_id(value, required=set(REQUIRED_ACTIVATION_TEST_IDS))

    @field_validator("requirement_ids", mode="before")
    @classmethod
    def normalize_requirement_ids(cls, value: object) -> object:
        return _normalize_string_list(value, label="requirement IDs")

    @field_validator("test_nodeids", mode="before")
    @classmethod
    def normalize_test_nodeids(cls, value: object) -> object:
        return _validate_node_ids(value)

    @field_validator("executed_at", mode="before")
    @classmethod
    def validate_executed_at(cls, value: object) -> datetime:
        return _utc_timestamp(value)

    @field_serializer("executed_at")
    def serialize_executed_at(self, value: datetime) -> str:
        return _format_utc_timestamp(value)

    @model_validator(mode="after")
    def validate_pass_summary(self) -> ActivationEvidenceArtifact:
        summary = self.result_summary
        if summary.collected != len(self.test_nodeids) or summary.passed != summary.collected:
            raise ValueError("PASSED summary must account for every bound test node")
        if any(
            (
                summary.failed,
                summary.skipped,
                summary.xfailed,
                summary.xpassed,
                summary.errors,
            )
        ):
            raise ValueError("PASSED evidence cannot contain a non-pass outcome")
        return self


class ActivationEvidenceError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _reject_floats(value: object) -> None:
    if isinstance(value, float):
        raise ActivationEvidenceError("ACTIVATION_EVIDENCE_FLOAT_FORBIDDEN")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_floats(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_floats(item)


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ActivationEvidenceError("ACTIVATION_EVIDENCE_DUPLICATE_KEY")
        result[key] = value
    return result


def _reject_json_float(_value: str) -> None:
    raise ActivationEvidenceError("ACTIVATION_EVIDENCE_FLOAT_FORBIDDEN")


def _strict_json_loads(data: str) -> object:
    try:
        return json.loads(
            data,
            object_pairs_hook=_duplicate_safe_object,
            parse_float=_reject_json_float,
            parse_constant=_reject_json_float,
        )
    except ActivationEvidenceError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ActivationEvidenceError("ACTIVATION_EVIDENCE_JSON_INVALID") from exc


def canonical_artifact_bytes(artifact: ActivationEvidenceArtifact) -> bytes:
    material = artifact.model_dump(mode="json")
    _reject_floats(material)
    try:
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ActivationEvidenceError("ACTIVATION_EVIDENCE_JSON_INVALID") from exc
    if len(encoded) > MAX_ARTIFACT_BYTES:
        raise ActivationEvidenceError("ACTIVATION_EVIDENCE_TOO_LARGE")
    return encoded


def parse_canonical_artifact(data: bytes) -> ActivationEvidenceArtifact:
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ActivationEvidenceError("ACTIVATION_EVIDENCE_TOO_LARGE")
    if data.startswith(b"\xef\xbb\xbf"):
        raise ActivationEvidenceError("ACTIVATION_EVIDENCE_BOM_FORBIDDEN")
    try:
        decoded = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ActivationEvidenceError("ACTIVATION_EVIDENCE_UTF8_INVALID") from exc
    parsed = _strict_json_loads(decoded)
    _reject_floats(parsed)
    try:
        artifact = ActivationEvidenceArtifact.model_validate(parsed)
    except ValidationError as exc:
        raise ActivationEvidenceError("ACTIVATION_EVIDENCE_SCHEMA_INVALID") from exc
    if canonical_artifact_bytes(artifact) != data:
        raise ActivationEvidenceError("ACTIVATION_EVIDENCE_NON_CANONICAL")
    return artifact


def artifact_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class EvidenceReference:
    algorithm: Literal["sha256"]
    digest: str

    @classmethod
    def parse(cls, value: str) -> EvidenceReference:
        if not isinstance(value, str):
            raise ActivationEvidenceReferenceError()
        match = REFERENCE_PATTERN.fullmatch(value)
        if match is None:
            raise ActivationEvidenceReferenceError()
        return cls(algorithm="sha256", digest=match.group(1))

    @classmethod
    def from_digest(cls, digest: str) -> EvidenceReference:
        return cls.parse(f"sha256:{digest}")

    @property
    def canonical(self) -> str:
        return f"{self.algorithm}:{self.digest}"

    def __str__(self) -> str:
        return self.canonical


class ActivationEvidenceReferenceError(ActivationEvidenceError):
    def __init__(self) -> None:
        super().__init__("ACTIVATION_EVIDENCE_REFERENCE_INVALID")


class EvidenceStoreFailureCategory(StrEnum):
    INVALID_REFERENCE = "INVALID_REFERENCE"
    NOT_FOUND = "NOT_FOUND"
    UNREADABLE = "UNREADABLE"
    CORRUPT_OR_HASH_MISMATCH = "CORRUPT_OR_HASH_MISMATCH"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"


class ActivationEvidenceStoreError(RuntimeError):
    def __init__(self, category: EvidenceStoreFailureCategory) -> None:
        super().__init__(category.value)
        self.category = category


@dataclass(frozen=True)
class StoredEvidence:
    reference: EvidenceReference
    evidence_hash: str
    byte_size: int
    created: bool


class ActivationEvidenceStore:
    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = Path(artifact_root)

    def _root(self) -> Path:
        try:
            root_stat = self.artifact_root.lstat()
            if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
                raise ActivationEvidenceStoreError(
                    EvidenceStoreFailureCategory.STORE_UNAVAILABLE
                )
            return self.artifact_root.resolve(strict=True)
        except ActivationEvidenceStoreError:
            raise
        except OSError as exc:
            raise ActivationEvidenceStoreError(
                EvidenceStoreFailureCategory.STORE_UNAVAILABLE
            ) from exc

    @staticmethod
    def _require_directory(path: Path, *, create: bool) -> None:
        try:
            if create:
                path.mkdir(mode=0o700, exist_ok=True)
            value = path.lstat()
            if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
                raise ActivationEvidenceStoreError(
                    EvidenceStoreFailureCategory.STORE_UNAVAILABLE
                )
        except ActivationEvidenceStoreError:
            raise
        except OSError as exc:
            raise ActivationEvidenceStoreError(
                EvidenceStoreFailureCategory.STORE_UNAVAILABLE
            ) from exc

    def _path(self, reference: EvidenceReference, *, create_directories: bool) -> Path:
        root = self._root()
        activation = root / "activation-evidence"
        sha256_root = activation / "sha256"
        prefix = sha256_root / reference.digest[:2]
        for directory in (activation, sha256_root, prefix):
            self._require_directory(directory, create=create_directories)
        target = prefix / f"{reference.digest}.json"
        try:
            if target.parent.resolve(strict=True) != prefix.resolve(strict=True):
                raise ActivationEvidenceStoreError(
                    EvidenceStoreFailureCategory.STORE_UNAVAILABLE
                )
            target.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ActivationEvidenceStoreError(
                EvidenceStoreFailureCategory.STORE_UNAVAILABLE
            ) from exc
        return target

    @staticmethod
    def _bounded_regular_file_read(path: Path) -> bytes:
        descriptor: int | None = None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise ActivationEvidenceStoreError(EvidenceStoreFailureCategory.UNREADABLE)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ActivationEvidenceStoreError(EvidenceStoreFailureCategory.UNREADABLE)
            chunks: list[bytes] = []
            remaining = MAX_ARTIFACT_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(8192, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > MAX_ARTIFACT_BYTES:
                raise ActivationEvidenceStoreError(EvidenceStoreFailureCategory.UNREADABLE)
            return data
        except ActivationEvidenceStoreError:
            raise
        except FileNotFoundError as exc:
            raise ActivationEvidenceStoreError(EvidenceStoreFailureCategory.NOT_FOUND) from exc
        except OSError as exc:
            raise ActivationEvidenceStoreError(EvidenceStoreFailureCategory.UNREADABLE) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def path_for(self, reference: EvidenceReference) -> Path:
        return self._path(reference, create_directories=False)

    def read(self, reference: EvidenceReference | str) -> bytes:
        if isinstance(reference, str):
            try:
                reference = EvidenceReference.parse(reference)
            except ActivationEvidenceReferenceError as exc:
                raise ActivationEvidenceStoreError(
                    EvidenceStoreFailureCategory.INVALID_REFERENCE
                ) from exc
        try:
            target = self._path(reference, create_directories=False)
        except ActivationEvidenceStoreError as exc:
            if exc.category == EvidenceStoreFailureCategory.STORE_UNAVAILABLE:
                try:
                    root = self._root()
                    expected_parent = (
                        root
                        / "activation-evidence"
                        / "sha256"
                        / reference.digest[:2]
                    )
                    if not expected_parent.exists():
                        raise ActivationEvidenceStoreError(
                            EvidenceStoreFailureCategory.NOT_FOUND
                        ) from exc
                except ActivationEvidenceStoreError as nested:
                    if nested.category == EvidenceStoreFailureCategory.NOT_FOUND:
                        raise
                raise
            raise
        data = self._bounded_regular_file_read(target)
        if artifact_digest(data) != reference.digest:
            raise ActivationEvidenceStoreError(
                EvidenceStoreFailureCategory.CORRUPT_OR_HASH_MISMATCH
            )
        return data

    def __call__(self, reference: str) -> bytes:
        """Resolve exact evidence bytes through the read-only loader interface."""

        return self.read(reference)

    @staticmethod
    def _write_complete_file(path: Path, data: bytes) -> None:
        descriptor: int | None = None
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short artifact write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def publish(self, canonical_bytes: bytes) -> StoredEvidence:
        if len(canonical_bytes) > MAX_ARTIFACT_BYTES:
            raise ActivationEvidenceStoreError(EvidenceStoreFailureCategory.UNREADABLE)
        digest = artifact_digest(canonical_bytes)
        reference = EvidenceReference.from_digest(digest)
        target = self._path(reference, create_directories=True)
        temporary = target.parent / f".{digest}.{secrets.token_hex(16)}.tmp"
        created = False
        try:
            self._write_complete_file(temporary, canonical_bytes)
            try:
                os.link(temporary, target)
                created = True
            except FileExistsError:
                try:
                    existing = self.read(reference)
                except ActivationEvidenceStoreError as exc:
                    raise ActivationEvidenceStoreError(
                        EvidenceStoreFailureCategory.CORRUPT_OR_HASH_MISMATCH
                    ) from exc
                if existing != canonical_bytes:
                    raise ActivationEvidenceStoreError(
                        EvidenceStoreFailureCategory.CORRUPT_OR_HASH_MISMATCH
                    )
            result = StoredEvidence(
                reference=reference,
                evidence_hash=digest,
                byte_size=len(canonical_bytes),
                created=created,
            )
        except ActivationEvidenceStoreError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                raise ActivationEvidenceStoreError(
                    EvidenceStoreFailureCategory.STORE_UNAVAILABLE
                ) from cleanup_error
            raise
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                raise ActivationEvidenceStoreError(
                    EvidenceStoreFailureCategory.STORE_UNAVAILABLE
                ) from cleanup_error
            raise ActivationEvidenceStoreError(
                EvidenceStoreFailureCategory.UNREADABLE
            ) from exc
        try:
            temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise ActivationEvidenceStoreError(
                EvidenceStoreFailureCategory.STORE_UNAVAILABLE
            ) from exc
        return result


@dataclass(frozen=True)
class PublishedActivationEvidence:
    test_id: str
    evidence_ref: str
    evidence_hash: str
    canonical_byte_size: int


class ActivationEvidencePublicationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ActivationAcceptancePublisher:
    def __init__(self, store: ActivationEvidenceStore) -> None:
        self.store = store

    def publish(
        self, artifact: ActivationEvidenceArtifact | Mapping[str, object]
    ) -> PublishedActivationEvidence:
        try:
            validated = (
                artifact
                if isinstance(artifact, ActivationEvidenceArtifact)
                else ActivationEvidenceArtifact.model_validate(artifact)
            )
            if validated.result != "PASSED":
                raise ValueError("activation evidence is not PASSED")
            canonical = canonical_artifact_bytes(validated)
        except (ActivationEvidenceError, ValidationError, ValueError) as exc:
            raise ActivationEvidencePublicationError(
                "ACTIVATION_EVIDENCE_NOT_PUBLISHABLE"
            ) from exc
        stored = self.store.publish(canonical)
        return PublishedActivationEvidence(
            test_id=validated.test_id,
            evidence_ref=stored.reference.canonical,
            evidence_hash=stored.evidence_hash,
            canonical_byte_size=stored.byte_size,
        )


class AcceptanceBinding(ActivationEvidenceModel):
    test_id: str
    requirement_ids: list[str]
    test_nodeids: list[str]
    backend: BACKENDS

    @field_validator("test_id")
    @classmethod
    def validate_test_id(cls, value: str) -> str:
        return _validate_test_id(value)

    @field_validator("requirement_ids", mode="before")
    @classmethod
    def normalize_requirements(cls, value: object) -> object:
        return _normalize_string_list(value, label="requirement IDs")

    @field_validator("test_nodeids", mode="before")
    @classmethod
    def normalize_nodes(cls, value: object) -> object:
        return _validate_node_ids(value)


class ActivationAcceptanceBindings(ActivationEvidenceModel):
    schema_version: Literal["activation-acceptance-bindings-v1"]
    test_plan_version: str = Field(min_length=1, pattern=VERSION_PATTERN)
    required_acceptance_set_hash: str = Field(pattern=HASH_PATTERN)
    bindings: list[AcceptanceBinding]

    @field_validator("bindings", mode="before")
    @classmethod
    def normalize_bindings(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        test_ids: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                test_id = item.get("test_id")
            elif isinstance(item, AcceptanceBinding):
                test_id = item.test_id
            else:
                return value
            if not isinstance(test_id, str):
                return value
            test_ids.append(test_id)
        if len(test_ids) != len(set(test_ids)):
            raise ValueError("binding test IDs must be unique")
        return sorted(
            value,
            key=lambda item: (
                item.test_id
                if isinstance(item, AcceptanceBinding)
                else str(item.get("test_id"))
            ),
        )

    @model_validator(mode="after")
    def require_bindings(self) -> ActivationAcceptanceBindings:
        if not self.bindings:
            raise ValueError("bindings must not be empty")
        return self


def parse_activation_acceptance_bindings(
    data: bytes | str,
) -> ActivationAcceptanceBindings:
    if isinstance(data, bytes):
        if data.startswith(b"\xef\xbb\xbf"):
            raise ActivationEvidenceError("ACTIVATION_BINDINGS_BOM_FORBIDDEN")
        try:
            decoded = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ActivationEvidenceError("ACTIVATION_BINDINGS_UTF8_INVALID") from exc
    else:
        decoded = data
    parsed = _strict_json_loads(decoded)
    _reject_floats(parsed)
    try:
        return ActivationAcceptanceBindings.model_validate(parsed)
    except ValidationError as exc:
        raise ActivationEvidenceError("ACTIVATION_BINDINGS_SCHEMA_INVALID") from exc


def required_acceptance_set_hash(test_ids: Iterable[str]) -> str:
    material: set[str] = set()
    for value in test_ids:
        material.add(_validate_test_id(value))
    if not material:
        raise ActivationEvidenceError("ACTIVATION_ACCEPTANCE_SET_EMPTY")
    encoded = json.dumps(
        sorted(material), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class BindingCompleteness:
    missing_ids: tuple[str, ...]
    unexpected_ids: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_ids and not self.unexpected_ids


class ActivationBindingCompletenessError(ActivationEvidenceError):
    def __init__(self, result: BindingCompleteness) -> None:
        super().__init__("ACTIVATION_BINDINGS_INCOMPLETE")
        self.result = result


def binding_completeness(
    manifest: ActivationAcceptanceBindings, required_test_ids: Iterable[str]
) -> BindingCompleteness:
    required = {_validate_test_id(value) for value in required_test_ids}
    actual = {item.test_id for item in manifest.bindings}
    return BindingCompleteness(
        missing_ids=tuple(sorted(required - actual)),
        unexpected_ids=tuple(sorted(actual - required)),
    )


def require_complete_bindings(
    manifest: ActivationAcceptanceBindings, required_test_ids: Iterable[str]
) -> None:
    result = binding_completeness(manifest, required_test_ids)
    if not result.complete:
        raise ActivationBindingCompletenessError(result)
