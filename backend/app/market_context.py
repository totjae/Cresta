from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MarketContextSnapshot

MARKET_CONTEXT_SCHEMA_VERSION = "market-context-v1"
_SIX_DP = Decimal("0.000001")


class MarketContextError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MarketContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["market-context-v1"] = MARKET_CONTEXT_SCHEMA_VERSION
    market: Literal["KRX", "NXT"]
    symbol: str
    source: str = Field(min_length=1, max_length=64)
    source_ref: str = Field(min_length=1, max_length=128)
    source_tier: Literal["PRIMARY", "CONTRACTED"]
    quality: Literal["NORMAL", "INCOMPLETE"]
    index_code: str | None = Field(default=None, max_length=32)
    index_change_pct: Decimal | None = Field(default=None, ge=-100, le=100)
    sector_code: str | None = Field(default=None, max_length=64)
    sector_change_pct: Decimal | None = Field(default=None, ge=-100, le=100)
    advancers: int | None = Field(default=None, ge=0)
    decliners: int | None = Field(default=None, ge=0)
    unchanged: int | None = Field(default=None, ge=0)
    observed_at: datetime
    received_at: datetime
    valid_until: datetime

    @model_validator(mode="after")
    def validate_contract(self) -> MarketContextInput:
        if not re.fullmatch(r"\d{6}", self.symbol):
            raise ValueError("symbol must contain six digits")
        if self.observed_at > self.received_at:
            raise ValueError("observed_at must not be after received_at")
        if self.valid_until <= self.observed_at:
            raise ValueError("valid_until must be after observed_at")
        counts = (self.advancers, self.decliners, self.unchanged)
        if any(value is not None for value in counts) and not all(
            value is not None for value in counts
        ):
            raise ValueError("breadth counts must be provided together")
        return self


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(_SIX_DP, rounding=ROUND_HALF_UP), "f")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def normalized_payload(event: MarketContextInput) -> dict[str, object]:
    total = (
        event.advancers + event.decliners + event.unchanged
        if event.advancers is not None
        and event.decliners is not None
        and event.unchanged is not None
        else None
    )
    breadth_ratio = (
        Decimal(event.advancers) * Decimal(100) / Decimal(total)
        if total and event.advancers is not None
        else None
    )
    return {
        "schema_version": MARKET_CONTEXT_SCHEMA_VERSION,
        "market": event.market,
        "symbol": event.symbol,
        "source": {
            "name": event.source,
            "ref": event.source_ref,
            "tier": event.source_tier,
        },
        "quality": event.quality,
        "index": {
            "code": event.index_code,
            "change_pct": _decimal(event.index_change_pct),
        },
        "sector": {
            "code": event.sector_code,
            "change_pct": _decimal(event.sector_change_pct),
        },
        "breadth": {
            "advancers": event.advancers,
            "decliners": event.decliners,
            "unchanged": event.unchanged,
            "advancer_ratio_pct": _decimal(breadth_ratio),
        },
        "observed_at": event.observed_at.astimezone(UTC).isoformat(),
        "received_at": event.received_at.astimezone(UTC).isoformat(),
        "valid_until": event.valid_until.astimezone(UTC).isoformat(),
    }


def ingest_market_context(
    db: Session, event: MarketContextInput
) -> tuple[MarketContextSnapshot, bool]:
    payload_json = _canonical(normalized_payload(event))
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    existing = db.scalar(
        select(MarketContextSnapshot).where(
            MarketContextSnapshot.source == event.source,
            MarketContextSnapshot.market == event.market,
            MarketContextSnapshot.symbol == event.symbol,
            MarketContextSnapshot.source_ref == event.source_ref,
        )
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise MarketContextError("MARKET_CONTEXT_IDENTITY_CONFLICT")
        return existing, False
    snapshot = MarketContextSnapshot(
        market=event.market,
        symbol=event.symbol,
        source=event.source,
        source_ref=event.source_ref,
        source_tier=event.source_tier,
        quality=event.quality,
        payload_json=payload_json,
        payload_hash=payload_hash,
        observed_at=event.observed_at,
        received_at=event.received_at,
        valid_until=event.valid_until,
    )
    db.add(snapshot)
    db.flush()
    return snapshot, True


def select_market_context(
    db: Session, *, market: str, symbol: str, now: datetime
) -> MarketContextSnapshot | None:
    return db.scalar(
        select(MarketContextSnapshot)
        .where(
            MarketContextSnapshot.market == market,
            MarketContextSnapshot.symbol == symbol,
            MarketContextSnapshot.quality == "NORMAL",
            MarketContextSnapshot.observed_at <= now,
            MarketContextSnapshot.valid_until > now,
        )
        .order_by(MarketContextSnapshot.observed_at.desc(), MarketContextSnapshot.id)
    )
