"""WhatsAppChannel: history, the 24-hour window, order of a split turn, and the
conversation states service. Invented data only."""

from contextlib import nullcontext
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.bot.choices import Choice
from app.channels import outgoing
from app.channels import processor as core
from app.channels.base import InboundMessage
from app.channels.locks import WHATSAPP_LOCK_NAMESPACE
from app.db.models import Unit, WaAuthor, WaConversationStatus, WaDirection, WaMessage
from app.llm import AssistantMessage, UserMessage
from app.whatsapp import conversations
from app.whatsapp.channel import WaSource, WhatsAppChannel
from app.whatsapp.client import WindowClosedError
from tests.llm.fakes import Call, Say
from tests.whatsapp.conftest import MakeWa, Wa
from tests.whatsapp.fakes import CONTACT_WA_ID, incoming, text_message


def _channel(wa: Wa) -> WhatsAppChannel:
    channel = wa.bot.processor.channel
    assert isinstance(channel, WhatsAppChannel)
    return channel


def _inbound(wa: Wa) -> InboundMessage:
    """The last message of the contact, as the processor gets it."""
    last = [m for m in wa.messages() if m.direction == WaDirection.INBOUND][-1]
    return InboundMessage(
        conversation_id=last.conversation_id,
        message_id=last.id,
        phone=f"+{CONTACT_WA_ID}",
        phone_trusted=True,
        content=last.body or "",
        source=WaSource(last.id, CONTACT_WA_ID),
    )


def test_history_alternates_skips_notes_and_keeps_options(make_wa: MakeWa) -> None:
    offer = {"text": "¿Te paso con una persona?", "options": ["Sí, pasame", "No, gracias"]}
    wa = make_wa([Say("¡Hola!"), Call("offer_choices", offer)])
    wa.post(incoming(text_message("hola", wa.now)))
    wa.post(incoming(text_message("tengo un problema", wa.now)))
    conversation = wa.conversation()
    assert conversation is not None
    wa.session.add(
        WaMessage(
            conversation_id=conversation.id,
            direction=WaDirection.OUTBOUND,
            author=WaAuthor.SYSTEM,
            message_type="note",
            body="nota interna que el bot no ve",
            is_internal_note=True,
        )
    )
    wa.session.commit()
    wa.post(incoming(text_message("Sí, pasame", wa.now)))  # no step left: the agent fails

    history = _channel(wa).history(_inbound(wa))

    assert history == [
        UserMessage("hola"),
        AssistantMessage("¡Hola!"),
        UserMessage("tengo un problema"),
        AssistantMessage("¿Te paso con una persona?\n[Opciones: Sí, pasame / No, gracias]"),
    ]


def test_history_joins_consecutive_messages_and_operator_turns(make_wa: MakeWa) -> None:
    wa = make_wa()
    wa.post(incoming(text_message("hola", wa.now)))  # no step: handed off (technical_error)
    conversation = wa.conversation()
    assert conversation is not None
    conversations.return_to_bot(conversation)
    wa.session.add(
        WaMessage(
            conversation_id=conversation.id,
            direction=WaDirection.OUTBOUND,
            author=WaAuthor.OPERATOR,
            message_type="text",
            body="Hola, soy Marta.",
        )
    )
    wa.session.commit()
    wa.script.steps.append(Say("ok"))
    wa.post(incoming(text_message("gracias", wa.now)))

    history = _channel(wa).history(_inbound(wa))

    assert [type(m) for m in history] == [UserMessage, AssistantMessage]
    assert history[1].text.endswith("Hola, soy Marta.")  # bot fallback + operator, joined


def test_window_open_and_closed(make_wa: MakeWa) -> None:
    wa = make_wa([Say("¡Hola!")])
    wa.post(incoming(text_message("hola", wa.now)))
    channel = _channel(wa)
    message = _inbound(wa)

    wa.advance(timedelta(hours=23, minutes=59))
    channel.send_text(message, "todavía puedo", more=False)
    wa.advance(timedelta(minutes=1))
    with pytest.raises(WindowClosedError):
        channel.send_text(message, "ya no", more=False)
    with pytest.raises(WindowClosedError):
        channel.send_choices(message, "ni con botones", (Choice("Sí", "Sí"), Choice("No", "No")))

    assert wa.fake.texts() == ["¡Hola!", "todavía puedo"]
    # Outside the window only a template can go (the client has no window check).
    wa.fake.send_template(CONTACT_WA_ID, "seguimiento")
    assert wa.fake.kinds()[-1] == "template"


SPLIT_BLOCK = "*RODAS II 04-C*\nEsta unidad no tiene cargado un código de pago."


def test_a_split_turn_goes_in_order(make_wa: MakeWa, monkeypatch: pytest.MonkeyPatch) -> None:
    wa = make_wa()
    unit_id = wa.session.scalar(select(Unit.id).where(Unit.label == "04-C"))
    wa.script.steps.extend(
        [Say("¡Hola!"), Call("get_payment_info", {"unit_id": unit_id}), Say("¿Algo más?")]
    )
    wa.post(incoming(text_message("hola", wa.now)))  # not the first message: no greeting
    sent_before = len(wa.fake.sent)

    # Parts of 60 characters at most: the payment block alone needs more than one.
    monkeypatch.setattr(core, "pack", lambda parts: outgoing.pack(parts, limit=60))
    wa.post(incoming(text_message("¿cómo pago?", wa.now)))

    parts = wa.fake.texts()[sent_before:]
    assert len(parts) >= 3
    assert parts[0].startswith("*RODAS II 04-C*")
    assert parts[-1].endswith("¿Algo más?")
    stored = [m.body for m in wa.messages() if m.author == WaAuthor.BOT][-len(parts) :]
    assert stored == parts  # stored in the same order they went out


def test_conversation_states_service(make_wa: MakeWa) -> None:
    wa = make_wa([Say("¡Hola!")])
    wa.post(incoming(text_message("hola", wa.now)))
    conversation = wa.conversation()
    assert conversation is not None

    conversations.assign(conversation, "operadora")
    assert (conversation.status, conversation.assigned_to) == (
        WaConversationStatus.HUMAN,
        "operadora",
    )
    conversations.resolve(conversation, wa.now)
    assert conversation.status == WaConversationStatus.RESOLVED
    assert (conversation.resolved_at, conversation.unread_count) == (wa.now, 0)
    conversations.on_inbound(conversation, 999, wa.now)
    assert conversation.status == WaConversationStatus.BOT
    assert (conversation.bot_since_message_id, conversation.assigned_to) == (999, None)
    conversations.taken_by_operator(conversation)
    conversations.return_to_bot(conversation)
    assert conversation.status == WaConversationStatus.BOT
    assert conversation.bot_since_message_id == 999  # same stretch


def test_the_processor_takes_the_conversation_lock(make_wa: MakeWa) -> None:
    wa = make_wa([Say("¡Hola!")])
    taken: list[tuple[int, int]] = []

    def lock(namespace: int, conversation_id: int):  # type: ignore[no-untyped-def]
        taken.append((namespace, conversation_id))
        return nullcontext()

    wa.bot.processor._lock = lock

    wa.post(incoming(text_message("hola", wa.now)))

    conversation = wa.conversation()
    assert conversation is not None
    assert taken == [(WHATSAPP_LOCK_NAMESPACE, conversation.id)]
