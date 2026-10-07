"""The inbox's logic (app.admin.inbox): tabs, search, contact card, window and actions.
Invented people, phones and messages."""

from contextlib import nullcontext
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin import inbox
from app.channels.base import ConversationTakenError, InboundMessage
from app.db.models import (
    Amenity,
    AmenitySlot,
    BotEvent,
    DebtSnapshot,
    PersonRole,
    Reservation,
    ReservationSource,
    SyncKind,
    Unit,
    WaAuthor,
    WaConversation,
    WaConversationStatus,
    WaMessage,
    WaMessageStatus,
    WaTemplate,
)
from app.whatsapp.channel import WaSource, WhatsAppChannel
from tests.admin.conftest import NOW
from tests.admin.wa_data import conversation, message
from tests.bot import factories as f
from tests.whatsapp.fakes import FakeWhatsApp

TZ = "America/Argentina/Cordoba"
ME = "marta"
OTHER = "lucia"
OWNER_WA = "5493515550101"


@pytest.fixture
def owner(db_session: Session) -> int:
    """Ana, owner of Rodas II 04-C and tenant of Torre Norte 02-B; returns the unit id."""
    rodas = f.building(db_session, "031 RODAS II")
    unit = f.unit(db_session, rodas, "04-C")
    ana = f.person(db_session, "Ana Prueba", phone=f"+{OWNER_WA}")
    f.link(db_session, unit, ana)
    torre = f.building(db_session, "040 TORRE NORTE")
    f.link(db_session, f.unit(db_session, torre, "02-B"), ana, PersonRole.TENANT)
    return unit.id


def _ids(rows: list[inbox.InboxRow]) -> list[int]:
    return [r.id for r in rows]


def _events(session: Session, action: str) -> list[BotEvent]:
    session.expire_all()
    rows = session.scalars(
        select(BotEvent).where(BotEvent.event_type == "admin_action").order_by(BotEvent.id)
    )
    return [e for e in rows if e.payload["action"] == action]


# --- Tabs and search ------------------------------------------------------------------------


def test_waiting_tab_puts_urgent_first_then_the_oldest_handoff(db_session: Session) -> None:
    waiting = WaConversationStatus.WAITING_HUMAN
    old = conversation(
        db_session, "5493515550201", status=waiting, handed_off_at=NOW - timedelta(hours=2)
    )
    new = conversation(
        db_session, "5493515550202", status=waiting, handed_off_at=NOW - timedelta(minutes=5)
    )
    urgent = conversation(
        db_session,
        "5493515550203",
        status=waiting,
        handed_off_at=NOW - timedelta(minutes=1),
        handoff_priority="urgent",
        handoff_reason="emergency",
    )
    conversation(db_session, "5493515550204")  # with the bot

    rows = inbox.list_conversations(db_session, "waiting", ME, NOW)

    assert _ids(rows) == [urgent.id, old.id, new.id]
    assert rows[0].urgent and rows[0].reason == ("Urgencia", "Urgencia en el edificio")


def test_mine_bot_and_resolved_tabs(db_session: Session) -> None:
    mine = conversation(
        db_session, "5493515550201", status=WaConversationStatus.HUMAN, assigned_to=ME
    )
    conversation(db_session, "5493515550202", status=WaConversationStatus.HUMAN, assigned_to=OTHER)
    bot = conversation(db_session, "5493515550203")
    resolved = conversation(
        db_session, "5493515550204", status=WaConversationStatus.RESOLVED, resolved_at=NOW
    )

    assert _ids(inbox.list_conversations(db_session, "mine", ME, NOW)) == [mine.id]
    assert _ids(inbox.list_conversations(db_session, "bot", ME, NOW)) == [bot.id]
    assert _ids(inbox.list_conversations(db_session, "resolved", ME, NOW)) == [resolved.id]
    assert inbox.tab_counts(db_session, ME) == {"waiting": 0, "mine": 1}


