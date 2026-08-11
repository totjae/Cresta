"""Add Agent SHADOW context and v2 assessment state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0026"
down_revision: str | None = "20260811_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATES = (
    "'PENDING','RUNNING','SUCCEEDED','INSUFFICIENT_DATA','CONFLICTED',"
    "'TIMED_OUT','FAILED','INVALID_OUTPUT'"
)
NEW_STATES = (
    "'PENDING','RUNNING','SUCCEEDED','NOT_APPLICABLE','INSUFFICIENT_DATA','CONFLICTED',"
    "'TIMED_OUT','FAILED','INVALID_OUTPUT'"
)


def _replace_stage_state_constraint(states: str) -> None:
    with op.batch_alter_table("agent_stage_runs") as batch:
        batch.drop_constraint("ck_agent_stage_runs_state", type_="check")
        batch.create_check_constraint(
            "ck_agent_stage_runs_state", f"state IN ({states})"
        )


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("analysis_context", sa.String(16), nullable=True))
        batch.add_column(sa.Column("position_snapshot_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("position_snapshot_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("shadow_assessment", sa.String(32), nullable=True))
        batch.create_check_constraint(
            "ck_agent_runs_analysis_context",
            "analysis_context IS NULL OR analysis_context IN ('ENTRY','POSITION')",
        )
        batch.create_check_constraint(
            "ck_agent_runs_shadow_assessment",
            "shadow_assessment IS NULL OR shadow_assessment IN ("
            "'ENTRY_STRONG','ENTRY_SUPPORTIVE','NEUTRAL','ENTRY_ADVERSE',"
            "'HOLD_SUPPORTIVE','EXIT_RISK_ELEVATED','EXIT_RISK_HIGH','UNKNOWN')",
        )
    _replace_stage_state_constraint(NEW_STATES)


def downgrade() -> None:
    op.execute(
        "UPDATE agent_stage_runs SET state = 'INSUFFICIENT_DATA', "
        "error_code = COALESCE(error_code, 'DOWNGRADE_NOT_APPLICABLE') "
        "WHERE state = 'NOT_APPLICABLE'"
    )
    _replace_stage_state_constraint(OLD_STATES)
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("ck_agent_runs_shadow_assessment", type_="check")
        batch.drop_constraint("ck_agent_runs_analysis_context", type_="check")
        batch.drop_column("shadow_assessment")
        batch.drop_column("position_snapshot_hash")
        batch.drop_column("position_snapshot_json")
        batch.drop_column("analysis_context")
