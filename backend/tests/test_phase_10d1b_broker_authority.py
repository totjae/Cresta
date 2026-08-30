from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.account_authority import (
    append_account_funds_snapshot,
    append_order_capacity_snapshot,
    get_latest_account_funds,
    get_latest_exact_order_capacity,
    query_and_persist_order_capacity,
)
from app.broker.kiwoom import (
    AccountFundsSnapshotData,
    OrderCapacityRequest,
    OrderCapacitySnapshotData,
)
from app.models import OrderCapacitySnapshot

BACKEND_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 28, 5, 0, tzinfo=UTC)


def funds(*, received_at: datetime = NOW, deposit: int | None = 100) -> AccountFundsSnapshotData:
    return AccountFundsSnapshotData(
        "KIWOOM",
        "KIWOOM_MOCK_PRIMARY",
        "MOCK",
        "kt00001",
        "3",
        deposit,
        90,
        80,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        received_at,
    )


def capacity(
    request: OrderCapacityRequest, *, received_at: datetime = NOW, cash: int | None = 70
) -> OrderCapacitySnapshotData:
    return OrderCapacitySnapshotData(
        "KIWOOM",
        "KIWOOM_MOCK_PRIMARY",
        "MOCK",
        "kt00010",
        request.symbol,
        request.side,
        "2" if request.side == "BUY" else "1",
        request.requested_price,
        request.io_amount,
        request.requested_quantity,
        request.expected_buy_price,
        cash,
        80,
        60,
        50,
        40,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        70,
        7,
        received_at,
    )


def test_append_only_funds_and_latest_selector(db: Session) -> None:
    older = append_account_funds_snapshot(db, funds(deposit=100))
    newer = append_account_funds_snapshot(
        db, funds(received_at=NOW + timedelta(seconds=1), deposit=0)
    )
    db.commit()

    selected = get_latest_account_funds(
        db, broker="KIWOOM", account_alias="KIWOOM_MOCK_PRIMARY", environment="MOCK"
    )

    assert selected is not None and selected.id == newer.id
    assert selected.deposit == 0
    assert older.deposit == 100
    assert (
        get_latest_account_funds(
            db, broker="KIWOOM", account_alias="other", environment="MOCK"
        )
        is None
    )


def test_capacity_selector_requires_full_exact_context(db: Session) -> None:
    request = OrderCapacityRequest("005930", "BUY", 70_000, 1, 7, 69_900)
    older = append_order_capacity_snapshot(db, capacity(request, cash=700_000))
    newer = append_order_capacity_snapshot(
        db,
        capacity(request, received_at=NOW + timedelta(seconds=1), cash=690_000),
    )
    append_order_capacity_snapshot(
        db, capacity(replace(request, requested_price=70_100), cash=999_999)
    )
    append_order_capacity_snapshot(
        db, capacity(replace(request, symbol="000660"), cash=999_999)
    )
    db.commit()

    selected = get_latest_exact_order_capacity(
        db,
        broker="KIWOOM",
        account_alias="KIWOOM_MOCK_PRIMARY",
        environment="MOCK",
        request=request,
    )

    assert selected is not None and selected.id == newer.id
    assert selected.orderable_cash == 690_000
    assert older.orderable_cash == 700_000
    assert (
        get_latest_exact_order_capacity(
            db,
            broker="KIWOOM",
            account_alias="KIWOOM_MOCK_PRIMARY",
            environment="MOCK",
            request=replace(request, requested_quantity=None),
        )
        is None
    )


def test_query_service_appends_every_successful_observation(db: Session) -> None:
    request = OrderCapacityRequest("005930", "BUY", 70_000)

    class Client:
        def query_order_capacity(self, actual: OrderCapacityRequest) -> OrderCapacitySnapshotData:
            assert actual == request
            return capacity(actual)

    first = query_and_persist_order_capacity(db, Client(), request)  # type: ignore[arg-type]
    second = query_and_persist_order_capacity(db, Client(), request)  # type: ignore[arg-type]

    assert first.id != second.id
    assert len(db.query(OrderCapacitySnapshot).all()) == 2


def _alembic(db_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["CRESTA_DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_0041_to_0042_adds_empty_financial_tables_and_refuses_data_loss(
    tmp_path: Path,
) -> None:
    database = tmp_path / "phase10d1b.db"
    result = _alembic(database, "upgrade", "20260828_0041")
    assert result.returncode == 0, result.stdout + result.stderr
    result = _alembic(database, "upgrade", "20260828_0042")
    assert result.returncode == 0, result.stdout + result.stderr
    engine = create_engine(f"sqlite:///{database.as_posix()}", poolclass=NullPool)
    assert {"account_funds_snapshots", "order_capacity_snapshots"}.issubset(
        set(inspect(engine).get_table_names())
    )
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM account_funds_snapshots")) == 0
        connection.execute(
            text(
                "INSERT INTO account_funds_snapshots "
                "(id,broker,account_alias,environment,source_api_id,query_type,received_at,created_at) "
                "VALUES ('funds-1','KIWOOM','KIWOOM_MOCK_PRIMARY','MOCK','kt00001','3',:now,:now)"
            ),
            {"now": NOW},
        )
    result = _alembic(database, "downgrade", "20260828_0041")
    assert result.returncode != 0
    assert "Refusing downgrade of 20260828_0042" in result.stderr
