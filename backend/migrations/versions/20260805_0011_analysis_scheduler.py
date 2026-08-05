"""Add periodic analysis scheduler lease and status."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0011"
down_revision: str | None = "20260805_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_scheduler_leases",
        sa.Column("scheduler_name", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fencing_token > 0", name="ck_analysis_scheduler_leases_fencing"),
        sa.CheckConstraint("version > 0", name="ck_analysis_scheduler_leases_version"),
    )
    op.create_table(
        "analysis_scheduler_states",
        sa.Column("scheduler_name", sa.String(64), primary_key=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("last_slot_key", sa.String(64)),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_tick_at", sa.DateTime(timezone=True)),
        sa.Column("last_completed_at", sa.DateTime(timezone=True)),
        sa.Column("next_due_at", sa.DateTime(timezone=True)),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("decision_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('STARTING','RUNNING','IDLE','DEGRADED','STOPPED')",
            name="ck_analysis_scheduler_states_state",
        ),
        sa.CheckConstraint("fencing_token > 0", name="ck_analysis_scheduler_states_fencing"),
        sa.CheckConstraint(
            "processed_count >= 0 AND decision_count >= 0 AND skipped_count >= 0 "
            "AND failed_count >= 0",
            name="ck_analysis_scheduler_states_counts",
        ),
    )


def downgrade() -> None:
    op.drop_table("analysis_scheduler_states")
    op.drop_table("analysis_scheduler_leases")
