"""Separate decision execution and Guard evaluation persistence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0010"
down_revision: str | None = "20260804_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decisions",
        sa.Column("purpose", sa.String(16), nullable=False, server_default="DIAGNOSTIC"),
    )

    op.create_table(
        "decision_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("execution_key", sa.String(128), nullable=False),
        sa.Column("decision_id", sa.String(36), sa.ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("account_alias", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("result_code", sa.String(64)),
        sa.Column("execution_policy_version_id", sa.String(36)),
        sa.Column("risk_policy_version_id", sa.String(36)),
        sa.Column("strategy_config_version_id", sa.String(36)),
        sa.Column("guard_evaluation_id", sa.String(36)),
        sa.Column("approval_id", sa.String(36)),
        sa.Column("order_intent_id", sa.String(36)),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("execution_key", name="uq_decision_executions_key"),
        sa.CheckConstraint(
            "mode IN ('AUTOMATIC','MANUAL_APPROVAL','DISABLED')",
            name="ck_decision_executions_mode",
        ),
        sa.CheckConstraint(
            "stage IN ('SHADOW','APPROVAL_ONLY','MOCK_AUTOMATIC')",
            name="ck_decision_executions_stage",
        ),
        sa.CheckConstraint(
            "state IN ('ROUTING','NO_ACTION','DISABLED','GUARD_BLOCKED','SHADOW_RECORDED',"
            "'APPROVAL_PENDING','EXPIRED','REJECTED','INVALIDATED','ORDER_CREATED','FAILED_SAFE')",
            name="ck_decision_executions_state",
        ),
    )
    op.create_index(
        "ix_decision_executions_decision", "decision_executions", ["decision_id", "created_at"]
    )

    op.create_table(
        "guard_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("decision_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phase", sa.String(24), nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("rule_results_json", sa.Text(), nullable=False),
        sa.Column("halt_scope", sa.String(24)),
        sa.Column("snapshot_id", sa.String(36)),
        sa.Column("position_version", sa.Integer()),
        sa.Column("execution_policy_version_id", sa.String(36)),
        sa.Column("risk_policy_version_id", sa.String(36)),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.CheckConstraint("result IN ('PASSED','BLOCKED')", name="ck_guard_evaluations_result"),
    )
    op.create_index(
        "ix_guard_evaluations_execution", "guard_evaluations", ["execution_id", "evaluated_at"]
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("decision_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision_id", sa.String(36), sa.ForeignKey("decisions.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("scope_snapshot_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(36)),
        sa.Column("reauth_proof_id", sa.String(36)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("execution_id", name="uq_approvals_execution"),
        sa.CheckConstraint(
            "state IN ('PENDING','APPROVED','REJECTED','EXPIRED','INVALIDATED')",
            name="ck_approvals_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("approvals")
    op.drop_index("ix_guard_evaluations_execution", table_name="guard_evaluations")
    op.drop_table("guard_evaluations")
    op.drop_index("ix_decision_executions_decision", table_name="decision_executions")
    op.drop_table("decision_executions")
    op.drop_column("decisions", "purpose")
