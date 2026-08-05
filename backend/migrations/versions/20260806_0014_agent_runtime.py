"""Add deterministic diagnostic agent runtime."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0014"
down_revision: str | None = "20260805_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False, server_default="DIAGNOSTIC"),
        sa.Column("execution_stage", sa.String(24), nullable=False, server_default="SHADOW"),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column(
            "market_snapshot_id",
            sa.String(36),
            sa.ForeignKey("market_snapshots.id"),
            nullable=False,
        ),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("dag_version", sa.String(64), nullable=False),
        sa.Column("route_versions_json", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="CREATED"),
        sa.Column("core_action", sa.String(32)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("purpose = 'DIAGNOSTIC'", name="ck_agent_runs_foundation_purpose"),
        sa.CheckConstraint("market IN ('KRX','NXT')", name="ck_agent_runs_market"),
        sa.CheckConstraint(
            "state IN ('CREATED','RUNNING','SUCCEEDED','PARTIAL','FAILED','CANCELLED')",
            name="ck_agent_runs_state",
        ),
        sa.CheckConstraint("execution_stage = 'SHADOW'", name="ck_agent_runs_execution_stage"),
    )
    op.create_index("ix_agent_runs_owner_id", "agent_runs", ["owner_id"])
    op.create_index("ix_agent_runs_market_snapshot_id", "agent_runs", ["market_snapshot_id"])
    op.create_index("ix_agent_runs_owner_created", "agent_runs", ["owner_id", "created_at"])

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_tier", sa.String(16), nullable=False),
        sa.Column("source_name", sa.String(128), nullable=False),
        sa.Column("source_url", sa.String(1000)),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("extraction_method", sa.String(16), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("event_at", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('KRX','NXT')", name="ck_evidence_items_market"),
    )
    op.create_index("ix_evidence_items_run_id", "evidence_items", ["run_id"])
    op.create_index(
        "ix_evidence_items_stream_received",
        "evidence_items",
        ["market", "symbol", "received_at"],
    )

    op.create_table(
        "evidence_bundles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("contradiction_groups_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("stale_evidence_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("reason_codes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("bundle_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('VERIFIED','PARTIAL','CONFLICTED','REJECTED')",
            name="ck_evidence_bundles_state",
        ),
    )
    op.create_index("ix_evidence_bundles_owner_id", "evidence_bundles", ["owner_id"])
    op.create_index("ix_evidence_bundles_bundle_hash", "evidence_bundles", ["bundle_hash"])

    op.create_table(
        "agent_stage_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("dependency_roles_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("route_id", sa.String(36), sa.ForeignKey("llm_role_routes.id")),
        sa.Column(
            "invocation_id",
            sa.String(36),
            sa.ForeignKey("llm_invocations.id"),
            unique=True,
        ),
        sa.Column("state", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_json", sa.Text()),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "role", name="uq_agent_stage_runs_run_role"),
        sa.CheckConstraint(
            "role IN ('INTEL_COLLECTOR','EVIDENCE_VERIFIER','TECHNICAL_SCOUT',"
            "'NEWS_DISCLOSURE_SCOUT','MARKET_SECTOR_SCOUT','POSITION_RISK_SCOUT','CORE')",
            name="ck_agent_stage_runs_role",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING','RUNNING','SUCCEEDED','INSUFFICIENT_DATA','CONFLICTED',"
            "'TIMED_OUT','FAILED','INVALID_OUTPUT')",
            name="ck_agent_stage_runs_state",
        ),
    )
    op.create_index("ix_agent_stage_runs_run_id", "agent_stage_runs", ["run_id"])
    op.create_index("ix_agent_stage_runs_run_sequence", "agent_stage_runs", ["run_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_agent_stage_runs_run_sequence", table_name="agent_stage_runs")
    op.drop_index("ix_agent_stage_runs_run_id", table_name="agent_stage_runs")
    op.drop_table("agent_stage_runs")
    op.drop_index("ix_evidence_bundles_bundle_hash", table_name="evidence_bundles")
    op.drop_index("ix_evidence_bundles_owner_id", table_name="evidence_bundles")
    op.drop_table("evidence_bundles")
    op.drop_index("ix_evidence_items_stream_received", table_name="evidence_items")
    op.drop_index("ix_evidence_items_run_id", table_name="evidence_items")
    op.drop_table("evidence_items")
    op.drop_index("ix_agent_runs_owner_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_market_snapshot_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_owner_id", table_name="agent_runs")
    op.drop_table("agent_runs")
