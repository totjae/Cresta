from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import AuthContext, get_auth_context, require_csrf
from app.config import Settings, get_settings
from app.db import get_db
from app.models import (
    InstrumentVenueState,
    MarketSnapshot,
    MarketStreamState,
    VenueSelectionEvaluation,
)
from app.schemas import (
    VenueSelectionDiagnosticRequest,
    VenueSelectionListResponse,
    VenueSelectionQuoteResponse,
    VenueSelectionResponse,
)
from app.venue_selection import evaluate_and_store_venue_selection

router = APIRouter(prefix="/venue-selections", tags=["venue-selections"])


def _snapshot(db: Session, market: str, symbol: str) -> MarketSnapshot | None:
    state = db.get(MarketStreamState, (market, symbol))
    if state is None or state.current_snapshot_id is None:
        return None
    return db.get(MarketSnapshot, state.current_snapshot_id)


def _response(
    request_id: str, evaluation: VenueSelectionEvaluation
) -> VenueSelectionResponse:
    input_record = json.loads(evaluation.input_json)
    quote_responses: dict[str, VenueSelectionQuoteResponse | None] = {}
    for market in ("KRX", "NXT"):
        item = input_record["quotes"].get(market)
        quote_responses[market] = (
            VenueSelectionQuoteResponse.model_validate(item) if item else None
        )
    return VenueSelectionResponse(
        request_id=request_id,
        selection_id=evaluation.id,
        policy_version=evaluation.policy_version,
        execution_stage="SHADOW",
        order_creation_allowed=False,
        environment=evaluation.environment,
        symbol=evaluation.symbol,
        side=evaluation.side,
        quantity=evaluation.quantity,
        order_type=evaluation.order_type,
        urgency=evaluation.urgency,
        session=evaluation.session,
        nxt_eligible=evaluation.nxt_eligible,
        nxt_eligibility_status=evaluation.nxt_eligibility_status,
        sor_supported=evaluation.sor_supported,
        selected_venue=evaluation.selected_venue,
        state=evaluation.state,
        reason_codes=json.loads(evaluation.reason_codes_json),
        quotes=quote_responses,
        input_hash=evaluation.input_hash,
        evaluated_at=evaluation.evaluated_at,
        created_at=evaluation.created_at,
    )


@router.get("", response_model=VenueSelectionListResponse)
def get_venue_selections(
    request: Request,
    symbol: str | None = Query(default=None, pattern=r"^[0-9]{6}$"),
    limit: int = Query(default=20, ge=1, le=100),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> VenueSelectionListResponse:
    statement = select(VenueSelectionEvaluation).where(
        VenueSelectionEvaluation.owner_id == context.user.id
    )
    if symbol is not None:
        statement = statement.where(VenueSelectionEvaluation.symbol == symbol)
    items = list(
        db.scalars(
            statement.order_by(VenueSelectionEvaluation.created_at.desc()).limit(limit)
        )
    )
    return VenueSelectionListResponse(
        request_id=request.state.request_id,
        items=[_response(request.state.request_id, item) for item in items],
    )


@router.post("/diagnostic", response_model=VenueSelectionResponse)
def post_venue_selection_diagnostic(
    payload: VenueSelectionDiagnosticRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VenueSelectionResponse:
    krx_snapshot = _snapshot(db, "KRX", payload.symbol)
    nxt_snapshot = _snapshot(db, "NXT", payload.symbol)
    nxt_state = db.get(InstrumentVenueState, (payload.symbol, "NXT"))
    now = datetime.now(UTC)
    environment = settings.environment.upper()
    evaluation = evaluate_and_store_venue_selection(
        db,
        owner=context.user,
        symbol=payload.symbol,
        side=payload.side,
        quantity=payload.quantity,
        order_type=payload.order_type,
        urgency=payload.urgency,
        environment=environment,
        nxt_eligibility_status=(
            nxt_state.eligibility_status
            if nxt_state is not None
            else "VERIFIED"
            if nxt_snapshot is not None
            else "UNKNOWN"
        ),
        sor_supported=settings.kiwoom_sor_enabled and environment == "REAL",
        krx_snapshot=krx_snapshot,
        nxt_snapshot=nxt_snapshot,
        now=now,
        max_age_seconds=settings.quote_stale_seconds,
    )
    db.commit()
    db.refresh(evaluation)
    return _response(request.state.request_id, evaluation)
