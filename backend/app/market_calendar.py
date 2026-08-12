from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

import holidays

KRX_CALENDAR_POLICY_VERSION = "krx-calendar-v2"


@dataclass(frozen=True)
class TradingDayDecision:
    status: str
    reason: str
    policy_version: str = KRX_CALENDAR_POLICY_VERSION
    override_id: str | None = None


@lru_cache(maxsize=16)
def _korean_public_holidays(year: int) -> frozenset[date]:
    calendar = holidays.country_holidays("KR", years=[year], observed=True)
    return frozenset(calendar.keys())


@lru_cache(maxsize=16)
def _year_end_closure(year: int) -> date:
    candidate = date(year, 12, 31)
    public_holidays = _korean_public_holidays(year)
    while candidate.weekday() >= 5 or candidate in public_holidays:
        candidate -= timedelta(days=1)
    return candidate


def evaluate_krx_trading_day(day: date) -> TradingDayDecision:
    """Evaluate the shared KRX/NXT equity trading day using published KRX rules."""
    try:
        if day.weekday() >= 5:
            return TradingDayDecision("CLOSED", "WEEKEND")
        if day.month == 5 and day.day == 1:
            return TradingDayDecision("CLOSED", "LABOR_DAY")
        if day in _korean_public_holidays(day.year):
            return TradingDayDecision("CLOSED", "PUBLIC_HOLIDAY")
        if day == _year_end_closure(day.year):
            return TradingDayDecision("CLOSED", "YEAR_END_CLOSURE")
        return TradingDayDecision("OPEN", "WEEKDAY")
    except Exception:  # noqa: BLE001 - calendar uncertainty must fail closed
        return TradingDayDecision("UNKNOWN", "CALENDAR_UNAVAILABLE")
