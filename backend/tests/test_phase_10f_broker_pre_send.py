from __future__ import annotations

import hashlib
import json
from copy import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.broker.pre_send_authority as authority_module
import app.sourced_execution as sourced_module
from app.account_authority import (
    append_account_funds_snapshot,
    append_order_capacity_snapshot,
)
from app.broker.kiwoom import KiwoomOrderAcknowledgement, KiwoomOrderRequest
from app.broker.order_sender import send_new_order_once
from app.broker.pre_send_authority import (
    PreSendStatus,
    reconcile_next_unsent_authority,
)
from app.execution_authority import ExecutionStage, order_authority_key
from app.execution_policy import SAFE_DEFAULT_POLICY
from app.execution_stage import (
    APPROVAL_ONLY_TEST_IDS,
    EXECUTION_STAGE_CATEGORY,
    MOCK_AUTOMATIC_TEST_IDS,
    ExecutionStagePayload,
    StageResolution,
    StageResolutionStatus,
    canonical_stage_json,
    stage_payload_hash,
)
from app.financial_authority import build_buy_financial_context
from app.models import (
    AccountFundsSnapshot,
    Approval,
    ConfigurationVersion,
    Decision,
    DecisionExecution,
    GuardEvaluation,
    MarketSnapshot,
    OrderCapacitySnapshot,
    OrderEvent,
    OrderIntent,
    RiskEvent,
    StopTrigger,
    TradingOrder,
    User,
)
from app.sourced_execution import execute_sourced_entry_decision
from app.stop_trigger import run_fixed_stop_triggers
from tests.test_approvals_api import _activate_risk
from tests.test_kiwoom_order_sender import FakeOrderClient, persisted_order, ready_worker
from tests.test_phase_10c2_sourced_execution import _activate_mode, _finalized
from tests.test_phase_10d_execution_authority import _capacity, _funds, _policy
from tests.test_phase_10e_mock_automatic import (
    PHASE_NOW,
    _activate_fixed_stop_policy,
    _activate_stage,
)
from tests.test_stop_trigger import _position, _set_gate, _snapshot


def _dynamic_stage(
    db: Session, admin: User, now: datetime, stage: ExecutionStage
) -> tuple[ConfigurationVersion, object]:
    ids = (
        MOCK_AUTOMATIC_TEST_IDS
        if stage is ExecutionStage.MOCK_AUTOMATIC
        else APPROVAL_ONLY_TEST_IDS
        if stage is ExecutionStage.APPROVAL_ONLY
        else ()
    )
    artifacts: dict[str, bytes] = {}
    evidence: list[dict[str, object]] = []
    for test_id in ids:
        artifact = f"phase10f:{test_id}".encode()
        reference = f"memory://phase10f/{test_id}"
        artifacts[reference] = artifact
        evidence.append(
            {
                "test_id": test_id,
                "requirement_ids": ["EXE-274"],
                "result": "PASSED",
                "code_revision": "phase-10f-test",
                "test_plan_version": "2026-08-29",
                "executed_at": now - timedelta(minutes=1),
                "valid_until": now + timedelta(hours=1),
                "freshness_contract": None,
                "evidence_ref": reference,
                "evidence_hash": hashlib.sha256(artifact).hexdigest(),
            }
        )
    payload = ExecutionStagePayload.model_validate(
        {
            "schema_version": "execution-stage-control-v1",
            "stage": stage,
            "target": "MOCK",
            "validation_policy_version": "execution-stage-validation-policy-v1",
            "safety_evidence": evidence,
            "validated_at": now - timedelta(minutes=1),
            "valid_until": now + timedelta(hours=2),
        }
    )
    encoded = canonical_stage_json(payload)
    sequence = int(
        db.scalar(
            select(func.max(ConfigurationVersion.sequence)).where(
                ConfigurationVersion.scope == "SYSTEM",
                ConfigurationVersion.target_id == "MOCK",
                ConfigurationVersion.category == EXECUTION_STAGE_CATEGORY,
            )
        )
        or 0
    ) + 1
    version = ConfigurationVersion(
        scope="SYSTEM",
        target_id="MOCK",
        category=EXECUTION_STAGE_CATEGORY,
        sequence=sequence,
        state="ACTIVE",
        payload_json=encoded,
        payload_hash=stage_payload_hash(encoded),
        reason="Phase 10F test",
        created_by=admin.id,
        validated_at=now,
        activated_at=now,
    )
    db.add(version)
    db.commit()
    return version, artifacts.__getitem__


