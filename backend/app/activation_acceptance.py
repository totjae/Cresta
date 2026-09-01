from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.activation_evidence import (
    ACTIVATION_SPEC_VERSION,
    ACTIVATION_TEST_PLAN_VERSION,
    EXACT_REVISION_FRESHNESS,
    EXPECTED_REQUIRED_ACCEPTANCE_SET_HASH,
    ActivationAcceptanceBindings,
    ActivationAcceptancePublisher,
    ActivationEvidenceArtifact,
    ActivationEvidenceStore,
    EvidenceResultSummary,
    EvidenceRunner,
    PublishedActivationEvidence,
    parse_activation_acceptance_bindings,
    require_complete_bindings,
    required_acceptance_set_hash,
)
from app.activation_gate import REQUIRED_ACTIVATION_TEST_IDS
from app.config import EXPECTED_MIGRATION_HEAD

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "tests" / (
    "activation_acceptance_bindings.json"
)


class AcceptanceOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    XFAIL = "XFAIL"
    XPASS = "XPASS"
    NOT_RUN = "NOT_RUN"


@dataclass
class NodeResult:
    nodeid: str
    reports: list[Any] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return round(sum(float(getattr(item, "duration", 0.0)) for item in self.reports) * 1000)

    @property
    def outcome(self) -> AcceptanceOutcome:
        if not self.reports:
            return AcceptanceOutcome.NOT_RUN
        for report in self.reports:
            if getattr(report, "wasxfail", None) is not None:
                if report.passed or report.failed:
                    return AcceptanceOutcome.XPASS
                return AcceptanceOutcome.XFAIL
        if any(report.failed and report.when != "call" for report in self.reports):
            return AcceptanceOutcome.ERROR
        if any(report.failed for report in self.reports):
            return AcceptanceOutcome.FAILED
        if any(report.skipped for report in self.reports):
            return AcceptanceOutcome.SKIPPED
        if any(report.when == "call" and report.passed for report in self.reports):
            return AcceptanceOutcome.PASSED
        return AcceptanceOutcome.NOT_RUN


class StructuredPytestCollector:
    def __init__(self) -> None:
        self.collected: set[str] = set()
        self.results: dict[str, NodeResult] = {}

    def pytest_collection_finish(self, session: Any) -> None:
        self.collected.update(item.nodeid for item in session.items)

    def pytest_runtest_logreport(self, report: Any) -> None:
        self.results.setdefault(report.nodeid, NodeResult(report.nodeid)).reports.append(report)


@dataclass(frozen=True)
class GateAcceptanceResult:
    test_id: str
    nodeids: tuple[str, ...]
    outcome: AcceptanceOutcome
    duration_ms: int


@contextmanager
def _working_directory(path: Path):
    original = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(original)


def load_authoritative_manifest(path: Path = DEFAULT_MANIFEST) -> ActivationAcceptanceBindings:
    manifest = parse_activation_acceptance_bindings(path.read_bytes())
    require_complete_bindings(manifest, REQUIRED_ACTIVATION_TEST_IDS)
    if manifest.test_plan_version != ACTIVATION_TEST_PLAN_VERSION:
        raise ValueError("activation manifest test-plan version mismatch")
    calculated = required_acceptance_set_hash(REQUIRED_ACTIVATION_TEST_IDS)
    if (
        calculated != EXPECTED_REQUIRED_ACCEPTANCE_SET_HASH
        or manifest.required_acceptance_set_hash != calculated
    ):
        raise ValueError("activation manifest required-set hash mismatch")
    return manifest


def _nodeids(manifest: ActivationAcceptanceBindings, test_ids: Iterable[str]) -> tuple[str, ...]:
    requested = set(test_ids)
    selected = [item for item in manifest.bindings if item.test_id in requested]
    if {item.test_id for item in selected} != requested:
        raise ValueError("requested activation ID is not bound")
    return tuple(sorted({nodeid for item in selected for nodeid in item.test_nodeids}))


def collect_bound_nodeids(
    manifest: ActivationAcceptanceBindings,
    *,
    backend_root: Path,
    test_ids: Iterable[str] = REQUIRED_ACTIVATION_TEST_IDS,
) -> tuple[str, ...]:
    import pytest

    expected = _nodeids(manifest, test_ids)
    plugin = StructuredPytestCollector()
    with _working_directory(backend_root):
        exit_code = pytest.main(["--collect-only", "-q", *expected], plugins=[plugin])
    unresolved = tuple(sorted(set(expected) - plugin.collected))
    if int(exit_code) != 0 or unresolved:
        raise ValueError("activation manifest contains unresolved pytest node IDs")
    return tuple(sorted(plugin.collected))


