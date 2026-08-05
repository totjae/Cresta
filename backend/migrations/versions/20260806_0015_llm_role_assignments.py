"""Add reusable model generation defaults and role assignment overrides."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0015"
down_revision: str | None = "20260806_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_model_profiles") as batch:
        batch.add_column(sa.Column("top_p", sa.Numeric(4, 3)))
        batch.add_column(sa.Column("reasoning_effort", sa.String(12)))
        batch.add_column(sa.Column("seed", sa.Integer()))
        batch.create_check_constraint(
            "ck_llm_model_profiles_top_p",
            "top_p IS NULL OR (top_p >= 0 AND top_p <= 1)",
        )
        batch.create_check_constraint(
            "ck_llm_model_profiles_reasoning_effort",
            "reasoning_effort IS NULL OR reasoning_effort IN ('LOW','MEDIUM','HIGH')",
        )

    with op.batch_alter_table("llm_role_routes") as batch:
        batch.add_column(sa.Column("temperature_override", sa.Numeric(4, 3)))
        batch.add_column(sa.Column("top_p_override", sa.Numeric(4, 3)))
        batch.add_column(sa.Column("max_output_tokens_override", sa.Integer()))
        batch.add_column(sa.Column("reasoning_effort_override", sa.String(12)))
        batch.add_column(sa.Column("seed_override", sa.Integer()))
        batch.create_check_constraint(
            "ck_llm_role_routes_temperature_override",
            "temperature_override IS NULL OR "
            "(temperature_override >= 0 AND temperature_override <= 2)",
        )
        batch.create_check_constraint(
            "ck_llm_role_routes_top_p_override",
            "top_p_override IS NULL OR (top_p_override >= 0 AND top_p_override <= 1)",
        )
        batch.create_check_constraint(
            "ck_llm_role_routes_max_output_override",
            "max_output_tokens_override IS NULL OR max_output_tokens_override > 0",
        )
        batch.create_check_constraint(
            "ck_llm_role_routes_reasoning_effort_override",
            "reasoning_effort_override IS NULL OR "
            "reasoning_effort_override IN ('LOW','MEDIUM','HIGH')",
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.drop_constraint("ck_llm_role_routes_reasoning_effort_override", type_="check")
        batch.drop_constraint("ck_llm_role_routes_max_output_override", type_="check")
        batch.drop_constraint("ck_llm_role_routes_top_p_override", type_="check")
        batch.drop_constraint("ck_llm_role_routes_temperature_override", type_="check")
        batch.drop_column("seed_override")
        batch.drop_column("reasoning_effort_override")
        batch.drop_column("max_output_tokens_override")
        batch.drop_column("top_p_override")
        batch.drop_column("temperature_override")

    with op.batch_alter_table("llm_model_profiles") as batch:
        batch.drop_constraint("ck_llm_model_profiles_reasoning_effort", type_="check")
        batch.drop_constraint("ck_llm_model_profiles_top_p", type_="check")
        batch.drop_column("seed")
        batch.drop_column("reasoning_effort")
        batch.drop_column("top_p")
