"""Add Phase 10 execution persistence and stage control-plane foundation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0041"
down_revision: str | None = "20260827_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCED_CONTRACT = "sourced-entry-execution-v1"


def _execution_representation_check() -> str:
    preselection_codes = (
        "'SOURCE_AUTHORITY_INVALID','DECISION_EXPIRED','EXECUTION_STAGE_UNAVAILABLE'"
    )
    return (
        "((contract_version IS NULL AND mode IS NOT NULL AND stage IS NOT NULL "
        "AND execution_stage_version_id IS NULL "
        "AND execution_stage_payload_hash IS NULL) OR "
        f"(contract_version = '{SOURCED_CONTRACT}' AND "
        "(((state = 'NO_ACTION' OR (state = 'FAILED_SAFE' AND result_code IN "
        f"({preselection_codes}))) AND mode IS NULL AND stage IS NULL "
        "AND execution_stage_version_id IS NULL "
        "AND execution_stage_payload_hash IS NULL) OR "
        "(state <> 'NO_ACTION' AND NOT (state = 'FAILED_SAFE' AND result_code IN "
        f"({preselection_codes})) AND mode IS NOT NULL AND stage IS NOT NULL "
        "AND execution_stage_version_id IS NOT NULL "
        "AND execution_stage_payload_hash IS NOT NULL))))"
    )


def _guard_subject_check() -> str:
    return (
        "((subject_type = 'DECISION_EXECUTION' AND execution_id IS NOT NULL "
        "AND stop_trigger_id IS NULL AND subject_id = execution_id) OR "
        "(subject_type = 'STOP_TRIGGER' AND execution_id IS NULL "
        "AND stop_trigger_id IS NOT NULL AND subject_id = stop_trigger_id) OR "
        "(subject_type NOT IN ('DECISION_EXECUTION','STOP_TRIGGER') "
        "AND execution_id IS NOT NULL AND stop_trigger_id IS NULL))"
    )


def _order_intent_provenance_check() -> str:
    return (
        "((source_type IS NULL AND source_id IS NULL AND decision_execution_id IS NULL "
        "AND stop_trigger_id IS NULL AND guard_evaluation_id IS NULL "
        "AND approval_id IS NULL AND execution_policy_version_id IS NULL "
        "AND risk_policy_version_id IS NULL AND execution_stage_version_id IS NULL "
        "AND execution_stage_payload_hash IS NULL AND authority_key IS NULL) OR "
        "(source_type = 'DECISION_EXECUTION' AND source_id = decision_execution_id "
        "AND decision_execution_id IS NOT NULL AND stop_trigger_id IS NULL "
        "AND guard_evaluation_id IS NOT NULL AND execution_stage_version_id IS NOT NULL "
        "AND execution_stage_payload_hash IS NOT NULL AND authority_key IS NOT NULL) OR "
        "(source_type = 'STOP_TRIGGER' AND source_id = stop_trigger_id "
        "AND stop_trigger_id IS NOT NULL AND decision_execution_id IS NULL "
        "AND guard_evaluation_id IS NOT NULL AND execution_stage_version_id IS NOT NULL "
        "AND execution_stage_payload_hash IS NOT NULL AND authority_key IS NOT NULL) OR "
        "(source_type IN ('BROKER_DIAGNOSTIC','LEGACY_EXECUTION') "
        "AND source_id IS NOT NULL AND decision_execution_id IS NULL "
        "AND stop_trigger_id IS NULL AND authority_key IS NOT NULL) OR "
        "(source_type = 'BROKER_IMPORTED' AND source_id IS NOT NULL "
        "AND decision_execution_id IS NULL AND stop_trigger_id IS NULL))"
    )


def upgrade() -> None:
    with op.batch_alter_table("decision_executions") as batch:
        batch.drop_constraint("ck_decision_executions_mode", type_="check")
        batch.drop_constraint("ck_decision_executions_stage", type_="check")
        batch.alter_column(
            "mode", existing_type=sa.String(24), existing_nullable=False, nullable=True
        )
        batch.alter_column(
            "stage", existing_type=sa.String(24), existing_nullable=False, nullable=True
        )
        batch.add_column(sa.Column("contract_version", sa.String(40), nullable=True))
        batch.add_column(sa.Column("execution_stage_version_id", sa.String(36), nullable=True))
        batch.add_column(
            sa.Column("execution_stage_payload_hash", sa.String(64), nullable=True)
        )
        batch.create_foreign_key(
            "fk_decision_executions_stage_version",
            "configuration_versions",
            ["execution_stage_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_decision_executions_mode",
            "mode IS NULL OR mode IN ('AUTOMATIC','MANUAL_APPROVAL','DISABLED')",
        )
        batch.create_check_constraint(
            "ck_decision_executions_stage",
            "stage IS NULL OR stage IN ('SHADOW','APPROVAL_ONLY','MOCK_AUTOMATIC')",
        )
        batch.create_check_constraint(
            "ck_decision_executions_representation", _execution_representation_check()
        )
    op.create_index(
        "uq_decision_executions_sourced_decision",
        "decision_executions",
        ["decision_id"],
        unique=True,
        sqlite_where=sa.text(f"contract_version = '{SOURCED_CONTRACT}'"),
        postgresql_where=sa.text(f"contract_version = '{SOURCED_CONTRACT}'"),
    )

    orphan_count = op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM guard_evaluations g "
            "LEFT JOIN stop_triggers s ON s.id = g.subject_id "
            "WHERE g.subject_type = 'STOP_TRIGGER' AND s.id IS NULL"
        )
    )
    if orphan_count:
        raise RuntimeError(
            "Refusing 20260828_0041: orphan STOP_TRIGGER Guard subject cannot be corrected"
        )
    op.add_column("guard_evaluations", sa.Column("stop_trigger_id", sa.String(36)))
    with op.batch_alter_table("guard_evaluations") as batch:
        batch.alter_column(
            "execution_id", existing_type=sa.String(36), existing_nullable=False, nullable=True
        )
    op.execute(
        "UPDATE guard_evaluations SET stop_trigger_id = subject_id, execution_id = NULL "
        "WHERE subject_type = 'STOP_TRIGGER'"
    )
    with op.batch_alter_table("guard_evaluations") as batch:
        batch.create_foreign_key(
            "fk_guard_evaluations_stop_trigger",
            "stop_triggers",
            ["stop_trigger_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_guard_evaluations_typed_subject", _guard_subject_check()
        )
    op.create_index(
        "ix_guard_evaluations_stop_trigger",
        "guard_evaluations",
        ["stop_trigger_id", "evaluated_at"],
    )

    with op.batch_alter_table("order_intents") as batch:
        batch.add_column(sa.Column("source_type", sa.String(32), nullable=True))
        batch.add_column(sa.Column("source_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("decision_execution_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("stop_trigger_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("guard_evaluation_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("approval_id", sa.String(36), nullable=True))
        batch.add_column(
            sa.Column("execution_policy_version_id", sa.String(36), nullable=True)
        )
        batch.add_column(sa.Column("risk_policy_version_id", sa.String(36), nullable=True))
        batch.add_column(
            sa.Column("execution_stage_version_id", sa.String(36), nullable=True)
        )
        batch.add_column(
            sa.Column("execution_stage_payload_hash", sa.String(64), nullable=True)
        )
        batch.add_column(sa.Column("authority_key", sa.String(128), nullable=True))
        batch.create_foreign_key(
            "fk_order_intents_decision_execution",
            "decision_executions",
            ["decision_execution_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_order_intents_stop_trigger",
            "stop_triggers",
            ["stop_trigger_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_order_intents_guard_evaluation",
            "guard_evaluations",
            ["guard_evaluation_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_order_intents_approval",
            "approvals",
            ["approval_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        for suffix, column in (
            ("execution_policy", "execution_policy_version_id"),
            ("risk_policy", "risk_policy_version_id"),
            ("stage_version", "execution_stage_version_id"),
        ):
            batch.create_foreign_key(
                f"fk_order_intents_{suffix}",
                "configuration_versions",
                [column],
                ["id"],
                ondelete="RESTRICT",
            )
        batch.create_check_constraint(
            "ck_order_intents_source_type",
            "source_type IS NULL OR source_type IN "
            "('DECISION_EXECUTION','STOP_TRIGGER','BROKER_DIAGNOSTIC',"
            "'LEGACY_EXECUTION','BROKER_IMPORTED')",
        )
        batch.create_check_constraint(
            "ck_order_intents_authority_provenance", _order_intent_provenance_check()
        )
    op.create_index(
        "uq_order_intents_authority_key",
        "order_intents",
        ["authority_key"],
        unique=True,
        sqlite_where=sa.text("authority_key IS NOT NULL"),
        postgresql_where=sa.text("authority_key IS NOT NULL"),
    )

    with op.batch_alter_table("approvals") as batch:
        batch.create_foreign_key(
            "fk_approvals_reauth_proof",
            "reauth_proofs",
            ["reauth_proof_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_approvals_order",
            "orders",
            ["order_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("ck_orders_status", type_="check")
        batch.create_check_constraint(
            "ck_orders_status",
            "status IN ('CREATED','VALIDATING','SUBMITTING','ACKNOWLEDGED','OPEN',"
            "'PARTIALLY_FILLED','FILLED','CANCEL_PENDING','CANCELLED','REPLACE_PENDING',"
            "'REPLACED','REJECTED','UNKNOWN','RECONCILING','INVALIDATED')",
        )


def _guard_destructive_downgrade() -> None:
    bind = op.get_bind()
    checks = (
        (
            "SELECT COUNT(*) FROM decision_executions WHERE contract_version IS NOT NULL "
            "OR execution_stage_version_id IS NOT NULL "
            "OR execution_stage_payload_hash IS NOT NULL"
        ),
        "SELECT COUNT(*) FROM guard_evaluations WHERE stop_trigger_id IS NOT NULL",
        (
            "SELECT COUNT(*) FROM order_intents WHERE source_type IS NOT NULL "
            "OR source_id IS NOT NULL OR decision_execution_id IS NOT NULL "
            "OR stop_trigger_id IS NOT NULL OR guard_evaluation_id IS NOT NULL "
            "OR approval_id IS NOT NULL OR execution_policy_version_id IS NOT NULL "
            "OR risk_policy_version_id IS NOT NULL OR execution_stage_version_id IS NOT NULL "
            "OR execution_stage_payload_hash IS NOT NULL OR authority_key IS NOT NULL"
        ),
        "SELECT COUNT(*) FROM orders WHERE status = 'INVALIDATED'",
    )
    if any(bind.scalar(sa.text(statement)) for statement in checks):
        raise RuntimeError(
            "Refusing downgrade of 20260828_0041 because Phase 10 execution semantics "
            "cannot be represented by the previous schema"
        )


def downgrade() -> None:
    _guard_destructive_downgrade()
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("ck_orders_status", type_="check")
        batch.create_check_constraint(
            "ck_orders_status",
            "status IN ('CREATED','VALIDATING','SUBMITTING','ACKNOWLEDGED','OPEN',"
            "'PARTIALLY_FILLED','FILLED','CANCEL_PENDING','CANCELLED','REPLACE_PENDING',"
            "'REPLACED','REJECTED','UNKNOWN','RECONCILING')",
        )
    with op.batch_alter_table("approvals") as batch:
        batch.drop_constraint("fk_approvals_order", type_="foreignkey")
        batch.drop_constraint("fk_approvals_reauth_proof", type_="foreignkey")
    op.drop_index("uq_order_intents_authority_key", table_name="order_intents")
    with op.batch_alter_table("order_intents") as batch:
        batch.drop_constraint("ck_order_intents_authority_provenance", type_="check")
        batch.drop_constraint("ck_order_intents_source_type", type_="check")
        for name in (
            "fk_order_intents_stage_version",
            "fk_order_intents_risk_policy",
            "fk_order_intents_execution_policy",
            "fk_order_intents_approval",
            "fk_order_intents_guard_evaluation",
            "fk_order_intents_stop_trigger",
            "fk_order_intents_decision_execution",
        ):
            batch.drop_constraint(name, type_="foreignkey")
        for column in (
            "authority_key",
            "execution_stage_payload_hash",
            "execution_stage_version_id",
            "risk_policy_version_id",
            "execution_policy_version_id",
            "approval_id",
            "guard_evaluation_id",
            "stop_trigger_id",
            "decision_execution_id",
            "source_id",
            "source_type",
        ):
            batch.drop_column(column)
    op.drop_index("ix_guard_evaluations_stop_trigger", table_name="guard_evaluations")
    with op.batch_alter_table("guard_evaluations") as batch:
        batch.drop_constraint("ck_guard_evaluations_typed_subject", type_="check")
        batch.drop_constraint("fk_guard_evaluations_stop_trigger", type_="foreignkey")
        batch.alter_column(
            "execution_id", existing_type=sa.String(36), existing_nullable=True, nullable=False
        )
        batch.drop_column("stop_trigger_id")
    op.drop_index(
        "uq_decision_executions_sourced_decision", table_name="decision_executions"
    )
    with op.batch_alter_table("decision_executions") as batch:
        batch.drop_constraint("ck_decision_executions_representation", type_="check")
        batch.drop_constraint("ck_decision_executions_stage", type_="check")
        batch.drop_constraint("ck_decision_executions_mode", type_="check")
        batch.drop_constraint("fk_decision_executions_stage_version", type_="foreignkey")
        batch.drop_column("execution_stage_payload_hash")
        batch.drop_column("execution_stage_version_id")
        batch.drop_column("contract_version")
        batch.alter_column(
            "stage", existing_type=sa.String(24), existing_nullable=True, nullable=False
        )
        batch.alter_column(
            "mode", existing_type=sa.String(24), existing_nullable=True, nullable=False
        )
        batch.create_check_constraint(
            "ck_decision_executions_stage",
            "stage IN ('SHADOW','APPROVAL_ONLY','MOCK_AUTOMATIC')",
        )
        batch.create_check_constraint(
            "ck_decision_executions_mode",
            "mode IN ('AUTOMATIC','MANUAL_APPROVAL','DISABLED')",
        )