def test_row_shows_identified_units_unread_and_how_long_ago(
    db_session: Session, owner: int
) -> None:
    conv = conversation(db_session, OWNER_WA, unread_count=3)
    message(db_session, conv, "¿cuánto debo?", at=NOW - timedelta(minutes=5))

    [row] = inbox.list_conversations(db_session, "bot", ME, NOW)

    assert row.name == "Ana Prueba"
    assert [u.text for u in row.units] == ["RODAS II · 04-C", "TORRE NORTE · 02-B"]
    assert row.unread == 3
    assert row.ago == "hace 5 min"
    assert row.preview == "¿cuánto debo?"


def test_unknown_contact_shows_its_whatsapp_name(db_session: Session) -> None:
    conversation(db_session, "5493515550999", profile_name="Perfil Inventado")

    [row] = inbox.list_conversations(db_session, "bot", ME, NOW)

    assert row.name == "Perfil Inventado" and row.units == []


@pytest.mark.parametrize("term", ["ana prueba", "550101", "0351 555-0101", "04-C", "rodas"])
def test_search_by_name_phone_or_unit_in_every_state(
    db_session: Session, owner: int, term: str
) -> None:
    ana = conversation(db_session, OWNER_WA, status=WaConversationStatus.RESOLVED)
    conversation(db_session, "5493515550999", profile_name="Otra Persona")

    rows = inbox.list_conversations(db_session, "waiting", ME, NOW, query=term)

    assert _ids(rows) == [ana.id]


def test_search_by_whatsapp_profile_name(db_session: Session) -> None:
    other = conversation(db_session, "5493515550999", profile_name="Perfil Inventado")

    rows = inbox.list_conversations(db_session, "bot", ME, NOW, query="perfil")

    assert _ids(rows) == [other.id]


def test_human_rows_tell_who_has_it(db_session: Session) -> None:
    conversation(db_session, "5493515550201", status=WaConversationStatus.HUMAN, assigned_to=OTHER)
    conversation(db_session, "5493515550202", status=WaConversationStatus.HUMAN)

    rows = inbox.list_conversations(
        db_session, "bot", ME, NOW, query="contacto", names={OTHER: "Lucía Inventada"}
    )

    assert {(r.assigned_to, r.from_phone_app) for r in rows} == {
        ("Lucía Inventada", False),
        (None, True),
    }


def test_window_closed_handoff_shows_its_label(db_session: Session) -> None:
    conversation(
        db_session,
        status=WaConversationStatus.WAITING_HUMAN,
        handoff_reason="window_closed",
        handed_off_at=NOW,
    )

    [row] = inbox.list_conversations(db_session, "waiting", ME, NOW)

    assert row.reason == ("Ventana cerrada", "Ventana de 24 h cerrada: el bot no pudo responder")


# --- Contact card and thread ------------------------------------------------------------------


def test_contact_card(db_session: Session, owner: int) -> None:
    conv = conversation(db_session, OWNER_WA)
    db_session.add(
        DebtSnapshot(
            unit_id=owner,
            source=SyncKind.NIGHTLY,
            total_amount=Decimal("1000"),
            is_up_to_date=True,
            fetched_at=NOW - timedelta(days=3),
        )
    )
    db_session.add(
        DebtSnapshot(
            unit_id=owner,
            source=SyncKind.LIVE,
            total_amount=Decimal("165060"),
            is_up_to_date=False,
            fetched_at=NOW - timedelta(hours=2),
        )
    )
    unit = db_session.get(Unit, owner)
    assert unit is not None
    amenity = Amenity(building_id=unit.building_id)
    db_session.add(amenity)
    db_session.flush()
    slot = AmenitySlot(amenity_id=amenity.id, weekday=4, start_time=time(20), end_time=time(2))
    db_session.add(slot)
    db_session.flush()
    for day in (NOW.date() + timedelta(days=2), NOW.date() - timedelta(days=5)):
        db_session.add(
            Reservation(
                amenity_id=amenity.id,
                slot_id=slot.id,
                date=day,
                unit_id=owner,
                source=ReservationSource.PANEL,
            )
        )
    db_session.flush()

    card = inbox.contact_card(db_session, conv.contact, TZ, NOW.date())

    assert card.known and card.name == "Ana Prueba" and not card.verified
    owner_unit, tenant_unit = card.units
    assert owner_unit.unit.role == "propietario" and tenant_unit.unit.role == "inquilino"
    assert owner_unit.debt is not None
    assert owner_unit.debt.total == "$165.060,00" and not owner_unit.debt.up_to_date
    assert owner_unit.debt.fetched_at == "hoy 09:00"  # the card's day, in Argentina's time
    assert tenant_unit.debt is None
    [reservation] = card.reservations  # only the upcoming one
    assert reservation.day == NOW.date() + timedelta(days=2)
    assert reservation.slot.startswith("20:00 a 02:00")


