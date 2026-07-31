from __future__ import annotations

import base64
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_settings
from app.auth.crypto import encrypt_totp_secret, hash_password
from app.config import Settings
from app.db import Base, get_db
from app.main import create_app
from app.models import TotpCredential, User

TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXP"
TEST_PASSWORD = "Cresta!Test-Password-2026"
TEST_KEY = base64.urlsafe_b64encode(b"test-only-key-material-32-bytes!").decode("ascii")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        cookie_secure=False,
        allowed_origins="https://testserver",
        totp_encryption_key=TEST_KEY,
    )


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    with session_factory() as database:
        yield database


@pytest.fixture
def admin(db: Session, settings: Settings) -> User:
    user = User(login_id="admin", password_hash=hash_password(TEST_PASSWORD))
    db.add(user)
    db.flush()
    db.add(
        TotpCredential(
            user_id=user.id,
            encrypted_secret=encrypt_totp_secret(TEST_TOTP_SECRET, settings.load_totp_encryption_key()),
            verified=True,
        )
    )
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    settings: Settings,
    admin: User,
) -> Generator[TestClient, None, None]:
    application = create_app()

    def override_db():
        with session_factory() as database:
            yield database

    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_settings] = lambda: settings
    with TestClient(application, base_url="https://testserver") as test_client:
        yield test_client
