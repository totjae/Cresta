from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context
from app.db import get_db
from app.errors import ResourceNotFoundError
from app.models import Position
from app.schemas import PositionDetailResponse, PositionListResponse, PositionSummary

router = APIRouter(prefix="/positions", tags=["positions"])


def _summary(position: Position) -> PositionSummary:
    return PositionSummary(
        id=position.id,
        account_alias=position.account_alias,
        symbol=position.symbol,
        quantity=position.quantity,
        average_price=position.average_price,
        state=position.state,
        version=position.version,
        created_at=position.created_at,
        updated_at=position.updated_at,
    )


@router.get("", response_model=PositionListResponse)
def list_positions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> PositionListResponse:
    positions = db.scalars(
        select(Position)
        .where(Position.account_alias == "PAPER")
        .order_by(Position.updated_at.desc(), Position.id.desc())
        .limit(limit)
    ).all()
    return PositionListResponse(
        request_id=request.state.request_id,
        items=[_summary(position) for position in positions],
    )


@router.get("/{symbol}", response_model=PositionDetailResponse)
def get_position(
    symbol: str,
    request: Request,
    _: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> PositionDetailResponse:
    position = db.scalar(
        select(Position).where(Position.account_alias == "PAPER", Position.symbol == symbol)
    )
    if position is None:
        raise ResourceNotFoundError("POSITION_NOT_FOUND", "포지션을 찾을 수 없습니다.")
    return PositionDetailResponse(
        **_summary(position).model_dump(),
        request_id=request.state.request_id,
    )