def _replace_stage(
    db: Session,
    previous: ConfigurationVersion,
    admin: User,
    now: datetime,
    stage: ExecutionStage,
) -> object:
    previous.state = "SUPERSEDED"
    db.flush()
    _, loader = _dynamic_stage(db, admin, now, stage)
    return loader


def _automatic_order(
    client,
    db: Session,
    admin: User,
    monkeypatch,
    settings,
) -> tuple[Decision, DecisionExecution, TradingOrder, ConfigurationVersion, object, datetime]:
    decision = _finalized(client, db, admin, monkeypatch, "BUY")
    now = datetime.now(UTC)
    _activate_risk(db, admin, entry_order_amount=500_000)
    _activate_mode(db, admin, "AUTOMATIC")
    stage, loader = _dynamic_stage(db, admin, now, ExecutionStage.MOCK_AUTOMATIC)
    payload = ExecutionStagePayload.model_validate_json(stage.payload_json)
    resolution = StageResolution(StageResolutionStatus.PASS, stage, payload)
    monkeypatch.setattr(
        sourced_module, "resolve_current_execution_stage", lambda *a, **k: resolution
    )
    monkeypatch.setattr(sourced_module, "classify_session", lambda value: "KRX_ONLY")
    monkeypatch.setattr(
        sourced_module,
        "buy_pre_order_guard_rules",
        lambda *a, **k: [{"code": "BASE", "result": "PASSED"}],
    )
    snapshot = db.get(MarketSnapshot, decision.input_snapshot_id)
    assert snapshot is not None and snapshot.best_ask_price is not None
    quantity = int(Decimal(500_000) // snapshot.best_ask_price)
    policy = _policy()
    context = build_buy_financial_context(
        symbol=decision.symbol,
        price=snapshot.best_ask_price,
        quantity=quantity,
        frozen_policy=policy,
        current_policy=policy,
    )
    append_account_funds_snapshot(db, _funds(now))
    append_order_capacity_snapshot(db, _capacity(context.request, now))
    db.commit()
    execution = execute_sourced_entry_decision(
        db,
        decision=decision,
        correlation_id="phase10f-auto",
        settings=settings,
        now=now,
    )
    order = db.scalar(select(TradingOrder))
    assert execution.state == "ORDER_CREATED" and order is not None
    monkeypatch.setattr(
        authority_module,
        "buy_pre_order_guard_rules",
        lambda *a, **k: [{"code": "BASE", "result": "PASSED"}],
    )
    monkeypatch.setattr(authority_module, "classify_session", lambda value: "KRX_ONLY")
    return decision, execution, order, stage, loader, now


def test_valid_automatic_authority_commits_submitting_before_mock_call(
    client, db: Session, admin: User, monkeypatch, settings
) -> None:
    _, _, order, _, loader, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    identity = ready_worker(db)

    class InspectingClient:
        def place_order(self, request: KiwoomOrderRequest) -> KiwoomOrderAcknowledgement:
            db.expire_all()
            persisted = db.get(TradingOrder, order.id)
            assert persisted is not None and persisted.status == "SUBMITTING"
            return KiwoomOrderAcknowledgement("phase10f-auto", "KRX")

    result = send_new_order_once(
        db,
        InspectingClient(),
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    assert result.status == "ACKNOWLEDGED" and result.sent
    guard = db.scalar(
        select(GuardEvaluation).where(GuardEvaluation.phase == "BROKER_SEND")
    )
    assert guard is not None and guard.subject_type == "DECISION_EXECUTION"


@pytest.mark.parametrize("downgrade", ("STAGE", "MODE", "EXPIRY", "PAUSE"))
def test_automatic_authority_revocation_is_unsent_and_idempotent(
    client, db: Session, admin: User, monkeypatch, settings, downgrade: str
) -> None:
    decision, execution, order, stage, loader, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    if downgrade == "STAGE":
        loader = _replace_stage(db, stage, admin, now, ExecutionStage.APPROVAL_ONLY)
    elif downgrade == "MODE":
        current = db.get(ConfigurationVersion, execution.execution_policy_version_id)
        assert current is not None
        current.state = "SUPERSEDED"
        payload = SAFE_DEFAULT_POLICY.model_copy(update={"buy": "MANUAL_APPROVAL"})
        encoded = json.dumps(payload.model_dump(), separators=(",", ":"), sort_keys=True)
        db.add(
            ConfigurationVersion(
                scope="USER_DEFAULT",
                target_id=admin.id,
                category="EXECUTION_POLICY",
                sequence=2,
                state="ACTIVE",
                payload_json=encoded,
                payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                reason="Phase 10F downgrade",
                created_by=admin.id,
            )
        )
        db.commit()
    elif downgrade == "EXPIRY":
        expiry = decision.valid_until
        now = expiry.replace(tzinfo=UTC) + timedelta(microseconds=1)
    else:
        from app.emergency_stop import activate_pause_entry

        activate_pause_entry(
            db,
            user=admin,
            reason="Phase 10F",
            idempotency_key="phase10f-pause-entry-01",
            correlation_id="phase10f-pause",
            request_ip="127.0.0.1",
            user_agent="test",
        )
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    first = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    second = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    assert first.status == second.status == "INVALIDATED"
    assert broker.requests == []
    db.refresh(execution)
    assert (execution.state, execution.result_code) == (
        "FAILED_SAFE",
        "EXECUTION_AUTHORITY_REVOKED_BEFORE_SEND",
    )
    assert db.scalar(
        select(func.count()).select_from(OrderEvent).where(
            OrderEvent.order_id == order.id,
            OrderEvent.event_type == "ORDER_AUTHORITY_REVOKED_BEFORE_SEND",
        )
    ) == 1


def test_manual_approval_is_rechecked_and_invalidated(
    client, db: Session, admin: User, monkeypatch, settings
) -> None:
    _, execution, order, _, loader, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    intent = db.get(OrderIntent, order.intent_id)
    assert intent is not None
    current = db.get(ConfigurationVersion, execution.execution_policy_version_id)
    assert current is not None
    payload = SAFE_DEFAULT_POLICY.model_copy(update={"buy": "MANUAL_APPROVAL"})
    encoded = json.dumps(payload.model_dump(), separators=(",", ":"), sort_keys=True)
    current.payload_json = encoded
    current.payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
    execution.mode = "MANUAL_APPROVAL"
    approval = Approval(
        execution_id=execution.id,
        decision_id=execution.decision_id,
        user_id=admin.id,
        state="APPROVED",
        scope_snapshot_json="{}",
        expires_at=now + timedelta(minutes=1),
        order_id=order.id,
        result_code="ORDER_CREATED",
    )
    db.add(approval)
    db.flush()
    execution.approval_id = approval.id
    intent.approval_id = approval.id
    intent.authority_key = order_authority_key(
        source_type="DECISION_EXECUTION",
        source_id=execution.id,
        approval_id=approval.id,
    )
    approval.state = "INVALIDATED"
    db.commit()
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    result = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    assert result.status == "INVALIDATED" and broker.requests == []


def test_valid_manual_approval_remains_eligible_and_sends_once(
    client, db: Session, admin: User, monkeypatch, settings
) -> None:
    _, execution, order, stage, loader, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    intent = db.get(OrderIntent, order.intent_id)
    current = db.get(ConfigurationVersion, execution.execution_policy_version_id)
    assert intent is not None and current is not None
    payload = SAFE_DEFAULT_POLICY.model_copy(update={"buy": "MANUAL_APPROVAL"})
    encoded = json.dumps(payload.model_dump(), separators=(",", ":"), sort_keys=True)
    current.payload_json = encoded
    current.payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
    execution.mode = "MANUAL_APPROVAL"
    approval = Approval(
        execution_id=execution.id,
        decision_id=execution.decision_id,
        user_id=admin.id,
        state="APPROVED",
        scope_snapshot_json="{}",
        expires_at=now + timedelta(minutes=1),
        order_id=order.id,
        result_code="ORDER_CREATED",
    )
    db.add(approval)
    db.flush()
    execution.approval_id = approval.id
    intent.approval_id = approval.id
    intent.authority_key = order_authority_key(
        source_type="DECISION_EXECUTION",
        source_id=execution.id,
        approval_id=approval.id,
    )
    db.commit()
    loader = _replace_stage(db, stage, admin, now, ExecutionStage.APPROVAL_ONLY)
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("phase10f-manual", "KRX"))
    first = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    second = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    assert first.status == second.status == "ACKNOWLEDGED"
    assert first.sent and not second.sent and len(broker.requests) == 1
    db.refresh(approval)
    assert approval.state == "APPROVED"


@pytest.mark.parametrize(
    "failure",
    (
        "FINANCIAL_STALE",
        "WRONG_PRICE_CAPACITY",
        "RISK_STRICTER",
        "OTHER_UNKNOWN",
        "STAGE_PROVENANCE",
        "AUTHORITY_KEY",
        "STRICT_MOCK",
    ),
)
def test_decision_pre_send_integrity_and_current_evidence_fail_closed(
    client, db: Session, admin: User, monkeypatch, settings, failure: str
) -> None:
    _, execution, order, _, loader, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    intent = db.get(OrderIntent, order.intent_id)
    assert intent is not None
    if failure == "FINANCIAL_STALE":
        funds = db.scalar(select(AccountFundsSnapshot))
        capacity = db.scalar(select(OrderCapacitySnapshot))
        assert funds is not None and capacity is not None
        funds.received_at = now - timedelta(minutes=5)
        capacity.received_at = now - timedelta(minutes=5)
    elif failure == "WRONG_PRICE_CAPACITY":
        assert order.limit_price is not None
        order.limit_price += Decimal(1)
    elif failure == "RISK_STRICTER":
        current = db.get(ConfigurationVersion, execution.risk_policy_version_id)
        assert current is not None
        current.state = "SUPERSEDED"
        payload = _policy().model_copy(update={"max_single_order_amount": 1})
        encoded = json.dumps(
            payload.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
        )
        db.add(
            ConfigurationVersion(
                scope="USER_DEFAULT",
                target_id=admin.id,
                category="RISK_POLICY",
                sequence=2,
                state="ACTIVE",
                payload_json=encoded,
                payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                reason="Phase 10F stricter risk",
                created_by=admin.id,
            )
        )
    elif failure == "OTHER_UNKNOWN":
        conflict = persisted_order(db, idempotency_key="phase10f-other-unknown")
        conflict.status = "UNKNOWN"
    elif failure == "STAGE_PROVENANCE":
        intent.execution_stage_payload_hash = "0" * 64
    elif failure == "AUTHORITY_KEY":
        intent.authority_key = "ordauth-" + "0" * 64
    else:
        settings.kiwoom_rest_base_url = "https://api.kiwoom.com"
    db.commit()
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    result = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    assert result.status == "INVALIDATED" and broker.requests == []


def test_current_risk_policy_looser_does_not_mutate_or_block_existing_order(
    client, db: Session, admin: User, monkeypatch, settings
) -> None:
    _, execution, order, _, loader, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    original_terms = (order.limit_price, order.requested_quantity)
    current = db.get(ConfigurationVersion, execution.risk_policy_version_id)
    assert current is not None
    current.state = "SUPERSEDED"
    payload = _policy().model_copy(update={"entry_order_amount": 900_000})
    encoded = json.dumps(
        payload.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
    )
    looser = ConfigurationVersion(
        scope="USER_DEFAULT",
        target_id=admin.id,
        category="RISK_POLICY",
        sequence=2,
        state="ACTIVE",
        payload_json=encoded,
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        reason="Phase 10F looser risk",
        created_by=admin.id,
    )
    db.add(looser)
    db.commit()
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("phase10f-looser", "KRX"))
    result = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    db.refresh(order)
    event = db.scalar(
        select(OrderEvent).where(
            OrderEvent.order_id == order.id,
            OrderEvent.event_type == "ORDER_AUTHORITY_REVOKED_BEFORE_SEND",
        )
    )
    assert result.status == "ACKNOWLEDGED" and len(broker.requests) == 1, (
        event.payload_json if event else None
    )
    assert (order.limit_price, order.requested_quantity) == original_terms


