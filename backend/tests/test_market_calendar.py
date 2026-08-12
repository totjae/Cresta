from __future__ import annotations

from datetime import date

from app.market_calendar import evaluate_krx_trading_day


def test_regular_weekday_is_open() -> None:
    decision = evaluate_krx_trading_day(date(2026, 8, 12))
    assert decision.status == "OPEN"
    assert decision.reason == "WEEKDAY"
    assert decision.policy_version == "krx-calendar-v2"


def test_weekend_public_holiday_labor_day_and_year_end_are_closed() -> None:
    assert evaluate_krx_trading_day(date(2026, 8, 15)).reason == "WEEKEND"
    assert evaluate_krx_trading_day(date(2026, 8, 17)).reason == "PUBLIC_HOLIDAY"
    assert evaluate_krx_trading_day(date(2026, 5, 1)).reason == "LABOR_DAY"
    assert evaluate_krx_trading_day(date(2026, 12, 31)).reason == "YEAR_END_CLOSURE"