def run_bound_acceptance(
    manifest: ActivationAcceptanceBindings,
    *,
    backend_root: Path,
    test_ids: Iterable[str],
) -> tuple[GateAcceptanceResult, ...]:
    import pytest

    requested = tuple(sorted(set(test_ids)))
    nodes = collect_bound_nodeids(manifest, backend_root=backend_root, test_ids=requested)
    plugin = StructuredPytestCollector()
    with _working_directory(backend_root):
        pytest.main(["-q", *nodes], plugins=[plugin])
    by_id = {item.test_id: item for item in manifest.bindings}
    results: list[GateAcceptanceResult] = []
    for test_id in requested:
        bound_nodes = tuple(by_id[test_id].test_nodeids)
        node_results = [plugin.results.get(nodeid, NodeResult(nodeid)) for nodeid in bound_nodes]
        outcomes = {item.outcome for item in node_results}
        outcome = (
            AcceptanceOutcome.PASSED
            if outcomes == {AcceptanceOutcome.PASSED}
            else next(
                value
                for value in (
                    AcceptanceOutcome.ERROR,
                    AcceptanceOutcome.FAILED,
                    AcceptanceOutcome.XPASS,
                    AcceptanceOutcome.XFAIL,
                    AcceptanceOutcome.SKIPPED,
                    AcceptanceOutcome.NOT_RUN,
                )
                if value in outcomes
            )
        )
        results.append(
            GateAcceptanceResult(
                test_id=test_id,
                nodeids=bound_nodes,
                outcome=outcome,
                duration_ms=sum(item.duration_ms for item in node_results),
            )
        )
    return tuple(results)


def publish_passed_results(
    manifest: ActivationAcceptanceBindings,
    results: Sequence[GateAcceptanceResult],
    *,
    artifact_root: Path,
    code_revision: str,
    runner_name: str,
    runner_version: str,
    executed_at: datetime,
) -> tuple[PublishedActivationEvidence, ...]:
    if not results or any(item.outcome != AcceptanceOutcome.PASSED for item in results):
        raise ValueError("activation evidence publication requires all selected IDs PASSED")
    bindings = {item.test_id: item for item in manifest.bindings}
    publisher = ActivationAcceptancePublisher(ActivationEvidenceStore(artifact_root))
    published: list[PublishedActivationEvidence] = []
    for result in sorted(results, key=lambda item: item.test_id):
        binding = bindings[result.test_id]
        artifact = ActivationEvidenceArtifact(
            schema_version="1.0",
            artifact_type="activation-evidence-artifact-v1",
            test_id=result.test_id,
            requirement_ids=binding.requirement_ids,
            result="PASSED",
            test_nodeids=list(result.nodeids),
            code_revision=code_revision,
            test_plan_version=ACTIVATION_TEST_PLAN_VERSION,
            spec_version=ACTIVATION_SPEC_VERSION,
            migration_revision=EXPECTED_MIGRATION_HEAD,
            environment="MOCK",
            required_acceptance_set_hash=EXPECTED_REQUIRED_ACCEPTANCE_SET_HASH,
            executed_at=executed_at,
            freshness_contract=EXACT_REVISION_FRESHNESS,
            runner=EvidenceRunner(name=runner_name, version=runner_version),
            backend=binding.backend,
            result_summary=EvidenceResultSummary(
                collected=len(result.nodeids),
                passed=len(result.nodeids),
                failed=0,
                skipped=0,
                xfailed=0,
                xpassed=0,
                errors=0,
                duration_ms=result.duration_ms,
            ),
        )
        published.append(publisher.publish(artifact))
    return tuple(published)


def _result_payload(results: Sequence[GateAcceptanceResult]) -> dict[str, object]:
    counts = {value.value: 0 for value in AcceptanceOutcome}
    for result in results:
        counts[result.outcome.value] += 1
    return {
        "schema_version": "activation-acceptance-run-v1",
        "required_ids": len(results),
        "outcomes": counts,
        "complete": counts[AcceptanceOutcome.PASSED.value] == len(results),
        "artifacts_published": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cresta-activation-acceptance")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-manifest")
    subparsers.add_parser("collect-nodeids")
    run_one = subparsers.add_parser("run-one")
    run_one.add_argument("test_id")
    run_all = subparsers.add_parser("run-all")
    for command in (run_one, run_all):
        command.add_argument("--publish", action="store_true")
        command.add_argument("--artifact-root", type=Path)
        command.add_argument("--code-revision")
        command.add_argument("--runner-name", default="pytest")
        command.add_argument("--runner-version", default="unknown")
    args = parser.parse_args(argv)
    manifest = load_authoritative_manifest(args.manifest)
    backend_root = args.manifest.resolve().parents[1]
    if args.command == "validate-manifest":
        print(json.dumps({"valid": True, "bound_ids": len(manifest.bindings)}))
        return 0
    if args.command == "collect-nodeids":
        nodes = collect_bound_nodeids(manifest, backend_root=backend_root)
        print(json.dumps({"valid": True, "nodeids": len(nodes)}))
        return 0
    test_ids = (
        (args.test_id,)
        if args.command == "run-one"
        else tuple(REQUIRED_ACTIVATION_TEST_IDS)
    )
    results = run_bound_acceptance(
        manifest, backend_root=backend_root, test_ids=test_ids
    )
    payload = _result_payload(results)
    if args.publish:
        if args.artifact_root is None or args.code_revision is None:
            raise ValueError("publish requires artifact root and exact code revision")
        published = publish_passed_results(
            manifest,
            results,
            artifact_root=args.artifact_root,
            code_revision=args.code_revision,
            runner_name=args.runner_name,
            runner_version=args.runner_version,
            executed_at=datetime.now(UTC),
        )
        payload["artifacts_published"] = len(published)
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0 if payload["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
