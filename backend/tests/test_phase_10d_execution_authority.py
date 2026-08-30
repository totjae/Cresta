from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

import app.approvals as approvals_module
import app.sourced_execution as sourced_module
from app.account_authority import (
    append_account_funds_snapshot,
    append_order_capacity_snapshot,
)
from app.approvals import ApprovalError, _commit_approval_versioned_mutation, approve
from app.auth.crypto import hash_password, token_hash
from app.broker.kiwoom import (
    AccountFundsSnapshotData,
    OrderCapacityRequest,
    OrderCapacitySnapshotData,
)
from app.execution_authority import ExecutionStage, order_authority_key
from app.execution_stage import StageResolution, StageResolutionStatus
from app.financial_authority import (
    build_buy_financial_context,
    financial_guard_rules,
)
from app.models import (
    Approval,
    ConfigurationVersion,
    GuardEvaluation,
    MarketSnapshot,
    OrderIntent,
    ReauthProof,
    TradingOrder,
    User,
)
from app.schemas import ApprovalApproveRequest, ApprovalRejectRequest, RiskPolicyPayload
from app.sourced_execution import execute_sourced_entry_decision
from tests.test_approvals_api import _activate_risk
from tests.test_phase_10c2_sourced_execution import (
    _activate_mode,
    _finalized,
)


def _policy(**updates: object) -> RiskPolicyPayload:
    return RiskPolicyPayload(
        entry_order_amount=500_000,
        max_single_order_amount=1_000_000,
        max_position_amount_per_symbol=1_000_000,
        max_total_position_amount=3_000_000,
        max_open_positions=3,
        max_daily_entries=5,
        fixed_stop_loss_pct="-2.0",
        quote_stale_seconds=2,
        max_spread_pct="0.30",
        max_price_deviation_pct="0.50",
        **updates,
    )


def _funds(now: datetime, amount: int | None = 1_000_000) -> AccountFundsSnapshotData:
    return AccountFundsSnapshotData(
        "KIWOOM", "KIWOOM_MOCK_PRIMARY", "MOCK", "kt00001", "3",
        1_000_000, amount, 1_000_000, None, None, None, None, None, None, None, None, now,
    )


def _capacity(
    request: OrderCapacityRequest,
    now: datetime,
    *,
    cash: int | None = 1_000_000,
    amount100: int | None = 1_000_000,
    quantity100: int | None = 10_000,
) -> OrderCapacitySnapshotData:
    return OrderCapacitySnapshotData(
        "KIWOOM", "KIWOOM_MOCK_PRIMARY", "MOCK", "kt00010", request.symbol,
        request.side, "2", request.requested_price, request.io_amount,
        request.requested_quantity, request.expected_buy_price, cash, 1_000_000,
        1_000_000, 1_000_000, 1_000_000,
        9_999_999, 99_999, 9_999_999, 99_999, 9_999_999, 99_999,
        9_999_999, 99_999, 9_999_999, 99_999, 9_999_999, 99_999,
        amount100, quantity100, now,
    )


def test_risk_policy_financial_defaults_ranges_and_quote_independence() -> None:
    policy = _policy()
    assert (policy.account_funds_stale_seconds, policy.order_capacity_stale_seconds) == (30, 10)
    assert _policy(account_funds_stale_seconds=1, order_capacity_stale_seconds=60).quote_stale_seconds == 2
    assert _policy(account_funds_stale_seconds=300, order_capacity_stale_seconds=1)
    for field, value in (("account_funds_stale_seconds", 0), ("account_funds_stale_seconds", 301), ("order_capacity_stale_seconds", 0), ("order_capacity_stale_seconds", 61)):
        with pytest.raises(ValidationError):
            _policy(**{field: value})


def test_approval_action_contract_requires_cas_and_bound_reauth() -> None:
    common = {"schema_version": "1.0", "idempotency_key": "phase10d-contract"}
    with pytest.raises(ValidationError):
        ApprovalApproveRequest.model_validate(common)
    with pytest.raises(ValidationError):
        ApprovalApproveRequest.model_validate({**common, "expected_version": 1})
    assert ApprovalApproveRequest.model_validate(
        {**common, "expected_version": 1, "reauth_proof": "proof-phase10d-01"}
    ).expected_version == 1
    with pytest.raises(ValidationError):
        ApprovalRejectRequest.model_validate(common)
    assert ApprovalRejectRequest.model_validate(
        {**common, "expected_version": 1}
    ).expected_version == 1


