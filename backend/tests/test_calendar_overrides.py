from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calendar_overrides import (
    CalendarOverrideError,
    create_calendar_override,
    revoke_calendar_override,
)
from app.market_calendar import evaluate_krx_trading_day
from app.models import (
    Approval,
    MarketCalendarOverride,
    OrderIntent,
    TradingOrder,
    User,
)
from app.venue_selection import evaluate_and_store_venue_selection
from tests.test_venue_selection import _login

KST = ZoneInfo("Asia/Seoul")


def _request_metadata() -> dict[str, str]:
    return {
        "correlation_id": "019f0000-0000-7000-8000-000000000031",
        "request_ip": "127.0.0.1",
        "user_agent": "pytest",
    }


def _next_open_day() -> datetime:
    candidate = datetime.now(KST) + timedelta(days=1)
    while evaluate_krx_trading_day(candidate.date()).status != "OPEN":
        candidate += timedelta(days=1)
    return candidate


def test_operational_closure_is_fixed_in_shadow_input_and_revocation_is_not_retroactive(
    db: Session, admin: User
) -> None:
    market_date = _next_open_day().date()
    item = create_calendar_override(
        db,
        user=admin,
        market_date=market_date,
        reason="거래소 임시 휴장 공지",
        source_reference="KRX notice 2026-test",
        now=datetime.combine(market_date - timedelta(days=1), time(12), tzinfo=KST),
        **_request_metadata(),
    )
    evaluated_at = datetime.combine(market_date, time(10), tzinfo=KST)
    evaluation = evaluate_and_store_venue_selection(
        db,
        owner=admin,
        symbol="005930",
        side="BUY",
        quantity=1,
        order_type="LIMIT",
        urgency="NORMAL",
        environment="MOCK",
        nxt_eligibility_status="UNKNOWN",
        sor_supported=False,
        krx_snapshot=None,
        nxt_snapshot=None,
        now=evaluated_at,
        max_age_seconds=2,
    )
    db.commit()

    assert evaluation.trading_day_status == "CLOSED"
    assert evaluation.calendar_reason == "OPERATIONAL_CLOSURE"
    assert evaluation.calendar_override_id == item.id
    assert evaluation.selected_venue == "WAIT"
    assert f'"calendar_override_id":"{item.id}"' in evaluation.input_json

    revoke_calendar_override(
        db,
        user=admin,
        override_id=item.id,
        now=evaluated_at.astimezone(UTC),
        **_request_metadata(),
    )
    new_evaluation = evaluate_and_store_venue_selection(
        db,
        owner=admin,
        symbol="005930",
        side="BUY",
        quantity=1,
        order_type="LIMIT",
        urgency="NORMAL",
        environment="MOCK",
        nxt_eligibility_status="UNKNOWN",
        sor_supported=False,
        krx_snapshot=None,
        nxt_snapshot=None,
        now=evaluated_at,
        max_age_seconds=2,
    )
    db.commit()

    assert evaluation.calendar_override_id == item.id
    assert new_evaluation.calendar_override_id is None
    assert new_evaluation.calendar_reason == "WEEKDAY"
    assert db.scalar(select(func.count()).select_from(Approval)) == 0
    assert db.scalar(select(func.count()).select_from(OrderIntent)) == 0
    assert db.scalar(select(func.count()).select_from(TradingOrder)) == 0


def test_calendar_override_rejects_unsafe_date_and_metadata(
    db: Session, admin: User
) -> None:
    current = datetime.now(KST)
    cases = (
        (current.date() - timedelta(days=1), "거래소 임시 휴장 공지", "KRX notice", "CALENDAR_OVERRIDE_DATE_OUT_OF_RANGE"),
        (current.date() + timedelta(days=731), "거래소 임시 휴장 공지", "KRX notice", "CALENDAR_OVERRIDE_DATE_OUT_OF_RANGE"),
        (current.date() + timedelta(days=1), "짧음", "KRX notice", "CALENDAR_OVERRIDE_REASON_INVALID"),
        (current.date() + timedelta(days=1), "거래소 임시 휴장 공지", "x", "CALENDAR_OVERRIDE_SOURCE_INVALID"),
    )
    for market_date, reason, source_reference, code in cases:
        with pytest.raises(CalendarOverrideError) as caught:
            create_calendar_override(
                db,
                user=admin,
                market_date=market_date,
                reason=reason,
                source_reference=source_reference,
                now=current,
                **_request_metadata(),
            )
        assert caught.value.code == code
        assert caught.value.status_code == 422
    assert db.scalar(select(func.count()).select_from(MarketCalendarOverride)) == 0


def test_calendar_override_api_create_conflict_list_and_revoke(
    client: TestClient, db: Session
) -> None:
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    market_date = _next_open_day().date()
    payload = {
        "schema_version": "1.0",
        "market_date": market_date.isoformat(),
        "reason": "거래소 임시 휴장 공지",
        "source_reference": "KRX notice 2026-test",
    }

    assert client.post(
        "/api/v1/venue-selections/calendar-overrides", json=payload
    ).status_code == 403
    created = client.post(
        "/api/v1/venue-selections/calendar-overrides", headers=headers, json=payload
    )
    assert created.status_code == 201
    item = created.json()
    assert item["state"] == "ACTIVE"
    assert item["market_date"] == market_date.isoformat()

    duplicate = client.post(
        "/api/v1/venue-selections/calendar-overrides", headers=headers, json=payload
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CALENDAR_OVERRIDE_ALREADY_ACTIVE"

    listing = client.get("/api/v1/venue-selections/calendar-overrides")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["override_id"] == item["override_id"]

    revoked = client.delete(
        f"/api/v1/venue-selections/calendar-overrides/{item['override_id']}",
        headers=headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["state"] == "REVOKED"
    assert revoked.json()["revoked_at"] is not None
    assert db.scalar(select(func.count()).select_from(MarketCalendarOverride)) == 1

    missing = client.delete(
        f"/api/v1/venue-selections/calendar-overrides/{item['override_id']}",
        headers=headers,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "CALENDAR_OVERRIDE_NOT_FOUND"
