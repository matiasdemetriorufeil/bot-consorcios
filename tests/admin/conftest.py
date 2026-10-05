"""The admin panel mounted on its own app, over the test database. Invented data only."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.admin import setup_admin
from app.admin.audit import EVENT_TYPE
from app.admin.auth import AdminAuth, LoginLimiter, hash_password
from app.config import Settings
from app.db.models import BotEvent

ADMIN = "operadora"
PASSWORD = "una-clave-inventada"
PASSWORD_HASH = hash_password(PASSWORD)


def admin_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "admin_username": ADMIN,
        "admin_password_hash": PASSWORD_HASH,
        "admin_secret_key": "clave-de-sesion-inventada-0123456789",
        **overrides,
    }
    return Settings(_env_file=None, **values)


@dataclass
class FakeClock:
    now: float = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Panel:
    client: TestClient
    session: Session
    clock: FakeClock
    responses: list[Any] = field(default_factory=list)

    def login(self, username: str = ADMIN, password: str = PASSWORD) -> Any:
        return self.client.post(
            "/admin/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )

    def admin_events(self, action: str | None = None) -> list[dict[str, Any]]:
        self.session.expire_all()
        rows = self.session.scalars(
            select(BotEvent).where(BotEvent.event_type == EVENT_TYPE).order_by(BotEvent.id)
        )
        payloads = [{**e.payload, "phone_e164": e.phone_e164} for e in rows]
        return [p for p in payloads if action is None or p["action"] == action]


def build_panel(db_session: Session, settings: Settings) -> Panel:
    # Panel sessions share the test connection: their commits only release SAVEPOINTs.
    maker = sessionmaker(bind=db_session.get_bind(), join_transaction_mode="create_savepoint")
    clock = FakeClock()
    auth = AdminAuth(settings, LoginLimiter(clock=clock), clock=clock)
    app = FastAPI()
    setup_admin(app, maker, settings, auth=auth)
    # https: APP_ENV defaults to production, so the session cookie is Secure.
    client = TestClient(app, base_url="https://testserver")
    return Panel(client=client, session=db_session, clock=clock)


@pytest.fixture
def panel(db_session: Session) -> Iterator[Panel]:
    built = build_panel(db_session, admin_settings())
    with built.client:
        yield built


@pytest.fixture
def logged_in(panel: Panel) -> Panel:
    assert panel.login().status_code == 302
    return panel