def test_financial_ttl_exact_context_cash_only_and_future_timestamp(db: Session) -> None:
    now = datetime.now(UTC)
    frozen = _policy(account_funds_stale_seconds=30, order_capacity_stale_seconds=10)
    current = _policy(account_funds_stale_seconds=10, order_capacity_stale_seconds=60)
    context = build_buy_financial_context(
        symbol="005930", price=Decimal(100), quantity=5,
        frozen_policy=frozen, current_policy=current,
    )
    assert (context.funds_ttl, context.capacity_ttl) == (10, 10)
    append_account_funds_snapshot(db, _funds(now - timedelta(seconds=10)))
    append_order_capacity_snapshot(db, _capacity(context.request, now - timedelta(seconds=10)))
    rules = financial_guard_rules(
        db, context=context, now=now, frozen_risk_policy_id="frozen",
        current_risk_policy_id="current", frozen_policy=frozen, current_policy=current,
    )
    assert all(item["result"] == "PASSED" for item in rules)

    wrong = OrderCapacityRequest("005930", "BUY", 101, requested_quantity=5, expected_buy_price=101)
    append_order_capacity_snapshot(db, _capacity(wrong, now, cash=99_999_999))
    limited = _capacity(context.request, now + timedelta(seconds=1), cash=99_999_999)
    append_order_capacity_snapshot(db, limited)
    future_rules = financial_guard_rules(
        db, context=context, now=now, frozen_risk_policy_id="frozen",
        current_risk_policy_id="current", frozen_policy=frozen, current_policy=current,
    )
    assert next(item for item in future_rules if item["code"] == "ORDER_CAPACITY_FRESH")["result"] == "BLOCKED"


def test_leveraged_capacity_never_replaces_100_percent_authority(db: Session) -> None:
    now = datetime.now(UTC)
    policy = _policy()
    context = build_buy_financial_context(
        symbol="005930", price=Decimal(100), quantity=5,
        frozen_policy=policy, current_policy=policy,
    )
    append_account_funds_snapshot(db, _funds(now))
    append_order_capacity_snapshot(db, _capacity(context.request, now, amount100=499, quantity100=4))
    rules = financial_guard_rules(
        db, context=context, now=now, frozen_risk_policy_id="risk",
        current_risk_policy_id="risk", frozen_policy=policy, current_policy=policy,
    )
    assert next(item for item in rules if item["code"] == "MARGIN_100_AMOUNT_SUFFICIENT")["result"] == "BLOCKED"
    assert next(item for item in rules if item["code"] == "MARGIN_100_QUANTITY_SUFFICIENT")["result"] == "BLOCKED"


def test_order_authority_key_is_stable_and_excludes_order_terms() -> None:
    first = order_authority_key(source_type="DECISION_EXECUTION", source_id="execution", approval_id="approval")
    second = order_authority_key(source_type="DECISION_EXECUTION", source_id="execution", approval_id="approval")
    automatic = order_authority_key(source_type="DECISION_EXECUTION", source_id="execution", approval_id=None)
    assert first == second and first.startswith("ordauth-") and len(first) == 72
    assert first != automatic


def _stage_resolution(db: Session, admin: User, now: datetime, stage: ExecutionStage) -> StageResolution:
    version = ConfigurationVersion(
        scope="SYSTEM", target_id="MOCK", category="V7_ENTRY_EXECUTION_STAGE",
        sequence=1, state="ACTIVE", payload_json="{}", payload_hash="a" * 64,
        reason="Phase 10D test", created_by=admin.id,
    )
    db.add(version)
    db.commit()
    return StageResolution(
        StageResolutionStatus.PASS,
        version=version,
        payload=SimpleNamespace(stage=stage),  # type: ignore[arg-type]
    )


