from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.ids import uuid7


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    login_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    password_params: Mapped[str] = mapped_column(String(64), default="argon2id-m65536-t3-p1")
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    failed_auth_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lockout_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    totp: Mapped[TotpCredential] = relationship(back_populates="user", uselist=False)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "market", "symbol", name="uq_watchlist_items_user_market_symbol"
        ),
        CheckConstraint("market IN ('KRX')", name="ck_watchlist_items_market"),
        Index("ix_watchlist_items_market_symbol", "market", "symbol"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(6), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False, default="KRX")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TotpCredential(Base):
    __tablename__ = "totp_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    last_used_step: Mapped[int | None] = mapped_column(Integer)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="totp")


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    challenge_type: Mapped[str] = mapped_column(String(24), default="LOGIN_TOTP")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))


class ReauthProof(Base):
    __tablename__ = "reauth_proofs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    proof_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"

    subject_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    lockout_level: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[str] = mapped_column(String(24), nullable=False)
    request_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ConfigurationVersion(Base):
    __tablename__ = "configuration_versions"
    __table_args__ = (
        UniqueConstraint(
            "scope", "target_id", "category", "sequence", name="uq_configuration_sequence"
        ),
        CheckConstraint(
            "state IN ('DRAFT','VALIDATED','ACTIVE','SUPERSEDED')",
            name="ck_configuration_versions_state",
        ),
        CheckConstraint("sequence > 0", name="ck_configuration_versions_sequence_positive"),
        Index(
            "uq_configuration_active_target",
            "scope",
            "target_id",
            "category",
            unique=True,
            sqlite_where=text("state = 'ACTIVE'"),
            postgresql_where=text("state = 'ACTIVE'"),
        ),
        Index("ix_configuration_target_created", "target_id", "category", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    base_active_version_id: Mapped[str | None] = mapped_column(String(36))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionInputSnapshot(Base):
    __tablename__ = "decision_input_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "purpose",
            "market_snapshot_id",
            "input_hash",
            name="uq_decision_input_snapshots_identity",
        ),
        CheckConstraint(
            "purpose IN ('DIAGNOSTIC','TRADING')",
            name="ck_decision_input_snapshots_purpose",
        ),
        CheckConstraint("market IN ('KRX','NXT')", name="ck_decision_input_snapshots_market"),
        Index(
            "ix_decision_input_snapshots_symbol_created",
            "market",
            "symbol",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    market_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("market_snapshots.id"), nullable=False
    )
    indicator_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("indicator_snapshots.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_quality: Mapped[str] = mapped_column(String(24), nullable=False)
    session_state: Mapped[str] = mapped_column(String(32), nullable=False)
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('BUY','WAIT','REJECT','RISK_BLOCK','HOLD','TIGHTEN_STOP',"
            "'PARTIAL_SELL','FULL_SELL','EMERGENCY_EXIT')",
            name="ck_decisions_action",
        ),
        CheckConstraint(
            "execution_mode IS NULL OR execution_mode IN "
            "('AUTOMATIC','MANUAL_APPROVAL','DISABLED')",
            name="ck_decisions_execution_mode",
        ),
        CheckConstraint(
            "execution_outcome IN ('NO_ACTION','DISABLED','APPROVAL_REQUIRED','GUARD_BLOCKED')",
            name="ck_decisions_execution_outcome",
        ),
        Index("ix_decisions_symbol_created", "symbol", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    decision_input_id: Mapped[str | None] = mapped_column(
        ForeignKey("decision_input_snapshots.id"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="DIAGNOSTIC")
    evaluation_request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    input_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("market_snapshots.id"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    scout_output_json: Mapped[str] = mapped_column(Text, nullable=False)
    core_output_json: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    configuration_version_id: Mapped[str | None] = mapped_column(String(36))
    execution_mode: Mapped[str | None] = mapped_column(String(24))
    execution_outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(16), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionExecution(Base):
    __tablename__ = "decision_executions"
    __table_args__ = (
        UniqueConstraint("execution_key", name="uq_decision_executions_key"),
        CheckConstraint(
            "mode IN ('AUTOMATIC','MANUAL_APPROVAL','DISABLED')",
            name="ck_decision_executions_mode",
        ),
        CheckConstraint(
            "stage IN ('SHADOW','APPROVAL_ONLY','MOCK_AUTOMATIC')",
            name="ck_decision_executions_stage",
        ),
        CheckConstraint(
            "state IN ('ROUTING','NO_ACTION','DISABLED','GUARD_BLOCKED',"
            "'SHADOW_RECORDED','APPROVAL_PENDING','EXPIRED','REJECTED',"
            "'INVALIDATED','ORDER_CREATED','FAILED_SAFE')",
            name="ck_decision_executions_state",
        ),
        Index("ix_decision_executions_decision", "decision_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    execution_key: Mapped[str] = mapped_column(String(128), nullable=False)
    decision_id: Mapped[str] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="ROUTING")
    result_code: Mapped[str | None] = mapped_column(String(64))
    execution_policy_version_id: Mapped[str | None] = mapped_column(String(36))
    risk_policy_version_id: Mapped[str | None] = mapped_column(String(36))
    strategy_config_version_id: Mapped[str | None] = mapped_column(String(36))
    guard_evaluation_id: Mapped[str | None] = mapped_column(String(36))
    approval_id: Mapped[str | None] = mapped_column(String(36))
    order_intent_id: Mapped[str | None] = mapped_column(String(36))
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class GuardEvaluation(Base):
    __tablename__ = "guard_evaluations"
    __table_args__ = (
        CheckConstraint("result IN ('PASSED','BLOCKED')", name="ck_guard_evaluations_result"),
        Index("ix_guard_evaluations_execution", "execution_id", "evaluated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("decision_executions.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_results_json: Mapped[str] = mapped_column(Text, nullable=False)
    halt_scope: Mapped[str | None] = mapped_column(String(24))
    snapshot_id: Mapped[str | None] = mapped_column(String(36))
    position_version: Mapped[int | None] = mapped_column(Integer)
    execution_policy_version_id: Mapped[str | None] = mapped_column(String(36))
    risk_policy_version_id: Mapped[str | None] = mapped_column(String(36))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("execution_id", name="uq_approvals_execution"),
        CheckConstraint(
            "state IN ('PENDING','APPROVED','REJECTED','EXPIRED','INVALIDATED')",
            name="ck_approvals_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("decision_executions.id", ondelete="CASCADE"), nullable=False
    )
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    scope_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    reauth_proof_id: Mapped[str | None] = mapped_column(String(36))
    order_id: Mapped[str | None] = mapped_column(String(36))
    result_code: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class EmergencyStop(Base):
    __tablename__ = "emergency_stops"
    __table_args__ = (
        UniqueConstraint("account_alias", name="uq_emergency_stops_account"),
        CheckConstraint("level = 'PAUSE_ENTRY'", name="ck_emergency_stops_level"),
        CheckConstraint("state IN ('ACTIVE','RELEASED')", name="ck_emergency_stops_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    account_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(24), nullable=False, default="PAUSE_ENTRY")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    activation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    release_key: Mapped[str | None] = mapped_column(String(128))
    activated_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    released_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class TradingGate(Base):
    __tablename__ = "trading_gates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('STARTING','RECONCILING','READY','DEGRADED','HALTED')",
            name="ck_trading_gates_status",
        ),
    )

    account_alias: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="MOCK")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="STARTING")
    reason: Mapped[str | None] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class BrokerLease(Base):
    __tablename__ = "broker_leases"
    __table_args__ = (
        CheckConstraint("fencing_token > 0", name="ck_broker_leases_fencing_positive"),
        CheckConstraint("version > 0", name="ck_broker_leases_version_positive"),
    )

    account_alias: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BrokerWorkerState(Base):
    __tablename__ = "broker_worker_states"
    __table_args__ = (
        CheckConstraint(
            "state IN ('STARTING','AUTHENTICATING','CONNECTING','SUBSCRIBING',"
            "'RECONCILING','READY','DEGRADED','STOPPED')",
            name="ck_broker_worker_states_state",
        ),
        CheckConstraint("fencing_token > 0", name="ck_broker_worker_states_fencing_positive"),
    )

    account_alias: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="MOCK")
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="STARTING")
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    websocket_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subscriptions_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_reconciliation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconciliation_run_id: Mapped[str | None] = mapped_column(String(36))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AnalysisSchedulerLease(Base):
    __tablename__ = "analysis_scheduler_leases"
    __table_args__ = (
        CheckConstraint("fencing_token > 0", name="ck_analysis_scheduler_leases_fencing"),
        CheckConstraint("version > 0", name="ck_analysis_scheduler_leases_version"),
    )

    scheduler_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisSchedulerState(Base):
    __tablename__ = "analysis_scheduler_states"
    __table_args__ = (
        CheckConstraint(
            "state IN ('STARTING','RUNNING','IDLE','DEGRADED','STOPPED')",
            name="ck_analysis_scheduler_states_state",
        ),
        CheckConstraint("fencing_token > 0", name="ck_analysis_scheduler_states_fencing"),
        CheckConstraint(
            "processed_count >= 0 AND decision_count >= 0 AND skipped_count >= 0 "
            "AND failed_count >= 0",
            name="ck_analysis_scheduler_states_counts",
        ),
    )

    scheduler_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="STARTING")
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_slot_key: Mapped[str | None] = mapped_column(String(64))
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('RUNNING','SUCCEEDED','MISMATCH','FAILED')",
            name="ck_reconciliation_runs_state",
        ),
        CheckConstraint("mismatch_count >= 0", name="ck_reconciliation_runs_mismatch_count"),
        CheckConstraint(
            "critical_mismatch_count >= 0",
            name="ck_reconciliation_runs_critical_count",
        ),
        Index("ix_reconciliation_runs_account_started", "account_alias", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    account_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="MOCK")
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mismatch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    critical_mismatch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    broker_request_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    result_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ReconciliationMismatch(Base):
    __tablename__ = "reconciliation_mismatches"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('WARNING','CRITICAL')",
            name="ck_reconciliation_mismatches_severity",
        ),
        CheckConstraint("state IN ('OPEN','RESOLVED')", name="ck_reconciliation_mismatches_state"),
        Index("ix_reconciliation_mismatches_run", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    broker_value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    internal_value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderIntent(Base):
    __tablename__ = "order_intents"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="ck_order_intents_requested_positive"),
        CheckConstraint("side IN ('BUY','SELL')", name="ck_order_intents_side"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    order_group_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, default=uuid7
    )
    account_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="MOCK")
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    config_version: Mapped[str | None] = mapped_column(String(36))
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TradingOrder(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint(
            "environment",
            "account_alias",
            "broker_order_id",
            name="uq_orders_broker_identity",
        ),
        CheckConstraint("requested_quantity > 0", name="ck_orders_requested_positive"),
        CheckConstraint("filled_quantity >= 0", name="ck_orders_filled_nonnegative"),
        CheckConstraint("cancelled_quantity >= 0", name="ck_orders_cancelled_nonnegative"),
        CheckConstraint("remaining_quantity >= 0", name="ck_orders_remaining_nonnegative"),
        CheckConstraint(
            "requested_quantity = filled_quantity + cancelled_quantity + remaining_quantity",
            name="ck_orders_quantity_invariant",
        ),
        CheckConstraint("side IN ('BUY','SELL')", name="ck_orders_side"),
        CheckConstraint("order_type IN ('LIMIT','MARKET')", name="ck_orders_type"),
        CheckConstraint(
            "unfilled_policy IN ('NONE','CANCEL')", name="ck_orders_unfilled_policy"
        ),
        CheckConstraint(
            "fill_timeout_seconds >= 0", name="ck_orders_fill_timeout_nonnegative"
        ),
        CheckConstraint(
            "reprice_attempts >= 0 AND max_reprice_attempts >= 0 "
            "AND reprice_attempts <= max_reprice_attempts",
            name="ck_orders_reprice_attempts_range",
        ),
        CheckConstraint(
            "status IN ('CREATED','VALIDATING','SUBMITTING','ACKNOWLEDGED','OPEN',"
            "'PARTIALLY_FILLED','FILLED','CANCEL_PENDING','CANCELLED','REPLACE_PENDING',"
            "'REPLACED','REJECTED','UNKNOWN','RECONCILING')",
            name="ck_orders_status",
        ),
        Index("ix_orders_account_broker", "account_alias", "broker_order_id"),
        Index("ix_orders_symbol_status", "symbol", "status"),
        Index("ix_orders_group_created", "order_group_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    intent_id: Mapped[str] = mapped_column(
        ForeignKey("order_intents.id"), nullable=False, index=True
    )
    order_group_id: Mapped[str] = mapped_column(String(36), nullable=False)
    parent_order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"))
    account_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="MOCK")
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(12), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remaining_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="CREATED")
    client_order_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, default=uuid7
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String(64))
    replacement_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unfilled_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE")
    fill_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_reprice_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reprice_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class OrderEvent(Base):
    __tablename__ = "order_events"
    __table_args__ = (
        UniqueConstraint("source", "source_key", name="uq_order_events_source_key"),
        Index("ix_order_events_order_occurred", "order_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    source_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Fill(Base):
    __tablename__ = "fills"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_fills_quantity_positive"),
        CheckConstraint("price > 0", name="ck_fills_price_positive"),
        CheckConstraint("fee >= 0", name="ck_fills_fee_nonnegative"),
        CheckConstraint("tax >= 0", name="ck_fills_tax_nonnegative"),
        Index("ix_fills_order_filled", "order_id", "filled_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    broker_fill_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal(0))
    tax: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal(0))
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("account_alias", "symbol", name="uq_positions_account_symbol"),
        CheckConstraint("quantity >= 0", name="ck_positions_quantity_nonnegative"),
        CheckConstraint(
            "available_quantity >= 0 AND available_quantity <= quantity",
            name="ck_positions_available_quantity_range",
        ),
        CheckConstraint(
            "managed_quantity >= 0 AND managed_quantity <= quantity",
            name="ck_positions_managed_quantity_range",
        ),
        CheckConstraint(
            "managed_average_price >= 0", name="ck_positions_managed_average_nonnegative"
        ),
        CheckConstraint("state IN ('OPEN','CLOSED')", name="ck_positions_state"),
        CheckConstraint(
            "origin IN ('CRESTA_MANAGED','EXTERNAL','MIXED')", name="ck_positions_origin"
        ),
        CheckConstraint(
            "state != 'OPEN' OR "
            "(origin = 'EXTERNAL' AND managed_quantity = 0) OR "
            "(origin = 'CRESTA_MANAGED' AND managed_quantity = quantity AND quantity > 0) OR "
            "(origin = 'MIXED' AND managed_quantity > 0 AND managed_quantity < quantity)",
            name="ck_positions_open_origin_matches_managed_quantity",
        ),
        Index("ix_positions_account_symbol", "account_alias", "symbol"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    account_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    managed_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal(0)
    )
    managed_average_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal(0)
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="CLOSED")
    origin: Mapped[str] = mapped_column(String(24), nullable=False, default="CRESTA_MANAGED")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class PositionEvent(Base):
    __tablename__ = "position_events"
    __table_args__ = (Index("ix_position_events_position_created", "position_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id"), nullable=False)
    cause_type: Mapped[str] = mapped_column(String(32), nullable=False)
    cause_id: Mapped[str] = mapped_column(String(36), nullable=False)
    before_json: Mapped[str] = mapped_column(Text, nullable=False)
    after_json: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RiskEvent(Base):
    """Generic immutable ledger row for Guard/rule risk signals.

    Scope distinguishes risk families (FIXED_STOP today; DAILY_LOSS, SPREAD,
    CONNECTION and position mismatch later). Resolution tracks lifecycle while
    the detailed state machine for stop triggers lives in ``stop_triggers``.
    """

    __tablename__ = "risk_events"
    __table_args__ = (
        CheckConstraint("state IN ('ACTIVE','RESOLVED')", name="ck_risk_events_state"),
        CheckConstraint(
            "severity IN ('INFO','WARNING','HIGH','CRITICAL')",
            name="ck_risk_events_severity",
        ),
        Index("ix_risk_events_scope_state", "scope", "state"),
        Index("ix_risk_events_account_created", "account_alias", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    account_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(16))
    input_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("market_snapshots.id"))
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(64))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StopTrigger(Base):
    """Fixed stop-loss trigger state machine.

    Fires deterministically from the broker worker (no AI/Core decision). The
    trigger binds to the position version and active risk policy version at
    evaluation time and persists ``EXIT_PENDING`` across restarts so a data gap
    does not erase a stop signal (GRD-085, EXE-053, ORD-023).
    """

    __tablename__ = "stop_triggers"
    __table_args__ = (
        CheckConstraint(
            "state IN ('PENDING','SHADOW_RECORDED','EXIT_PENDING','SUPERSEDED','FULFILLED')",
            name="ck_stop_triggers_state",
        ),
        UniqueConstraint(
            "position_id",
            "position_version",
            "risk_policy_version_id",
            name="uq_stop_triggers_position_version_policy",
        ),
        Index("ix_stop_triggers_account_state", "account_alias", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    account_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id"), nullable=False)
    position_version: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_policy_version_id: Mapped[str | None] = mapped_column(String(36))
    stop_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    trigger_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("market_snapshots.id"))
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    result_code: Mapped[str | None] = mapped_column(String(64))
    guard_evaluation_id: Mapped[str | None] = mapped_column(String(36))
    risk_event_id: Mapped[str | None] = mapped_column(ForeignKey("risk_events.id"), index=True)
    halt_scope: Mapped[str | None] = mapped_column(String(24))
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "market",
            "symbol",
            "sequence_or_hash",
            name="uq_market_snapshots_source_identity",
        ),
        CheckConstraint("market IN ('KRX','NXT')", name="ck_market_snapshots_market"),
        CheckConstraint(
            "quality IN ('NORMAL','LATE','GAP_DETECTED')",
            name="ck_market_snapshots_quality",
        ),
        Index("ix_market_snapshots_stream_event", "market", "symbol", "event_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence_or_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    source_sequence: Mapped[int | None] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    open_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    low_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    cumulative_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    best_bid_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    best_bid_quantity: Mapped[int | None] = mapped_column(BigInteger)
    best_ask_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    best_ask_quantity: Mapped[int | None] = mapped_column(BigInteger)
    trading_status: Mapped[str] = mapped_column(String(32), nullable=False)
    quality: Mapped[str] = mapped_column(String(24), nullable=False)
    recovery_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketStreamState(Base):
    __tablename__ = "market_stream_states"
    __table_args__ = (
        CheckConstraint("market IN ('KRX','NXT')", name="ck_market_stream_states_market"),
        CheckConstraint(
            "quality IN ('NORMAL','GAP_DETECTED')",
            name="ck_market_stream_states_quality",
        ),
    )

    market: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    current_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("market_snapshots.id"))
    last_sequence: Mapped[int | None] = mapped_column(Integer)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cumulative_volume: Mapped[int | None] = mapped_column(BigInteger)
    quality: Mapped[str] = mapped_column(String(24), nullable=False, default="NORMAL")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class InstrumentVenueState(Base):
    __tablename__ = "instrument_venue_states"
    __table_args__ = (
        CheckConstraint("venue IN ('KRX','NXT','SOR')", name="ck_instrument_venue_state_venue"),
        CheckConstraint(
            "eligibility_status IN ('VERIFIED','INELIGIBLE','UNKNOWN')",
            name="ck_instrument_venue_state_eligibility",
        ),
        Index("ix_instrument_venue_state_status", "venue", "eligibility_status"),
    )

    symbol: Mapped[str] = mapped_column(String(6), primary_key=True)
    venue: Mapped[str] = mapped_column(String(8), primary_key=True)
    eligibility_status: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_source: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_quote_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MarketCalendarOverride(Base):
    __tablename__ = "market_calendar_overrides"
    __table_args__ = (
        CheckConstraint(
            "state IN ('ACTIVE','REVOKED')",
            name="ck_market_calendar_override_state",
        ),
        CheckConstraint(
            "override_type = 'OPERATIONAL_CLOSURE'",
            name="ck_market_calendar_override_type",
        ),
        Index(
            "uq_market_calendar_override_active_date",
            "market_date",
            unique=True,
            sqlite_where=text("state = 'ACTIVE'"),
            postgresql_where=text("state = 'ACTIVE'"),
        ),
        Index("ix_market_calendar_override_date_created", "market_date", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    market_date: Mapped[date] = mapped_column(Date, nullable=False)
    override_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="OPERATIONAL_CLOSURE"
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revoked_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VenueSelectionEvaluation(Base):
    __tablename__ = "venue_selection_evaluations"
    __table_args__ = (
        CheckConstraint("side IN ('BUY','SELL')", name="ck_venue_selection_side"),
        CheckConstraint("order_type IN ('LIMIT','MARKET')", name="ck_venue_selection_order_type"),
        CheckConstraint("urgency IN ('NORMAL','EMERGENCY')", name="ck_venue_selection_urgency"),
        CheckConstraint(
            "selected_venue IN ('KRX','NXT','SOR','WAIT')",
            name="ck_venue_selection_selected_venue",
        ),
        CheckConstraint("state IN ('SELECTED','WAIT')", name="ck_venue_selection_state"),
        CheckConstraint("execution_stage = 'SHADOW'", name="ck_venue_selection_shadow_only"),
        CheckConstraint("NOT order_creation_allowed", name="ck_venue_selection_no_order_creation"),
        CheckConstraint(
            "nxt_eligibility_status IN ('VERIFIED','INELIGIBLE','UNKNOWN')",
            name="ck_venue_selection_nxt_eligibility",
        ),
        CheckConstraint(
            "trading_day_status IN ('OPEN','CLOSED','UNKNOWN')",
            name="ck_venue_selection_trading_day_status",
        ),
        CheckConstraint(
            "calendar_reason IN "
            "('WEEKDAY','WEEKEND','PUBLIC_HOLIDAY','LABOR_DAY',"
            "'YEAR_END_CLOSURE','CALENDAR_UNAVAILABLE','OPERATIONAL_CLOSURE')",
            name="ck_venue_selection_calendar_reason",
        ),
        Index("ix_venue_selection_symbol_created", "symbol", "created_at"),
        Index("ix_venue_selection_input_hash", "input_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(6), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    urgency: Mapped[str] = mapped_column(String(16), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_stage: Mapped[str] = mapped_column(String(16), nullable=False, default="SHADOW")
    session: Mapped[str] = mapped_column(String(32), nullable=False)
    trading_day_status: Mapped[str] = mapped_column(String(16), nullable=False)
    calendar_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    calendar_policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    calendar_override_id: Mapped[str | None] = mapped_column(
        ForeignKey("market_calendar_overrides.id")
    )
    nxt_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    nxt_eligibility_status: Mapped[str] = mapped_column(String(16), nullable=False)
    sor_supported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    selected_venue: Mapped[str] = mapped_column(String(8), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    order_creation_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    krx_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("market_snapshots.id"))
    nxt_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("market_snapshots.id"))
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MinuteBar(Base):
    __tablename__ = "minute_bars"
    __table_args__ = (
        UniqueConstraint("market", "symbol", "bucket_start", name="uq_minute_bars_stream_bucket"),
        CheckConstraint("market IN ('KRX','NXT')", name="ck_minute_bars_market"),
        Index("ix_minute_bars_stream_bucket", "market", "symbol", "bucket_start"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    low_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    close_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    turnover: Mapped[Decimal] = mapped_column(Numeric(28, 4), nullable=False, default=0)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("market_snapshots.id"), nullable=False
    )
    last_snapshot_id: Mapped[str] = mapped_column(ForeignKey("market_snapshots.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class IndicatorSnapshot(Base):
    __tablename__ = "indicator_snapshots"
    __table_args__ = (
        CheckConstraint("market IN ('KRX','NXT')", name="ck_indicator_snapshots_market"),
        Index("ix_indicator_snapshots_stream_created", "market", "symbol", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    market_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("market_snapshots.id"), nullable=False, unique=True, index=True
    )
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    calculator_version: Mapped[str] = mapped_column(String(32), nullable=False)
    vwap: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    sma5: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    session_high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    drawdown_from_high_pct: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    spread_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    price_vs_vwap_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    sma5_slope_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    relative_volume_5: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    realized_volatility_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    minute_bar_count: Mapped[int] = mapped_column(Integer, nullable=False)
    input_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketContextSnapshot(Base):
    __tablename__ = "market_context_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "market",
            "symbol",
            "source_ref",
            name="uq_market_context_source_identity",
        ),
        CheckConstraint("market IN ('KRX','NXT')", name="ck_market_context_market"),
        CheckConstraint(
            "source_tier IN ('PRIMARY','CONTRACTED')",
            name="ck_market_context_source_tier",
        ),
        CheckConstraint("quality IN ('NORMAL','INCOMPLETE')", name="ck_market_context_quality"),
        Index(
            "ix_market_context_selection",
            "market",
            "symbol",
            "quality",
            "observed_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    source_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LlmProviderProfile(Base):
    __tablename__ = "llm_provider_profiles"
    __table_args__ = (
        Index(
            "uq_llm_provider_profiles_owner_name_active",
            "owner_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "adapter_type IN ('MOCK','OPENAI_RESPONSES','ANTHROPIC_MESSAGES',"
            "'GEMINI_GENERATE_CONTENT','VERCEL_AI_GATEWAY','OPENAI_COMPATIBLE',"
            "'OLLAMA_NATIVE','OLLAMA_OPENAI_COMPATIBLE')",
            name="ck_llm_provider_profiles_adapter_type",
        ),
        CheckConstraint(
            "state IN ('DRAFT','VALIDATED','DISABLED')",
            name="ck_llm_provider_profiles_state",
        ),
        CheckConstraint(
            "data_policy IN ('EXTERNAL_CLOUD','GATEWAY','LOCAL','NONE')",
            name="ck_llm_provider_profiles_data_policy",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_template_id: Mapped[str | None] = mapped_column(String(64), index=True)
    adapter_type: Mapped[str] = mapped_column(String(40), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(500))
    credential_secret_ref: Mapped[str | None] = mapped_column(String(128))
    data_policy: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    health_status: Mapped[str] = mapped_column(String(24), nullable=False, default="UNKNOWN")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class LlmModelProfile(Base):
    __tablename__ = "llm_model_profiles"
    __table_args__ = (
        UniqueConstraint(
            "provider_profile_id", "alias", name="uq_llm_model_profiles_provider_alias"
        ),
        CheckConstraint(
            "state IN ('DRAFT','VALIDATED','DISABLED')",
            name="ck_llm_model_profiles_state",
        ),
        CheckConstraint("max_output_tokens > 0", name="ck_llm_model_profiles_max_output"),
        CheckConstraint(
            "temperature >= 0 AND temperature <= 2",
            name="ck_llm_model_profiles_temperature",
        ),
        CheckConstraint(
            "top_p IS NULL OR (top_p >= 0 AND top_p <= 1)",
            name="ck_llm_model_profiles_top_p",
        ),
        CheckConstraint(
            "reasoning_effort IS NULL OR reasoning_effort IN ('LOW','MEDIUM','HIGH')",
            name="ck_llm_model_profiles_reasoning_effort",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    provider_profile_id: Mapped[str] = mapped_column(
        ForeignKey("llm_provider_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    capabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    max_context_tokens: Mapped[int | None] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=8192)
    temperature: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    top_p: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    reasoning_effort: Mapped[str | None] = mapped_column(String(12))
    seed: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class LlmPromptProfile(Base):
    __tablename__ = "llm_prompt_profiles"
    __table_args__ = (
        UniqueConstraint("owner_id", "role", "version_number", name="uq_llm_prompt_role_number"),
        UniqueConstraint("owner_id", "role", "version_label", name="uq_llm_prompt_role_label"),
        CheckConstraint(
            "role IN ('TECHNICAL_SCOUT','NEWS_DISCLOSURE_SCOUT','MARKET_SECTOR_SCOUT',"
            "'POSITION_RISK_SCOUT','CORE')",
            name="ck_llm_prompt_profiles_role",
        ),
        CheckConstraint(
            "state IN ('DRAFT','VALIDATED','DISABLED')",
            name="ck_llm_prompt_profiles_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class LlmRoleRoute(Base):
    __tablename__ = "llm_role_routes"
    __table_args__ = (
        CheckConstraint(
            "role IN ('INTEL_COLLECTOR','EVIDENCE_VERIFIER','TECHNICAL_SCOUT',"
            "'NEWS_DISCLOSURE_SCOUT','MARKET_SECTOR_SCOUT','POSITION_RISK_SCOUT','CORE')",
            name="ck_llm_role_routes_role",
        ),
        CheckConstraint(
            "state IN ('DRAFT','VALIDATED','ACTIVE','SUPERSEDED')",
            name="ck_llm_role_routes_state",
        ),
        CheckConstraint("execution_stage = 'SHADOW'", name="ck_llm_role_routes_foundation_stage"),
        CheckConstraint(
            "fallback_policy IN ('FAIL_STOP','FAILOVER')",
            name="ck_llm_role_routes_foundation_fallback",
        ),
        CheckConstraint("timeout_ms BETWEEN 1000 AND 600000", name="ck_llm_role_routes_timeout"),
        CheckConstraint(
            "service_tier IN ('DEFAULT','PRIORITY','FLEX')",
            name="ck_llm_role_routes_service_tier",
        ),
        CheckConstraint("max_attempts = 1", name="ck_llm_role_routes_attempts"),
        CheckConstraint(
            "temperature_override IS NULL OR "
            "(temperature_override >= 0 AND temperature_override <= 2)",
            name="ck_llm_role_routes_temperature_override",
        ),
        CheckConstraint(
            "top_p_override IS NULL OR (top_p_override >= 0 AND top_p_override <= 1)",
            name="ck_llm_role_routes_top_p_override",
        ),
        CheckConstraint(
            "max_output_tokens_override IS NULL OR max_output_tokens_override > 0",
            name="ck_llm_role_routes_max_output_override",
        ),
        CheckConstraint(
            "reasoning_effort_override IS NULL OR "
            "reasoning_effort_override IN ('LOW','MEDIUM','HIGH')",
            name="ck_llm_role_routes_reasoning_effort_override",
        ),
        Index(
            "uq_llm_role_routes_active",
            "owner_id",
            "role",
            unique=True,
            sqlite_where=text("state = 'ACTIVE'"),
            postgresql_where=text("state = 'ACTIVE'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    primary_model_profile_id: Mapped[str] = mapped_column(
        ForeignKey("llm_model_profiles.id"), nullable=False, index=True
    )
    fallback_model_profile_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    fallback_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="FAIL_STOP")
    execution_stage: Mapped[str] = mapped_column(String(24), nullable=False, default="SHADOW")
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=120000)
    service_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="DEFAULT")
    web_search_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    daily_call_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    daily_cost_limit_krw: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_prompt_profiles.id"), index=True
    )
    output_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    temperature_override: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    top_p_override: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    max_output_tokens_override: Mapped[int | None] = mapped_column(Integer)
    reasoning_effort_override: Mapped[str | None] = mapped_column(String(12))
    seed_override: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __mapper_args__: ClassVar[dict[str, object]] = {
        "version_id_col": version,
        "version_id_generator": False,
    }


class LlmInvocation(Base):
    __tablename__ = "llm_invocations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('CREATED','RUNNING','SUCCEEDED','REFUSED','TIMED_OUT',"
            "'RATE_LIMITED','PROVIDER_ERROR','INVALID_OUTPUT','AMBIGUOUS')",
            name="ck_llm_invocations_state",
        ),
        CheckConstraint("retry_count >= 0", name="ck_llm_invocations_retry_count"),
        Index("ix_llm_invocations_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    stage_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    requested_provider_profile_id: Mapped[str] = mapped_column(
        ForeignKey("llm_provider_profiles.id"), nullable=False
    )
    requested_model_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_model_profiles.id")
    )
    actual_provider: Mapped[str | None] = mapped_column(String(128))
    actual_model: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    gateway_request_id: Mapped[str | None] = mapped_column(String(128))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    runtime_context_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    web_search_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_response_hash: Mapped[str | None] = mapped_column(String(64))
    model_output_json: Mapped[str | None] = mapped_column(Text)
    model_output_hash: Mapped[str | None] = mapped_column(String(64))
    model_output_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_krw: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_path_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    validation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NOT_RUN")
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('DIAGNOSTIC','TRADING_ADVISORY')",
            name="ck_agent_runs_foundation_purpose",
        ),
        CheckConstraint("market IN ('KRX','NXT')", name="ck_agent_runs_market"),
        CheckConstraint(
            "state IN ('CREATED','RUNNING','SUCCEEDED','PARTIAL','FAILED','CANCELLED')",
            name="ck_agent_runs_state",
        ),
        CheckConstraint("execution_stage = 'SHADOW'", name="ck_agent_runs_execution_stage"),
        CheckConstraint(
            "analysis_context IS NULL OR analysis_context IN ('ENTRY','POSITION')",
            name="ck_agent_runs_analysis_context",
        ),
        CheckConstraint(
            "shadow_assessment IS NULL OR shadow_assessment IN ("
            "'ENTRY_STRONG','ENTRY_SUPPORTIVE','NEUTRAL','ENTRY_ADVERSE',"
            "'HOLD_SUPPORTIVE','EXIT_RISK_ELEVATED','EXIT_RISK_HIGH','UNKNOWN')",
            name="ck_agent_runs_shadow_assessment",
        ),
        CheckConstraint(
            "fusion_state IS NULL OR fusion_state IN ("
            "'PENDING','NO_ESCALATION','ESCALATED','EXPIRED','FAILED_SAFE')",
            name="ck_agent_runs_fusion_state",
        ),
        CheckConstraint(
            "(purpose = 'DIAGNOSTIC' AND basis_decision_id IS NULL "
            "AND fusion_policy_version IS NULL AND fusion_state IS NULL) OR "
            "(purpose = 'TRADING_ADVISORY' AND basis_decision_id IS NOT NULL "
            "AND fusion_policy_version IS NOT NULL AND fusion_state IS NOT NULL)",
            name="ck_agent_runs_advisory_context",
        ),
        Index("ix_agent_runs_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="DIAGNOSTIC")
    execution_stage: Mapped[str] = mapped_column(String(24), nullable=False, default="SHADOW")
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    market_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("market_snapshots.id"), nullable=False, index=True
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dag_version: Mapped[str] = mapped_column(String(64), nullable=False)
    route_versions_json: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="CREATED")
    core_action: Mapped[str | None] = mapped_column(String(32))
    analysis_context: Mapped[str | None] = mapped_column(String(16))
    position_snapshot_json: Mapped[str | None] = mapped_column(Text)
    position_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    shadow_assessment: Mapped[str | None] = mapped_column(String(32))
    basis_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("decisions.id"), unique=True, index=True
    )
    fusion_policy_version: Mapped[str | None] = mapped_column(String(32))
    fusion_state: Mapped[str | None] = mapped_column(String(24))
    fusion_reason_code: Mapped[str | None] = mapped_column(String(64))
    fusion_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("decisions.id"), unique=True, index=True
    )
    server_input_policy_version: Mapped[str | None] = mapped_column(String(32))
    market_context_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("market_context_snapshots.id"), index=True
    )
    market_context_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentStageRun(Base):
    __tablename__ = "agent_stage_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "role", name="uq_agent_stage_runs_run_role"),
        CheckConstraint(
            "role IN ('INTEL_COLLECTOR','EVIDENCE_VERIFIER','TECHNICAL_SCOUT',"
            "'NEWS_DISCLOSURE_SCOUT','MARKET_SECTOR_SCOUT','POSITION_RISK_SCOUT',"
            "'EVIDENCE_CANDIDATE_AUDITOR','CORE')",
            name="ck_agent_stage_runs_role",
        ),
        CheckConstraint(
            "state IN ('PENDING','RUNNING','SUCCEEDED','NOT_APPLICABLE','INSUFFICIENT_DATA','CONFLICTED',"
            "'TIMED_OUT','FAILED','INVALID_OUTPUT')",
            name="ck_agent_stage_runs_state",
        ),
        CheckConstraint("fencing_token >= 0", name="ck_agent_stage_runs_fencing_token"),
        CheckConstraint("attempt_count >= 0", name="ck_agent_stage_runs_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="ck_agent_stage_runs_max_attempts"),
        Index("ix_agent_stage_runs_run_sequence", "run_id", "sequence"),
        Index(
            "ix_agent_stage_runs_claim",
            "state",
            "available_at",
            "lease_expires_at",
            "created_at",
            "sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    dependency_roles_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    route_id: Mapped[str | None] = mapped_column(ForeignKey("llm_role_routes.id"))
    invocation_id: Mapped[str | None] = mapped_column(ForeignKey("llm_invocations.id"), unique=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_json: Mapped[str | None] = mapped_column(Text)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    lease_owner_id: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        CheckConstraint("market IN ('KRX','NXT')", name="ck_evidence_items_market"),
        Index("ix_evidence_items_stream_received", "market", "symbol", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    facts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(16), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvidenceBundle(Base):
    __tablename__ = "evidence_bundles"
    __table_args__ = (
        CheckConstraint(
            "state IN ('VERIFIED','PARTIAL','CONFLICTED','REJECTED')",
            name="ck_evidence_bundles_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    contradiction_groups_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    stale_evidence_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reason_codes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
