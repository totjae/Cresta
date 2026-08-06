"""Add Agent Worker v2 lease and fencing fields."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0016"
down_revision: str | None = "20260806_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_stage_runs") as batch:
        batch.add_column(sa.Column("lease_owner_id", sa.String(36)))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column(
                "available_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch.add_column(sa.Column("timeout_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint("ck_agent_stage_runs_fencing_token", "fencing_token >= 0")
        batch.create_check_constraint("ck_agent_stage_runs_attempt_count", "attempt_count >= 0")
        batch.create_check_constraint("ck_agent_stage_runs_max_attempts", "max_attempts >= 1")
        batch.create_index(
            "ix_agent_stage_runs_claim",
            [
                "state",
                "available_at",
                "lease_expires_at",
                "created_at",
                "sequence",
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_stage_runs") as batch:
        batch.drop_index("ix_agent_stage_runs_claim")
        batch.drop_constraint("ck_agent_stage_runs_max_attempts", type_="check")
        batch.drop_constraint("ck_agent_stage_runs_attempt_count", type_="check")
        batch.drop_constraint("ck_agent_stage_runs_fencing_token", type_="check")
        batch.drop_column("heartbeat_at")
        batch.drop_column("timeout_at")
        batch.drop_column("available_at")
        batch.drop_column("max_attempts")
        batch.drop_column("attempt_count")
        batch.drop_column("fencing_token")
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner_id")
