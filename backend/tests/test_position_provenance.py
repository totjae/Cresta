from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Position


def test_position_defaults_to_cresta_managed(db: Session) -> None:
    position = Position(
        account_alias="KIWOOM_MOCK_PRIMARY",
        symbol="005930",
        quantity=10,
        average_price=Decimal(50000),
        state="OPEN",
    )
    db.add(position)
    db.commit()
    db.refresh(position)
    assert position.origin == "CRESTA_MANAGED"


def test_position_can_be_tagged_external(db: Session) -> None:
    position = Position(
        account_alias="KIWOOM_MOCK_PRIMARY",
        symbol="005931",
        quantity=5,
        average_price=Decimal(60000),
        state="OPEN",
        origin="EXTERNAL",
    )
    db.add(position)
    db.commit()
    db.refresh(position)
    assert position.origin == "EXTERNAL"
    assert db.scalar(select(Position).where(Position.symbol == "005931")).origin == "EXTERNAL"