def test_unknown_contact_card(db_session: Session) -> None:
    conv = conversation(db_session, "5493515550999")

    card = inbox.contact_card(db_session, conv.contact, TZ, date(2026, 9, 30))

    assert not card.known and card.units == [] and card.reservations == []


def test_thread_tells_authors_apart(db_session: Session) -> None:
    conv = conversation(db_session)
    message(db_session, conv, "hola")
    message(db_session, conv, "¡Hola!", author=WaAuthor.BOT)
    message(db_session, conv, "Soy Marta", author=WaAuthor.OPERATOR, operator=ME)
    message(db_session, conv, "Desde el celu", author=WaAuthor.OPERATOR)
    message(
        db_session,
        conv,
        "🤖 Derivado por el bot",
        author=WaAuthor.SYSTEM,
        message_type="note",
        is_internal_note=True,
        status=None,
    )

    views = inbox.thread(db_session, conv.id, {ME: "Marta Inventada"}, TZ)

    assert [(v.side, v.author, v.author_name) for v in views] == [
        ("in", "contact", ""),
        ("out", "bot", "Bot"),
        ("out", "operator", "Marta Inventada"),
        ("out", "operator", "Desde el celular"),
        ("note", "system", "Sistema"),
    ]
    assert views[-1].is_handoff_note
    assert [v.id for v in inbox.thread(db_session, conv.id, {}, TZ, after_id=views[2].id)] == [
        views[3].id,
        views[4].id,
    ]


def test_window_open_and_closed(db_session: Session) -> None:
    conv = conversation(db_session, last_inbound_at=NOW - timedelta(hours=23, minutes=59))
    assert inbox.window_open(conv, NOW)
    conv.last_inbound_at = NOW - timedelta(hours=24)
    assert not inbox.window_open(conv, NOW)
    conv.last_inbound_at = None
    assert not inbox.window_open(conv, NOW)


# --- Actions ----------------------------------------------------------------------------------


def _reload(session: Session, conv: WaConversation) -> WaConversation:
    session.expire_all()
    row = session.get(WaConversation, conv.id)
    assert row is not None
    return row


def test_take_return_resolve_are_audited(db_session: Session) -> None:
    conv = conversation(db_session, status=WaConversationStatus.WAITING_HUMAN)

    inbox.take(db_session, conv.id, ME)
    assert (_reload(db_session, conv).status, conv.assigned_to) == (
        WaConversationStatus.HUMAN,
        ME,
    )
    inbox.return_to_bot(db_session, conv.id, ME)
    assert _reload(db_session, conv).status == WaConversationStatus.BOT
    assert conv.assigned_to is None
    inbox.resolve(db_session, conv.id, ME, NOW)
    assert _reload(db_session, conv).status == WaConversationStatus.RESOLVED

    for action in ("conversation_taken", "conversation_returned_to_bot", "conversation_resolved"):
        [event] = _events(db_session, action)
        assert event.payload["admin_user"] == ME
        assert event.conversation_id == conv.id
        assert event.phone_e164 == "+5493515550101"
    [taken] = _events(db_session, "conversation_taken")
    assert taken.payload["previous_status"] == "waiting_human"


def test_note_is_internal_and_its_text_is_not_audited(db_session: Session) -> None:
    conv = conversation(db_session)

    note = inbox.add_note(db_session, conv.id, ME, "  Llamar mañana  ")

    assert note.is_internal_note and note.body == "Llamar mañana" and note.operator == ME
    [event] = _events(db_session, "conversation_note_added")
    assert event.payload["message_id"] == note.id
    assert "Llamar" not in str(event.payload)
    with pytest.raises(inbox.InboxError):
        inbox.add_note(db_session, conv.id, ME, "   ")


