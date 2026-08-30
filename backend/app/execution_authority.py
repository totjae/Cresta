from __future__ import annotations

import hashlib
import json
from enum import StrEnum

ENTRY_EXECUTION_IDENTITY_SCHEMA = "entry-execution-identity-v1"
SOURCED_EXECUTION_CONTRACT = "sourced-entry-execution-v1"
ORDER_AUTHORITY_KEY_SCHEMA = "order-authority-key-v1"


class ExecutionStage(StrEnum):
    SHADOW = "SHADOW"
    APPROVAL_ONLY = "APPROVAL_ONLY"
    MOCK_AUTOMATIC = "MOCK_AUTOMATIC"


class ActionMode(StrEnum):
    DISABLED = "DISABLED"
    MANUAL_APPROVAL = "MANUAL_APPROVAL"
    AUTOMATIC = "AUTOMATIC"


_STAGE_RANK = {
    ExecutionStage.SHADOW: 0,
    ExecutionStage.APPROVAL_ONLY: 1,
    ExecutionStage.MOCK_AUTOMATIC: 2,
}
_MODE_RANK = {
    ActionMode.DISABLED: 0,
    ActionMode.MANUAL_APPROVAL: 1,
    ActionMode.AUTOMATIC: 2,
}


def sourced_execution_key(decision_id: str) -> str:
    """Build the policy-independent sourced Decision execution identity."""
    if not isinstance(decision_id, str) or not decision_id:
        raise ValueError("decision_id must be a non-empty string")
    material = json.dumps(
        {"schema_version": ENTRY_EXECUTION_IDENTITY_SCHEMA, "decision_id": decision_id},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"v7exe-{hashlib.sha256(material).hexdigest()}"


def order_authority_key(
    *, source_type: str, source_id: str, approval_id: str | None
) -> str:
    """Build the stable identity of one initial source order authority."""
    if source_type not in {"DECISION_EXECUTION", "STOP_TRIGGER"}:
        raise ValueError("source_type has no order-authority-key-v1 contract")
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("source_id must be a non-empty persisted ID string")
    if approval_id is not None and (not isinstance(approval_id, str) or not approval_id):
        raise ValueError("approval_id must be a persisted ID string or null")
    material = json.dumps(
        {
            "schema_version": ORDER_AUTHORITY_KEY_SCHEMA,
            "source_type": source_type,
            "source_id": source_id,
            "approval_id": approval_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"ordauth-{hashlib.sha256(material).hexdigest()}"


def validate_sourced_execution_representation(decision: object, execution: object) -> None:
    """Reject mixed legacy/sourced rows before Phase 10C.2 persistence."""
    if getattr(execution, "contract_version", None) != SOURCED_EXECUTION_CONTRACT:
        raise ValueError("execution is not a sourced-entry-execution-v1 lifecycle")
    if (
        getattr(decision, "schema_version", None) != "sourced-entry-decision-v1"
        or getattr(decision, "purpose", None) != "TRADING"
        or getattr(decision, "decision_kind", None) != "ENTRY"
        or getattr(decision, "validation_status", None) != "VALID"
        or getattr(decision, "id", None) != getattr(execution, "decision_id", None)
        or getattr(execution, "execution_key", None)
        != sourced_execution_key(str(getattr(decision, "id", "")))
    ):
        raise ValueError("sourced DecisionExecution representation is inconsistent")


def effective_execution_stage(
    frozen: ExecutionStage | str, current: ExecutionStage | str
) -> ExecutionStage:
    frozen_stage = ExecutionStage(frozen)
    current_stage = ExecutionStage(current)
    return min((frozen_stage, current_stage), key=_STAGE_RANK.__getitem__)


def effective_action_mode(frozen: ActionMode | str, current: ActionMode | str) -> ActionMode:
    frozen_mode = ActionMode(frozen)
    current_mode = ActionMode(current)
    return min((frozen_mode, current_mode), key=_MODE_RANK.__getitem__)
