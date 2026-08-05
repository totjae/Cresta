from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class AgentCoreOutput(AgentContract):
    schema_version: Literal["agent-core-v1"] = "agent-core-v1"
    action: Literal["WAIT"] = "WAIT"
    confidence: float = Field(ge=0, le=1)
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    reason_codes: list[str]
    incomplete_roles: list[str]
