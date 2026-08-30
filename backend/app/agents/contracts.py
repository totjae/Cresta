from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


DecisionAgentRole = Literal[
    "CONSERVATIVE_DECISION",
    "BALANCED_DECISION",
    "AGGRESSIVE_DECISION",
]
DecisionAgentType = Literal["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]
DecisionAgentStatus = Literal[
    "SUCCEEDED",
    "INSUFFICIENT_DATA",
    "CONFLICTED",
    "TIMED_OUT",
    "FAILED",
    "INVALID_OUTPUT",
]
DecisionAgentAction = Literal["BUY", "WAIT", "REJECT", "UNKNOWN"]
DecisionPattern = Literal[
    "MANDATORY_UNKNOWN",
    "MULTIPLE_REJECT",
    "SINGLE_REJECT",
    "ALL_BUY",
    "BALANCED_PLUS_ONE_BUY",
    "DEFAULT_WAIT",
]


def _validate_canonical_decimal(
    value: str, *, minimum: Decimal, maximum: Decimal
) -> str:
    if not isinstance(value, str) or not value or "e" in value.casefold() or value.startswith("+"):
        raise ValueError("decimal must be a canonical base-10 string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("decimal must be a canonical base-10 string") from exc
    if not parsed.is_finite() or parsed < minimum or parsed > maximum or value == "-0":
        raise ValueError("decimal is outside the allowed range")
    canonical = format(parsed, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if canonical == "-0":
        canonical = "0"
    if value != canonical:
        raise ValueError("decimal must use canonical base-10 representation")
    return value


def _validate_utc_timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("timestamp must be a canonical UTC string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be a canonical UTC string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamp must use UTC +00:00")
    normalized = parsed.astimezone(UTC).isoformat(
        timespec="seconds" if parsed.microsecond == 0 else "microseconds"
    )
    if value != normalized:
        raise ValueError("timestamp must use canonical UTC +00:00 representation")
    return value


class DecisionPolicyParameters(AgentContract):
    minimum_confidence: str
    minimum_entry_score: Annotated[int, Field(strict=True, ge=0, le=100)]
    risk_tolerance_score: Annotated[int, Field(strict=True, ge=0, le=100)]
    uncertainty_tolerance_ratio: str
    momentum_deterioration_tolerance_pct: str
    drawdown_tolerance_pct: str

    @field_validator("minimum_confidence", "uncertainty_tolerance_ratio")
    @classmethod
    def validate_ratio(cls, value: str) -> str:
        return _validate_canonical_decimal(value, minimum=Decimal(0), maximum=Decimal(1))

    @field_validator("momentum_deterioration_tolerance_pct", "drawdown_tolerance_pct")
    @classmethod
    def validate_percentage(cls, value: str) -> str:
        return _validate_canonical_decimal(value, minimum=Decimal(0), maximum=Decimal(100))


class DecisionAgentPolicyProfile(AgentContract):
    configuration_version_id: str
    category: str
    sequence: Annotated[int, Field(strict=True, ge=1)]
    agent_type: DecisionAgentType
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: Literal["policy-schema-v1"] = "policy-schema-v1"
    policy_parameters: DecisionPolicyParameters


class DecisionAgentIdentity(AgentContract):
    role: DecisionAgentRole
    agent_type: DecisionAgentType

    @model_validator(mode="after")
    def validate_mapping(self) -> DecisionAgentIdentity:
        expected = {
            "CONSERVATIVE_DECISION": "CONSERVATIVE",
            "BALANCED_DECISION": "BALANCED",
            "AGGRESSIVE_DECISION": "AGGRESSIVE",
        }[self.role]
        if self.agent_type != expected:
            raise ValueError("decision agent role and agent_type mismatch")
        return self


class DecisionInputResolvedMaterial(AgentContract):
    snapshot_id: str
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: Literal["scout-input-v2"] = "scout-input-v2"
    material: dict[str, object]


class DecisionEvidenceMaterial(AgentContract):
    evidence_id: str
    source_type: str
    source_tier: str
    source_name: str
    title: str
    facts: object
    content_hash: str
    extraction_method: str
    published_at: str | None
    event_at: str | None
    received_at: str

    @field_validator("published_at", "event_at", "received_at")
    @classmethod
    def validate_timestamps(cls, value: str | None) -> str | None:
        return _validate_utc_timestamp(value) if value is not None else None


class DecisionEvidenceBundleMaterial(AgentContract):
    bundle_id: str
    bundle_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: str
    state: str
    verified_evidence: list[DecisionEvidenceMaterial]


class DecisionScoutResultMaterial(AgentContract):
    role: Literal[
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
    ]
    stage_run_id: str
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: dict[str, object]


class DecisionCandidateAuditMaterial(AgentContract):
    stage_run_id: str
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: dict[str, object]


class DecisionMarketContextMaterial(AgentContract):
    snapshot_id: str
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality: str
    observed_at: str
    received_at: str
    valid_until: str
    material: dict[str, object]

    @field_validator("observed_at", "received_at", "valid_until")
    @classmethod
    def validate_timestamps(cls, value: str) -> str:
        return _validate_utc_timestamp(value)


class ResolvedDecisionContext(AgentContract):
    decision_context_id: str
    decision_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    analysis_context: Literal["ENTRY"] = "ENTRY"
    purpose: Literal["DIAGNOSTIC"] = "DIAGNOSTIC"
    decision_input: DecisionInputResolvedMaterial
    evidence_bundle: DecisionEvidenceBundleMaterial
    scout_results: list[DecisionScoutResultMaterial]
    candidate_audit: DecisionCandidateAuditMaterial
    market_context: DecisionMarketContextMaterial | None
    observed_at: str
    frozen_at: str
    valid_until: str

    @field_validator("observed_at", "frozen_at", "valid_until")
    @classmethod
    def validate_timestamps(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @field_validator("scout_results")
    @classmethod
    def validate_scout_order(
        cls, value: list[DecisionScoutResultMaterial]
    ) -> list[DecisionScoutResultMaterial]:
        expected = [
            "TECHNICAL_SCOUT",
            "NEWS_DISCLOSURE_SCOUT",
            "MARKET_SECTOR_SCOUT",
            "POSITION_RISK_SCOUT",
        ]
        if [item.role for item in value] != expected:
            raise ValueError("scout results must use canonical role ordering")
        return value


class DecisionAgentInput(AgentContract):
    schema_version: Literal["decision-agent-input-v1"] = "decision-agent-input-v1"
    decision_context: ResolvedDecisionContext
    agent: DecisionAgentIdentity
    policy_profile: DecisionAgentPolicyProfile
    allowed_evidence_refs: list[str]
    valid_until: str

    @field_validator("valid_until")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @model_validator(mode="after")
    def validate_provenance(self) -> DecisionAgentInput:
        if self.agent.agent_type != self.policy_profile.agent_type:
            raise ValueError("decision agent policy type mismatch")
        if self.valid_until != self.decision_context.valid_until:
            raise ValueError("decision agent validity must equal context validity")
        if self.allowed_evidence_refs != sorted(set(self.allowed_evidence_refs)):
            raise ValueError("allowed evidence refs must be unique and sorted")
        return self


class DecisionAgentStageInput(AgentContract):
    schema_version: Literal["decision-agent-stage-input-v1"] = "decision-agent-stage-input-v1"
    decision_context_id: str
    decision_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: DecisionAgentRole
    agent_type: DecisionAgentType
    policy_profile_id: str
    policy_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    route_id: str
    route_version: Annotated[int, Field(strict=True, ge=1)]
    route_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_profile_id: str
    prompt_version: str
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_model_profile_id: str
    input_contract_version: Literal["decision-agent-input-v1"] = "decision-agent-input-v1"

    @model_validator(mode="after")
    def validate_mapping(self) -> DecisionAgentStageInput:
        DecisionAgentIdentity(role=self.role, agent_type=self.agent_type)
        return self


def _validate_decision_result_matrix(
    *,
    status: str,
    action: str,
    confidence: float,
    entry_score: int | None,
    risk_score: int | None,
) -> None:
    if status == "SUCCEEDED":
        if action not in {"BUY", "WAIT", "REJECT"}:
            raise ValueError("successful decision action must not be UNKNOWN")
        return
    if action != "UNKNOWN" or confidence != 0.0 or entry_score is not None or risk_score is not None:
        raise ValueError("non-success decision must be UNKNOWN with zero confidence and null scores")


class DecisionAgentModelOutput(AgentContract):
    schema_version: Literal["decision-agent-model-output-v1"] = "decision-agent-model-output-v1"
    status: Literal["SUCCEEDED", "INSUFFICIENT_DATA", "CONFLICTED"]
    action: DecisionAgentAction
    confidence: float = Field(strict=True, ge=0, le=1)
    entry_score: int | None = Field(default=None, strict=True, ge=0, le=100)
    risk_score: int | None = Field(default=None, strict=True, ge=0, le=100)
    reason_codes: list[str]
    positive_evidence_refs: list[str]
    negative_evidence_refs: list[str]

    @model_validator(mode="after")
    def validate_semantics(self) -> DecisionAgentModelOutput:
        _validate_decision_result_matrix(**self.model_dump(include={"status", "action", "confidence", "entry_score", "risk_score"}))
        from app.agents.reason_codes import DECISION_AGENT_MODEL_REASON_CODES

        if not self.reason_codes or self.reason_codes != sorted(set(self.reason_codes)):
            raise ValueError("decision reason codes must be non-empty, unique and sorted")
        if any(code not in DECISION_AGENT_MODEL_REASON_CODES for code in self.reason_codes):
            raise ValueError("decision reason code is not allowed")
        if self.positive_evidence_refs != sorted(set(self.positive_evidence_refs)):
            raise ValueError("positive evidence refs must be unique and sorted")
        if self.negative_evidence_refs != sorted(set(self.negative_evidence_refs)):
            raise ValueError("negative evidence refs must be unique and sorted")
        if set(self.positive_evidence_refs) & set(self.negative_evidence_refs):
            raise ValueError("positive and negative evidence refs must be disjoint")
        return self


class DecisionAgentResult(AgentContract):
    schema_version: Literal["decision-agent-result-v1"] = "decision-agent-result-v1"
    stage_run_id: str
    role: DecisionAgentRole
    decision_context_id: str
    decision_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_type: DecisionAgentType
    status: DecisionAgentStatus
    action: DecisionAgentAction
    confidence: float = Field(strict=True, ge=0, le=1)
    entry_score: int | None = Field(default=None, strict=True, ge=0, le=100)
    risk_score: int | None = Field(default=None, strict=True, ge=0, le=100)
    reason_codes: list[str]
    positive_evidence_refs: list[str]
    negative_evidence_refs: list[str]
    policy_profile_id: str
    policy_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_profile_version: Annotated[int, Field(strict=True, ge=1)]
    policy_profile_category: str
    route_id: str
    route_version: Annotated[int, Field(strict=True, ge=1)]
    route_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_profile_id: str
    prompt_version: str
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str
    requested_model_profile_id: str
    actual_provider: str | None
    actual_model: str | None
    fallback_used: bool
    valid_until: str

    @field_validator("valid_until")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @model_validator(mode="after")
    def validate_semantics(self) -> DecisionAgentResult:
        DecisionAgentIdentity(role=self.role, agent_type=self.agent_type)
        _validate_decision_result_matrix(**self.model_dump(include={"status", "action", "confidence", "entry_score", "risk_score"}))
        if self.model_id != self.requested_model_profile_id:
            raise ValueError("model_id must equal requested_model_profile_id")
        from app.agents.reason_codes import (
            DECISION_AGENT_MODEL_REASON_CODES,
            DECISION_AGENT_SERVER_FAILURE_REASON_CODES,
        )

        allowed_reasons = (
            set(DECISION_AGENT_SERVER_FAILURE_REASON_CODES)
            if self.status in {"TIMED_OUT", "FAILED", "INVALID_OUTPUT"}
            else set(DECISION_AGENT_MODEL_REASON_CODES)
            | set(DECISION_AGENT_SERVER_FAILURE_REASON_CODES)
        )
        if not self.reason_codes or any(
            code not in allowed_reasons for code in self.reason_codes
        ):
            raise ValueError("decision result reason code is not allowed")
        if (self.actual_provider is None) != (self.actual_model is None):
            raise ValueError("actual provider and model provenance must be both present or null")
        if self.reason_codes != sorted(set(self.reason_codes)):
            raise ValueError("decision result reason codes must be unique and sorted")
        if self.positive_evidence_refs != sorted(set(self.positive_evidence_refs)):
            raise ValueError("positive evidence refs must be unique and sorted")
        if self.negative_evidence_refs != sorted(set(self.negative_evidence_refs)):
            raise ValueError("negative evidence refs must be unique and sorted")
        if set(self.positive_evidence_refs) & set(self.negative_evidence_refs):
            raise ValueError("positive and negative evidence refs must be disjoint")
        return self


class EntryArbiterInputResult(AgentContract):
    role: DecisionAgentRole
    agent_type: DecisionAgentType
    stage_run_id: str
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: DecisionAgentStatus
    action: DecisionAgentAction

    @model_validator(mode="after")
    def validate_semantics(self) -> EntryArbiterInputResult:
        DecisionAgentIdentity(role=self.role, agent_type=self.agent_type)
        if self.status == "SUCCEEDED":
            if self.action not in {"BUY", "WAIT", "REJECT"}:
                raise ValueError("successful Arbiter input must not be UNKNOWN")
        elif self.action != "UNKNOWN":
            raise ValueError("non-success Arbiter input must be UNKNOWN")
        return self


class EntryArbiterInput(AgentContract):
    schema_version: Literal["entry-arbiter-input-v1"] = "entry-arbiter-input-v1"
    decision_context_id: str
    decision_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: Literal["consensus-policy-v1"] = "consensus-policy-v1"
    input_results: list[EntryArbiterInputResult]
    valid_until: str

    @field_validator("valid_until")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @field_validator("input_results")
    @classmethod
    def validate_result_order(
        cls, value: list[EntryArbiterInputResult]
    ) -> list[EntryArbiterInputResult]:
        expected = [
            "CONSERVATIVE_DECISION",
            "BALANCED_DECISION",
            "AGGRESSIVE_DECISION",
        ]
        if [item.role for item in value] != expected:
            raise ValueError("Arbiter inputs must use canonical C/B/A role ordering")
        if len({item.stage_run_id for item in value}) != len(expected):
            raise ValueError("Arbiter input stage IDs must be unique")
        return value


class ArbiterResult(AgentContract):
    schema_version: Literal["entry-consensus-v1"] = "entry-consensus-v1"
    decision_context_id: str
    decision_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: DecisionAgentAction
    policy_version: Literal["consensus-policy-v1"] = "consensus-policy-v1"
    input_result_ids: list[str]
    input_results: list[EntryArbiterInputResult]
    decision_pattern: DecisionPattern
    reason_codes: list[str]
    valid_until: str

    @field_validator("valid_until")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @model_validator(mode="after")
    def validate_semantics(self) -> ArbiterResult:
        from app.agents.reason_codes import ARBITER_PATTERN_REASONS

        EntryArbiterInput(
            decision_context_id=self.decision_context_id,
            decision_context_hash=self.decision_context_hash,
            policy_version=self.policy_version,
            input_results=self.input_results,
            valid_until=self.valid_until,
        )
        expected_ids = [item.stage_run_id for item in self.input_results]
        if self.input_result_ids != expected_ids:
            raise ValueError("Arbiter result IDs must match ordered input results")
        expected_action, expected_reason = ARBITER_PATTERN_REASONS[
            self.decision_pattern
        ]
        if self.action != expected_action or self.reason_codes != [expected_reason]:
            raise ValueError("Arbiter pattern, action and reason must match")
        return self


def validate_decision_evidence_refs(
    output: DecisionAgentModelOutput | DecisionAgentResult,
    *,
    allowed_evidence_refs: set[str],
) -> None:
    referenced = set(output.positive_evidence_refs) | set(output.negative_evidence_refs)
    if not referenced <= allowed_evidence_refs:
        raise ValueError("decision evidence reference is outside the frozen verified bundle")


def score_band(score: int) -> Literal[
    "STRONGLY_ADVERSE", "ADVERSE", "MIXED", "SUPPORTIVE", "STRONGLY_SUPPORTIVE"
]:
    if not 0 <= score <= 100:
        raise ValueError("score must be between 0 and 100")
    if score <= 24:
        return "STRONGLY_ADVERSE"
    if score <= 44:
        return "ADVERSE"
    if score <= 55:
        return "MIXED"
    if score <= 74:
        return "SUPPORTIVE"
    return "STRONGLY_SUPPORTIVE"


class AgentAssessment(AgentContract):
    schema_version: Literal["agent-assessment-v1"] = "agent-assessment-v1"
    stage_run_id: str
    role: Literal[
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
    ]
    symbol: str
    input_refs: list[str]
    status: Literal[
        "SUCCEEDED",
        "INSUFFICIENT_DATA",
        "CONFLICTED",
        "TIMED_OUT",
        "FAILED",
        "INVALID_OUTPUT",
    ]
    stance: Literal["SUPPORTIVE", "NEUTRAL", "CAUTION", "RISK", "UNKNOWN"]
    entry_score: int | None = Field(default=None, ge=0, le=100)
    exit_risk_score: int | None = Field(default=None, ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=1)
    reason_codes: list[str]
    evidence_refs: list[str]
    observed_at: datetime
    valid_until: datetime


class AgentAssessmentV2(AgentContract):
    schema_version: Literal["agent-assessment-v2"] = "agent-assessment-v2"
    score_policy_version: Literal["score-policy-v1"] = "score-policy-v1"
    stage_run_id: str
    role: Literal[
        "TECHNICAL_SCOUT",
        "NEWS_DISCLOSURE_SCOUT",
        "MARKET_SECTOR_SCOUT",
        "POSITION_RISK_SCOUT",
    ]
    symbol: str
    input_refs: list[str]
    status: Literal[
        "SUCCEEDED",
        "NOT_APPLICABLE",
        "INSUFFICIENT_DATA",
        "CONFLICTED",
        "TIMED_OUT",
        "FAILED",
        "INVALID_OUTPUT",
    ]
    stance: Literal["SUPPORTIVE", "NEUTRAL", "CAUTION", "RISK", "UNKNOWN"]
    entry_score: int | None = Field(default=None, ge=0, le=100)
    exit_risk_score: int | None = Field(default=None, ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=1)
    reason_codes: list[str]
    evidence_refs: list[str]
    observed_at: datetime
    valid_until: datetime

    @model_validator(mode="after")
    def validate_score_state(self) -> AgentAssessmentV2:
        if self.status != "SUCCEEDED" and (
            self.entry_score is not None or self.exit_risk_score is not None
        ):
            raise ValueError("non-success assessment scores must be null")
        if self.status == "NOT_APPLICABLE" and self.stance != "UNKNOWN":
            raise ValueError("not-applicable assessment stance must be UNKNOWN")
        return self


class AgentScoutModelOutput(AgentContract):
    """Model-owned fields; runtime provenance is stamped by the server."""

    status: Literal["SUCCEEDED", "INSUFFICIENT_DATA", "CONFLICTED"]
    stance: Literal["SUPPORTIVE", "NEUTRAL", "CAUTION", "RISK", "UNKNOWN"]
    entry_score: int | None = Field(ge=0, le=100)
    exit_risk_score: int | None = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(min_length=1, max_length=20)
    evidence_refs: list[str] = Field(
        max_length=50,
        description=(
            "Internal evidence IDs copied only from allowed_evidence_refs; "
            "never URLs or provider citation strings."
        ),
    )

    @model_validator(mode="after")
    def validate_score_state(self) -> AgentScoutModelOutput:
        if self.status != "SUCCEEDED" and (
            self.entry_score is not None or self.exit_risk_score is not None
        ):
            raise ValueError("non-success assessment scores must be null")
        return self


class AgentCoreModelOutput(AgentContract):
    action: Literal["WAIT"]
    confidence: float = Field(ge=0, le=1)
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    reason_codes: list[str] = Field(min_length=1, max_length=20)
    incomplete_roles: list[str] = Field(max_length=4)


class AgentCoreModelOutputV2(AgentCoreModelOutput):
    shadow_assessment: Literal[
        "ENTRY_STRONG",
        "ENTRY_SUPPORTIVE",
        "NEUTRAL",
        "ENTRY_ADVERSE",
        "HOLD_SUPPORTIVE",
        "EXIT_RISK_ELEVATED",
        "EXIT_RISK_HIGH",
        "UNKNOWN",
    ]


class AgentCoreOutput(AgentContract):
    schema_version: Literal["agent-core-v1"] = "agent-core-v1"
    action: Literal["WAIT"] = "WAIT"
    confidence: float = Field(ge=0, le=1)
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    reason_codes: list[str]
    incomplete_roles: list[str]


class AgentCoreOutputV2(AgentContract):
    schema_version: Literal["agent-core-v2"] = "agent-core-v2"
    score_policy_version: Literal["score-policy-v1"] = "score-policy-v1"
    action: Literal["WAIT"] = "WAIT"
    shadow_assessment: Literal[
        "ENTRY_STRONG",
        "ENTRY_SUPPORTIVE",
        "NEUTRAL",
        "ENTRY_ADVERSE",
        "HOLD_SUPPORTIVE",
        "EXIT_RISK_ELEVATED",
        "EXIT_RISK_HIGH",
        "UNKNOWN",
    ]
    confidence: float = Field(ge=0, le=1)
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    reason_codes: list[str]
    incomplete_roles: list[str]
