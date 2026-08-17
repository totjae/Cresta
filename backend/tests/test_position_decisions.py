from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.mock_ai import create_mock_position_trading_decision
from app.models import (
    DecisionInputSnapshot,
    IndicatorSnapshot,
    MarketSnapshot,
    MarketStreamState,
    Position,
    User,
)


def _position_case(
    db: Session,
    *,
    admin: User,
    settings: Settings,
    last_price: str,
    vwap: str,
    slope: str,
    drawdown: str,
    relative_volume: str,
    with_indicator: bool = True,
):
    now = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)
    snapshot = MarketSnapshot(
        symbol="005930",
        market="KRX",
        source="TEST",
        sequence_or_hash=f"position-{last_price}-{drawdown}",
        source_sequence=1,
        payload_hash="b" * 64,
        last_price=Decimal(last_price),
        open_price=Decimal(100),
        high_price=Decimal(100),
        low_price=Decimal(last_price),
        cumulative_volume=10000,
        best_bid_price=Decimal(last_price) - Decimal("0.1"),
        best_bid_quantity=100,
        best_ask_price=Decimal(last_price) + Decimal("0.1"),
        best_ask_quantity=100,
        trading_status="TRADING",
        quality="NORMAL",
        recovery_snapshot=False,
        event_at=now,
        received_at=now,
    )
    db.add(snapshot)
    db.flush()
    db.add(
        MarketStreamState(
            market="KRX",
            symbol="005930",
            source="TEST",
            current_snapshot_id=snapshot.id,
            last_sequence=1,
            last_event_at=now,
            last_received_at=now,
            cumulative_volume=10000,
            quality="NORMAL",
        )
    )
    if with_indicator:
        db.add(
            IndicatorSnapshot(
                market_snapshot_id=snapshot.id,
                market="KRX",
                symbol="005930",
                calculator_version="watch-indicators-v2",
                vwap=Decimal(vwap),
                sma5=Decimal(vwap),
                session_high=Decimal(100),
                drawdown_from_high_pct=Decimal(drawdown),
                spread_pct=Decimal("0.2"),
                price_vs_vwap_pct=(
                    (Decimal(last_price) / Decimal(vwap) - Decimal(1)) * Decimal(100)
                ),
                sma5_slope_pct=Decimal(slope),
                relative_volume_5=Decimal(relative_volume),
                realized_volatility_pct=Decimal("0.5"),
                minute_bar_count=10,
                input_start_at=now - timedelta(minutes=10),
                input_end_at=now,
            )
        )
    position = Position(
        account_alias="KIWOOM_MOCK_PRIMARY",
        symbol="005930",
        quantity=10,
        available_quantity=10,
        managed_quantity=10,
        average_price=Decimal(100),
        managed_average_price=Decimal(100),
        state="OPEN",
        origin="CRESTA_MANAGED",
        created_at=now - timedelta(hours=1),
        updated_at=now,
    )
    db.add(position)
    db.commit()
    decision, created = create_mock_position_trading_decision(
        db,
        user=admin,
        position=position,
        evaluation_request_id=f"position-eval-{last_price}-{drawdown}-{with_indicator}",
        symbol="005930",
        market="KRX",
        settings=settings,
        now=now,
    )
    db.commit()
    return decision, created


def test_position_policy_generates_hold_partial_and_full_sell(
    db: Session, admin: User, settings: Settings
) -> None:
    hold, created = _position_case(
        db,
        admin=admin,
        settings=settings,
        last_price="101",
        vwap="100",
        slope="0.2",
        drawdown="-0.5",
        relative_volume="1.2",
    )
    assert created is True
    assert hold.action == "HOLD"
    assert json.loads(hold.core_output_json)["sell_ratio"] is None

    db.query(MarketStreamState).delete()
    db.query(Position).delete()
    db.commit()
    partial, _ = _position_case(
        db,
        admin=admin,
        settings=settings,
        last_price="98.6",
        vwap="100",
        slope="-0.2",
        drawdown="-2.0",
        relative_volume="0.7",
    )
    assert partial.action == "PARTIAL_SELL"
    assert json.loads(partial.core_output_json)["sell_ratio"] == "0.5"

    db.query(MarketStreamState).delete()
    db.query(Position).delete()
    db.commit()
    full, _ = _position_case(
        db,
        admin=admin,
        settings=settings,
        last_price="97.9",
        vwap="100",
        slope="-0.2",
        drawdown="-2.1",
        relative_volume="0.7",
    )
    assert full.action == "FULL_SELL"
    assert "FIXED_STOP_TRIGGERED" in json.loads(full.reason_codes_json)


def test_position_input_is_frozen_and_missing_indicator_fails_to_hold(
    db: Session, admin: User, settings: Settings
) -> None:
    decision, _ = _position_case(
        db,
        admin=admin,
        settings=settings,
        last_price="97.9",
        vwap="100",
        slope="-0.2",
        drawdown="-2.1",
        relative_volume="0.7",
        with_indicator=False,
    )
    decision_input = db.scalar(
        select(DecisionInputSnapshot).where(
            DecisionInputSnapshot.id == decision.decision_input_id
        )
    )
    assert decision_input is not None
    payload = json.loads(decision_input.input_json)
    assert decision.action == "HOLD"
    assert json.loads(decision.reason_codes_json) == ["DATA_INSUFFICIENT"]
    assert payload["position"]["position_id"]
    assert payload["position"]["version"] == 1
    assert payload["position"]["unrealized_return_pct"] == "-2.100000"
    assert "account_number" not in decision_input.input_json
