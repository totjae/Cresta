from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.activation_gate import ActivationGateError, validate_frozen_activation_provenance
from app.agents.contracts import (
    DecisionAgentIdentity,
    DecisionAgentInput,
    DecisionAgentPolicyProfile,
    DecisionAgentStageInput,
    DecisionCandidateAuditMaterial,
    DecisionEvidenceBundleMaterial,
    DecisionEvidenceMaterial,
    DecisionInputResolvedMaterial,
    DecisionMarketContextMaterial,
    DecisionScoutResultMaterial,
    ResolvedDecisionContext,
)
from app.agents.decision_context import (
    CONTEXT_SCHEMA_VERSION,
    SCOUT_ROLES,
    V7_DAG_VERSION,
    DecisionContextFreezeError,
    canonical_context_json,
    context_digest,
    validate_decision_context_integrity,
)
from app.agents.policy_profiles import (
    ROLE_AGENT_TYPES,
    PolicyProfileError,
    resolve_decision_agent_policy,
)
from app.models import (
    AgentRun,
    AgentStageRun,
    DecisionContext,
    DecisionInputSnapshot,
    EvidenceBundle,
    EvidenceItem,
    LlmModelProfile,
    LlmPromptProfile,
    LlmRoleRoute,
    MarketContextSnapshot,
)

DECISION_AGENT_INPUT_VERSION = "decision-agent-input-v1"
DECISION_AGENT_STAGE_INPUT_VERSION = "decision-agent-stage-input-v1"
DECISION_AGENT_MODEL_OUTPUT_VERSION = "decision-agent-model-output-v1"
DECISION_AGENT_RESULT_VERSION = "decision-agent-result-v1"
DECISION_AGENT_ROLES = tuple(ROLE_AGENT_TYPES)
DECISION_AGENT_STAGE_PLAN = (
    ("CONSERVATIVE_DECISION", 70),
    ("BALANCED_DECISION", 71),
    ("AGGRESSIVE_DECISION", 72),
)
DECISION_AGENT_DEPENDENCIES = ("EVIDENCE_CANDIDATE_AUDITOR",)
V7_SCOUT_ROUTE_ROLES = (
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
)
V7_LLM_ROUTE_ROLES = (*V7_SCOUT_ROUTE_ROLES, *DECISION_AGENT_ROLES)