@pytest.mark.parametrize(
    "corruption",
    (
        "MISSING_INTENT",
        "NULL_SOURCE",
        "UNKNOWN_SOURCE",
        "BROKEN_SOURCE_TARGET",
        "SOURCE_ID_MISMATCH",
        "LEGACY_EXECUTION",
        "BROKER_IMPORTED",
    ),
)
def test_source_integrity_corruption_is_fail_closed_before_broker_send(
    client, db: Session, admin: User, monkeypatch, settings, corruption: str
) -> None:
    _, _, order, _, loader, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    persisted_intent = db.get(OrderIntent, order.intent_id)
    assert persisted_intent is not None
    original_get = db.get
    projected_intent = copy(persisted_intent)

    if corruption == "NULL_SOURCE":
        projected_intent.source_type = None
        projected_intent.source_id = None
    elif corruption == "UNKNOWN_SOURCE":
        projected_intent.source_type = "UNKNOWN_SOURCE"
    elif corruption == "SOURCE_ID_MISMATCH":
        projected_intent.source_id = "v7exe-mismatched-source"
    elif corruption == "LEGACY_EXECUTION":
        projected_intent.source_type = "LEGACY_EXECUTION"
    elif corruption == "BROKER_IMPORTED":
        projected_intent.source_type = "BROKER_IMPORTED"

    def corrupted_get(entity, ident, *args, **kwargs):
        if entity is OrderIntent and ident == order.intent_id:
            return None if corruption == "MISSING_INTENT" else projected_intent
        if (
            corruption == "BROKEN_SOURCE_TARGET"
            and entity is DecisionExecution
            and ident == persisted_intent.decision_execution_id
        ):
            return None
        return original_get(entity, ident, *args, **kwargs)

    monkeypatch.setattr(db, "get", corrupted_get)
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    result = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    assert result.status == "INVALIDATED"
    assert broker.requests == []


