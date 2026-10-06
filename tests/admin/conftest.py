"""The admin panel mounted on its own app, over the test database. Invented data only."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.admin import setup_admin
from app.admin.audit import EVENT_TYPE
from app.admin.auth import AdminAuth, LoginLimiter, hash_password
from app.config import Settings
from app.db.models import BotEvent, PanelRole, PanelUser
from tests.whatsapp.fakes import FakeWhatsApp

ADMIN = "operadora"
PASSWORD = "una-clave-inventada"
PASSWORD_HASH = hash_password(PASSWORD)
# Users of panel_users (the .env one above is the rescue admin).
OPERATOR = "marta"
OPERATOR_NAME = "Marta Inventada"
OTHER_OPERATOR = "lucia"
OTHER_OPERATOR_NAME = "Lucía Inventada"
TABLE_ADMIN = "jefa"
USER_PASSWORD = "clave-de-usuaria-inventada"
NOW = datetime(2026, 9, 30, 11, 0, tzinfo=ZoneInfo("America/Argentina/Cordoba"))


def make_user(
    session: Session,
    username: str,
    role: PanelRole = PanelRole.OPERATOR,
    *,
    display_name: str | None = None,
    password: str = USER_PASSWORD,
    active: bool = True,
) -> PanelUser:
    user = PanelUser(
        username=username,
        display_name=display_name or username.title(),
        password_hash=hash_password(password),
        role=role,
        active=active,
    )
    session.add(user)
    session.flush()
    return user


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
    whatsapp: FakeWhatsApp = field(default_factory=FakeWhatsApp)
    # What the conversations page takes as "now" (a list: tests move it).
    now: list[datetime] = field(default_factory=lambda: [NOW])

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


def build_panel(db_session: Session, settings: Settings, *, whatsapp_ready: bool = True) -> Panel:
    # Panel sessions share the test connection: their commits only release SAVEPOINTs.
    maker = sessionmaker(bind=db_session.get_bind(), join_transaction_mode="create_savepoint")
    clock = FakeClock()
    auth = AdminAuth(settings, LoginLimiter(clock=clock), clock=clock)
    whatsapp = FakeWhatsApp()
    now = [NOW]
    app = FastAPI()
    setup_admin(
        app,
        maker,
        settings,
        auth=auth,
        sender_factory=(lambda: whatsapp) if whatsapp_ready else (lambda: None),
        clock=lambda: now[0],
    )
    # https: APP_ENV defaults to production, so the session cookie is Secure.
    client = TestClient(app, base_url="https://testserver")
    return Panel(client=client, session=db_session, clock=clock, whatsapp=whatsapp, now=now)


@pytest.fixture
def panel(db_session: Session) -> Iterator[Panel]:
    built = build_panel(db_session, admin_settings())
    with built.client:
        yield built


@pytest.fixture
def logged_in(panel: Panel) -> Panel:
    assert panel.login().status_code == 302
    return panel


@pytest.fixture
def operator(panel: Panel) -> Panel:
    """Logged in as an operator of panel_users."""
    make_user(panel.session, OPERATOR, display_name=OPERATOR_NAME)
    assert panel.login(OPERATOR, USER_PASSWORD).status_code == 302
    return panel
