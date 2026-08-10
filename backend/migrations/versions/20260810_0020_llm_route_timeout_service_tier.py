"""Add role-level response timeout and inference service tier."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0020"
down_revision: str | None = "20260808_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.drop_constraint("ck_llm_role_routes_timeout", type_="check")
        batch.add_column(
            sa.Column("service_tier", sa.String(16), nullable=False, server_default="DEFAULT")
        )
        batch.create_check_constraint(
            "ck_llm_role_routes_timeout", "timeout_ms BETWEEN 1000 AND 600000"
        )
        batch.create_check_constraint(
            "ck_llm_role_routes_service_tier",
            "service_tier IN ('DEFAULT','PRIORITY','FLEX')",
        )
        batch.alter_column(
            "timeout_ms",
            existing_type=sa.Integer(),
            server_default="30000",
        )


def downgrade() -> None:
    op.execute("UPDATE llm_role_routes SET timeout_ms = 60000 WHERE timeout_ms > 60000")
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.drop_constraint("ck_llm_role_routes_service_tier", type_="check")
        batch.drop_constraint("ck_llm_role_routes_timeout", type_="check")
        batch.create_check_constraint(
            "ck_llm_role_routes_timeout", "timeout_ms BETWEEN 1000 AND 60000"
        )
        batch.alter_column(
            "timeout_ms",
            existing_type=sa.Integer(),
            server_default="10000",
        )
        batch.drop_column("service_tier")