class DecisionAgentFoundationError(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class FrozenDecisionRoute:
    route: LlmRoleRoute
    prompt: LlmPromptProfile
    model: LlmModelProfile
    snapshot: dict[str, object]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = _aware(value)
    return normalized.isoformat(timespec="seconds" if normalized.microsecond == 0 else "microseconds")


def _object(encoded: str | None, code: str) -> dict[str, object]:
    if not encoded:
        raise DecisionAgentFoundationError(code)
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise DecisionAgentFoundationError(code) from exc
    if not isinstance(value, dict):
        raise DecisionAgentFoundationError(code)
    return value


def _list(encoded: str | None, code: str) -> list[object]:
    if encoded is None:
        raise DecisionAgentFoundationError(code)
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise DecisionAgentFoundationError(code) from exc
    if not isinstance(value, list):
        raise DecisionAgentFoundationError(code)
    return value


def _route_snapshot(run: AgentRun) -> dict[str, dict[str, object]]:
    value = _object(run.route_versions_json, "DECISION_AGENT_ROUTE_PROVENANCE_INVALID")
    if set(value) != set(V7_LLM_ROUTE_ROLES):
        raise DecisionAgentFoundationError("DECISION_AGENT_ROUTE_SET_INCOMPLETE")
    result: dict[str, dict[str, object]] = {}
    for role, item in value.items():
        if not isinstance(item, dict):
            raise DecisionAgentFoundationError("DECISION_AGENT_ROUTE_PROVENANCE_INVALID")
        expected_hash = item.get("route_version_hash")
        material = {key: entry for key, entry in item.items() if key != "route_version_hash"}
        if not isinstance(expected_hash, str) or context_digest(
            canonical_context_json(material)
        ) != expected_hash:
            raise DecisionAgentFoundationError("DECISION_AGENT_ROUTE_PROVENANCE_INVALID")
        result[role] = item
    return result


def resolve_frozen_decision_route(
    db: Session, *, run: AgentRun, role: str
) -> FrozenDecisionRoute:
    if role not in ROLE_AGENT_TYPES:
        raise DecisionAgentFoundationError("DECISION_AGENT_ROLE_INVALID")
    snapshot = _route_snapshot(run)[role]
    route_id = snapshot.get("route_id")
    prompt_id = snapshot.get("prompt_profile_id")
    model_id = snapshot.get("model_id")
    if not all(isinstance(value, str) for value in (route_id, prompt_id, model_id)):
        raise DecisionAgentFoundationError("DECISION_AGENT_ROUTE_PROVENANCE_INVALID")
    route = db.get(LlmRoleRoute, route_id)
    prompt = db.get(LlmPromptProfile, prompt_id)
    model = db.get(LlmModelProfile, model_id)
    if (
        route is None
        or prompt is None
        or model is None
        or route.owner_id != run.owner_id
        or route.role != role
        or route.id != route_id
        or route.primary_model_profile_id != model.id
        or route.prompt_profile_id != prompt.id
        or route.prompt_version != snapshot.get("prompt_version")
        or prompt.owner_id != run.owner_id
        or prompt.role != role
        or prompt.state != "VALIDATED"
        or prompt.content_hash != snapshot.get("prompt_content_hash")
        or model.state != "VALIDATED"
        or model.version != snapshot.get("model_version")
        or route.fallback_policy != snapshot.get("failure_policy")
        or route.output_schema_version != snapshot.get("declared_output_schema_version")
        or route.web_search_enabled
        or snapshot.get("web_search_enabled") is not False
        or snapshot.get("declared_output_schema_version")
        != DECISION_AGENT_MODEL_OUTPUT_VERSION
        or snapshot.get("effective_output_schema_version")
        != DECISION_AGENT_MODEL_OUTPUT_VERSION
    ):
        raise DecisionAgentFoundationError("DECISION_AGENT_ROUTE_PROVENANCE_INVALID")
    return FrozenDecisionRoute(route=route, prompt=prompt, model=model, snapshot=snapshot)


def build_decision_agent_stage_input(
    db: Session,
    *,
    run: AgentRun,
    context: DecisionContext,
    role: str,
) -> DecisionAgentStageInput:
    _, policy = resolve_decision_agent_policy(db, run_id=run.id, role=role)
    binding = resolve_frozen_decision_route(db, run=run, role=role)
    snapshot = binding.snapshot
    return DecisionAgentStageInput(
        decision_context_id=context.id,
        decision_context_hash=context.context_hash,
        role=role,
        agent_type=ROLE_AGENT_TYPES[role],
        policy_profile_id=policy.configuration_version_id,
        policy_profile_hash=policy.payload_hash,
        route_id=binding.route.id,
        route_version=snapshot["route_version"],
        route_version_hash=snapshot["route_version_hash"],
        prompt_profile_id=binding.prompt.id,
        prompt_version=snapshot["prompt_version"],
        prompt_hash=binding.prompt.content_hash,
        requested_model_profile_id=binding.model.id,
    )


def decision_agent_stage_input_hash(value: DecisionAgentStageInput) -> str:
    return context_digest(canonical_context_json(value.model_dump(mode="json")))


def decision_agent_input_json(value: DecisionAgentInput) -> str:
    return canonical_context_json(value.model_dump(mode="json"))


def decision_agent_input_hash(value: DecisionAgentInput) -> str:
    return context_digest(decision_agent_input_json(value))


def _load_context_rows(
    db: Session, *, run: AgentRun, context: DecisionContext, now: datetime
) -> tuple[
    dict[str, object],
    DecisionInputSnapshot,
    EvidenceBundle,
    list[EvidenceItem],
    list[AgentStageRun],
    AgentStageRun,
    MarketContextSnapshot | None,
]:
    manifest = validate_decision_context_integrity(
        db,
        run=run,
        context=context,
        now=now,
    )
    decision_input = db.get(DecisionInputSnapshot, context.decision_input_snapshot_id)
    bundle = db.get(EvidenceBundle, context.evidence_bundle_id)
    stage_ids = [
        context.technical_scout_stage_id,
        context.news_disclosure_scout_stage_id,
        context.market_sector_scout_stage_id,
        context.position_risk_scout_stage_id,
    ]
    scout_stages = list(
        db.scalars(select(AgentStageRun).where(AgentStageRun.id.in_(stage_ids)))
    )
    candidate = db.get(AgentStageRun, context.candidate_audit_stage_id)
    market_context = (
        db.get(MarketContextSnapshot, context.market_context_snapshot_id)
        if context.market_context_snapshot_id
        else None
    )
    if decision_input is None or bundle is None or candidate is None:
        raise DecisionAgentFoundationError("DECISION_AGENT_INPUT_PROVENANCE_INVALID")
    if len(scout_stages) != len(SCOUT_ROLES):
        raise DecisionAgentFoundationError("DECISION_AGENT_INPUT_PROVENANCE_INVALID")
    evidence_ids = _list(
        bundle.evidence_ids_json,
        "DECISION_AGENT_EVIDENCE_NOT_ALLOWED",
    )
    if any(not isinstance(value, str) for value in evidence_ids) or evidence_ids != sorted(
        set(evidence_ids)
    ):
        raise DecisionAgentFoundationError("DECISION_AGENT_EVIDENCE_NOT_ALLOWED")
    evidence_items = list(
        db.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.id.in_(evidence_ids))
            .order_by(EvidenceItem.id)
        )
    )
    if (
        [item.id for item in evidence_items] != evidence_ids
        or any(item.run_id != run.id or item.source_tier == "UNRATED" for item in evidence_items)
    ):
        raise DecisionAgentFoundationError("DECISION_AGENT_EVIDENCE_NOT_ALLOWED")
    return (
        manifest,
        decision_input,
        bundle,
        evidence_items,
        scout_stages,
        candidate,
        market_context,
    )


