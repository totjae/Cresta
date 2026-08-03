"""Create Kiwoom reconciliation run and mismatch tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0004"
down_revision: str | None = "20260801_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_alias", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("snapshot_at", sa.DateTime(timezone=True)),
        sa.Column("mismatch_count", sa.Integer(), nullable=False),
        sa.Column("critical_mismatch_count", sa.Integer(), nullable=False),
        sa.Column("broker_request_ids_json", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("result_summary_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "state IN ('RUNNING','SUCCEEDED','MISMATCH','FAILED')",
            name="ck_reconciliation_runs_state",
        ),
        sa.CheckConstraint("mismatch_count >= 0", name="ck_reconciliation_runs_mismatch_count"),
        sa.CheckConstraint(
            "critical_mismatch_count >= 0",
            name="ck_reconciliation_runs_critical_count",
        ),
    )
    op.create_index(
        "ix_reconciliation_runs_account_started",
        "reconciliation_runs",
        ["account_alias", "started_at"],
    )
    op.create_table(
        "reconciliation_mismatches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("reconciliation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(16)),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("broker_value_json", sa.Text(), nullable=False),
        sa.Column("internal_value_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "severity IN ('WARNING','CRITICAL')",
            name="ck_reconciliation_mismatches_severity",
        ),
        sa.CheckConstraint(
            "state IN ('OPEN','RESOLVED')",
            name="ck_reconciliation_mismatches_state",
        ),
    )
    op.create_index("ix_reconciliation_mismatches_run", "reconciliation_mismatches", ["run_id"])


def downgrade() -> None:
    op.drop_table("reconciliation_mismatches")
    op.drop_table("reconciliation_runs")
