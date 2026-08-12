"""Shared Guard helpers for order creation paths.

The BUY hard guard lives in ``app.decision_execution._buy_guard_rules`` and the
FIXED_STOP SELL guard lives in ``app.stop_trigger._sell_guard_rules``. This
module provides cross-cutting helpers they share: persisting a
``GuardEvaluation`` row in a consistent shape, and the position-origin check
that gates automatic SELL to Cresta-managed positions only.

Full Risk Guard (daily loss, spread, connection risk, total exposure) is a
later milestone; this milestone ships only the minimal hard guard plus the
existing sell-guard rules already implemented in ``stop_trigger``.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import GuardEvaluation, Position

CRESTA_MANAGED = "CRESTA_MANAGED"
EXTERNAL = "EXTERNAL"


def rule(code: str, passed: bool) -> dict[str, object]:
    return {"code": code, "result": "PASSED" if passed else "BLOCKED"}


def blocking_code(rules: list[dict[str, object]]) -> str:
    """Return the first blocking rule code, or ``BLOCKED`` as a fallback."""
    for item in rules:
        if item["result"] == "BLOCKED":
            return str(item["code"])
    return "BLOCKED"


def is_cresta_managed(position: Position | None) -> bool:
    """An automatic SELL target must be a Cresta-managed position.

    External positions (entered outside Cresta, detected by reconciliation in
    a later step) are never auto-sold by triggers.
    """
    return position is not None and getattr(position, "origin", CRESTA_MANAGED) == CRESTA_MANAGED


def persist_guard_evaluation(
    db: Session,
    *,
    execution_id: str,
    subject_type: str,
    subject_id: str,
    rules: list[dict[str, object]],
    snapshot_id: str | None,
    position_version: int | None,
    execution_policy_version_id: str | None,
    risk_policy_version_id: str | None,
    halt_scope: str | None,
    valid_until: datetime | None,
    now: datetime,
    phase: str = "PRE_ORDER",
) -> GuardEvaluation:
    blocked = [item for item in rules if item["result"] == "BLOCKED"]
    guard = GuardEvaluation(
        execution_id=execution_id,
        phase=phase,
        subject_type=subject_type,
        subject_id=subject_id,
        result="BLOCKED" if blocked else "PASSED",
        rule_results_json=json.dumps(rules, separators=(",", ":"), sort_keys=True),
        halt_scope=halt_scope if blocked else None,
        snapshot_id=snapshot_id,
        position_version=position_version,
        execution_policy_version_id=execution_policy_version_id,
        risk_policy_version_id=risk_policy_version_id,
        evaluated_at=now,
        valid_until=valid_until,
    )
    db.add(guard)
    db.flush()
    return guard
