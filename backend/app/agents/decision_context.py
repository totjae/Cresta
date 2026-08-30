from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.activation_gate import ActivationGateError, validate_frozen_activation_provenance
from app.decision_inputs import input_digest
from app.models import (
    AgentRun,
    AgentStageRun,
    DecisionContext,
    DecisionInputSnapshot,
    EvidenceBundle,
    EvidenceItem,
    MarketContextSnapshot,
)

CONTEXT_SCHEMA_VERSION = "decision-context-v1"
INPUT_SCHEMA_VERSION = "scout-input-v2"
V7_DAG_VERSION = "agent-dag-v7"
DECISION_AGENT_ROLES = frozenset(
    {
        "CONSERVATIVE_DECISION",
        "BALANCED_DECISION",
        "AGGRESSIVE_DECISION",
    }
)
SCOUT_ROLES = (
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
)
CANDIDATE_AUDIT_ROLE = "EVIDENCE_CANDIDATE_AUDITOR"
EVIDENCE_VERIFIER_ROLE = "EVIDENCE_VERIFIER"
_SCOUT_TERMINAL = frozenset({"SUCCEEDED", "INSUFFICIENT_DATA", "CONFLICTED"})


class DecisionContextFreezeError(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class _StageReference:
    stage: AgentStageRun
    payload: dict[str, object]
    valid_until: datetime | None


@dataclass(frozen=True)
class _PreparedContext:
    decision_input: DecisionInputSnapshot
    bundle: EvidenceBundle
    market_context: MarketContextSnapshot | None
    stages: dict[str, _StageReference]
    configuration_json: str
    configuration_hash: str
    version_json: str
    version_hash: str
    manifest_json: str
    context_hash: str
    valid_until: datetime


def canonical_context_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def context_digest(encoded: str) -> str:
    return input_digest(encoded)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _aware(value).isoformat()


def _payload(encoded: str | None, error_code: str) -> dict[str, object]:
    if not encoded:
        raise DecisionContextFreezeError(error_code)
    try:
        parsed = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise DecisionContextFreezeError(error_code) from exc
    if not isinstance(parsed, dict):
        raise DecisionContextFreezeError(error_code)
    return parsed


def _payload_timestamp(payload: dict[str, object], field: str, error_code: str) -> datetime:
    value = payload.get(field)
    if not isinstance(value, str):
        raise DecisionContextFreezeError(error_code)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DecisionContextFreezeError(error_code) from exc
    return _aware(parsed)


def _validate_stage_output(
    stage: AgentStageRun,
    *,
    allowed_states: frozenset[str],
    require_valid_until: bool,
) -> _StageReference:
    if stage.state not in allowed_states:
        raise DecisionContextFreezeError("DECISION_CONTEXT_STAGE_STATE_INVALID")
    if not stage.output_json or not stage.output_hash:
        raise DecisionContextFreezeError("DECISION_CONTEXT_STAGE_OUTPUT_MISSING")
    payload = _payload(stage.output_json, "DECISION_CONTEXT_STAGE_OUTPUT_INVALID")
    if payload.get("status") != stage.state:
        raise DecisionContextFreezeError("DECISION_CONTEXT_STAGE_STATUS_MISMATCH")
    if payload.get("stage_run_id") != stage.id or payload.get("role") != stage.role:
        raise DecisionContextFreezeError("DECISION_CONTEXT_STAGE_PROVENANCE_MISMATCH")
    if not isinstance(payload.get("schema_version"), str):
        raise DecisionContextFreezeError("DECISION_CONTEXT_STAGE_SCHEMA_INVALID")
    canonical = canonical_context_json(payload)
    if canonical != stage.output_json or context_digest(canonical) != stage.output_hash:
        raise DecisionContextFreezeError("DECISION_CONTEXT_STAGE_OUTPUT_HASH_MISMATCH")
    valid_until = (
        _payload_timestamp(
            payload,
            "valid_until",
            "DECISION_CONTEXT_STAGE_VALIDITY_INVALID",
        )
        if require_valid_until
        else None
    )
    return _StageReference(stage=stage, payload=payload, valid_until=valid_until)


def _validate_bundle_hash(bundle: EvidenceBundle, run: AgentRun) -> None:
    try:
        evidence_ids = json.loads(bundle.evidence_ids_json)
        stale_ids = json.loads(bundle.stale_evidence_ids_json)
        reason_codes = json.loads(bundle.reason_codes_json)
    except (TypeError, ValueError) as exc:
        raise DecisionContextFreezeError("DECISION_CONTEXT_EVIDENCE_BUNDLE_INVALID") from exc
    if not all(isinstance(value, list) for value in (evidence_ids, stale_ids, reason_codes)):
        raise DecisionContextFreezeError("DECISION_CONTEXT_EVIDENCE_BUNDLE_INVALID")
    record = {
        "schema_version": "evidence-bundle-v1",
        "market": run.market,
        "symbol": run.symbol,
        "market_snapshot_id": run.market_snapshot_id,
        "policy_version": bundle.policy_version,
        "state": bundle.state,
        "evidence_ids": evidence_ids,
        "stale_evidence_ids": stale_ids,
        "reason_codes": reason_codes,
    }
    if context_digest(canonical_context_json(record)) != bundle.bundle_hash:
        raise DecisionContextFreezeError("DECISION_CONTEXT_EVIDENCE_BUNDLE_HASH_MISMATCH")


def _validate_evidence_items(db: Session, bundle: EvidenceBundle, run_id: str) -> None:
    try:
        referenced_ids = {
            str(value)
            for value in (
                json.loads(bundle.evidence_ids_json) + json.loads(bundle.stale_evidence_ids_json)
            )
        }
    except (TypeError, ValueError) as exc:
        raise DecisionContextFreezeError("DECISION_CONTEXT_EVIDENCE_BUNDLE_INVALID") from exc
    if not referenced_ids:
        return
    same_run_ids = set(
        db.scalars(
            select(EvidenceItem.id).where(
                EvidenceItem.id.in_(referenced_ids), EvidenceItem.run_id == run_id
            )
        )
    )
    if same_run_ids != referenced_ids:
        raise DecisionContextFreezeError("DECISION_CONTEXT_EVIDENCE_CROSS_RUN")


def _load_input(
    db: Session, run: AgentRun
) -> tuple[DecisionInputSnapshot, dict[str, object], datetime]:
    decision_input = db.scalar(
        select(DecisionInputSnapshot)
        .where(
            DecisionInputSnapshot.user_id == run.owner_id,
            DecisionInputSnapshot.purpose == run.purpose,
            DecisionInputSnapshot.market_snapshot_id == run.market_snapshot_id,
            DecisionInputSnapshot.input_hash == run.input_hash,
            DecisionInputSnapshot.schema_version == INPUT_SCHEMA_VERSION,
        )
        .with_for_update()
    )
    if decision_input is None:
        raise DecisionContextFreezeError("DECISION_CONTEXT_INPUT_NOT_FOUND")
    payload = _payload(decision_input.input_json, "DECISION_CONTEXT_INPUT_INVALID")
    canonical = canonical_context_json(payload)
    if (
        canonical != decision_input.input_json
        or context_digest(canonical) != decision_input.input_hash
        or payload.get("schema_version") != INPUT_SCHEMA_VERSION
    ):
        raise DecisionContextFreezeError("DECISION_CONTEXT_INPUT_HASH_MISMATCH")
    valid_until = _payload_timestamp(
        payload, "valid_until", "DECISION_CONTEXT_INPUT_VALIDITY_INVALID"
    )
    return decision_input, payload, valid_until


def _load_bundle(db: Session, run: AgentRun) -> tuple[EvidenceBundle, _StageReference]:
    bundle = db.scalar(
        select(EvidenceBundle).where(EvidenceBundle.run_id == run.id).with_for_update()
    )
    if bundle is None:
        raise DecisionContextFreezeError("DECISION_CONTEXT_EVIDENCE_BUNDLE_NOT_FOUND")
    if (
        bundle.owner_id != run.owner_id
        or bundle.market != run.market
        or bundle.symbol != run.symbol
        or bundle.run_id != run.id
    ):
        raise DecisionContextFreezeError("DECISION_CONTEXT_EVIDENCE_CROSS_RUN")
    _validate_bundle_hash(bundle, run)
    _validate_evidence_items(db, bundle, run.id)

    verifier = db.scalar(
        select(AgentStageRun)
        .where(
            AgentStageRun.run_id == run.id,
            AgentStageRun.role == EVIDENCE_VERIFIER_ROLE,
        )
        .with_for_update()
    )
    if verifier is None:
        raise DecisionContextFreezeError("DECISION_CONTEXT_EVIDENCE_VERIFIER_NOT_FOUND")
    reference = _validate_stage_output(
        verifier,
        allowed_states=frozenset({"SUCCEEDED"}),
        require_valid_until=True,
    )
    bundle_id = reference.payload.get("evidence_bundle_id", reference.payload.get("bundle_id"))
    bundle_hash = reference.payload.get(
        "evidence_bundle_hash", reference.payload.get("bundle_hash")
    )
    if bundle_id != bundle.id or bundle_hash != bundle.bundle_hash:
        raise DecisionContextFreezeError("DECISION_CONTEXT_EVIDENCE_PROVENANCE_MISMATCH")
    return bundle, reference


def _load_market_context(
    db: Session, run: AgentRun
) -> tuple[MarketContextSnapshot | None, dict[str, object] | None]:
    if run.market_context_snapshot_id is None:
        if run.market_context_snapshot_hash is not None:
            raise DecisionContextFreezeError("DECISION_CONTEXT_MARKET_PROVENANCE_MISMATCH")
        return None, None
    market_context = db.scalar(
        select(MarketContextSnapshot)
        .where(MarketContextSnapshot.id == run.market_context_snapshot_id)
        .with_for_update()
    )
    if market_context is None:
        raise DecisionContextFreezeError("DECISION_CONTEXT_MARKET_CONTEXT_NOT_FOUND")
    payload = _payload(market_context.payload_json, "DECISION_CONTEXT_MARKET_CONTEXT_INVALID")
    canonical = canonical_context_json(payload)
    if (
        market_context.market != run.market
        or market_context.symbol != run.symbol
        or market_context.quality != "NORMAL"
        or market_context.payload_hash != run.market_context_snapshot_hash
        or canonical != market_context.payload_json
        or context_digest(canonical) != market_context.payload_hash
    ):
        raise DecisionContextFreezeError("DECISION_CONTEXT_MARKET_PROVENANCE_MISMATCH")
    return market_context, payload


def _load_required_stages(db: Session, run: AgentRun) -> dict[str, _StageReference]:
    required_roles = (*SCOUT_ROLES, CANDIDATE_AUDIT_ROLE)
    rows = list(
        db.scalars(
            select(AgentStageRun)
            .where(
                AgentStageRun.run_id == run.id,
                AgentStageRun.role.in_(required_roles),
            )
            .order_by(AgentStageRun.sequence, AgentStageRun.id)
            .with_for_update()
        )
    )
    by_role = {stage.role: stage for stage in rows}
    if set(by_role) != set(required_roles):
        raise DecisionContextFreezeError("DECISION_CONTEXT_REQUIRED_STAGE_NOT_FOUND")

    references: dict[str, _StageReference] = {}
    for role in SCOUT_ROLES:
        allowed = (
            _SCOUT_TERMINAL | {"NOT_APPLICABLE"}
            if role == "POSITION_RISK_SCOUT"
            else _SCOUT_TERMINAL
        )
        references[role] = _validate_stage_output(
            by_role[role], allowed_states=frozenset(allowed), require_valid_until=True
        )
    references[CANDIDATE_AUDIT_ROLE] = _validate_stage_output(
        by_role[CANDIDATE_AUDIT_ROLE],
        allowed_states=frozenset({"SUCCEEDED"}),
        require_valid_until=False,
    )
    return references


def _prepare_context(db: Session, run: AgentRun, now: datetime) -> _PreparedContext:
    decision_input, input_payload, input_valid_until = _load_input(db, run)
    bundle, verifier = _load_bundle(db, run)
    market_context, market_payload = _load_market_context(db, run)
    stages = _load_required_stages(db, run)
    assert verifier.valid_until is not None

    validity_sources: list[dict[str, str]] = [
        {"source": "AGENT_RUN", "valid_until": _timestamp(run.valid_until)},
        {
            "source": "DECISION_INPUT_SNAPSHOT",
            "valid_until": _timestamp(input_valid_until),
        },
        {
            "source": "EVIDENCE_BUNDLE",
            "valid_until": _timestamp(verifier.valid_until),
        },
    ]
    validity_values = [_aware(run.valid_until), input_valid_until, verifier.valid_until]
    for role in SCOUT_ROLES:
        stage_valid_until = stages[role].valid_until
        assert stage_valid_until is not None
        validity_sources.append({"source": role, "valid_until": _timestamp(stage_valid_until)})
        validity_values.append(stage_valid_until)
    if market_context is not None:
        validity_sources.append(
            {
                "source": "MARKET_CONTEXT_SNAPSHOT",
                "valid_until": _timestamp(market_context.valid_until),
            }
        )
        validity_values.append(_aware(market_context.valid_until))
    valid_until = min(validity_values)
    if any(value <= now for value in validity_values):
        raise DecisionContextFreezeError("DECISION_CONTEXT_SOURCE_EXPIRED")

    configuration = {
        "decision_input_configuration": input_payload.get("configuration_version"),
        "server_input_policy_version": run.server_input_policy_version,
    }
    configuration_json = canonical_context_json(configuration)
    configuration_hash = context_digest(configuration_json)

    try:
        route_versions = json.loads(run.route_versions_json)
    except (TypeError, ValueError) as exc:
        raise DecisionContextFreezeError("DECISION_CONTEXT_VERSION_MANIFEST_INVALID") from exc
    if not isinstance(route_versions, dict):
        raise DecisionContextFreezeError("DECISION_CONTEXT_VERSION_MANIFEST_INVALID")
    route_versions_json = canonical_context_json(route_versions)
    version_manifest = {
        "candidate_audit_schema_version": stages[CANDIDATE_AUDIT_ROLE].payload["schema_version"],
        "dag_version": run.dag_version,
        "decision_input_schema_version": decision_input.schema_version,
        "evidence_policy_version": bundle.policy_version,
        "market_context_schema_version": (
            market_payload.get("schema_version") if market_payload is not None else None
        ),
        "route_versions_hash": context_digest(route_versions_json),
        "scout_schema_versions": [
            {"role": role, "schema_version": stages[role].payload["schema_version"]}
            for role in SCOUT_ROLES
        ],
        "server_input_policy_version": run.server_input_policy_version,
    }
    version_json = canonical_context_json(version_manifest)
    version_hash = context_digest(version_json)

    scout_manifest = [
        {
            "observed_at": _timestamp(
                _payload_timestamp(
                    stages[role].payload,
                    "observed_at",
                    "DECISION_CONTEXT_STAGE_VALIDITY_INVALID",
                )
            ),
            "output_hash": stages[role].stage.output_hash,
            "role": role,
            "schema_version": stages[role].payload["schema_version"],
            "stage_id": stages[role].stage.id,
            "state": stages[role].stage.state,
            "status": stages[role].payload["status"],
            "valid_until": _timestamp(stages[role].valid_until),
        }
        for role in SCOUT_ROLES
    ]
    manifest = {
        "candidate_audit": {
            "output_hash": stages[CANDIDATE_AUDIT_ROLE].stage.output_hash,
            "role": CANDIDATE_AUDIT_ROLE,
            "schema_version": stages[CANDIDATE_AUDIT_ROLE].payload["schema_version"],
            "stage_id": stages[CANDIDATE_AUDIT_ROLE].stage.id,
            "state": stages[CANDIDATE_AUDIT_ROLE].stage.state,
            "status": stages[CANDIDATE_AUDIT_ROLE].payload["status"],
        },
        "configuration_provenance": configuration,
        "configuration_provenance_hash": configuration_hash,
        "decision_input": {
            "id": decision_input.id,
            "indicator_snapshot_id": decision_input.indicator_snapshot_id,
            "input_hash": decision_input.input_hash,
            "market_snapshot_id": decision_input.market_snapshot_id,
            "observed_at": _timestamp(decision_input.observed_at),
            "schema_version": decision_input.schema_version,
            "valid_until": _timestamp(input_valid_until),
        },
        "evidence_bundle": {
            "as_of": _timestamp(bundle.as_of),
            "bundle_hash": bundle.bundle_hash,
            "id": bundle.id,
            "policy_version": bundle.policy_version,
            "state": bundle.state,
            "valid_until": _timestamp(verifier.valid_until),
            "verifier_output_hash": verifier.stage.output_hash,
            "verifier_stage_id": verifier.stage.id,
        },
        "market_context": (
            {
                "id": market_context.id,
                "observed_at": _timestamp(market_context.observed_at),
                "payload_hash": market_context.payload_hash,
                "quality": market_context.quality,
                "received_at": _timestamp(market_context.received_at),
                "valid_until": _timestamp(market_context.valid_until),
            }
            if market_context is not None
            else None
        ),
        "run": {
            "analysis_context": run.analysis_context,
            "dag_version": run.dag_version,
            "id": run.id,
            "input_hash": run.input_hash,
            "market": run.market,
            "purpose": run.purpose,
            "symbol": run.symbol,
            "valid_until": _timestamp(run.valid_until),
        },
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "scouts": scout_manifest,
        "valid_until": _timestamp(valid_until),
        "validity_sources": validity_sources,
        "version_manifest": version_manifest,
        "version_manifest_hash": version_hash,
    }
    manifest_json = canonical_context_json(manifest)
    return _PreparedContext(
        decision_input=decision_input,
        bundle=bundle,
        market_context=market_context,
        stages=stages,
        configuration_json=configuration_json,
        configuration_hash=configuration_hash,
        version_json=version_json,
        version_hash=version_hash,
        manifest_json=manifest_json,
        context_hash=context_digest(manifest_json),
        valid_until=valid_until,
    )


def _matches(existing: DecisionContext, prepared: _PreparedContext) -> bool:
    return (
        existing.schema_version == CONTEXT_SCHEMA_VERSION
        and existing.decision_input_snapshot_id == prepared.decision_input.id
        and existing.evidence_bundle_id == prepared.bundle.id
        and existing.market_context_snapshot_id
        == (prepared.market_context.id if prepared.market_context else None)
        and existing.technical_scout_stage_id == prepared.stages["TECHNICAL_SCOUT"].stage.id
        and existing.news_disclosure_scout_stage_id
        == prepared.stages["NEWS_DISCLOSURE_SCOUT"].stage.id
        and existing.market_sector_scout_stage_id == prepared.stages["MARKET_SECTOR_SCOUT"].stage.id
        and existing.position_risk_scout_stage_id == prepared.stages["POSITION_RISK_SCOUT"].stage.id
        and existing.candidate_audit_stage_id == prepared.stages[CANDIDATE_AUDIT_ROLE].stage.id
        and existing.configuration_provenance_json == prepared.configuration_json
        and existing.configuration_provenance_hash == prepared.configuration_hash
        and existing.version_manifest_json == prepared.version_json
        and existing.version_manifest_hash == prepared.version_hash
        and existing.manifest_json == prepared.manifest_json
        and existing.context_hash == prepared.context_hash
        and _aware(existing.valid_until) == prepared.valid_until
    )


def validate_decision_context_integrity(
    db: Session,
    *,
    run: AgentRun,
    context: DecisionContext,
    now: datetime,
    allow_terminal: bool = False,
    validate_activation: bool = True,
) -> dict[str, object]:
    observed = _aware(now)
    if (
        context.run_id != run.id
        or context.schema_version != CONTEXT_SCHEMA_VERSION
        or run.dag_version != V7_DAG_VERSION
        or run.analysis_context != "ENTRY"
        or run.purpose not in {"DIAGNOSTIC", "TRADING"}
        or run.state
        not in ({"CREATED", "RUNNING", "SUCCEEDED"} if allow_terminal else {"CREATED", "RUNNING"})
        or _aware(context.valid_until) <= observed
    ):
        raise DecisionContextFreezeError("DECISION_CONTEXT_RUN_NOT_ELIGIBLE")
    if validate_activation:
        try:
            validate_frozen_activation_provenance(db, run=run)
        except ActivationGateError as exc:
            raise DecisionContextFreezeError(
                "DECISION_CONTEXT_ACTIVATION_PROVENANCE_INVALID"
            ) from exc
    prepared = _prepare_context(db, run, observed)
    if not _matches(context, prepared):
        raise DecisionContextFreezeError("DECISION_CONTEXT_FREEZE_CONFLICT", 409)
    manifest = _payload(context.manifest_json, "DECISION_CONTEXT_MANIFEST_INVALID")
    if context_digest(canonical_context_json(manifest)) != context.context_hash:
        raise DecisionContextFreezeError("DECISION_CONTEXT_HASH_MISMATCH")
    return manifest


def freeze_decision_context(
    db: Session, *, run_id: str, now: datetime | None = None
) -> DecisionContext:
    observed = _aware(now or datetime.now(UTC))
    try:
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None:
            raise DecisionContextFreezeError("DECISION_CONTEXT_RUN_NOT_FOUND", 404)
        if (
            run.dag_version != V7_DAG_VERSION
            or run.analysis_context != "ENTRY"
            or run.purpose not in {"DIAGNOSTIC", "TRADING"}
            or run.state not in {"CREATED", "RUNNING"}
        ):
            raise DecisionContextFreezeError("DECISION_CONTEXT_RUN_NOT_ELIGIBLE")
        if _aware(run.valid_until) <= observed:
            raise DecisionContextFreezeError("DECISION_CONTEXT_RUN_EXPIRED")
        try:
            validate_frozen_activation_provenance(db, run=run)
        except ActivationGateError as exc:
            raise DecisionContextFreezeError(
                "DECISION_CONTEXT_ACTIVATION_PROVENANCE_INVALID"
            ) from exc

        existing = db.scalar(
            select(DecisionContext).where(DecisionContext.run_id == run.id).with_for_update()
        )
        prepared = _prepare_context(db, run, observed)
        if existing is not None:
            if not _matches(existing, prepared):
                raise DecisionContextFreezeError("DECISION_CONTEXT_FREEZE_CONFLICT", 409)
            db.commit()
            return existing

        context = DecisionContext(
            run_id=run.id,
            schema_version=CONTEXT_SCHEMA_VERSION,
            decision_input_snapshot_id=prepared.decision_input.id,
            evidence_bundle_id=prepared.bundle.id,
            market_context_snapshot_id=(
                prepared.market_context.id if prepared.market_context else None
            ),
            technical_scout_stage_id=prepared.stages["TECHNICAL_SCOUT"].stage.id,
            news_disclosure_scout_stage_id=prepared.stages["NEWS_DISCLOSURE_SCOUT"].stage.id,
            market_sector_scout_stage_id=prepared.stages["MARKET_SECTOR_SCOUT"].stage.id,
            position_risk_scout_stage_id=prepared.stages["POSITION_RISK_SCOUT"].stage.id,
            candidate_audit_stage_id=prepared.stages[CANDIDATE_AUDIT_ROLE].stage.id,
            configuration_provenance_json=prepared.configuration_json,
            configuration_provenance_hash=prepared.configuration_hash,
            version_manifest_json=prepared.version_json,
            version_manifest_hash=prepared.version_hash,
            manifest_json=prepared.manifest_json,
            context_hash=prepared.context_hash,
            frozen_at=observed,
            valid_until=prepared.valid_until,
        )
        db.add(context)
        db.flush()
        db.commit()
        db.refresh(context)
        return context
    except IntegrityError:
        db.rollback()
        try:
            existing = db.scalar(select(DecisionContext).where(DecisionContext.run_id == run_id))
            if existing is not None:
                run = db.get(AgentRun, run_id)
                if run is not None:
                    prepared = _prepare_context(db, run, observed)
                    if _matches(existing, prepared):
                        db.commit()
                        return existing
        except Exception:
            db.rollback()
            raise
        db.rollback()
        raise DecisionContextFreezeError("DECISION_CONTEXT_FREEZE_CONFLICT", 409) from None
    except Exception:
        db.rollback()
        raise


def decision_context_allows_claim(db: Session, *, run: AgentRun, now: datetime) -> bool:
    context = db.scalar(select(DecisionContext).where(DecisionContext.run_id == run.id))
    if (
        context is None
        or context.schema_version != CONTEXT_SCHEMA_VERSION
        or _aware(context.valid_until) <= now
        or context_digest(context.manifest_json) != context.context_hash
    ):
        return False
    try:
        manifest = json.loads(context.manifest_json)
    except (TypeError, ValueError):
        return False
    if not isinstance(manifest, dict) or not isinstance(manifest.get("run"), dict):
        return False
    manifest_run = manifest["run"]
    return bool(
        manifest.get("schema_version") == CONTEXT_SCHEMA_VERSION
        and manifest_run.get("id") == run.id
        and manifest_run.get("dag_version") == run.dag_version
        and manifest_run.get("analysis_context") == run.analysis_context
        and manifest_run.get("purpose") == run.purpose
        and manifest_run.get("input_hash") == run.input_hash
        and manifest.get("valid_until") == _timestamp(context.valid_until)
    )
