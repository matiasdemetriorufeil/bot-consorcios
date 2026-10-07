"""app.whatsapp.simulator: what goes to the test chat's contacts never reaches Meta. Postgres
test database, invented numbers."""

from contextlib import nullcontext
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.choices import Choice
from app.config import Settings
from app.db.models import (
    WaAuthor,
    WaContact,
    WaConversationStatus,
    WaDirection,
    WaMessage,
)
from app.whatsapp import simulator, store
from app.whatsapp.client import WhatsAppClient, WhatsAppError
from tests.whatsapp.fakes import FakeWhatsApp

NOW = datetime(2026, 9, 30, 11, 0, tzinfo=ZoneInfo("America/Argentina/Cordoba"))
TEST_PHONE = "+5493515550000"
REAL_WA_ID = "5493515550101"


def _client(session: Session, real: FakeWhatsApp | None = None) -> simulator.DevClient:
    return simulator.DevClient(real, lambda: nullcontext(session))  # type: ignore[arg-type]


def test_receive_stores_a_delivery_and_marks_the_contact_simulated(db_session: Session) -> None:
    message_id = simulator.receive(db_session, TEST_PHONE, "hola", NOW, profile_name="Prueba")
    tap_id = simulator.receive(db_session, TEST_PHONE, "Sí, pasame", NOW, tapped=True)
    db_session.commit()

    contact = db_session.scalar(select(WaContact).where(WaContact.phone_e164 == TEST_PHONE))
    assert contact is not None
    assert (contact.simulated, contact.wa_id, contact.profile_name) == (
        True,
        TEST_PHONE[1:],
        "Prueba",
    )
    message, tap = db_session.get(WaMessage, message_id), db_session.get(WaMessage, tap_id)
    assert message is not None and tap is not None
    assert (message.author, message.direction, message.body) == (
        WaAuthor.CONTACT,
        WaDirection.INBOUND,
        "hola",
    )
    assert message.wa_message_id.startswith(simulator.WAMID_PREFIX)
    assert (tap.message_type, tap.body) == ("interactive", "Sí, pasame")
    conversation = contact.conversation
    assert conversation is not None
    assert conversation.unread_count == 2 and conversation.last_inbound_at == NOW


def test_simulated_contacts_never_reach_meta(db_session: Session) -> None:
    simulator.receive(db_session, TEST_PHONE, "hola", NOW)
    store.contact_conversation(db_session, REAL_WA_ID)  # a real contact
    db_session.commit()
    real = FakeWhatsApp()
    client = _client(db_session, real)
    wa_id = TEST_PHONE[1:]

    wamids = [
        client.send_text(wa_id, "hola"),
        client.send_choices(wa_id, "¿Sí o no?", (Choice("Sí", "Sí"), Choice("No", "No"))),
        client.send_template(wa_id, "recordatorio"),
    ]
    assert all(w.startswith(simulator.WAMID_PREFIX) for w in wamids)
    assert real.sent == []

    client.send_text(REAL_WA_ID, "a un contacto real")
    assert real.sent == [("text", REAL_WA_ID, "a un contacto real")]


def test_simulated_options_are_checked_like_meta_does(db_session: Session) -> None:
    simulator.receive(db_session, TEST_PHONE, "hola", NOW)
    db_session.commit()

    with pytest.raises(ValueError):
        _client(db_session).send_choices(
            TEST_PHONE[1:], "Elegí", (Choice("Un título demasiado largo para un botón", "x"),)
        )


def test_without_whatsapp_configured_only_the_test_chat_works(db_session: Session) -> None:
    simulator.receive(db_session, TEST_PHONE, "hola", NOW)
    db_session.commit()
    client = _client(db_session, real=None)

    assert client.send_text(TEST_PHONE[1:], "hola").startswith(simulator.WAMID_PREFIX)
    with pytest.raises(WhatsAppError, match="no está configurado"):
        client.send_text(REAL_WA_ID, "hola")
    with pytest.raises(WhatsAppError):
        client.get_media("1")


def test_build_client_simulates_only_in_development(db_session: Session) -> None:
    sessions = lambda: nullcontext(db_session)  # noqa: E731
    configured = {"whatsapp_access_token": "token-inventado", "whatsapp_phone_number_id": "1"}

    production = simulator.build_client(Settings(_env_file=None, **configured), sessions)
    development = simulator.build_client(
        Settings(_env_file=None, app_env="development", **configured), sessions
    )
    bare = simulator.build_client(Settings(_env_file=None, app_env="development"), sessions)

    assert isinstance(production, WhatsAppClient)
    assert isinstance(development, simulator.DevClient)
    assert isinstance(development.real, WhatsAppClient)
    assert isinstance(bare, simulator.DevClient) and bare.real is None
    with pytest.raises(WhatsAppError):
        simulator.build_client(Settings(_env_file=None), sessions)


def test_restart_only_touches_simulated_conversations(db_session: Session) -> None:
    simulator.receive(db_session, TEST_PHONE, "hola", NOW)
    real = store.contact_conversation(db_session, REAL_WA_ID)
    db_session.commit()
    found = simulator.simulated_conversation(db_session, TEST_PHONE)
    assert found is not None
    _, conversation = found
    conversation.status = WaConversationStatus.HUMAN
    conversation.assigned_to = "marta"
    db_session.commit()

    assert simulator.restart(db_session, TEST_PHONE)
    assert not simulator.restart(db_session, f"+{REAL_WA_ID}")
    db_session.commit()

    db_session.refresh(conversation)
    assert conversation.status == WaConversationStatus.BOT
    assert conversation.assigned_to is None and conversation.last_inbound_at is None
    left = db_session.scalars(
        select(WaMessage.id).where(WaMessage.conversation_id == conversation.id)
    )
    assert list(left) == []
    assert db_session.get(type(real), real.id) is not None
