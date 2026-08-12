from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis_scheduler_state import get_scheduler_status
from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.broker.mock_order_test import create_mock_order_test
from app.broker.worker_state import get_broker_status
from app.config import Settings, get_settings
from app.db import get_db
from app.emergency_stop import active_pause_entry
from app.models import (
    MarketStreamState,
    Position,
    RiskEvent,
    StopTrigger,
    TradingGate,
    TradingOrder,
)
from app.schemas import (
    AnalysisSchedulerStatusResponse,
    BrokerStatusResponse,
    MockOrderTestRequest,
    MockOrderTestResponse,
    RiskEventListResponse,
    RiskEventResponse,
    StopTriggerListResponse,
    StopTriggerResponse,
    SystemCountResponse,
    SystemHealthResponse,
    TradingGateResponse,
)

router = APIRouter(prefix="/system", tags=["system"])

ACTIVE_ORDER_STATES = {
    "CREATED",
    "VALIDATING",
    "SUBMITTING",
    "ACKNOWLEDGED",
    "OPEN",
    "PARTIALLY_FILLED",
    "CANCEL_PENDING",
    "REPLACE_PENDING",
    "UNKNOWN",
    "RECONCILING",
}


def _market_data_status(db: Session, settings: Settings) -> str:
    states = db.scalars(select(MarketStreamState)).all()
    if not states:
        return "NOT_STARTED"
    if any(state.quality == "GAP_DETECTED" for state in states):
        return "DEGRADED"
    received = [state.last_received_at for state in states if state.last_received_at is not None]
    if not received:
        return "NOT_STARTED"
    latest = max(value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC) for value in received)
    age = (datetime.now(UTC) - latest).total_seconds()
    return "AVAILABLE" if age <= settings.quote_stale_seconds else "STALE"


@router.get("/health", response_model=SystemHealthResponse)
def system_health(
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SystemHealthResponse:
    db.scalar(select(1))
    gate = db.get(TradingGate, "PAPER")
    pause_entry = active_pause_entry(db)
    scheduler = get_scheduler_status(db)
    counts = SystemCountResponse(
        orders=int(
            db.scalar(
                select(func.count(TradingOrder.id)).where(
                    TradingOrder.account_alias == "PAPER"
                )
            )
            or 0
        ),
        active_orders=int(
            db.scalar(
                select(func.count(TradingOrder.id)).where(
                    TradingOrder.account_alias == "PAPER",
                    TradingOrder.status.in_(ACTIVE_ORDER_STATES),
                )
            )
            or 0
        ),
        open_positions=int(
            db.scalar(
                select(func.count(Position.id)).where(
                    Position.account_alias == "PAPER",
                    Position.state == "OPEN",
                    Position.quantity > 0,
                )
            )
            or 0
        ),
    )
    gate_response = None
    if gate is not None:
        gate_response = TradingGateResponse(
            account_alias=gate.account_alias,
            environment=gate.environment,
            status=gate.status,
            reason=gate.reason,
            version=gate.version,
            updated_at=gate.updated_at,
        )
    return SystemHealthResponse(
        request_id=request.state.request_id,
        environment=settings.environment.upper(),
        live_trading_enabled=settings.live_trading_enabled,
        execution_stage=settings.execution_stage,
        decision_execution_status="SHADOW_ONLY",
        buy_execution_ready=False,
        buy_execution_block_reason=(
            "EMERGENCY_STOP_ACTIVE" if pause_entry is not None else "ORDER_SIZE_NOT_CONFIGURED"
        ),
        pause_entry_active=pause_entry is not None,
        analysis_scheduler=AnalysisSchedulerStatusResponse(
            state=scheduler.state,
            lease_valid=scheduler.lease_valid,
            last_heartbeat_at=scheduler.last_heartbeat_at,
            last_tick_at=scheduler.last_tick_at,
            last_completed_at=scheduler.last_completed_at,
            next_due_at=scheduler.next_due_at,
            processed_count=scheduler.processed_count,
            decision_count=scheduler.decision_count,
            skipped_count=scheduler.skipped_count,
            failed_count=scheduler.failed_count,
            last_error_code=scheduler.last_error_code,
        ),
        database_status="CONNECTED",
        paper_broker_status="AVAILABLE" if gate else "NOT_INITIALIZED",
        kiwoom_broker_status=settings.kiwoom_configuration_status(),
        market_data_status=_market_data_status(db, settings),
        trading_gate=gate_response,
        counts=counts,
    )


@router.get("/broker", response_model=BrokerStatusResponse)
def broker_status(
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> BrokerStatusResponse:
    status = get_broker_status(db)
    return BrokerStatusResponse(
        request_id=request.state.request_id,
        state=status.state,
        gate_status=status.gate_status,
        gate_reason=status.gate_reason,
        fencing_token=status.fencing_token,
        lease_valid=status.lease_valid,
        websocket_connected=status.websocket_connected,
        subscriptions_ready=status.subscriptions_ready,
        last_heartbeat_at=status.last_heartbeat_at,
        last_reconciliation_at=status.last_reconciliation_at,
        last_reconciliation_run_id=status.last_reconciliation_run_id,
        last_error_code=status.last_error_code,
    )


@router.post("/broker/mock-order-test", response_model=MockOrderTestResponse)
def mock_order_test(
    payload: MockOrderTestRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MockOrderTestResponse:
    request_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    order = create_mock_order_test(
        db,
        user=context.user,
        payload=payload,
        correlation_id=request.state.request_id,
        request_ip=request_ip,
        user_agent=user_agent,
        settings=settings,
    )
    return MockOrderTestResponse(
        request_id=request.state.request_id,
        order_id=order.id,
        status=order.status,
        symbol=order.symbol,
    )


@router.get("/stop-triggers", response_model=StopTriggerListResponse)
def list_stop_triggers(
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> StopTriggerListResponse:
    triggers = list(
        db.scalars(
            select(StopTrigger)
            .order_by(StopTrigger.created_at.desc())
            .limit(100)
        )
    )
    items = [
        StopTriggerResponse(
            trigger_id=trigger.id,
            account_alias=trigger.account_alias,
            symbol=trigger.symbol,
            market=trigger.market,
            position_id=trigger.position_id,
            position_version=trigger.position_version,
            risk_policy_version_id=trigger.risk_policy_version_id,
            stop_price=trigger.stop_price,
            trigger_price=trigger.trigger_price,
            snapshot_id=trigger.snapshot_id,
            state=trigger.state,
            result_code=trigger.result_code,
            guard_evaluation_id=trigger.guard_evaluation_id,
            risk_event_id=trigger.risk_event_id,
            halt_scope=trigger.halt_scope,
            version=trigger.version,
            created_at=trigger.created_at,
            updated_at=trigger.updated_at,
        )
        for trigger in triggers
    ]
    return StopTriggerListResponse(
        request_id=request.state.request_id,
        items=items,
    )


@router.get("/risk-events", response_model=RiskEventListResponse)
def list_risk_events(
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> RiskEventListResponse:
    events = list(
        db.scalars(
            select(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(100)
        )
    )
    items = [
        RiskEventResponse(
            event_id=event.id,
            scope=event.scope,
            rule_code=event.rule_code,
            severity=event.severity,
            state=event.state,
            account_alias=event.account_alias,
            symbol=event.symbol,
            input_snapshot_id=event.input_snapshot_id,
            resolution=event.resolution,
            resolved_at=event.resolved_at,
            correlation_id=event.correlation_id,
            created_at=event.created_at,
            updated_at=event.updated_at,
        )
        for event in events
    ]
    return RiskEventListResponse(
        request_id=request.state.request_id,
        items=items,
    )
