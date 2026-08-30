"""Add sourced v7 Decision representation foundation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0040"
down_revision: str | None = "20260825_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_ACTIONS = (
    "'BUY','WAIT','REJECT','RISK_BLOCK','HOLD','TIGHTEN_STOP',"
    "'PARTIAL_SELL','FULL_SELL','EMERGENCY_EXIT'"
)
ALL_ACTIONS = LEGACY_ACTIONS + ",'UNKNOWN'"
LEGACY_FIELDS = (
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


def _representation_check() -> str:
    legacy_non_null = " AND ".join(f"{column} IS NOT NULL" for column in LEGACY_FIELDS)
    sourced_null = " AND ".join(f"{column} IS NULL" for column in LEGACY_FIELDS)
    return (
        "((source_agent_run_id IS NULL AND source_stage_run_id IS NULL "
        "AND source_stage_output_hash IS NULL "
        "AND schema_version <> 'sourced-entry-decision-v1' "
        f"AND {legacy_non_null}) OR "
        "(source_agent_run_id IS NOT NULL AND source_stage_run_id IS NOT NULL "
        "AND source_stage_output_hash IS NOT NULL "
        "AND schema_version = 'sourced-entry-decision-v1' "
        "AND purpose = 'TRADING' AND decision_kind = 'ENTRY' "
        "AND action IN ('BUY','WAIT','REJECT','UNKNOWN') "
        "AND validation_status = 'VALID' "
        f"AND {sourced_null} "
        "AND execution_mode IS NULL AND configuration_version_id IS NULL))"
    )


def upgrade() -> None:
    with op.batch_alter_table("decisions") as batch:
        batch.drop_constraint("ck_decisions_action", type_="check")
        batch.drop_constraint("ck_decisions_execution_outcome", type_="check")
        batch.alter_column(
            "schema_version",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
        for column in LEGACY_FIELDS:
            existing_type: sa.TypeEngine[object]
            if column in {"scout_output_json", "core_output_json"}:
                existing_type = sa.Text()
            elif column == "confidence":
                existing_type = sa.Numeric(6, 5)
            elif column == "latency_ms":
                existing_type = sa.Integer()
            elif column == "model_provider":
                existing_type = sa.String(length=32)
            elif column == "model_id":
                existing_type = sa.String(length=64)
            elif column == "prompt_version":
                existing_type = sa.String(length=32)
            elif column == "risk_level":
                existing_type = sa.String(length=16)
            else:
                existing_type = sa.String(length=32)
            batch.alter_column(column, existing_type=existing_type, nullable=True)
        batch.create_check_constraint(
            "ck_decisions_action", f"action IN ({ALL_ACTIONS})"
        )
        batch.create_check_constraint(
            "ck_decisions_execution_outcome",
            "execution_outcome IS NULL OR execution_outcome IN "
            "('NO_ACTION','DISABLED','APPROVAL_REQUIRED','GUARD_BLOCKED')",
        )
        batch.create_check_constraint(
            "ck_decisions_representation", _representation_check()
        )


def _guard_destructive_downgrade() -> None:
    bind = op.get_bind()
    nullable_use = " OR ".join(f"{column} IS NULL" for column in LEGACY_FIELDS)
    count = bind.scalar(
        sa.text(
            "SELECT COUNT(*) FROM decisions WHERE action = 'UNKNOWN' "
            "OR source_agent_run_id IS NOT NULL OR source_stage_run_id IS NOT NULL "
            f"OR source_stage_output_hash IS NOT NULL OR {nullable_use}"
        )
    )
    if count:
        raise RuntimeError(
            "Refusing downgrade of 20260827_0040 because sourced/UNKNOWN/nullable "
            "Decision semantics cannot be represented by the legacy schema"
        )


def downgrade() -> None:
    _guard_destructive_downgrade()
    with op.batch_alter_table("decisions") as batch:
        batch.drop_constraint("ck_decisions_representation", type_="check")
        batch.drop_constraint("ck_decisions_execution_outcome", type_="check")
        batch.drop_constraint("ck_decisions_action", type_="check")
        for column in LEGACY_FIELDS:
            existing_type: sa.TypeEngine[object]
            if column in {"scout_output_json", "core_output_json"}:
                existing_type = sa.Text()
            elif column == "confidence":
                existing_type = sa.Numeric(6, 5)
            elif column == "latency_ms":
                existing_type = sa.Integer()
            elif column == "model_provider":
                existing_type = sa.String(length=32)
            elif column == "model_id":
                existing_type = sa.String(length=64)
            elif column == "prompt_version":
                existing_type = sa.String(length=32)
            elif column == "risk_level":
                existing_type = sa.String(length=16)
            else:
                existing_type = sa.String(length=32)
            batch.alter_column(column, existing_type=existing_type, nullable=False)
        batch.alter_column(
            "schema_version",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            "ck_decisions_action", f"action IN ({LEGACY_ACTIONS})"
        )
        batch.create_check_constraint(
            "ck_decisions_execution_outcome",
            "execution_outcome IN "
            "('NO_ACTION','DISABLED','APPROVAL_REQUIRED','GUARD_BLOCKED')",
        )
