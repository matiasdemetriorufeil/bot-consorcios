"""Messages Meta delivers late and out of order (a retry after the webhook was down), the
history the agent gets, and the same reply never sent twice in a row. Invented data only."""

from datetime import timedelta

from app.channels.base import InboundMessage
from app.db.models import (
    WaAuthor,
    WaConversationStatus,
    WaDirection,
    WaMessage,
    WaMessageStatus,
)
from app.llm import AssistantMessage, UserMessage
from app.whatsapp import store
from app.whatsapp.bot import LATE_EVENT, LATE_NOTE, LATE_NOTE_TYPE
from app.whatsapp.channel import REPEAT_EVENT, WaSource, WhatsAppChannel
from app.whatsapp.events import parse_delivery
from tests.llm.fakes import Call, Say
from tests.whatsapp.conftest import MakeWa, Wa
from tests.whatsapp.fakes import CONTACT_WA_ID, PHONE_NUMBER_ID, incoming, text_message

MENU = {"text": "¡Hola! ¿En qué te ayudo?", "options": ["Mi deuda", "Hablar con alguien"]}


def _inbound(wa: Wa) -> list[WaMessage]:
    return [m for m in wa.messages() if m.direction == WaDirection.INBOUND]


def _notes(wa: Wa) -> list[WaMessage]:
    return [m for m in wa.messages() if m.is_internal_note]


# --- Out of order: stored, not answered, left for the studio -----------------------------------


def test_an_older_message_after_a_newer_one_is_not_answered(make_wa: MakeWa) -> None:
    wa = make_wa([Call("offer_choices", MENU), Say("No debería salir")])
    wa.post(incoming(text_message("Hola", wa.now - timedelta(seconds=2))))
    # Sent 5 minutes before, delivered now by a retry of Meta.
    wa.post(incoming(text_message("Hola", wa.now - timedelta(minutes=5))))

    assert wa.fake.sent == [("choices", CONTACT_WA_ID, (MENU["text"], MENU["options"]))]
    assert wa.llm_calls == 1
    newer, late = _inbound(wa)
    assert newer.processing_note is None
    assert (late.processing_note, late.processed_at) == (LATE_NOTE, wa.now)
    [note] = _notes(wa)
    assert (note.author, note.message_type) == (WaAuthor.SYSTEM, LATE_NOTE_TYPE)
    assert "lo mandó el 30/09 a las 10:55" in note.body
    assert "llegó el 30/09 a las 11:00" in note.body and note.body.endswith("Mensaje: «Hola»")
    [event] = wa.events(LATE_EVENT)
    assert event.payload == {"message_id": late.id, "late_seconds": 300}


def test_the_late_note_does_not_hand_off_and_the_bot_goes_on(make_wa: MakeWa) -> None:
    wa = make_wa([Say("Primera"), Say("Sigo atendiendo")])
    wa.post(incoming(text_message("mi unidad es la 4 C", wa.now)))
    wa.post(incoming(text_message("Hola", wa.now - timedelta(minutes=7))))
    conversation = wa.conversation()
    assert conversation is not None and conversation.status == WaConversationStatus.BOT

    wa.post(incoming(text_message("¿seguís ahí?", wa.now + timedelta(seconds=5))))

    assert wa.fake.texts() == ["Primera", "Sigo atendiendo"]


def test_only_delayed_but_still_the_newest_is_answered(make_wa: MakeWa) -> None:
    wa = make_wa([Say("Primera"), Say("Segunda")])
    # Both late (the webhook was down), in the order they were sent.
    wa.post(incoming(text_message("Hola", wa.now - timedelta(minutes=8))))
    wa.post(incoming(text_message("¿Hay alguien?", wa.now - timedelta(minutes=6))))

    assert wa.fake.texts() == ["Primera", "Segunda"]
    assert [m.processing_note for m in _inbound(wa)] == [None, None]
    assert _notes(wa) == [] and wa.events(LATE_EVENT) == []


def test_sent_in_the_same_second_is_not_out_of_order(make_wa: MakeWa) -> None:
    wa = make_wa([Say("Primera"), Say("Segunda")])
    wa.post(incoming(text_message("Hola", wa.now)))
    wa.post(incoming(text_message("Soy inquilino", wa.now)))

    assert wa.fake.texts() == ["Primera", "Segunda"]


