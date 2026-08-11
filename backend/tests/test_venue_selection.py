from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Approval, OrderIntent, TradingOrder, VenueSelectionEvaluation
from app.venue_selection import VenueQuote, classify_session, select_venue
from app.watch import QuoteEvent, ingest_quote
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET

KST = ZoneInfo("Asia/Seoul")


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 8, 12, hour, minute, second, tzinfo=KST)


def _quote(
    market: str,
    *,
    now: datetime,
    bid: str = "70000",
    ask: str = "70100",
    bid_quantity: int = 100,
    ask_quantity: int = 100,
    age_seconds: int = 0,
) -> VenueQuote:
    return VenueQuote(
        market=market,
        snapshot_id=f"{market}-snapshot",
        bid_price=Decimal(bid),
        bid_quantity=bid_quantity,
        ask_price=Decimal(ask),
        ask_quantity=ask_quantity,
        trading_status="TRADING",
        quality="NORMAL",
        event_at=now - timedelta(seconds=age_seconds),
    )


def test_session_boundaries_follow_nxt_and_krx_windows() -> None:
    assert classify_session(_at(7, 59, 59)) == "CLOSED"
    assert classify_session(_at(8, 0)) == "NXT_PRE"
    assert classify_session(_at(8, 49, 59)) == "NXT_PRE"
    assert classify_session(_at(8, 50)) == "KRX_OPENING_AUCTION"
    assert classify_session(_at(9, 0)) == "KRX_ONLY"
    assert classify_session(_at(9, 0, 30)) == "DUAL_CONTINUOUS"
    assert classify_session(_at(15, 20)) == "KRX_CLOSING_AUCTION"
    assert classify_session(_at(15, 30)) == "NXT_AFTER_AUCTION"
    assert classify_session(_at(15, 40)) == "NXT_AFTER"
    assert classify_session(_at(20, 0)) == "CLOSED"
    assert classify_session(datetime(2026, 8, 15, 10, 0, tzinfo=KST)) == "CLOSED"


def test_nxt_only_session_requires_eligibility_and_fresh_quote() -> None:
    now = _at(8, 10)
    quote = _quote("NXT", now=now)
    selected = select_venue(
        side="BUY",
        urgency="NORMAL",
        environment="MOCK",
        nxt_eligibility_status="VERIFIED",
        sor_supported=False,
        krx_quote=None,
        nxt_quote=quote,
        now=now,
        max_age_seconds=2,
    )
    assert selected.selected_venue == "NXT"
    assert selected.reason_codes == (
        "NXT_ONLY_SESSION",
        "MOCK_NXT_EXECUTION_UNAVAILABLE",
    )

    ineligible = select_venue(
        side="BUY",
        urgency="NORMAL",
        environment="MOCK",
        nxt_eligibility_status="INELIGIBLE",
        sor_supported=False,
        krx_quote=None,
        nxt_quote=quote,
        now=now,
        max_age_seconds=2,
    )
    assert ineligible.selected_venue == "WAIT"
    assert ineligible.reason_codes == ("NXT_SYMBOL_INELIGIBLE",)

    unknown = select_venue(
        side="BUY",
        urgency="NORMAL",
        environment="MOCK",
        nxt_eligibility_status="UNKNOWN",
        sor_supported=False,
        krx_quote=None,
        nxt_quote=None,
        now=now,
        max_age_seconds=2,
    )
    assert unknown.selected_venue == "WAIT"
    assert unknown.reason_codes == ("NXT_ELIGIBILITY_UNVERIFIED",)


def test_dual_session_uses_price_for_normal_and_liquidity_for_emergency() -> None:
    now = _at(10, 0)
    krx = _quote("KRX", now=now, ask="70200", ask_quantity=1000)
    nxt = _quote("NXT", now=now, ask="70100", ask_quantity=50)

    normal = select_venue(
        side="BUY",
        urgency="NORMAL",
        environment="MOCK",
        nxt_eligibility_status="VERIFIED",
        sor_supported=False,
        krx_quote=krx,
        nxt_quote=nxt,
        now=now,
        max_age_seconds=2,
    )
    assert normal.selected_venue == "NXT"
    assert normal.reason_codes == ("BETTER_EXECUTABLE_PRICE_NXT",)

    emergency = select_venue(
        side="BUY",
        urgency="EMERGENCY",
        environment="MOCK",
        nxt_eligibility_status="VERIFIED",
        sor_supported=False,
        krx_quote=krx,
        nxt_quote=nxt,
        now=now,
        max_age_seconds=2,
    )
    assert emergency.selected_venue == "KRX"
    assert emergency.reason_codes == ("EMERGENCY_LIQUIDITY_KRX",)


