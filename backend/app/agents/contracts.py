from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