def test_stage_db_retryable_keeps_created_and_commit_failure_never_calls_broker(
    client, db: Session, admin: User, monkeypatch, settings
) -> None:
    _, _, order, _, loader, now = _automatic_order(
        client, db, admin, monkeypatch, settings
    )
    identity = ready_worker(db)
    original_resolver = authority_module.resolve_current_execution_stage
    monkeypatch.setattr(
        authority_module,
        "resolve_current_execution_stage",
        lambda *a, **k: StageResolution(StageResolutionStatus.DB_RETRYABLE_FAILURE),
    )
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    retry = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=now,
        settings=settings,
        stage_evidence_loader=loader,
    )
    assert retry.status == "CREATED" and broker.requests == []
    assert db.get(TradingOrder, order.id).status == "CREATED"
    monkeypatch.setattr(
        authority_module, "resolve_current_execution_stage", original_resolver
    )
    with pytest.raises(RuntimeError, match="commit failed"):
        send_new_order_once(
            db,
            broker,
            identity,
            order.id,
            now=now,
            settings=settings,
            stage_evidence_loader=loader,
            before_submission_commit=lambda: (_ for _ in ()).throw(
                RuntimeError("commit failed")
            ),
        )
    assert db.get(TradingOrder, order.id).status == "CREATED"
    assert broker.requests == []


