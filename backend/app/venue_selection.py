from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.market_calendar import TradingDayDecision, evaluate_krx_trading_day
from app.models import MarketSnapshot, User, VenueSelectionEvaluation

VENUE_SELECTION_POLICY_VERSION = "venue-selection-v2"
KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class VenueQuote:
    market: str
    snapshot_id: str
    bid_price: Decimal | None
    bid_quantity: int | None
    ask_price: Decimal | None
    ask_quantity: int | None
    trading_status: str
    quality: str
    event_at: datetime


@dataclass(frozen=True)
class VenueSelectionResult:
    session: str
    trading_day_status: str
    calendar_reason: str
    calendar_policy_version: str
    selected_venue: str
    state: str
    reason_codes: tuple[str, ...]
    krx_quote_valid: bool
    nxt_quote_valid: bool


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _trading_day(now: datetime) -> TradingDayDecision:
    return evaluate_krx_trading_day(now.astimezone(KST).date())


def classify_session(now: datetime, trading_day: TradingDayDecision | None = None) -> str:
    local_now = now.astimezone(KST)
    decision = trading_day or _trading_day(now)
    if decision.status != "OPEN":
        return "CLOSED"
    current = local_now.time().replace(tzinfo=None)
    if time(8, 0) <= current < time(8, 50):
        return "NXT_PRE"
    if time(8, 50) <= current < time(9, 0):
        return "KRX_OPENING_AUCTION"
    if time(9, 0) <= current < time(9, 0, 30):
        return "KRX_ONLY"
    if time(9, 0, 30) <= current < time(15, 20):
        return "DUAL_CONTINUOUS"
    if time(15, 20) <= current < time(15, 30):
        return "KRX_CLOSING_AUCTION"
    if time(15, 30) <= current < time(15, 40):
        return "NXT_AFTER_AUCTION"
    if time(15, 40) <= current < time(20, 0):
        return "NXT_AFTER"
    return "CLOSED"


def quote_from_snapshot(snapshot: MarketSnapshot | None) -> VenueQuote | None:
    if snapshot is None:
        return None
    return VenueQuote(
        market=snapshot.market,
        snapshot_id=snapshot.id,
        bid_price=snapshot.best_bid_price,
        bid_quantity=snapshot.best_bid_quantity,
        ask_price=snapshot.best_ask_price,
        ask_quantity=snapshot.best_ask_quantity,
        trading_status=snapshot.trading_status,
        quality=snapshot.quality,
        event_at=snapshot.event_at,
    )


def _quote_valid(
    quote: VenueQuote | None, *, side: str, now: datetime, max_age_seconds: int
) -> bool:
    if quote is None or quote.quality != "NORMAL" or quote.trading_status != "TRADING":
        return False
    observed = _utc(quote.event_at)
    current = _utc(now)
    if observed > current or (current - observed).total_seconds() > max_age_seconds:
        return False
    price = quote.ask_price if side == "BUY" else quote.bid_price
    quantity = quote.ask_quantity if side == "BUY" else quote.bid_quantity
    return bool(price is not None and price > 0 and quantity is not None and quantity > 0)


def _price(quote: VenueQuote, side: str) -> Decimal:
    value = quote.ask_price if side == "BUY" else quote.bid_price
    assert value is not None
    return value


def _quantity(quote: VenueQuote, side: str) -> int:
    value = quote.ask_quantity if side == "BUY" else quote.bid_quantity
    assert value is not None
    return value


