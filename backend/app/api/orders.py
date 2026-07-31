from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context
from app.db import get_db
from app.errors import ResourceNotFoundError
from app.models import Fill, OrderEvent, TradingOrder
from app.schemas import (
    FillResponse,
    OrderDetailResponse,
    OrderEventResponse,
    OrderListResponse,
    OrderSummary,
)

router = APIRouter(prefix="/orders", tags=["orders"])


def _summary(order: TradingOrder) -> OrderSummary:
    return OrderSummary(
        id=order.id,
        order_group_id=order.order_group_id,
        parent_order_id=order.parent_order_id,
        symbol=order.symbol,
        market=order.market,
        side=order.side,
        order_type=order.order_type,
        limit_price=order.limit_price,
        requested_quantity=order.requested_quantity,
        filled_quantity=order.filled_quantity,
        cancelled_quantity=order.cancelled_quantity,
        remaining_quantity=order.remaining_quantity,
        status=order.status,
        environment=order.environment,
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        replacement_sequence=order.replacement_sequence,
        trading_date=order.trading_date,
        version=order.version,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


@router.get("", response_model=OrderListResponse)
def list_orders(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> OrderListResponse:
    orders = db.scalars(select(TradingOrder).order_by(TradingOrder.created_at.desc()).limit(limit)).all()
    return OrderListResponse(request_id=request.state.request_id, items=[_summary(order) for order in orders])


@router.get("/{order_id}", response_model=OrderDetailResponse)
def get_order(
    order_id: str,
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> OrderDetailResponse:
    order = db.get(TradingOrder, order_id)
    if order is None:
        raise ResourceNotFoundError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
    events = db.scalars(
        select(OrderEvent).where(OrderEvent.order_id == order.id).order_by(OrderEvent.occurred_at, OrderEvent.id)
    ).all()
    fills = db.scalars(select(Fill).where(Fill.order_id == order.id).order_by(Fill.filled_at, Fill.id)).all()
    return OrderDetailResponse(
        **_summary(order).model_dump(),
        request_id=request.state.request_id,
        events=[
            OrderEventResponse(
                id=event.id,
                event_type=event.event_type,
                source=event.source,
                occurred_at=event.occurred_at,
            )
            for event in events
        ],
        fills=[
            FillResponse(
                id=fill.id,
                quantity=fill.quantity,
                price=fill.price,
                fee=fill.fee,
                tax=fill.tax,
                filled_at=fill.filled_at,
            )
            for fill in fills
        ],
    )
