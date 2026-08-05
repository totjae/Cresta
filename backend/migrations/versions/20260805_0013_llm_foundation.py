"""Add LLM provider, model, role route and invocation foundation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0013"
down_revision: str | None = "20260805_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_provider_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("adapter_type", sa.String(40), nullable=False),
        sa.Column("endpoint", sa.String(500)),
        sa.Column("credential_secret_ref", sa.String(128)),
        sa.Column("data_policy", sa.String(24), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("health_status", sa.String(24), nullable=False, server_default="UNKNOWN"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "adapter_type IN ('MOCK','OPENAI_RESPONSES','ANTHROPIC_MESSAGES',"
            "'GEMINI_GENERATE_CONTENT','VERCEL_AI_GATEWAY','OPENAI_COMPATIBLE',"
            "'OLLAMA_NATIVE','OLLAMA_OPENAI_COMPATIBLE')",
            name="ck_llm_provider_profiles_adapter_type",
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT','VALIDATED','DISABLED')",
            name="ck_llm_provider_profiles_state",
        ),
        sa.CheckConstraint(
            "data_policy IN ('EXTERNAL_CLOUD','GATEWAY','LOCAL','NONE')",
            name="ck_llm_provider_profiles_data_policy",
        ),
        sa.UniqueConstraint("owner_id", "name", name="uq_llm_provider_profiles_owner_name"),
    )
    op.create_index("ix_llm_provider_profiles_owner_id", "llm_provider_profiles", ["owner_id"])

    op.create_table(
        "llm_model_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "provider_profile_id",
            sa.String(36),
            sa.ForeignKey("llm_provider_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(64), nullable=False),
        sa.Column("provider_model_id", sa.String(128), nullable=False),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("max_context_tokens", sa.Integer()),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("temperature", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("state", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('DRAFT','VALIDATED','DISABLED')", name="ck_llm_model_profiles_state"
        ),
        sa.CheckConstraint("max_output_tokens > 0", name="ck_llm_model_profiles_max_output"),
        sa.CheckConstraint(
            "temperature >= 0 AND temperature <= 2", name="ck_llm_model_profiles_temperature"
        ),
        sa.UniqueConstraint(
            "provider_profile_id", "alias", name="uq_llm_model_profiles_provider_alias"
        ),
    )
    op.create_index(
        "ix_llm_model_profiles_provider_profile_id",
        "llm_model_profiles",
        ["provider_profile_id"],
    )

    op.create_table(
        "llm_role_routes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column(
            "primary_model_profile_id",
            sa.String(36),
            sa.ForeignKey("llm_model_profiles.id"),
            nullable=False,
        ),
        sa.Column(
            "fallback_model_profile_ids_json", sa.Text(), nullable=False, server_default="[]"
        ),
        sa.Column("fallback_policy", sa.String(32), nullable=False, server_default="NONE"),
        sa.Column("execution_stage", sa.String(24), nullable=False, server_default="SHADOW"),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="10000"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("daily_call_limit", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("daily_cost_limit_krw", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("output_schema_version", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('INTEL_COLLECTOR','EVIDENCE_VERIFIER','TECHNICAL_SCOUT',"
            "'NEWS_DISCLOSURE_SCOUT','MARKET_SECTOR_SCOUT','POSITION_RISK_SCOUT','CORE')",
            name="ck_llm_role_routes_role",
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT','VALIDATED','ACTIVE','SUPERSEDED')",
            name="ck_llm_role_routes_state",
        ),
        sa.CheckConstraint(
            "execution_stage = 'SHADOW'", name="ck_llm_role_routes_foundation_stage"
        ),
        sa.CheckConstraint(
            "fallback_policy = 'NONE'", name="ck_llm_role_routes_foundation_fallback"
        ),
        sa.CheckConstraint("timeout_ms BETWEEN 1000 AND 60000", name="ck_llm_role_routes_timeout"),
        sa.CheckConstraint("max_attempts = 1", name="ck_llm_role_routes_attempts"),
    )
    op.create_index("ix_llm_role_routes_owner_id", "llm_role_routes", ["owner_id"])
    op.create_index(
        "ix_llm_role_routes_primary_model_profile_id",
        "llm_role_routes",
        ["primary_model_profile_id"],
    )
    op.create_index(
        "uq_llm_role_routes_active",
        "llm_role_routes",
        ["owner_id", "role"],
        unique=True,
        sqlite_where=sa.text("state = 'ACTIVE'"),
        postgresql_where=sa.text("state = 'ACTIVE'"),
    )

    op.create_table(
        "llm_invocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stage_run_id", sa.String(36)),
        sa.Column(
            "requested_provider_profile_id",
            sa.String(36),
            sa.ForeignKey("llm_provider_profiles.id"),
            nullable=False,
        ),
        sa.Column(
            "requested_model_profile_id",
            sa.String(36),
            sa.ForeignKey("llm_model_profiles.id"),
        ),
        sa.Column("actual_provider", sa.String(128)),
        sa.Column("actual_model", sa.String(128)),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("provider_request_id", sa.String(128)),
        sa.Column("gateway_request_id", sa.String(128)),
        sa.Column("input_hash", sa.String(64)),
        sa.Column("raw_response_hash", sa.String(64)),
        sa.Column("usage_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_krw", sa.Numeric(18, 4)),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fallback_path_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("validation_status", sa.String(16), nullable=False, server_default="NOT_RUN"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('CREATED','RUNNING','SUCCEEDED','REFUSED','TIMED_OUT',"
            "'RATE_LIMITED','PROVIDER_ERROR','INVALID_OUTPUT','AMBIGUOUS')",
            name="ck_llm_invocations_state",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_llm_invocations_retry_count"),
    )
    op.create_index("ix_llm_invocations_stage_run_id", "llm_invocations", ["stage_run_id"])
    op.create_index("ix_llm_invocations_created", "llm_invocations", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_invocations_created", table_name="llm_invocations")
    op.drop_index("ix_llm_invocations_stage_run_id", table_name="llm_invocations")
    op.drop_table("llm_invocations")
    op.drop_index("uq_llm_role_routes_active", table_name="llm_role_routes")
    op.drop_index("ix_llm_role_routes_primary_model_profile_id", table_name="llm_role_routes")
    op.drop_index("ix_llm_role_routes_owner_id", table_name="llm_role_routes")
    op.drop_table("llm_role_routes")
    op.drop_index("ix_llm_model_profiles_provider_profile_id", table_name="llm_model_profiles")
    op.drop_table("llm_model_profiles")
    op.drop_index("ix_llm_provider_profiles_owner_id", table_name="llm_provider_profiles")
    op.drop_table("llm_provider_profiles")