def _choose_dual(
    krx: VenueQuote,
    nxt: VenueQuote,
    *,
    side: str,
    urgency: str,
) -> tuple[str, str]:
    krx_price = _price(krx, side)
    nxt_price = _price(nxt, side)
    krx_quantity = _quantity(krx, side)
    nxt_quantity = _quantity(nxt, side)
    if urgency == "EMERGENCY" and krx_quantity != nxt_quantity:
        return (
            ("KRX", "EMERGENCY_LIQUIDITY_KRX")
            if krx_quantity > nxt_quantity
            else ("NXT", "EMERGENCY_LIQUIDITY_NXT")
        )
    if krx_price != nxt_price:
        krx_better = krx_price < nxt_price if side == "BUY" else krx_price > nxt_price
        return (
            ("KRX", "BETTER_EXECUTABLE_PRICE_KRX")
            if krx_better
            else ("NXT", "BETTER_EXECUTABLE_PRICE_NXT")
        )
    if krx_quantity != nxt_quantity:
        return (
            ("KRX", "GREATER_DISPLAYED_LIQUIDITY_KRX")
            if krx_quantity > nxt_quantity
            else ("NXT", "GREATER_DISPLAYED_LIQUIDITY_NXT")
        )
    return "KRX", "DETERMINISTIC_KRX_TIE_BREAK"


def select_venue(
    *,
    side: str,
    urgency: str,
    environment: str,
    nxt_eligibility_status: str,
    sor_supported: bool,
    krx_quote: VenueQuote | None,
    nxt_quote: VenueQuote | None,
    now: datetime,
    max_age_seconds: int,
    trading_day: TradingDayDecision | None = None,
) -> VenueSelectionResult:
    day = trading_day or _trading_day(now)
    session = classify_session(now, day)
    krx_valid = _quote_valid(
        krx_quote, side=side, now=now, max_age_seconds=max_age_seconds
    )
    nxt_eligible = nxt_eligibility_status == "VERIFIED"
    nxt_valid = nxt_eligible and _quote_valid(
        nxt_quote, side=side, now=now, max_age_seconds=max_age_seconds
    )

    def result(
        selected_venue: str,
        state: str,
        reason_codes: tuple[str, ...],
    ) -> VenueSelectionResult:
        return VenueSelectionResult(
            session,
            day.status,
            day.reason,
            day.policy_version,
            selected_venue,
            state,
            reason_codes,
            krx_valid,
            nxt_valid,
        )

    if day.status == "UNKNOWN":
        return result("WAIT", "WAIT", ("CALENDAR_UNAVAILABLE",))
    if session == "CLOSED":
        return result("WAIT", "WAIT", ("SESSION_CLOSED",))
    if session in {"KRX_OPENING_AUCTION", "KRX_CLOSING_AUCTION"}:
        return result("WAIT", "WAIT", ("AUCTION_TRADING_DISABLED",))
    if session == "NXT_AFTER_AUCTION":
        return result("WAIT", "WAIT", ("NXT_AFTER_AUCTION_DISABLED",))
    if session in {"NXT_PRE", "NXT_AFTER"}:
        if nxt_eligibility_status == "UNKNOWN":
            return result("WAIT", "WAIT", ("NXT_ELIGIBILITY_UNVERIFIED",))
        if not nxt_eligible:
            return result("WAIT", "WAIT", ("NXT_SYMBOL_INELIGIBLE",))
        if not nxt_valid:
            return result("WAIT", "WAIT", ("NO_FRESH_EXECUTABLE_NXT_QUOTE",))
        reasons = ["NXT_ONLY_SESSION"]
        if environment == "MOCK":
            reasons.append("MOCK_NXT_EXECUTION_UNAVAILABLE")
        return result("NXT", "SELECTED", tuple(reasons))
    if session == "KRX_ONLY":
        if not krx_valid:
            return result("WAIT", "WAIT", ("NO_FRESH_EXECUTABLE_KRX_QUOTE",))
        return result("KRX", "SELECTED", ("KRX_ONLY_SESSION",))

    if krx_valid and nxt_valid:
        if sor_supported and environment == "REAL":
            return result("SOR", "SELECTED", ("BROKER_SOR_AVAILABLE",))
        assert krx_quote is not None and nxt_quote is not None
        selected, reason = _choose_dual(krx_quote, nxt_quote, side=side, urgency=urgency)
        return result(selected, "SELECTED", (reason,))
    if krx_valid:
        return result("KRX", "SELECTED", ("SINGLE_FRESH_VENUE", "ONLY_KRX_QUOTE_VALID"))
    if nxt_valid:
        reasons = ["SINGLE_FRESH_VENUE", "ONLY_NXT_QUOTE_VALID"]
        if environment == "MOCK":
            reasons.append("MOCK_NXT_EXECUTION_UNAVAILABLE")
        return result("NXT", "SELECTED", tuple(reasons))
    return result("WAIT", "WAIT", ("NO_FRESH_EXECUTABLE_QUOTE",))


