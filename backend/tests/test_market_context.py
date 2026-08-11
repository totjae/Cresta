from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.market_context import (
    MarketContextError,
    MarketContextInput,
    ingest_market_context,
    select_market_context,
)


def _event(
    *,
    source_ref: str = "krx-sector-1",
    observed_at: datetime | None = None,
    quality: str = "NORMAL",
    index_change_pct: str = "0.50",
) -> MarketContextInput:
    observed = observed_at or datetime.now(UTC)
    return MarketContextInput(
        market="KRX",
        symbol="005930",
        source="TEST_CONTRACTED_FEED",
        source_ref=source_ref,
        source_tier="CONTRACTED",
        quality=quality,
        index_code="KOSPI",
        index_change_pct=Decimal(index_change_pct),
        sector_code="G25",
        sector_change_pct=Decimal("0.40"),
        advancers=600,
        decliners=300,
        unchanged=100,
        observed_at=observed,
        received_at=observed + timedelta(seconds=1),
        valid_until=observed + timedelta(minutes=5),
    )


def test_market_context_is_canonical_idempotent_and_server_calculates_breadth(
    db: Session,
) -> None:
    event = _event()

    created, was_created = ingest_market_context(db, event)
    duplicate, duplicate_created = ingest_market_context(db, event)
    db.commit()

    payload = json.loads(created.payload_json)
    assert was_created is True
    assert duplicate_created is False
    assert duplicate.id == created.id
    assert payload["index"]["change_pct"] == "0.500000"
    assert payload["sector"]["change_pct"] == "0.400000"
    assert payload["breadth"]["advancer_ratio_pct"] == "60.000000"
    assert len(created.payload_hash) == 64


def test_market_context_identity_conflict_is_rejected(db: Session) -> None:
    ingest_market_context(db, _event())
    db.flush()

    with pytest.raises(MarketContextError) as error:
        ingest_market_context(db, _event(index_change_pct="-1.0"))

    assert error.value.code == "MARKET_CONTEXT_IDENTITY_CONFLICT"


def test_market_context_selection_uses_latest_valid_normal_snapshot(db: Session) -> None:
    now = datetime.now(UTC)
    stale, _ = ingest_market_context(
        db,
        _event(source_ref="stale", observed_at=now - timedelta(minutes=10)),
    )
    stale.valid_until = now - timedelta(minutes=1)
    incomplete, _ = ingest_market_context(
        db,
        _event(
            source_ref="incomplete",
            observed_at=now - timedelta(minutes=2),
            quality="INCOMPLETE",
        ),
    )
    selected, _ = ingest_market_context(
        db,
        _event(source_ref="selected", observed_at=now - timedelta(minutes=1)),
    )
    future, _ = ingest_market_context(
        db,
        _event(source_ref="future", observed_at=now + timedelta(minutes=1)),
    )
    db.commit()

    result = select_market_context(db, market="KRX", symbol="005930", now=now)

    assert result is not None
    assert result.id == selected.id
    assert result.id not in {stale.id, incomplete.id, future.id}


def test_market_context_rejects_partial_breadth_counts() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="breadth counts must be provided together"):
        MarketContextInput(
            market="KRX",
            symbol="005930",
            source="TEST",
            source_ref="partial",
            source_tier="PRIMARY",
            quality="NORMAL",
            advancers=10,
            observed_at=now,
            received_at=now,
            valid_until=now + timedelta(minutes=1),
        )
