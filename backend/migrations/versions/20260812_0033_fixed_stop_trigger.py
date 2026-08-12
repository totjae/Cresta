"""Add risk_events and stop_triggers for fixed stop-loss trigger."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0033"
down_revision: str | None = "20260812_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("rule_code", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("account_alias", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(16)),
        sa.Column("input_snapshot_id", sa.String(36)),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("resolution", sa.String(64)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('ACTIVE','RESOLVED')", name="ck_risk_events_state"),
        sa.CheckConstraint(
            "severity IN ('INFO','WARNING','HIGH','CRITICAL')",
            name="ck_risk_events_severity",
        ),
        sa.ForeignKeyConstraint(["input_snapshot_id"], ["market_snapshots.id"]),
    )
    op.create_index(
        "ix_risk_events_scope_state", "risk_events", ["scope", "state"]
    )
    op.create_index(
        "ix_risk_events_account_created", "risk_events", ["account_alias", "created_at"]
    )

    op.create_table(
        "stop_triggers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_alias", sa.String(64), nullable=False),
        sa.Column("position_id", sa.String(36), nullable=False),
        sa.Column("position_version", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("risk_policy_version_id", sa.String(36)),
        sa.Column("stop_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("trigger_price", sa.Numeric(18, 4)),
        sa.Column("snapshot_id", sa.String(36)),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("result_code", sa.String(64)),
        sa.Column("guard_evaluation_id", sa.String(36)),
        sa.Column("risk_event_id", sa.String(36)),
        sa.Column("halt_scope", sa.String(24)),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('PENDING','SHADOW_RECORDED','EXIT_PENDING','SUPERSEDED','FULFILLED')",
            name="ck_stop_triggers_state",
        ),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"]),
        sa.ForeignKeyConstraint(["risk_event_id"], ["risk_events.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["market_snapshots.id"]),
        sa.UniqueConstraint(
            "position_id",
            "position_version",
            "risk_policy_version_id",
            name="uq_stop_triggers_position_version_policy",
        ),
    )
    op.create_index(
        "ix_stop_triggers_account_state", "stop_triggers", ["account_alias", "state"]
    )
    op.create_index("ix_stop_triggers_risk_event_id", "stop_triggers", ["risk_event_id"])


def downgrade() -> None:
    op.drop_index("ix_stop_triggers_risk_event_id", table_name="stop_triggers")
    op.drop_index("ix_stop_triggers_account_state", table_name="stop_triggers")
    op.drop_table("stop_triggers")
    op.drop_index("ix_risk_events_account_created", table_name="risk_events")
    op.drop_index("ix_risk_events_scope_state", table_name="risk_events")
    op.drop_table("risk_events")
