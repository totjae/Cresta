"""Add scheduler-owned POSITION agent advisory provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0038"
down_revision: str | None = "20260814_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("ck_agent_runs_foundation_purpose", type_="check")
        batch.add_column(sa.Column("basis_decision_id", sa.String(length=36)))
        batch.add_column(sa.Column("fusion_policy_version", sa.String(length=32)))
        batch.add_column(sa.Column("fusion_state", sa.String(length=24)))
        batch.add_column(sa.Column("fusion_reason_code", sa.String(length=64)))
        batch.add_column(sa.Column("fusion_decision_id", sa.String(length=36)))
        batch.create_foreign_key(
            "fk_agent_runs_basis_decision", "decisions", ["basis_decision_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_agent_runs_fusion_decision", "decisions", ["fusion_decision_id"], ["id"]
        )
        batch.create_unique_constraint(
            "uq_agent_runs_basis_decision", ["basis_decision_id"]
        )
        batch.create_unique_constraint(
            "uq_agent_runs_fusion_decision", ["fusion_decision_id"]
        )
        batch.create_index("ix_agent_runs_basis_decision_id", ["basis_decision_id"])
        batch.create_index("ix_agent_runs_fusion_decision_id", ["fusion_decision_id"])
        batch.create_check_constraint(
            "ck_agent_runs_foundation_purpose",
            "purpose IN ('DIAGNOSTIC','TRADING_ADVISORY')",
        )
        batch.create_check_constraint(
            "ck_agent_runs_fusion_state",
            "fusion_state IS NULL OR fusion_state IN ("
            "'PENDING','NO_ESCALATION','ESCALATED','EXPIRED','FAILED_SAFE')",
        )
        batch.create_check_constraint(
            "ck_agent_runs_advisory_context",
            "(purpose = 'DIAGNOSTIC' AND basis_decision_id IS NULL "
            "AND fusion_policy_version IS NULL AND fusion_state IS NULL) OR "
            "(purpose = 'TRADING_ADVISORY' AND basis_decision_id IS NOT NULL "
            "AND fusion_policy_version IS NOT NULL AND fusion_state IS NOT NULL)",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE agent_runs SET purpose = 'DIAGNOSTIC' "
        "WHERE purpose = 'TRADING_ADVISORY'"
    )
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("ck_agent_runs_advisory_context", type_="check")
        batch.drop_constraint("ck_agent_runs_fusion_state", type_="check")
        batch.drop_constraint("ck_agent_runs_foundation_purpose", type_="check")
        batch.create_check_constraint(
            "ck_agent_runs_foundation_purpose", "purpose = 'DIAGNOSTIC'"
        )
        batch.drop_index("ix_agent_runs_fusion_decision_id")
        batch.drop_index("ix_agent_runs_basis_decision_id")
        batch.drop_constraint("uq_agent_runs_fusion_decision", type_="unique")
        batch.drop_constraint("uq_agent_runs_basis_decision", type_="unique")
        batch.drop_constraint("fk_agent_runs_fusion_decision", type_="foreignkey")
        batch.drop_constraint("fk_agent_runs_basis_decision", type_="foreignkey")
        batch.drop_column("fusion_decision_id")
        batch.drop_column("fusion_reason_code")
        batch.drop_column("fusion_state")
        batch.drop_column("fusion_policy_version")
        batch.drop_column("basis_decision_id")
