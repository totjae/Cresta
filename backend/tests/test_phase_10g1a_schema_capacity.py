from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from app.models import OrderEvent
from tests.test_v7_persistence_foundation import _alembic


def _engine(database: Path):
    return create_engine(f"sqlite:///{database.as_posix()}", poolclass=NullPool)


def _event_type_capacity(engine) -> int:
    column = next(
        item
        for item in inspect(engine).get_columns("order_events")
        if item["name"] == "event_type"
    )
    return int(column["type"].length)


def _insert_event(engine, event_type: str) -> None:
    now = datetime.now(UTC).isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO order_events "
                "(id, order_id, event_type, source, source_key, payload_hash, "
                "payload_json, correlation_id, occurred_at, created_at) "
                "VALUES (:id, :order_id, :event_type, 'CRESTA', :source_key, :payload_hash, "
                "'{}', :correlation_id, :occurred_at, :created_at)"
            ),
            {
                "id": str(uuid4()),
                "order_id": str(uuid4()),
                "event_type": event_type,
                "source_key": str(uuid4()),
                "payload_hash": "a" * 64,
                "correlation_id": str(uuid4()),
                "occurred_at": now,
                "created_at": now,
            },
        )


def test_0043_sqlite_upgrade_safe_downgrade_and_orm_capacity(tmp_path: Path) -> None:
    database = tmp_path / "phase-10g1a-safe.db"
    _alembic(database, "upgrade", "20260828_0042")
    engine = _engine(database)
    assert _event_type_capacity(engine) == 32
    _alembic(database, "upgrade", "20260829_0043")
    assert _event_type_capacity(engine) == 64
    assert OrderEvent.__table__.c.event_type.type.length == 64
    _insert_event(engine, "ORDER_CREATED")
    _alembic(database, "downgrade", "20260828_0042")
    assert _event_type_capacity(engine) == 32
    _alembic(database, "upgrade", "20260829_0043")
    assert _event_type_capacity(engine) == 64
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM order_events WHERE event_type='ORDER_CREATED'")
        ) == 1
    engine.dispose()


def test_0043_sqlite_downgrade_refuses_long_event_without_loss(tmp_path: Path) -> None:
    database = tmp_path / "phase-10g1a-guard.db"
    _alembic(database, "upgrade", "20260829_0043")
    engine = _engine(database)
    normative = "ORDER_AUTHORITY_REVOKED_BEFORE_SEND"
    _insert_event(engine, normative)
    result = _alembic(
        database,
        "downgrade",
        "20260828_0042",
        check=False,
    )
    assert result.returncode != 0
    assert "Refusing downgrade of 20260829_0043" in result.stderr
    assert _event_type_capacity(engine) == 64
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM order_events WHERE event_type=:event_type"),
            {"event_type": normative},
        ) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260829_0043"
        )
    engine.dispose()
