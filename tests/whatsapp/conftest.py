"""The WhatsApp channel end to end: webhook, store, bot and channel over the Postgres test
database, with the Cloud API faked (tests.whatsapp.fakes) and the LLM scripted. Invented data."""

from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.agent import Agent
from app.channels.processor import BotProcessor
from app.config import Settings, get_settings
from app.db.models import BotEvent, WaContact, WaConversation, WaMessage
from app.db.session import get_session
from app.main import app
from app.sync.live import DebtResult
from app.whatsapp.bot import WhatsAppBot
from app.whatsapp.channel import WhatsAppChannel
from app.whatsapp.media import MediaStore
from app.whatsapp.webhook import get_whatsapp_bot
from tests.bot import factories as f
from tests.llm.fakes import Step, scripted_provider
from tests.whatsapp.fakes import (
    APP_SECRET,
    CONTACT_WA_ID,
    PHONE_NUMBER_ID,
    VERIFY_TOKEN,
    FakeWhatsApp,
    body_of,
    signed_headers,
)

TZ = ZoneInfo("America/Argentina/Cordoba")
NOW = datetime(2026, 9, 30, 11, 0, tzinfo=TZ)  # a Wednesday, office hours
OWNER_PHONE = f"+{CONTACT_WA_ID}"
NON_PILOT_WA_ID = "5493515550303"


def wa_settings(media_dir: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "channel": "whatsapp",
        "whatsapp_app_secret": APP_SECRET,
        "whatsapp_verify_token": VERIFY_TOKEN,
        "whatsapp_phone_number_id": PHONE_NUMBER_ID,
        "whatsapp_media_dir": str(media_dir),
        "whatsapp_media_max_bytes": 1000,
        **overrides,
    }
    return Settings(_env_file=None, **values)


@dataclass
class Wa:
    session: Session
    fake: FakeWhatsApp
    bot: WhatsAppBot
    script: Any
    settings: Settings
    client: TestClient
    clock: list[datetime]

    def post(self, payload: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
        body = body_of(payload)
        return self.client.post(
            "/webhooks/whatsapp", content=body, headers=headers or signed_headers(body)
        )

    def advance(self, delta: timedelta) -> None:
        self.clock[0] += delta

    @property
    def now(self) -> datetime:
        return self.clock[0]

    def conversation(self, wa_id: str = CONTACT_WA_ID) -> WaConversation | None:
        self.session.expire_all()
        return self.session.scalar(
            select(WaConversation)
            .join(WaContact, WaContact.id == WaConversation.contact_id)
            .where(WaContact.wa_id == wa_id)
        )

    def messages(self, *, notes: bool = True) -> list[WaMessage]:
        self.session.expire_all()
        stmt = select(WaMessage).order_by(WaMessage.id)
        if not notes:
            stmt = stmt.where(WaMessage.is_internal_note.is_(False))
        return list(self.session.scalars(stmt))

    def events(self, event_type: str) -> list[BotEvent]:
        return list(
            self.session.scalars(
                select(BotEvent).where(BotEvent.event_type == event_type).order_by(BotEvent.id)
            )
        )

    @property
    def llm_calls(self) -> int:
        return len(self.script.requests)


@pytest.fixture
def people(db_session: Session) -> None:
    pilot = f.building(db_session, "031 RODAS II")
    pilot.pilot = True
    unit = f.unit(db_session, pilot, "04-C")
    f.link(db_session, unit, f.person(db_session, "Ana Prueba", phone=OWNER_PHONE))
    other = f.building(db_session, "040 TORRE NORTE")  # pilot=False
    other_unit = f.unit(db_session, other, "02-B")
    f.link(db_session, other_unit, f.person(db_session, "Beto Prueba", phone=f"+{NON_PILOT_WA_ID}"))


MakeWa = Callable[..., Wa]


@pytest.fixture
def make_wa(db_session: Session, people: None, tmp_path: Path) -> Iterator[MakeWa]:
    def make(
        steps: list[Step] | None = None,
        *,
        fake: FakeWhatsApp | None = None,
        refresh_debt: Callable[[int], DebtResult] | None = None,
        **settings_overrides: Any,
    ) -> Wa:
        settings = wa_settings(tmp_path / "media", **settings_overrides)
        llm, script = scripted_provider("anthropic", steps or [])
        fake = fake or FakeWhatsApp()
        clock = [NOW]

        def no_debt(unit_id: int) -> Any:
            raise LookupError(unit_id)

        def now() -> datetime:
            return clock[0]

        agent = Agent(llm, settings=settings, refresh_debt=refresh_debt or no_debt, now=now)
        sessions = lambda: nullcontext(db_session)  # noqa: E731
        channel = WhatsAppChannel(fake, sessions, now=now)  # type: ignore[arg-type]
        processor = BotProcessor(channel, sessions, lambda: agent, settings, now=now)
        media = MediaStore(fake, settings.whatsapp_media_dir, settings.whatsapp_media_max_bytes)  # type: ignore[arg-type]
        bot = WhatsAppBot(processor, sessions, media, now=now)

        def session_override() -> Iterator[Session]:
            yield db_session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_whatsapp_bot] = lambda: bot
        return Wa(db_session, fake, bot, script, settings, TestClient(app), clock)

    try:
        yield make
    finally:
        app.dependency_overrides.clear()