def resolve_decision_context_material(
    db: Session,
    *,
    run: AgentRun,
    context: DecisionContext,
    now: datetime,
) -> ResolvedDecisionContext:
    (
        _manifest,
        decision_input,
        bundle,
        evidence_items,
        scout_stages,
        candidate,
        market_context,
    ) = _load_context_rows(db, run=run, context=context, now=now)
    input_material = _object(
        decision_input.input_json,
        "DECISION_AGENT_INPUT_PROVENANCE_INVALID",
    )
    input_material.pop("user_id", None)
    evidence = [
        DecisionEvidenceMaterial(
            evidence_id=item.id,
            source_type=item.source_type,
            source_tier=item.source_tier,
            source_name=item.source_name,
            title=item.title,
            facts=json.loads(item.facts_json),
            content_hash=item.content_hash,
            extraction_method=item.extraction_method,
            published_at=_timestamp(item.published_at),
            event_at=_timestamp(item.event_at),
            received_at=_timestamp(item.received_at),
        )
        for item in evidence_items
    ]
    by_role = {stage.role: stage for stage in scout_stages}
    scouts = [
        DecisionScoutResultMaterial(
            role=role,
            stage_run_id=by_role[role].id,
            output_hash=by_role[role].output_hash,
            result=_object(
                by_role[role].output_json,
                "DECISION_AGENT_INPUT_PROVENANCE_INVALID",
            ),
        )
        for role in SCOUT_ROLES
    ]
    market_material = None
    if market_context is not None:
        market_material = DecisionMarketContextMaterial(
            snapshot_id=market_context.id,
            payload_hash=market_context.payload_hash,
            quality=market_context.quality,
            observed_at=_timestamp(market_context.observed_at),
            received_at=_timestamp(market_context.received_at),
            valid_until=_timestamp(market_context.valid_until),
            material=_object(
                market_context.payload_json,
                "DECISION_AGENT_INPUT_PROVENANCE_INVALID",
            ),
        )
    return ResolvedDecisionContext(
        decision_context_id=context.id,
        decision_context_hash=context.context_hash,
        run_id=run.id,
        decision_input=DecisionInputResolvedMaterial(
            snapshot_id=decision_input.id,
            input_hash=decision_input.input_hash,
            material=input_material,
        ),
        evidence_bundle=DecisionEvidenceBundleMaterial(
            bundle_id=bundle.id,
            bundle_hash=bundle.bundle_hash,
            policy_version=bundle.policy_version,
            state=bundle.state,
            verified_evidence=evidence,
        ),
        scout_results=scouts,
        candidate_audit=DecisionCandidateAuditMaterial(
            stage_run_id=candidate.id,
            output_hash=candidate.output_hash,
            result=_object(
                candidate.output_json,
                "DECISION_AGENT_INPUT_PROVENANCE_INVALID",
            ),
        ),
        market_context=market_material,
        observed_at=_timestamp(decision_input.observed_at),
        frozen_at=_timestamp(context.frozen_at),
        valid_until=_timestamp(context.valid_until),
    )


