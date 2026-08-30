"""Add Cresta v2 v7 persistence and ORM foundation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0039"
down_revision: str | None = "20260817_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_STAGE_ROLES = (
    "'INTEL_COLLECTOR','EVIDENCE_VERIFIER','TECHNICAL_SCOUT',"
    "'NEWS_DISCLOSURE_SCOUT','MARKET_SECTOR_SCOUT','POSITION_RISK_SCOUT',"
    "'EVIDENCE_CANDIDATE_AUDITOR','CORE'"
)
V7_STAGE_ROLES = (
    LEGACY_STAGE_ROLES
    + ",'CONSERVATIVE_DECISION','BALANCED_DECISION','AGGRESSIVE_DECISION','ENTRY_ARBITER'"
)
LEGACY_ROUTE_ROLES = (
    "'INTEL_COLLECTOR','EVIDENCE_VERIFIER','TECHNICAL_SCOUT',"
    "'NEWS_DISCLOSURE_SCOUT','MARKET_SECTOR_SCOUT','POSITION_RISK_SCOUT','CORE'"
)
V7_ROUTE_ROLES = (
    LEGACY_ROUTE_ROLES
    + ",'CONSERVATIVE_DECISION','BALANCED_DECISION','AGGRESSIVE_DECISION'"
)
LEGACY_PROMPT_ROLES = (
    "'TECHNICAL_SCOUT','NEWS_DISCLOSURE_SCOUT','MARKET_SECTOR_SCOUT',"
    "'POSITION_RISK_SCOUT','CORE'"
)
V7_PROMPT_ROLES = (
    LEGACY_PROMPT_ROLES
    + ",'CONSERVATIVE_DECISION','BALANCED_DECISION','AGGRESSIVE_DECISION'"
)


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("ck_agent_runs_advisory_context", type_="check")
        batch.drop_constraint("ck_agent_runs_foundation_purpose", type_="check")
        batch.add_column(sa.Column("policy_profile_version_map_json", sa.Text()))
        batch.add_column(sa.Column("policy_profile_version_map_hash", sa.String(length=64)))
        batch.add_column(sa.Column("activation_gate_version_id", sa.String(length=36)))
        batch.add_column(sa.Column("activation_gate_version_hash", sa.String(length=64)))
        batch.create_foreign_key(
            "fk_agent_runs_activation_gate_version",
            "configuration_versions",
            ["activation_gate_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_agent_runs_foundation_purpose",
            "purpose IN ('DIAGNOSTIC','TRADING_ADVISORY','TRADING')",
        )
        batch.create_check_constraint(
            "ck_agent_runs_advisory_context",
            "(purpose = 'DIAGNOSTIC' AND basis_decision_id IS NULL "
            "AND fusion_policy_version IS NULL AND fusion_state IS NULL) OR "
            "(purpose = 'TRADING_ADVISORY' AND basis_decision_id IS NOT NULL "
            "AND fusion_policy_version IS NOT NULL AND fusion_state IS NOT NULL) OR "
            "(purpose = 'TRADING' AND basis_decision_id IS NULL "
            "AND fusion_policy_version IS NULL AND fusion_state IS NULL)",
        )

    with op.batch_alter_table("agent_stage_runs") as batch:
        batch.drop_constraint("ck_agent_stage_runs_role", type_="check")
        batch.create_check_constraint(
            "ck_agent_stage_runs_role", f"role IN ({V7_STAGE_ROLES})"
        )

    with op.batch_alter_table("llm_role_routes") as batch:
        batch.drop_constraint("ck_llm_role_routes_role", type_="check")
        batch.create_check_constraint(
            "ck_llm_role_routes_role", f"role IN ({V7_ROUTE_ROLES})"
        )

    with op.batch_alter_table("llm_prompt_profiles") as batch:
        batch.drop_constraint("ck_llm_prompt_profiles_role", type_="check")
        batch.create_check_constraint(
            "ck_llm_prompt_profiles_role", f"role IN ({V7_PROMPT_ROLES})"
        )

    with op.batch_alter_table("decisions") as batch:
        batch.add_column(sa.Column("source_agent_run_id", sa.String(length=36)))
        batch.add_column(sa.Column("source_stage_run_id", sa.String(length=36)))
        batch.add_column(sa.Column("source_stage_output_hash", sa.String(length=64)))
        batch.create_foreign_key(
            "fk_decisions_source_agent_run",
            "agent_runs",
            ["source_agent_run_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_decisions_source_stage_run",
            "agent_stage_runs",
            ["source_stage_run_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_decisions_source_lineage_all_or_none",
            "(source_agent_run_id IS NULL AND source_stage_run_id IS NULL "
            "AND source_stage_output_hash IS NULL) OR "
            "(source_agent_run_id IS NOT NULL AND source_stage_run_id IS NOT NULL "
            "AND source_stage_output_hash IS NOT NULL)",
        )

    op.create_index(
        "uq_decisions_source_agent_run",
        "decisions",
        ["source_agent_run_id"],
        unique=True,
        sqlite_where=sa.text("source_agent_run_id IS NOT NULL"),
        postgresql_where=sa.text("source_agent_run_id IS NOT NULL"),
    )
    op.create_index(
        "uq_decisions_source_stage_run",
        "decisions",
        ["source_stage_run_id"],
        unique=True,
        sqlite_where=sa.text("source_stage_run_id IS NOT NULL"),
        postgresql_where=sa.text("source_stage_run_id IS NOT NULL"),
    )

    op.create_table(
        "decision_contexts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("decision_input_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_bundle_id", sa.String(length=36), nullable=False),
        sa.Column("market_context_snapshot_id", sa.String(length=36)),
        sa.Column("technical_scout_stage_id", sa.String(length=36), nullable=False),
        sa.Column("news_disclosure_scout_stage_id", sa.String(length=36), nullable=False),
        sa.Column("market_sector_scout_stage_id", sa.String(length=36), nullable=False),
        sa.Column("position_risk_scout_stage_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_audit_stage_id", sa.String(length=36), nullable=False),
        sa.Column("configuration_provenance_json", sa.Text(), nullable=False),
        sa.Column("configuration_provenance_hash", sa.String(length=64), nullable=False),
        sa.Column("version_manifest_json", sa.Text(), nullable=False),
        sa.Column("version_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["decision_input_snapshot_id"],
            ["decision_input_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_bundle_id"], ["evidence_bundles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["market_context_snapshot_id"],
            ["market_context_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["technical_scout_stage_id"],
            ["agent_stage_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["news_disclosure_scout_stage_id"],
            ["agent_stage_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["market_sector_scout_stage_id"],
            ["agent_stage_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["position_risk_scout_stage_id"],
            ["agent_stage_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_audit_stage_id"],
            ["agent_stage_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_decision_contexts_run_id"),
    )
    op.create_index(
        "ix_decision_contexts_context_hash", "decision_contexts", ["context_hash"]
    )
    op.create_index(
        "ix_decision_contexts_valid_until", "decision_contexts", ["valid_until"]
    )


def _guard_destructive_downgrade() -> None:
    bind = op.get_bind()
    guarded_counts = {
        "decision_contexts": bind.scalar(sa.text("SELECT COUNT(*) FROM decision_contexts")),
        "source-linked decisions": bind.scalar(
            sa.text(
                "SELECT COUNT(*) FROM decisions "
                "WHERE source_agent_run_id IS NOT NULL "
                "OR source_stage_run_id IS NOT NULL OR source_stage_output_hash IS NOT NULL"
            )
        ),
        "v7 agent runs": bind.scalar(
            sa.text(
                "SELECT COUNT(*) FROM agent_runs WHERE purpose = 'TRADING' "
                "OR dag_version = 'agent-dag-v7' "
                "OR policy_profile_version_map_json IS NOT NULL "
                "OR policy_profile_version_map_hash IS NOT NULL "
                "OR activation_gate_version_id IS NOT NULL "
                "OR activation_gate_version_hash IS NOT NULL"
            )
        ),
        "v7 stage roles": bind.scalar(
            sa.text(
                "SELECT COUNT(*) FROM agent_stage_runs WHERE role IN ("
                "'CONSERVATIVE_DECISION','BALANCED_DECISION',"
                "'AGGRESSIVE_DECISION','ENTRY_ARBITER')"
            )
        ),
        "v7 route roles": bind.scalar(
            sa.text(
                "SELECT COUNT(*) FROM llm_role_routes WHERE role IN ("
                "'CONSERVATIVE_DECISION','BALANCED_DECISION','AGGRESSIVE_DECISION')"
            )
        ),
        "v7 prompt roles": bind.scalar(
            sa.text(
                "SELECT COUNT(*) FROM llm_prompt_profiles WHERE role IN ("
                "'CONSERVATIVE_DECISION','BALANCED_DECISION','AGGRESSIVE_DECISION')"
            )
        ),
    }
    populated = [name for name, count in guarded_counts.items() if count]
    if populated:
        raise RuntimeError(
            "Refusing downgrade of 20260825_0039 because v7 lineage would be lost: "
            + ", ".join(populated)
        )


def downgrade() -> None:
    _guard_destructive_downgrade()

    op.drop_index("ix_decision_contexts_valid_until", table_name="decision_contexts")
    op.drop_index("ix_decision_contexts_context_hash", table_name="decision_contexts")
    op.drop_table("decision_contexts")

    op.drop_index("uq_decisions_source_stage_run", table_name="decisions")
    op.drop_index("uq_decisions_source_agent_run", table_name="decisions")
    with op.batch_alter_table("decisions") as batch:
        batch.drop_constraint("ck_decisions_source_lineage_all_or_none", type_="check")
        batch.drop_constraint("fk_decisions_source_stage_run", type_="foreignkey")
        batch.drop_constraint("fk_decisions_source_agent_run", type_="foreignkey")
        batch.drop_column("source_stage_output_hash")
        batch.drop_column("source_stage_run_id")
        batch.drop_column("source_agent_run_id")

    with op.batch_alter_table("llm_prompt_profiles") as batch:
        batch.drop_constraint("ck_llm_prompt_profiles_role", type_="check")
        batch.create_check_constraint(
            "ck_llm_prompt_profiles_role", f"role IN ({LEGACY_PROMPT_ROLES})"
        )

    with op.batch_alter_table("llm_role_routes") as batch:
        batch.drop_constraint("ck_llm_role_routes_role", type_="check")
        batch.create_check_constraint(
            "ck_llm_role_routes_role", f"role IN ({LEGACY_ROUTE_ROLES})"
        )

    with op.batch_alter_table("agent_stage_runs") as batch:
        batch.drop_constraint("ck_agent_stage_runs_role", type_="check")
        batch.create_check_constraint(
            "ck_agent_stage_runs_role", f"role IN ({LEGACY_STAGE_ROLES})"
        )

    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("ck_agent_runs_advisory_context", type_="check")
        batch.drop_constraint("ck_agent_runs_foundation_purpose", type_="check")
        batch.drop_constraint("fk_agent_runs_activation_gate_version", type_="foreignkey")
        batch.create_check_constraint(
            "ck_agent_runs_foundation_purpose",
            "purpose IN ('DIAGNOSTIC','TRADING_ADVISORY')",
        )
        batch.create_check_constraint(
            "ck_agent_runs_advisory_context",
            "(purpose = 'DIAGNOSTIC' AND basis_decision_id IS NULL "
            "AND fusion_policy_version IS NULL AND fusion_state IS NULL) OR "
            "(purpose = 'TRADING_ADVISORY' AND basis_decision_id IS NOT NULL "
            "AND fusion_policy_version IS NOT NULL AND fusion_state IS NOT NULL)",
        )
        batch.drop_column("activation_gate_version_hash")
        batch.drop_column("activation_gate_version_id")
        batch.drop_column("policy_profile_version_map_hash")
        batch.drop_column("policy_profile_version_map_json")