def test_sourced_manual_approval_to_one_authoritative_created_order(
    client, db: Session, admin: User, monkeypatch, settings
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    now = datetime.now(UTC)
    _activate_risk(db, admin, entry_order_amount=500_000)
    _activate_mode(db, admin, "MANUAL_APPROVAL")
    resolution = _stage_resolution(db, admin, now, ExecutionStage.APPROVAL_ONLY)
    monkeypatch.setattr(sourced_module, "resolve_current_execution_stage", lambda *a, **k: resolution)
    monkeypatch.setattr(sourced_module, "classify_session", lambda value: "KRX_ONLY")
    monkeypatch.setattr(sourced_module, "buy_pre_order_guard_rules", lambda *a, **k: [{"code": "BASE", "result": "PASSED"}])
    snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
    assert snapshot is not None and snapshot.best_ask_price is not None
    quantity = int(Decimal(500_000) // snapshot.best_ask_price)
    policy = _policy()
    context = build_buy_financial_context(
        symbol=decision.symbol, price=snapshot.best_ask_price, quantity=quantity,
        frozen_policy=policy, current_policy=policy,
    )
    append_account_funds_snapshot(db, _funds(now))
    append_order_capacity_snapshot(db, _capacity(context.request, now))
    db.commit()

    execution = execute_sourced_entry_decision(
        db, decision=decision, correlation_id="phase10d", settings=settings, now=now
    )
    assert execution.state == "APPROVAL_PENDING"
    approval = db.get(Approval, execution.approval_id)
    assert approval is not None and approval.state == "PENDING"
    raw_proof = "phase-10d-proof-token"
    proof = ReauthProof(
        proof_hash=token_hash(raw_proof), user_id=admin.id,
        target_action="APPROVE_ORDER", target_id=f"{approval.id}:1",
        expires_at=now + timedelta(minutes=5),
    )
    db.add(proof)
    db.commit()
    monkeypatch.setattr(approvals_module, "resolve_current_execution_stage", lambda *a, **k: resolution)
    monkeypatch.setattr(
        __import__("app.decision_execution", fromlist=["buy_pre_order_guard_rules"]),
        "buy_pre_order_guard_rules",
        lambda *a, **k: [{"code": "BASE", "result": "PASSED"}],
    )
    other = User(login_id="phase10d-other", password_hash=hash_password("test-password"))
    db.add(other)
    db.commit()
    with pytest.raises(ApprovalError, match="APPROVAL_OWNER_MISMATCH"):
        approve(
            db, approval_id=approval.id, user=other, settings=settings,
            correlation_id="wrong-owner", idempotency_key="phase10d-owner-key",
            expected_version=1, reauth_proof=raw_proof, now=now,
        )
    with pytest.raises(ApprovalError, match="APPROVAL_VERSION_CONFLICT"):
        approve(
            db, approval_id=approval.id, user=admin, settings=settings,
            correlation_id="wrong-version", idempotency_key="phase10d-version-key",
            expected_version=2, reauth_proof=raw_proof, now=now,
        )
    with pytest.raises(ApprovalError, match="REAUTH_PROOF_INVALID"):
        approve(
            db, approval_id=approval.id, user=admin, settings=settings,
            correlation_id="wrong-proof", idempotency_key="phase10d-proof-key",
            expected_version=1, reauth_proof="wrong-proof-token", now=now,
        )
    assert db.scalar(select(func.count()).select_from(GuardEvaluation).where(
        GuardEvaluation.phase == "APPROVAL_REVALIDATION"
    )) == 0

    def injected_failure() -> None:
        raise RuntimeError("injected after order flush")

    with pytest.raises(RuntimeError, match="injected after order flush"):
        approve(
            db, approval_id=approval.id, user=admin, settings=settings,
            correlation_id="rollback-injection", idempotency_key="phase10d-order-key",
            expected_version=1, reauth_proof=raw_proof, now=now,
            before_commit=injected_failure,
        )
    db.rollback()
    db.refresh(approval)
    db.refresh(proof)
    db.refresh(execution)
    assert approval.state == "PENDING" and approval.order_id is None
    assert proof.consumed_at is None
    assert execution.state == "APPROVAL_PENDING"
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
    assert db.scalar(select(func.count()).select_from(GuardEvaluation).where(
        GuardEvaluation.phase == "APPROVAL_REVALIDATION"
    )) == 0

    approved = approve(
        db, approval_id=approval.id, user=admin, settings=settings,
        correlation_id="phase10d-approve", idempotency_key="phase10d-order-key",
        expected_version=1, reauth_proof=raw_proof, now=now,
    )
    assert approved.state == "APPROVED"
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 1
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 1
    order = db.scalar(select(TradingOrder))
    intent = db.get(OrderIntent, order.intent_id)
    assert order.status == "CREATED" and intent.authority_key.startswith("ordauth-")
    assert intent.source_type == "DECISION_EXECUTION"
    assert intent.guard_evaluation_id == execution.guard_evaluation_id
    assert db.scalar(select(func.count()).select_from(GuardEvaluation).where(GuardEvaluation.phase == "APPROVAL_REVALIDATION")) == 1
    db.refresh(proof)
    assert proof.consumed_at is not None


@pytest.mark.parametrize(
    ("stage", "mode", "state", "code", "approval_count", "order_count"),
    (
        (ExecutionStage.APPROVAL_ONLY, "AUTOMATIC", "FAILED_SAFE", "AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY", 0, 0),
        (ExecutionStage.MOCK_AUTOMATIC, "MANUAL_APPROVAL", "APPROVAL_PENDING", "APPROVAL_PENDING", 1, 0),
        (ExecutionStage.MOCK_AUTOMATIC, "AUTOMATIC", "ORDER_CREATED", "ORDER_CREATED", 0, 1),
    ),
)
def test_sourced_stage_action_matrix_keeps_automatic_closed(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings,
    stage: ExecutionStage,
    mode: str,
    state: str,
    code: str,
    approval_count: int,
    order_count: int,
) -> None:
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    now = datetime.now(UTC)
    _activate_risk(db, admin, entry_order_amount=500_000)
    _activate_mode(db, admin, mode)
    resolution = _stage_resolution(db, admin, now, stage)
    monkeypatch.setattr(sourced_module, "resolve_current_execution_stage", lambda *a, **k: resolution)
    monkeypatch.setattr(sourced_module, "classify_session", lambda value: "KRX_ONLY")
    monkeypatch.setattr(sourced_module, "buy_pre_order_guard_rules", lambda *a, **k: [{"code": "BASE", "result": "PASSED"}])
    snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
    assert snapshot is not None and snapshot.best_ask_price is not None
    quantity = int(Decimal(500_000) // snapshot.best_ask_price)
    policy = _policy()
    context = build_buy_financial_context(
        symbol=decision.symbol, price=snapshot.best_ask_price, quantity=quantity,
        frozen_policy=policy, current_policy=policy,
    )
    append_account_funds_snapshot(db, _funds(now))
    append_order_capacity_snapshot(db, _capacity(context.request, now))
    db.commit()
    execution = execute_sourced_entry_decision(
        db, decision=decision, correlation_id=f"matrix-{stage}-{mode}", settings=settings, now=now
    )
    assert (execution.state, execution.result_code) == (state, code)
    assert db.scalar(select(func.count()).select_from(Approval)) == approval_count
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == order_count


def test_sourced_owner_version_and_rollback_are_fail_closed() -> None:
    # The service-level branches are covered in the full sourced transaction test;
    # this assertion keeps the externally visible conflict taxonomy stable.
    assert ApprovalError("APPROVAL_OWNER_MISMATCH", 403).status_code == 403
    assert ApprovalError("APPROVAL_VERSION_CONFLICT", 409).code == "APPROVAL_VERSION_CONFLICT"


def test_approval_cas_normalizer_rolls_back_before_canonical_mapping() -> None:
    state = SimpleNamespace(rolled_back=False)

    class StaleApprovalSession:
        def flush(self, objects) -> None:
            assert len(objects) == 1
            raise StaleDataError("approval optimistic conflict")

        def rollback(self) -> None:
            state.rolled_back = True

        def commit(self) -> None:
            raise AssertionError("commit must not run after stale Approval flush")

    with pytest.raises(ApprovalError) as error:
        _commit_approval_versioned_mutation(
            StaleApprovalSession(), SimpleNamespace(id="approval-cas")
        )
    assert state.rolled_back
    assert (error.value.code, error.value.status_code) == (
        "APPROVAL_VERSION_CONFLICT",
        409,
    )


def test_approval_cas_normalizer_does_not_relabel_unrelated_db_failure() -> None:
    state = SimpleNamespace(committed=False, rolled_back=False)
    retryable = OperationalError("COMMIT", {}, RuntimeError("connection lost"))

    class RetryableFailureSession:
        def flush(self, objects) -> None:
            assert len(objects) == 1

        def rollback(self) -> None:
            state.rolled_back = True

        def commit(self) -> None:
            state.committed = True
            raise retryable

    with pytest.raises(OperationalError) as error:
        _commit_approval_versioned_mutation(
            RetryableFailureSession(), SimpleNamespace(id="approval-db-retry")
        )
    assert error.value is retryable
    assert state.committed and not state.rolled_back