def test_reply_takes_the_conversation_and_sends(db_session: Session) -> None:
    conv = conversation(db_session, status=WaConversationStatus.WAITING_HUMAN)
    fake = FakeWhatsApp()

    result = inbox.reply(db_session, conv.id, ME, "Hola, soy Marta", fake, NOW)

    assert result.sent and result.assigned_to_other is None
    assert fake.sent == [("text", "5493515550101", "Hola, soy Marta")]
    conv = _reload(db_session, conv)
    assert conv.status == WaConversationStatus.HUMAN and conv.assigned_to == ME
    row = db_session.get(WaMessage, result.message_id)
    assert row is not None
    assert (row.author, row.operator, row.status) == (WaAuthor.OPERATOR, ME, WaMessageStatus.SENT)
    [event] = _events(db_session, "conversation_replied")
    assert event.payload["message_id"] == row.id and "Hola" not in str(event.payload)


def test_reply_to_a_conversation_another_operator_has_keeps_her(db_session: Session) -> None:
    conv = conversation(db_session, status=WaConversationStatus.HUMAN, assigned_to=OTHER)

    result = inbox.reply(db_session, conv.id, ME, "Te ayudo yo", FakeWhatsApp(), NOW)

    assert result.assigned_to_other == OTHER
    assert _reload(db_session, conv).assigned_to == OTHER


def test_reply_with_the_window_closed_sends_nothing(db_session: Session) -> None:
    conv = conversation(db_session, last_inbound_at=NOW - timedelta(hours=25))
    fake = FakeWhatsApp()

    with pytest.raises(inbox.InboxError, match="24 h"):
        inbox.reply(db_session, conv.id, ME, "Hola", fake, NOW)

    assert fake.sent == []
    assert _reload(db_session, conv).status == WaConversationStatus.BOT  # not taken either


def test_a_rejected_reply_is_stored_failed(db_session: Session) -> None:
    conv = conversation(db_session, status=WaConversationStatus.HUMAN, assigned_to=ME)
    fake = FakeWhatsApp(fail_on={"text"})

    result = inbox.reply(db_session, conv.id, ME, "Hola", fake, NOW)

    assert not result.sent and "rechazado" in result.error
    row = db_session.get(WaMessage, result.message_id)
    assert row is not None and row.status == WaMessageStatus.FAILED


def test_template_goes_outside_the_window(db_session: Session) -> None:
    conv = conversation(db_session, last_inbound_at=NOW - timedelta(days=3))
    template = WaTemplate(name="seguimiento", label="Seguimiento", body="¿Pudiste resolverlo?")
    db_session.add(template)
    db_session.flush()
    fake = FakeWhatsApp()

    result = inbox.send_template(db_session, conv.id, ME, template.id, fake, NOW)

    assert result.sent
    assert fake.sent == [("template", "5493515550101", ("seguimiento", "es_AR"))]
    row = db_session.get(WaMessage, result.message_id)
    assert row is not None
    assert (row.message_type, row.body) == ("template", "¿Pudiste resolverlo?")
    [event] = _events(db_session, "conversation_template_sent")
    assert event.payload["template_id"] == template.id


def test_without_whatsapp_nothing_is_sent(db_session: Session) -> None:
    conv = conversation(db_session)

    with pytest.raises(inbox.InboxError, match="no está configurado"):
        inbox.reply(db_session, conv.id, ME, "Hola", None, NOW)


def test_once_taken_the_bot_sends_nothing(db_session: Session) -> None:
    """The panel and the bot lock the same row: after "Tomar control" the bot's next part
    does not go out."""
    conv = conversation(db_session)
    fake = FakeWhatsApp()
    channel = WhatsAppChannel(fake, lambda: nullcontext(db_session), now=lambda: NOW)  # type: ignore[arg-type]
    incoming = message(db_session, conv, "hola")
    inbound = InboundMessage(
        conversation_id=conv.id,
        message_id=incoming.id,
        phone="+5493515550101",
        phone_trusted=True,
        content="hola",
        source=WaSource(message_id=incoming.id, wa_id="5493515550101"),
    )
    channel.send_text(inbound, "primera parte", more=True)

    inbox.take(db_session, conv.id, ME)

    with pytest.raises(ConversationTakenError):
        channel.send_text(inbound, "segunda parte", more=False)
    assert fake.texts() == ["primera parte"]
