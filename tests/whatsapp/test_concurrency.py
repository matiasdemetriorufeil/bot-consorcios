"""Two deliveries at the same time, with real connections to the test database (as two
requests or two uvicorn workers would have): the same wamid is stored and answered once, and
two messages of one number are answered one after the other. Invented data only; what these
tests commit is deleted at the end."""

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.bot.agent import Agent
from app.channels.locks import advisory_lock
from app.channels.processor import BotProcessor
from app.db.models import BotEvent, WaContact, WaConversation, WaDirection, WaMessage
from app.whatsapp import store
from app.whatsapp.bot import WhatsAppBot
from app.whatsapp.channel import WhatsAppChannel
from app.whatsapp.events import WaIncoming, parse_delivery
from tests.llm.fakes import Say, scripted_provider
from tests.whatsapp.conftest import wa_settings
from tests.whatsapp.fakes import PHONE_NUMBER_ID, FakeWhatsApp, incoming, text_message

WA_ID = "5493515550777"  # invented, nobody in the roster


@pytest.fixture
def engine(db_engine: Engine) -> Iterator[Engine]:
    try:
        yield db_engine
    finally:
        with db_engine.begin() as connection:
            ids = select(WaConversation.id).join(WaContact).where(WaContact.wa_id == WA_ID)
            connection.execute(
                delete(BotEvent).where(
                    BotEvent.conversation_id.in_(ids) | (BotEvent.phone_e164 == f"+{WA_ID}")
                )
            )
            connection.execute(delete(WaContact).where(WaContact.wa_id == WA_ID))  # cascades


def _message(body: str, wamid: str | None = None, at: datetime | None = None) -> WaIncoming:
    payload = incoming(text_message(body, at or datetime.now(UTC), message_id=wamid), wa_id=WA_ID)
    [message] = parse_delivery(payload, PHONE_NUMBER_ID).messages
    return message


def _at_once(*jobs: Any) -> list[Any]:
    """Runs the jobs in threads released together; their results, in order."""
    start = threading.Barrier(len(jobs))
    results: list[Any] = [None] * len(jobs)

    def run(i: int) -> None:
        start.wait(5)
        results[i] = jobs[i]()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(jobs))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return results


def _bot(engine: Engine, tmp_path: Path, steps: list[Any], slow_first: float = 0) -> Any:
    """The webhook's bot over real sessions and the advisory lock, as in production."""
    settings = wa_settings(tmp_path / "media")
    llm, script = scripted_provider("anthropic", steps)
    if slow_first:
        answer = script.next

        def slow(kwargs: dict[str, Any]) -> Any:
            if not script.requests:
                time.sleep(slow_first)
            return answer(kwargs)

        script.next = slow  # type: ignore[method-assign]
    fake = FakeWhatsApp()
    sessions = sessionmaker(engine)
    agent = Agent(llm, settings=settings, refresh_debt=lambda unit_id: None)  # type: ignore[arg-type,return-value]
    channel = WhatsAppChannel(fake, sessions)  # type: ignore[arg-type]
    processor = BotProcessor(channel, sessions, lambda: agent, settings, lock=advisory_lock(engine))
    return WhatsAppBot(processor, sessions, None), fake, script


def _deliver(engine: Engine, bot: WhatsAppBot, message: WaIncoming) -> int | None:
    """What the webhook does with one message: store it, commit, and answer it if new."""
    with Session(engine) as session:
        message_id = store.record_incoming(session, message, datetime.now(UTC))
        session.commit()
    if message_id is not None:
        bot.process(message_id)
    return message_id


def test_the_same_wamid_twice_at_once_is_stored_and_answered_once(
    engine: Engine, tmp_path: Path
) -> None:
    bot, fake, script = _bot(engine, tmp_path, [Say("¡Hola!"), Say("No debería salir")])
    message = _message("Hola", wamid="wamid.INVENTADO.MISMO")

    ids = _at_once(lambda: _deliver(engine, bot, message), lambda: _deliver(engine, bot, message))

    assert sorted(ids, key=lambda i: i is None)[1] is None and any(ids)
    with Session(engine) as session:
        stored = session.scalar(
            select(func.count())
            .select_from(WaMessage)
            .where(WaMessage.wa_message_id == "wamid.INVENTADO.MISMO")
        )
    assert stored == 1
    assert fake.texts() == ["¡Hola!"] and len(script.requests) == 1


def test_two_messages_of_one_number_are_answered_one_after_the_other(
    engine: Engine, tmp_path: Path
) -> None:
    # The first answer takes a while: the second message must wait for it, not run beside it.
    bot, fake, script = _bot(engine, tmp_path, [Say("Bienvenida")])
    _deliver(engine, bot, _message("Hola"))  # the conversation exists before the race
    bot, fake, script = _bot(
        engine, tmp_path, [Say("Respuesta uno"), Say("Respuesta dos")], slow_first=0.5
    )

    # Sent in the same second (otherwise the older one, stored second, is out of order).
    now = datetime.now(UTC)
    first, second = _message("Quiero registrarme", at=now), _message("Soy inquilino", at=now)

    _at_once(lambda: _deliver(engine, bot, first), lambda: _deliver(engine, bot, second))

    assert fake.texts() == ["Respuesta uno", "Respuesta dos"]
    # The second turn saw the first answer: it started after it went out.
    assert "Respuesta uno" in str(script.requests[1]["messages"])
    with Session(engine) as session:
        inbound = session.scalars(
            select(WaMessage)
            .join(WaConversation)
            .join(WaContact)
            .where(WaContact.wa_id == WA_ID, WaMessage.direction == WaDirection.INBOUND)
        ).all()
    assert len(inbound) == 3 and all(m.processed_at is not None for m in inbound)
