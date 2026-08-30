from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SOURCED_ENTRY_DECISION_SCHEMA = "sourced-entry-decision-v1"
SOURCED_ACTIONS = frozenset({"BUY", "WAIT", "REJECT", "UNKNOWN"})
LEGACY_REQUIRED_FIELDS = (
    "model_provider",
    "model_id",
    "prompt_version",
    "scout_output_json",
    "core_output_json",
    "confidence",
    "risk_level",
    "latency_ms",
    "execution_outcome",
)
SOURCE_FIELDS = (
    "source_agent_run_id",
    "source_stage_run_id",
    "source_stage_output_hash",
)


class DecisionRepresentationError(ValueError):
    pass


def _value(subject: object | Mapping[str, Any], name: str) -> Any:
    return subject.get(name) if isinstance(subject, Mapping) else getattr(subject, name, None)


def decision_representation(subject: object | Mapping[str, Any]) -> str:
    source_values = [_value(subject, field) for field in SOURCE_FIELDS]
    if all(value is None for value in source_values):
        return "LEGACY"
    if all(value is not None for value in source_values):
        return "SOURCED_V7"
    raise DecisionRepresentationError("DECISION_SOURCE_LINEAGE_MIXED")


def validate_decision_representation(subject: object | Mapping[str, Any]) -> str:
    representation = decision_representation(subject)
    schema_version = _value(subject, "schema_version")
    legacy_values = [_value(subject, field) for field in LEGACY_REQUIRED_FIELDS]
    if representation == "LEGACY":
        if schema_version == SOURCED_ENTRY_DECISION_SCHEMA or any(
            value is None for value in legacy_values
        ):
            raise DecisionRepresentationError("LEGACY_DECISION_REPRESENTATION_INVALID")
        return representation

    if (
        schema_version != SOURCED_ENTRY_DECISION_SCHEMA
        or _value(subject, "purpose") != "TRADING"
        or _value(subject, "decision_kind") != "ENTRY"
        or _value(subject, "action") not in SOURCED_ACTIONS
        or _value(subject, "validation_status") != "VALID"
        or _value(subject, "decision_input_id") is None
        or any(value is not None for value in legacy_values)
        or _value(subject, "execution_mode") is not None
        or _value(subject, "configuration_version_id") is not None
    ):
        raise DecisionRepresentationError("SOURCED_DECISION_REPRESENTATION_INVALID")
    return representation
