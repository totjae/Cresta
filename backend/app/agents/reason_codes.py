from __future__ import annotations

from copy import deepcopy

from app.agents.contracts import (
    AgentCoreModelOutput,
    AgentCoreModelOutputV2,
    AgentScoutModelOutput,
)

REASON_CODE_POLICY_VERSION = "reason-code-policy-v1"

COMMON_SCOUT_REASON_CODES = (
    "DATA_SUFFICIENT",
    "INPUT_DATA_MISSING",
    "INPUT_DATA_STALE",
    "INPUT_DATA_CONFLICTED",
    "NO_VERIFIED_EVIDENCE",
    "VERIFIED_EVIDENCE_AVAILABLE",
)

ROLE_REASON_CODES: dict[str, tuple[str, ...]] = {
    "TECHNICAL_SCOUT": COMMON_SCOUT_REASON_CODES
    + (
        "PRICE_ABOVE_VWAP",
        "PRICE_AT_VWAP",
        "PRICE_BELOW_VWAP",
        "SMA5_RISING",
        "SMA5_FLAT",
        "SMA5_FALLING",
        "RELATIVE_VOLUME_HIGH",
        "RELATIVE_VOLUME_NORMAL",
        "RELATIVE_VOLUME_LOW",
        "VOLATILITY_ELEVATED",
        "VOLATILITY_NORMAL",
        "DRAWDOWN_FROM_RECENT_HIGH",
        "SPREAD_ACCEPTABLE",
        "SPREAD_WIDE",
        "MARKET_DATA_QUALITY_DEGRADED",
        "INDICATOR_DATA_MISSING",
        "TECHNICAL_SIGNALS_MIXED",
        "MOMENTUM_SUPPORTIVE",
        "MOMENTUM_WEAKENING",
    ),
    "NEWS_DISCLOSURE_SCOUT": COMMON_SCOUT_REASON_CODES
    + (
        "MATERIAL_POSITIVE_DISCLOSURE",
        "MATERIAL_NEGATIVE_DISCLOSURE",
        "MATERIAL_NEUTRAL_DISCLOSURE",
        "RECENT_POSITIVE_NEWS",
        "RECENT_NEGATIVE_NEWS",
        "NEWS_IMPACT_NEUTRAL",
        "DISCLOSURE_AND_NEWS_CONFLICT",
        "EVIDENCE_STALE",
        "EVIDENCE_NOT_SYMBOL_RELEVANT",
        "NEWS_DATA_INSUFFICIENT",
    ),
    "MARKET_SECTOR_SCOUT": COMMON_SCOUT_REASON_CODES
    + (
        "MARKET_TREND_SUPPORTIVE",
        "MARKET_TREND_NEUTRAL",
        "MARKET_TREND_WEAK",
        "SECTOR_MOMENTUM_SUPPORTIVE",
        "SECTOR_MOMENTUM_NEUTRAL",
        "SECTOR_MOMENTUM_WEAK",
        "MARKET_BREADTH_POSITIVE",
        "MARKET_BREADTH_NEUTRAL",
        "MARKET_BREADTH_NEGATIVE",
        "MARKET_RISK_OFF",
        "MARKET_VOLATILITY_ELEVATED",
        "MARKET_SECTOR_SIGNALS_MIXED",
        "MARKET_DATA_INSUFFICIENT",
        "MARKET_DATA_QUALITY_DEGRADED",
    ),
    "POSITION_RISK_SCOUT": COMMON_SCOUT_REASON_CODES
    + (
        "OPEN_POSITION_NOT_FOUND",
        "POSITION_DATA_STALE",
        "POSITION_DATA_CONFLICTED",
        "POSITION_PROFITABLE",
        "POSITION_LOSING",
        "DRAWDOWN_LOW",
        "DRAWDOWN_MODERATE",
        "DRAWDOWN_HIGH",
        "FIXED_STOP_NEAR",
        "FIXED_STOP_TRIGGERED",
        "TRAILING_STOP_NEAR",
        "TRAILING_STOP_TRIGGERED",
        "BREAK_EVEN_STOP_ACTIVE",
        "TIME_STOP_NEAR",
        "TIME_STOP_TRIGGERED",
        "LIQUIDITY_EXIT_RISK",
        "POSITION_RISK_NORMAL",
        "POSITION_RISK_ELEVATED",
        "POSITION_RISK_CRITICAL",
    ),
    "CORE": (
        "AGENT_RUNTIME_SHADOW_ONLY",
        "DIAGNOSTIC_WAIT_ONLY",
        "REQUIRED_SCOUT_INCOMPLETE",
        "SCOUT_SIGNALS_SUPPORTIVE",
        "SCOUT_SIGNALS_NEUTRAL",
        "SCOUT_SIGNALS_CAUTION",
        "SCOUT_SIGNALS_CONFLICTED",
        "NO_VERIFIED_EVIDENCE",
        "MATERIAL_EVENT_RISK",
        "MARKET_RISK_ELEVATED",
        "POSITION_RISK_ELEVATED",
        "POSITION_RISK_CRITICAL",
        "DATA_QUALITY_INSUFFICIENT",
        "HIGH_UNCERTAINTY",
        "ENTRY_CONDITIONS_INCOMPLETE",
        "RISK_REWARD_UNFAVORABLE",
    ),
}


def allowed_reason_codes(role: str) -> tuple[str, ...]:
    try:
        return ROLE_REASON_CODES[role]
    except KeyError as exc:
        raise ValueError(f"Unsupported agent role: {role}") from exc


def reason_code_context(role: str) -> dict[str, object]:
    return {
        "reason_code_policy_version": REASON_CODE_POLICY_VERSION,
        "allowed_reason_codes": list(allowed_reason_codes(role)),
    }


def output_schema_for_role(
    role: str, *, core_schema_version: str = "agent-core-v1"
) -> dict[str, object]:
    base_schema = (
        (
            AgentCoreModelOutputV2.model_json_schema()
            if core_schema_version == "agent-core-v2"
            else AgentCoreModelOutput.model_json_schema()
        )
        if role == "CORE"
        else AgentScoutModelOutput.model_json_schema()
    )
    schema = deepcopy(base_schema)
    reason_codes = schema["properties"]["reason_codes"]
    reason_codes["items"] = {
        "type": "string",
        "enum": list(allowed_reason_codes(role)),
    }
    return schema


def invalid_reason_codes(role: str, values: list[str]) -> list[str]:
    allowed = set(allowed_reason_codes(role))
    return [value for value in values if value not in allowed]