def build_decision_agent_input(
    db: Session,
    *,
    run: AgentRun,
    context: DecisionContext,
    role: str,
    now: datetime,
    resolved_context: ResolvedDecisionContext | None = None,
) -> DecisionAgentInput:
    _, policy = resolve_decision_agent_policy(db, run_id=run.id, role=role)
    resolve_frozen_decision_route(db, run=run, role=role)
    material = resolved_context or resolve_decision_context_material(
        db,
        run=run,
        context=context,
        now=now,
    )
    allowed_refs = [item.evidence_id for item in material.evidence_bundle.verified_evidence]
    return DecisionAgentInput(
        decision_context=material,
        agent=DecisionAgentIdentity(role=role, agent_type=ROLE_AGENT_TYPES[role]),
        policy_profile=DecisionAgentPolicyProfile.model_validate(policy.model_dump()),
        allowed_evidence_refs=allowed_refs,
        valid_until=material.valid_until,
    )


def materialize_decision_agent_stages(
    db: Session,
    *,
    run_id: str,
    now: datetime | None = None,
) -> tuple[AgentStageRun, ...]:
    observed = _aware(now or datetime.now(UTC))
    try:
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        context = db.scalar(
            select(DecisionContext)
            .where(DecisionContext.run_id == run_id)
            .with_for_update()
        )
        if (
            run is None
            or context is None
            or run.dag_version != V7_DAG_VERSION
            or run.purpose not in {"DIAGNOSTIC", "TRADING"}
            or run.analysis_context != "ENTRY"
            or run.state not in {"CREATED", "RUNNING"}
            or _aware(run.valid_until) <= observed
            or _aware(context.valid_until) <= observed
            or context.schema_version != CONTEXT_SCHEMA_VERSION
        ):
            raise DecisionAgentFoundationError("DECISION_AGENT_MATERIALIZATION_NOT_ELIGIBLE")
        try:
            validate_frozen_activation_provenance(db, run=run)
        except ActivationGateError as exc:
            raise DecisionAgentFoundationError(
                "DECISION_AGENT_MATERIALIZATION_NOT_ELIGIBLE"
            ) from exc
        validate_decision_context_integrity(db, run=run, context=context, now=observed)
        _route_snapshot(run)

        expected: dict[str, tuple[int, str, str]] = {}
        for role, sequence in DECISION_AGENT_STAGE_PLAN:
            stage_input = build_decision_agent_stage_input(
                db,
                run=run,
                context=context,
                role=role,
            )
            expected[role] = (
                sequence,
                stage_input.route_id,
                decision_agent_stage_input_hash(stage_input),
            )

        existing = list(
            db.scalars(
                select(AgentStageRun)
                .where(
                    AgentStageRun.run_id == run.id,
                    AgentStageRun.role.in_(DECISION_AGENT_ROLES),
                )
                .with_for_update()
            )
        )
        by_role = {stage.role: stage for stage in existing}
        if len(by_role) != len(existing):
            raise DecisionAgentFoundationError("DECISION_AGENT_MATERIALIZATION_CONFLICT", 409)
        dependency_json = canonical_context_json(list(DECISION_AGENT_DEPENDENCIES))
        for role, stage in by_role.items():
            sequence, route_id, input_hash = expected[role]
            if (
                stage.sequence != sequence
                or stage.route_id != route_id
                or stage.input_hash != input_hash
                or stage.dependency_roles_json != dependency_json
                or stage.max_attempts != 1
            ):
                raise DecisionAgentFoundationError(
                    "DECISION_AGENT_MATERIALIZATION_CONFLICT",
                    409,
                )
        for role, (sequence, route_id, input_hash) in expected.items():
            if role in by_role:
                continue
            stage = AgentStageRun(
                run_id=run.id,
                role=role,
                sequence=sequence,
                dependency_roles_json=dependency_json,
                route_id=route_id,
                state="PENDING",
                input_hash=input_hash,
                max_attempts=1,
                available_at=observed,
            )
            db.add(stage)
            by_role[role] = stage
        db.flush()
        db.commit()
        return tuple(by_role[role] for role in DECISION_AGENT_ROLES)
    except (DecisionAgentFoundationError, DecisionContextFreezeError, PolicyProfileError):
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise DecisionAgentFoundationError(
            "DECISION_AGENT_MATERIALIZATION_CONFLICT",
            409,
        ) from exc
