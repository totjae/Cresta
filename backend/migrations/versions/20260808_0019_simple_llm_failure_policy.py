"""Allow one explicit fallback model per LLM role route."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0019"
down_revision: str | None = "20260808_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.drop_constraint("ck_llm_role_routes_foundation_fallback", type_="check")
    op.execute(
        "UPDATE llm_role_routes SET fallback_policy = 'FAIL_STOP' "
        "WHERE fallback_policy = 'NONE'"
    )
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.create_check_constraint(
            "ck_llm_role_routes_foundation_fallback",
            "fallback_policy IN ('FAIL_STOP','FAILOVER')",
        )
        batch.alter_column(
            "fallback_policy",
            existing_type=sa.String(32),
            server_default="FAIL_STOP",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE llm_role_routes SET fallback_policy = 'FAIL_STOP', "
        "fallback_model_profile_ids_json = '[]' WHERE fallback_policy = 'FAILOVER'"
    )
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.drop_constraint("ck_llm_role_routes_foundation_fallback", type_="check")
    op.execute(
        "UPDATE llm_role_routes SET fallback_policy = 'NONE' "
        "WHERE fallback_policy = 'FAIL_STOP'"
    )
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.create_check_constraint(
            "ck_llm_role_routes_foundation_fallback",
            "fallback_policy = 'NONE'",
        )
        batch.alter_column(
            "fallback_policy",
            existing_type=sa.String(32),
            server_default="NONE",
        )