def test_fixed_stop_valid_send_and_lost_quantity_restores_exit_pending(
    db: Session, admin: User, settings
) -> None:
    _, loader = _activate_stage(db, admin, ExecutionStage.MOCK_AUTOMATIC)
    _activate_fixed_stop_policy(db, admin)
    _activate_risk(db, admin)
    _set_gate(db, "READY")
    _position(db, average_price=Decimal(50000), quantity=10)
    _snapshot(db, bid_price=Decimal(49000), received_at=PHASE_NOW)
    run_fixed_stop_triggers(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    )
    order = db.scalar(select(TradingOrder))
    trigger = db.scalar(select(StopTrigger))
    assert order is not None and trigger is not None
    from app.emergency_stop import activate_pause_entry

    activate_pause_entry(
        db,
        user=admin,
        reason="Phase 10F risk reduction",
        idempotency_key="phase10f-stop-pause-01",
        correlation_id="phase10f-stop-pause",
        request_ip="127.0.0.1",
        user_agent="test",
    )
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("phase10f-stop", "KRX"))
    result = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=PHASE_NOW,
        settings=settings,
        stage_evidence_loader=loader,
    )
    assert result.status == "ACKNOWLEDGED" and len(broker.requests) == 1
    stop_guard = db.scalar(
        select(GuardEvaluation).where(GuardEvaluation.phase == "BROKER_SEND")
    )
    assert stop_guard is not None
    assert stop_guard.stop_trigger_id == trigger.id and stop_guard.execution_id is None

    # A second independent fixture is covered by the recovery helper using a
    # legacy/unclassified CREATED row; it must close exactly once without send.
    unclassified = persisted_order(db, idempotency_key="phase10f-unclassified")
    intent = db.get(OrderIntent, unclassified.intent_id)
    assert intent is not None
    intent.source_type = None
    intent.source_id = None
    intent.authority_key = None
    db.commit()
    reconciled = reconcile_next_unsent_authority(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    )
    assert reconciled is not None and reconciled.status is PreSendStatus.REVOKED
    assert db.get(TradingOrder, unclassified.id).status == "INVALIDATED"
    assert reconcile_next_unsent_authority(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    ) is None
    assert db.scalar(
        select(func.count()).select_from(OrderEvent).where(
            OrderEvent.order_id == unclassified.id,
            OrderEvent.event_type == "ORDER_AUTHORITY_REVOKED_BEFORE_SEND",
        )
    ) == 1


