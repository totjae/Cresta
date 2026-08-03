"""Create persistent mock AI decisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0007"
down_revision: str | None = "20260804_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("evaluation_request_id", sa.String(64), nullable=False, unique=True),
        sa.Column("input_snapshot_id", sa.String(36), sa.ForeignKey("market_snapshots.id"), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("decision_kind", sa.String(16), nullable=False),
        sa.Column("model_provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("scout_output_json", sa.Text(), nullable=False),
        sa.Column("core_output_json", sa.Text(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("configuration_version_id", sa.String(36)),
        sa.Column("execution_mode", sa.String(24)),
        sa.Column("execution_outcome", sa.String(32), nullable=False),
        sa.Column("validation_status", sa.String(16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('BUY','WAIT','REJECT','RISK_BLOCK','HOLD','TIGHTEN_STOP',"
            "'PARTIAL_SELL','FULL_SELL','EMERGENCY_EXIT')",
            name="ck_decisions_action",
        ),
        sa.CheckConstraint(
            "execution_mode IS NULL OR execution_mode IN "
            "('AUTOMATIC','MANUAL_APPROVAL','DISABLED')",
            name="ck_decisions_execution_mode",
        ),
        sa.CheckConstraint(
            "execution_outcome IN ('NO_ACTION','DISABLED','APPROVAL_REQUIRED','GUARD_BLOCKED')",
            name="ck_decisions_execution_outcome",
        ),
    )
    op.create_index("ix_decisions_input_snapshot_id", "decisions", ["input_snapshot_id"])
    op.create_index("ix_decisions_symbol_created", "decisions", ["symbol", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_decisions_symbol_created", table_name="decisions")
    op.drop_index("ix_decisions_input_snapshot_id", table_name="decisions")
    op.drop_table("decisions")