def _quote_record(quote: VenueQuote | None, valid: bool) -> dict[str, object] | None:
    if quote is None:
        return None
    return {
        "market": quote.market,
        "snapshot_id": quote.snapshot_id,
        "bid_price": str(quote.bid_price) if quote.bid_price is not None else None,
        "bid_quantity": quote.bid_quantity,
        "ask_price": str(quote.ask_price) if quote.ask_price is not None else None,
        "ask_quantity": quote.ask_quantity,
        "event_at": _utc(quote.event_at).isoformat(),
        "valid": valid,
    }


def evaluate_and_store_venue_selection(
    db: Session,
    *,
    owner: User,
    symbol: str,
    side: str,
    quantity: int,
    order_type: str,
    urgency: str,
    environment: str,
    nxt_eligibility_status: str,
    sor_supported: bool,
    krx_snapshot: MarketSnapshot | None,
    nxt_snapshot: MarketSnapshot | None,
    now: datetime,
    max_age_seconds: int,
) -> VenueSelectionEvaluation:
    krx_quote = quote_from_snapshot(krx_snapshot)
    nxt_quote = quote_from_snapshot(nxt_snapshot)
    result = select_venue(
        side=side,
        urgency=urgency,
        environment=environment,
        nxt_eligibility_status=nxt_eligibility_status,
        sor_supported=sor_supported,
        krx_quote=krx_quote,
        nxt_quote=nxt_quote,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    input_record = {
        "schema_version": "venue-selection-input-v1",
        "policy_version": VENUE_SELECTION_POLICY_VERSION,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "order_type": order_type,
        "urgency": urgency,
        "environment": environment,
        "execution_stage": "SHADOW",
        "evaluated_at": _utc(now).isoformat(),
        "session": result.session,
        "trading_day_status": result.trading_day_status,
        "calendar_reason": result.calendar_reason,
        "calendar_policy_version": result.calendar_policy_version,
        "nxt_eligible": nxt_eligibility_status == "VERIFIED",
        "nxt_eligibility_status": nxt_eligibility_status,
        "sor_supported": sor_supported,
        "quotes": {
            "KRX": _quote_record(krx_quote, result.krx_quote_valid),
            "NXT": _quote_record(nxt_quote, result.nxt_quote_valid),
        },
    }
    input_json = json.dumps(
        input_record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    evaluation = VenueSelectionEvaluation(
        owner_id=owner.id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        urgency=urgency,
        environment=environment,
        execution_stage="SHADOW",
        session=result.session,
        trading_day_status=result.trading_day_status,
        calendar_reason=result.calendar_reason,
        calendar_policy_version=result.calendar_policy_version,
        nxt_eligible=nxt_eligibility_status == "VERIFIED",
        nxt_eligibility_status=nxt_eligibility_status,
        sor_supported=sor_supported,
        selected_venue=result.selected_venue,
        state=result.state,
        order_creation_allowed=False,
        krx_snapshot_id=krx_snapshot.id if krx_snapshot else None,
        nxt_snapshot_id=nxt_snapshot.id if nxt_snapshot else None,
        input_json=input_json,
        input_hash=hashlib.sha256(input_json.encode()).hexdigest(),
        reason_codes_json=json.dumps(list(result.reason_codes), separators=(",", ":")),
        policy_version=VENUE_SELECTION_POLICY_VERSION,
        evaluated_at=_utc(now),
    )
    db.add(evaluation)
    db.flush()
    return evaluation