def test_dual_session_uses_sor_only_when_real_and_supported() -> None:
    now = _at(10, 0)
    result = select_venue(
        side="SELL",
        urgency="NORMAL",
        environment="REAL",
        nxt_eligibility_status="VERIFIED",
        sor_supported=True,
        krx_quote=_quote("KRX", now=now),
        nxt_quote=_quote("NXT", now=now),
        now=now,
        max_age_seconds=2,
    )
    assert result.selected_venue == "SOR"
    assert result.reason_codes == ("BROKER_SOR_AVAILABLE",)


def test_stale_quote_is_excluded_and_no_fresh_quote_waits() -> None:
    now = _at(10, 0)
    krx = _quote("KRX", now=now, age_seconds=3)
    nxt = _quote("NXT", now=now)
    single = select_venue(
        side="SELL",
        urgency="NORMAL",
        environment="MOCK",
        nxt_eligibility_status="VERIFIED",
        sor_supported=False,
        krx_quote=krx,
        nxt_quote=nxt,
        now=now,
        max_age_seconds=2,
    )
    assert single.selected_venue == "NXT"
    assert "SINGLE_FRESH_VENUE" in single.reason_codes

    none = select_venue(
        side="SELL",
        urgency="NORMAL",
        environment="MOCK",
        nxt_eligibility_status="VERIFIED",
        sor_supported=False,
        krx_quote=krx,
        nxt_quote=_quote("NXT", now=now, age_seconds=3),
        now=now,
        max_age_seconds=2,
    )
    assert none.selected_venue == "WAIT"
    assert none.reason_codes == ("NO_FRESH_EXECUTABLE_QUOTE",)


def _login(client: TestClient) -> str:
    now = datetime.now(UTC)
    challenge = client.post(
        "/api/v1/auth/login/password",
        json={"schema_version": "1.0", "login_id": "admin", "password": TEST_PASSWORD},
    )
    response = client.post(
        "/api/v1/auth/login/totp",
        json={
            "schema_version": "1.0",
            "challenge_id": challenge.json()["challenge_id"],
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).at(now),
        },
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _event(market: str, now: datetime) -> QuoteEvent:
    return QuoteEvent(
        symbol="005930",
        market=market,
        source="KIWOOM_FIXTURE",
        sequence_or_hash=f"venue-{market}",
        source_sequence=1,
        event_at=now - timedelta(milliseconds=20),
        received_at=now,
        last_price=Decimal(70100),
        open_price=Decimal(70000),
        high_price=Decimal(70200),
        low_price=Decimal(69900),
        cumulative_volume=1000,
        best_bid_price=Decimal(70000),
        best_bid_quantity=100,
        best_ask_price=Decimal(70100),
        best_ask_quantity=100,
        trading_status="TRADING",
    )


def test_diagnostic_api_persists_shadow_evaluation_without_orders(
    client: TestClient, db: Session
) -> None:
    now = datetime.now(UTC)
    ingest_quote(db, _event("KRX", now))
    ingest_quote(db, _event("NXT", now))
    db.commit()
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}

    response = client.post(
        "/api/v1/venue-selections/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "symbol": "005930",
            "side": "BUY",
            "quantity": 10,
            "order_type": "LIMIT",
            "urgency": "NORMAL",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["execution_stage"] == "SHADOW"
    assert body["order_creation_allowed"] is False
    assert body["nxt_eligibility_status"] == "VERIFIED"
    assert body["selected_venue"] in {"KRX", "NXT", "WAIT"}
    assert len(body["input_hash"]) == 64

    listing = client.get("/api/v1/venue-selections?symbol=005930")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["selection_id"] == body["selection_id"]
    assert db.scalar(select(func.count()).select_from(VenueSelectionEvaluation)) == 1
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0