def test_with_a_person_in_charge_no_late_note(make_wa: MakeWa) -> None:
    wa = make_wa()
    wa.post(incoming(text_message("Hola", wa.now)))  # no step: fails and hands off
    conversation = wa.conversation()
    assert conversation is not None and conversation.status == WaConversationStatus.WAITING_HUMAN

    wa.post(incoming(text_message("Hola de antes", wa.now - timedelta(minutes=5))))

    assert [n.message_type for n in _notes(wa)] == ["note"]  # only the handoff's
    assert wa.events(LATE_EVENT) == []


# --- History: what was already sent, also after the message was stored ----------------------


def _store(wa: Wa, body: str) -> int:
    """Stored as the webhook does, without answering it (yet)."""
    [message] = parse_delivery(incoming(text_message(body, wa.now)), PHONE_NUMBER_ID).messages
    message_id = store.record_incoming(wa.session, message, wa.now)
    wa.session.commit()
    assert message_id is not None
    return message_id


def test_history_includes_replies_sent_while_the_message_waited(make_wa: MakeWa) -> None:
    wa = make_wa([Say("Menú")])
    first = _store(wa, "Hola")
    waiting = _store(wa, "Quiero registrarme")  # came in while the first was being answered
    wa.bot.process(first)  # its answer goes out after the second was stored
    _store(wa, "otro mensaje que llegó después")
    conversation = wa.conversation()
    assert conversation is not None

    channel = wa.bot.processor.channel
    assert isinstance(channel, WhatsAppChannel)
    history = channel.history(
        InboundMessage(
            conversation_id=conversation.id,
            message_id=waiting,
            phone=f"+{CONTACT_WA_ID}",
            phone_trusted=True,
            content="Quiero registrarme",
            source=WaSource(waiting, CONTACT_WA_ID),
        )
    )

    # The answer already sent is there; neither this message nor a later one of the contact.
    assert history == [UserMessage("Hola"), AssistantMessage("Menú")]


# --- The same reply never twice in a row within 60 s -----------------------------------------


def test_the_same_menu_is_not_sent_twice_in_a_row(make_wa: MakeWa) -> None:
    wa = make_wa([Call("offer_choices", MENU), Call("offer_choices", MENU)])
    wa.post(incoming(text_message("Hola", wa.now)))
    wa.advance(timedelta(seconds=4))
    wa.post(incoming(text_message("Hola", wa.now)))

    assert wa.fake.sent == [("choices", CONTACT_WA_ID, (MENU["text"], MENU["options"]))]
    assert len([m for m in wa.messages() if m.author == WaAuthor.BOT]) == 1
    [event] = wa.events(REPEAT_EVENT)
    assert event.payload == {"message_id": _inbound(wa)[1].id, "kind": "interactive"}
    assert _inbound(wa)[1].processed_at is not None


def test_the_same_text_goes_again_after_60_s(make_wa: MakeWa) -> None:
    wa = make_wa([Say("Dale."), Say("Dale.")])
    wa.post(incoming(text_message("gracias", wa.now)))
    wa.advance(timedelta(seconds=61))
    wa.post(incoming(text_message("gracias de nuevo", wa.now)))

    assert wa.fake.texts() == ["Dale.", "Dale."]
    assert wa.events(REPEAT_EVENT) == []


def test_a_different_text_or_options_go_out(make_wa: MakeWa) -> None:
    other_options = {**MENU, "options": ["Mi deuda", "Registrar reclamo"]}
    wa = make_wa(
        [Call("offer_choices", MENU), Call("offer_choices", other_options), Say(MENU["text"])]
    )
    for text in ("Hola", "Hola otra vez", "y?"):
        wa.post(incoming(text_message(text, wa.now)))
        wa.advance(timedelta(seconds=2))

    # Same text with other options, then the same text alone (a plain text, not buttons).
    assert [kind for kind, _, _ in wa.fake.sent] == ["choices", "choices", "text"]
    assert wa.events(REPEAT_EVENT) == []


def test_after_a_failed_send_the_same_goes_again(make_wa: MakeWa) -> None:
    wa = make_wa([Say("Dale."), Say("Dale.")])
    wa.post(incoming(text_message("gracias", wa.now)))
    sent = next(m for m in wa.messages() if m.author == WaAuthor.BOT)
    sent.status = WaMessageStatus.FAILED  # Meta said it did not go
    wa.session.commit()

    wa.post(incoming(text_message("¿me escuchás?", wa.now)))

    assert wa.fake.texts() == ["Dale.", "Dale."]