def test_fixed_stop_lost_position_authority_invalidates_without_quantity_mutation(
    db: Session, admin: User, settings
) -> None:
    _, loader = _activate_stage(db, admin, ExecutionStage.MOCK_AUTOMATIC)
    _activate_fixed_stop_policy(db, admin)
    _activate_risk(db, admin)
    _set_gate(db, "READY")
    position = _position(db, average_price=Decimal(50000), quantity=10)
    _snapshot(db, bid_price=Decimal(49000), received_at=PHASE_NOW)
    run_fixed_stop_triggers(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    )
    order = db.scalar(select(TradingOrder))
    trigger = db.scalar(select(StopTrigger))
    assert order is not None and trigger is not None
    original_quantity = order.requested_quantity
    position.available_quantity = original_quantity - 1
    db.commit()
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    result = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=PHASE_NOW,
        settings=settings,
        stage_evidence_loader=loader,
    )
    db.refresh(order)
    db.refresh(trigger)
    assert result.status == "INVALIDATED" and broker.requests == []
    assert order.requested_quantity == order.remaining_quantity == original_quantity
    assert trigger.state == "EXIT_PENDING"
    event = db.get(RiskEvent, trigger.risk_event_id)
    assert event is not None and event.state == "ACTIVE"


@pytest.mark.parametrize(
    "failure",
    (
        "APPROVAL_ONLY",
        "SHADOW",
        "STAGE_INVALID",
        "STAGE_DB_RETRYABLE",
        "POSITION_VERSION",
        "AUTHORITY_KEY",
        "STRICT_MOCK",
    ),
)
def test_fixed_stop_pre_send_authority_failures_never_rewrite_or_send(
    db: Session, admin: User, monkeypatch, settings, failure: str
) -> None:
    stage, loader = _activate_stage(db, admin, ExecutionStage.MOCK_AUTOMATIC)
    _activate_fixed_stop_policy(db, admin)
    _activate_risk(db, admin)
    _set_gate(db, "READY")
    position = _position(db, average_price=Decimal(50000), quantity=10)
    _snapshot(db, bid_price=Decimal(49000), received_at=PHASE_NOW)
    run_fixed_stop_triggers(
        db, settings=settings, now=PHASE_NOW, stage_evidence_loader=loader
    )
    order = db.scalar(select(TradingOrder))
    trigger = db.scalar(select(StopTrigger))
    intent = db.get(OrderIntent, order.intent_id) if order else None
    assert order is not None and trigger is not None and intent is not None
    original_terms = (order.limit_price, order.requested_quantity)
    if failure in {"APPROVAL_ONLY", "SHADOW"}:
        loader = _replace_stage(
            db,
            stage,
            admin,
            PHASE_NOW,
            (
                ExecutionStage.APPROVAL_ONLY
                if failure == "APPROVAL_ONLY"
                else ExecutionStage.SHADOW
            ),
        )
    elif failure == "STAGE_INVALID":
        stage.state = "SUPERSEDED"
        invalid_json = "{}"
        db.add(
            ConfigurationVersion(
                scope="SYSTEM",
                target_id="MOCK",
                category=EXECUTION_STAGE_CATEGORY,
                sequence=2,
                state="ACTIVE",
                payload_json=invalid_json,
                payload_hash=hashlib.sha256(invalid_json.encode()).hexdigest(),
                reason="Phase 10F invalid stage",
                created_by=admin.id,
            )
        )
        db.commit()
    elif failure == "STAGE_DB_RETRYABLE":
        monkeypatch.setattr(
            authority_module,
            "resolve_current_execution_stage",
            lambda *a, **k: StageResolution(
                StageResolutionStatus.DB_RETRYABLE_FAILURE
            ),
        )
    elif failure == "POSITION_VERSION":
        position.version += 1
        db.commit()
    elif failure == "AUTHORITY_KEY":
        intent.authority_key = "ordauth-" + "0" * 64
        db.commit()
    else:
        settings.kiwoom_ws_base_url = "wss://api.kiwoom.com:10000"
    identity = ready_worker(db)
    broker = FakeOrderClient(KiwoomOrderAcknowledgement("must-not-send", "KRX"))
    result = send_new_order_once(
        db,
        broker,
        identity,
        order.id,
        now=PHASE_NOW,
        settings=settings,
        stage_evidence_loader=loader,
    )
    db.refresh(order)
    db.refresh(trigger)
    assert broker.requests == []
    assert (order.limit_price, order.requested_quantity) == original_terms
    if failure == "STAGE_DB_RETRYABLE":
        assert result.status == order.status == "CREATED"
        assert trigger.state == "FULFILLED"
    else:
        assert result.status == order.status == "INVALIDATED"
        assert trigger.state == "EXIT_PENDING"
