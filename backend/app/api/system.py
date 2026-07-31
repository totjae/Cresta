from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Position, TradingGate, TradingOrder
from app.schemas import (
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


@router.get("/health", response_model=SystemHealthResponse)
def system_health(
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SystemHealthResponse:
    db.scalar(select(1))
    gate = db.get(TradingGate, "PAPER")
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
        database_status="CONNECTED",
        paper_broker_status="AVAILABLE" if gate else "NOT_INITIALIZED",
        kiwoom_broker_status="NOT_CONFIGURED",
        market_data_status="NOT_STARTED",
        trading_gate=gate_response,
        counts=counts,
    )
