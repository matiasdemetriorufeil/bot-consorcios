"""GET/POST /webhooks/whatsapp: verification, signature, idempotency, statuses, echoes, and
the bot answering through the Cloud API (faked). Invented data only."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.bot.agent import FALLBACK_REPLY
from app.channels.processor import ATTACHMENT_REPLY, NON_PILOT_GREETING, UNSUPPORTED_REPLY
from app.config import Settings, get_settings
from app.db.models import (
    WaAuthor,
    WaConversationStatus,
    WaDirection,
    WaMessage,
    WaMessageStatus,
)
from app.main import app
from app.whatsapp.webhook import verify_signature
from tests.llm.fakes import Call, Say
from tests.whatsapp.conftest import NON_PILOT_WA_ID, OWNER_PHONE, MakeWa
from tests.whatsapp.fakes import (
    APP_SECRET,
    CONTACT_WA_ID,
    OTHER_PHONE_NUMBER_ID,
    VERIFY_TOKEN,
    FakeWhatsApp,
    body_of,
    button_reply,
    echoes,
    incoming,
    list_reply,
    media_message,
    reaction,
    signed_headers,
    status,
    statuses,
    text_message,
)

HELLO = "¡Hola! Soy el asistente automático."


# --- Signature and verification -------------------------------------------------------------


def test_verify_signature() -> None:
    body = b'{"object":"whatsapp_business_account"}'
    good = signed_headers(body)["X-Hub-Signature-256"]

    assert verify_signature(APP_SECRET, body, good)
    assert not verify_signature("otro-secreto", body, good)
    assert not verify_signature(APP_SECRET, body + b" ", good)
    assert not verify_signature(APP_SECRET, body, None)
    assert not verify_signature(APP_SECRET, body, good.removeprefix("sha256="))


def test_meta_verification(make_wa: MakeWa) -> None:
    wa = make_wa()
    params = {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1234"}

    ok = wa.client.get("/webhooks/whatsapp", params=params)
    bad = wa.client.get("/webhooks/whatsapp", params={**params, "hub.verify_token": "otro"})
    no_mode = wa.client.get("/webhooks/whatsapp", params={**params, "hub.mode": "unsubscribe"})

    assert (ok.status_code, ok.text) == (200, "1234")
    assert bad.status_code == 403
    assert no_mode.status_code == 403


def test_verification_without_token_configured(make_wa: MakeWa) -> None:
    wa = make_wa(whatsapp_verify_token=None)

    response = wa.client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe"})

    assert response.status_code == 503


def test_rejects_bad_or_missing_signature(make_wa: MakeWa) -> None:
    wa = make_wa([Say(HELLO)])
    body = body_of(incoming(text_message("hola", wa.now)))

    bad = wa.client.post(
        "/webhooks/whatsapp", content=body, headers=signed_headers(body, "otro-secreto")
    )
    missing = wa.client.post("/webhooks/whatsapp", content=body)

    assert bad.status_code == 401
    assert missing.status_code == 401
    assert wa.messages() == []
    assert wa.fake.sent == []


def test_without_app_secret_rejects_everything(make_wa: MakeWa) -> None:
    wa = make_wa(whatsapp_app_secret=None)

    assert wa.post(incoming(text_message("hola", wa.now))).status_code == 503


def test_404_when_the_channel_is_chatwoot(make_wa: MakeWa) -> None:
    wa = make_wa()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)

    assert wa.post(incoming(text_message("hola", wa.now))).status_code == 404
    assert wa.client.get("/webhooks/whatsapp").status_code == 404


def test_the_chatwoot_webhook_404s_with_channel_whatsapp(make_wa: MakeWa) -> None:
    wa = make_wa()

    assert wa.client.post("/webhooks/chatwoot", content=b"{}").status_code == 404


# --- Messages -------------------------------------------------------------------------------


def test_accepts_stores_and_answers_in_the_background(make_wa: MakeWa) -> None:
    wa = make_wa([Say(HELLO)])

    response = wa.post(incoming(text_message("hola", wa.now - timedelta(minutes=1))))

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    # TestClient runs the background task before returning.
    assert wa.fake.sent == [("text", CONTACT_WA_ID, HELLO)]
    contact_msg, bot_msg = wa.messages()
    assert (contact_msg.author, contact_msg.direction) == (WaAuthor.CONTACT, WaDirection.INBOUND)
    assert contact_msg.body == "hola"
    assert contact_msg.processed_at is not None
    assert (bot_msg.author, bot_msg.status, bot_msg.body) == (
        WaAuthor.BOT,
        WaMessageStatus.SENT,
        HELLO,
    )
    assert bot_msg.wa_message_id == wa.fake.wamids[0]
    conversation = wa.conversation()
    assert conversation is not None
    assert conversation.status == WaConversationStatus.BOT
    assert conversation.unread_count == 1
    assert conversation.last_inbound_at == wa.now - timedelta(minutes=1)
    assert conversation.contact.profile_name == "Contacto Prueba"
    assert conversation.contact.phone_e164 == OWNER_PHONE


def test_a_duplicate_delivery_is_answered_once(make_wa: MakeWa) -> None:
    wa = make_wa([Say(HELLO), Say("No debería salir")])
    payload = incoming(text_message("hola", wa.now, message_id="wamid.DUPLICADO"))

    first = wa.post(payload)
    second = wa.post(payload)

    assert first.status_code == second.status_code == 200
    assert wa.fake.texts() == [HELLO]
    assert wa.llm_calls == 1
    inbound = wa.session.scalar(
        select(func.count()).select_from(WaMessage).where(WaMessage.direction == "inbound")
    )
    assert inbound == 1
    conversation = wa.conversation()
    assert conversation is not None and conversation.unread_count == 1


def test_another_phone_number_is_ignored(make_wa: MakeWa) -> None:
    wa = make_wa([Say(HELLO)])

    response = wa.post(
        incoming(text_message("hola", wa.now), phone_number_id=OTHER_PHONE_NUMBER_ID)
    )

    assert response.json() == {"status": "ignored"}
    assert wa.messages() == []


@pytest.mark.parametrize("tap", [button_reply, list_reply])
def test_an_option_tapped_reaches_the_agent_as_its_title(make_wa: MakeWa, tap) -> None:  # type: ignore[no-untyped-def]
    wa = make_wa([Say("Dale.")])

    wa.post(incoming(tap("Sí, pasame", wa.now)))

    assert "Sí, pasame" in str(wa.script.requests[0])
    assert wa.messages()[0].body == "Sí, pasame"


def test_replies_with_buttons_and_keeps_the_titles(make_wa: MakeWa) -> None:
    choices = {"text": "¿Te paso con una persona?", "options": ["Sí, pasame", "No, gracias"]}
    wa = make_wa([Call("offer_choices", choices)])

    wa.post(incoming(text_message("tengo un problema", wa.now)))

    assert wa.fake.kinds() == ["choices"]
    _, _, (text, titles) = wa.fake.sent[0]
    assert titles == ["Sí, pasame", "No, gracias"]
    bot_msg = wa.messages()[-1]
    assert bot_msg.message_type == "interactive"
    assert bot_msg.choices == ["Sí, pasame", "No, gracias"]


def test_options_rejected_by_meta_go_numbered(make_wa: MakeWa) -> None:
    choices = {"text": "¿Te paso con una persona?", "options": ["Sí, pasame", "No, gracias"]}
    wa = make_wa([Call("offer_choices", choices)], fake=FakeWhatsApp(fail_on={"choices"}))

    wa.post(incoming(text_message("tengo un problema", wa.now)))

    [text] = wa.fake.texts()
    assert "1. Sí, pasame" in text and "2. No, gracias" in text
    failed, sent = wa.messages()[-2:]
    assert failed.status == WaMessageStatus.FAILED
    assert failed.error_code == 131000
    assert sent.status == WaMessageStatus.SENT


def test_audio_and_photo_get_the_fixed_replies(make_wa: MakeWa) -> None:
    wa = make_wa()
    wa.fake.media["900000000000001"] = {"url": "https://x/a", "mime_type": "audio/ogg"}
    wa.fake.files["https://x/a"] = b"ogg"
    wa.fake.media["900000000000002"] = {"url": "https://x/f", "mime_type": "image/jpeg"}
    wa.fake.files["https://x/f"] = b"jpg"

    wa.post(incoming(media_message("audio", wa.now, mime="audio/ogg; codecs=opus")))
    wa.post(incoming(media_message("image", wa.now, media_id="900000000000002")))

    assert wa.fake.texts() == [UNSUPPORTED_REPLY, ATTACHMENT_REPLY]
    assert wa.llm_calls == 0


def test_a_photo_with_caption_goes_to_the_agent_with_the_note(make_wa: MakeWa) -> None:
    wa = make_wa([Say("Veo que mandaste una foto.")])
    wa.fake.media["900000000000001"] = {"url": "https://x/f", "mime_type": "image/jpeg"}
    wa.fake.files["https://x/f"] = b"jpg"

    wa.post(incoming(media_message("image", wa.now, caption="se rompió el portón")))

    request = str(wa.script.requests[0])
    assert "se rompió el portón" in request
    assert "adjunto (image)" in request


def test_a_reaction_is_stored_but_not_answered(make_wa: MakeWa) -> None:
    wa = make_wa()

    wa.post(incoming(reaction(wa.now, "wamid.OTRO")))

    assert wa.fake.sent == []
    [message] = wa.messages()
    assert message.message_type == "reaction"
    assert message.processing_note == "silent"


def test_a_non_pilot_owner_is_handed_off(make_wa: MakeWa) -> None:
    wa = make_wa()

    wa.post(incoming(text_message("hola", wa.now), wa_id=NON_PILOT_WA_ID))

    [text] = wa.fake.texts()
    assert text.startswith(NON_PILOT_GREETING)
    conversation = wa.conversation(NON_PILOT_WA_ID)
    assert conversation is not None
    assert conversation.status == WaConversationStatus.WAITING_HUMAN
    assert conversation.handoff_reason == "non_pilot"
    assert "fuera-de-piloto" in conversation.handoff_labels


# --- Conversation states --------------------------------------------------------------------


HANDOFF = Call(
    "handoff_to_human",
    {"reason": "person_requested", "summary": "Pide hablar con alguien.", "priority": "urgent"},
)


def test_handoff_waits_for_a_human_with_note_and_fields(make_wa: MakeWa) -> None:
    wa = make_wa([HANDOFF, Say("Ya le pasé tu consulta a una persona del estudio.")])

    wa.post(incoming(text_message("quiero hablar con una persona", wa.now)))

    conversation = wa.conversation()
    assert conversation is not None
    assert conversation.status == WaConversationStatus.WAITING_HUMAN
    assert (conversation.handoff_reason, conversation.handoff_priority) == (
        "person_requested",
        "urgent",
    )
    assert conversation.handoff_summary == "Pide hablar con alguien."
    assert {"pide-persona", "urgente", "edificio-rodas-ii"} <= set(conversation.handoff_labels)
    assert conversation.handed_off_at == wa.now
    note = wa.messages()[-1]
    assert note.is_internal_note and note.author == WaAuthor.SYSTEM
    assert note.body is not None and note.body.startswith("🤖 Derivado por el bot")
    assert note.wa_message_id is None
    assert len(wa.fake.texts()) == 1  # the note never goes to WhatsApp


@pytest.mark.parametrize("state", [WaConversationStatus.WAITING_HUMAN, WaConversationStatus.HUMAN])
def test_the_bot_stays_quiet_with_a_human(make_wa: MakeWa, state: WaConversationStatus) -> None:
    wa = make_wa([Say(HELLO)])
    wa.post(incoming(reaction(wa.now, "wamid.X")))  # creates the conversation
    conversation = wa.conversation()
    assert conversation is not None
    conversation.status = state
    wa.session.commit()

    wa.post(incoming(text_message("¿hay alguien?", wa.now)))
    wa.post(incoming(text_message("hola??", wa.now)))

    assert wa.fake.sent == []
    assert wa.llm_calls == 0
    conversation = wa.conversation()
    assert conversation is not None
    assert conversation.status == state
    assert conversation.unread_count == 3
    assert len(wa.events("whatsapp_skipped")) == 2


def test_resolved_goes_back_to_the_bot_with_a_fresh_history(make_wa: MakeWa) -> None:
    wa = make_wa([Say(HELLO), Say("¡Hola de nuevo!")])
    wa.post(incoming(text_message("hola", wa.now)))
    conversation = wa.conversation()
    assert conversation is not None
    conversation.status = WaConversationStatus.RESOLVED
    conversation.handoff_reason = "other"
    wa.session.commit()

    wa.post(incoming(text_message("buenas", wa.now)))

    assert wa.fake.texts() == [HELLO, "¡Hola de nuevo!"]
    conversation = wa.conversation()
    assert conversation is not None
    assert conversation.status == WaConversationStatus.BOT
    assert conversation.handoff_reason is None
    assert conversation.bot_since_message_id == wa.messages()[2].id
    # The second turn did not see the first one: a new stretch of the bot.
    second = str(wa.script.requests[1])
    assert "buenas" in second and HELLO not in second


def test_reply_dropped_if_a_human_took_it_meanwhile(make_wa: MakeWa) -> None:
    wa = make_wa([Say(HELLO)])
    agent = wa.bot.processor.agent
    think = agent.reply

    def reply_after_an_operator_took_it(*args, **kwargs):  # type: ignore[no-untyped-def]
        conversation = wa.conversation()
        assert conversation is not None
        conversation.status = WaConversationStatus.HUMAN  # e.g. a message from the phone app
        wa.session.commit()
        return think(*args, **kwargs)

    agent.reply = reply_after_an_operator_took_it  # type: ignore[method-assign]

    wa.post(incoming(text_message("hola", wa.now)))

    assert wa.fake.sent == []
    assert len(wa.events("whatsapp_reply_dropped")) == 1


def test_taken_between_two_parts_of_a_turn_the_rest_is_dropped(make_wa: MakeWa) -> None:
    long_reply = "\n\n".join(["Un párrafo largo de la respuesta. " * 40] * 6)  # > 4000: split
    wa = make_wa([Say(long_reply)])
    send_text = wa.fake.send_text

    def send_then_an_operator_takes_it(to: str, text: str) -> str:
        wamid = send_text(to, text)
        conversation = wa.conversation()
        assert conversation is not None
        conversation.status = WaConversationStatus.HUMAN  # the panel's "Tomar control"
        wa.session.commit()
        return wamid

    wa.fake.send_text = send_then_an_operator_takes_it  # type: ignore[method-assign]

    wa.post(incoming(text_message("hola", wa.now)))

    assert len(wa.fake.texts()) == 1  # the second part never went out
    [event] = wa.events("whatsapp_reply_dropped")
    assert event.payload["while_sending"] is True
    conversation = wa.conversation()
    assert conversation is not None
    assert conversation.status == WaConversationStatus.HUMAN  # no handoff, no fallback
    assert conversation.handoff_reason is None


def test_an_error_hands_off_with_the_fixed_message(make_wa: MakeWa) -> None:
    wa = make_wa()

    def broken() -> object:
        raise RuntimeError("falta la clave del LLM")

    wa.bot.processor._agent_factory = broken  # type: ignore[assignment]

    wa.post(incoming(text_message("hola", wa.now)))

    [text] = wa.fake.texts()
    assert text.startswith(FALLBACK_REPLY)
    conversation = wa.conversation()
    assert conversation is not None
    assert conversation.status == WaConversationStatus.WAITING_HUMAN
    assert conversation.handoff_reason == "technical_error"
    assert wa.messages()[0].processed_at is not None


# --- 24-hour window -------------------------------------------------------------------------


def test_outside_the_24_hour_window_nothing_free_goes_out(make_wa: MakeWa) -> None:
    wa = make_wa([Say(HELLO)])
    # The message is old: the bot answers it a day later (e.g. stuck in a queue).
    wa.post(incoming(text_message("hola", wa.now - timedelta(hours=24, minutes=1))))

    assert wa.fake.sent == []
    # The fixed fallback cannot go either: the conversation is left to a human, with its own
    # reason (not technical_error) and label.
    conversation = wa.conversation()
    assert conversation is not None
    assert conversation.status == WaConversationStatus.WAITING_HUMAN
    assert conversation.handoff_reason == "window_closed"
    assert "ventana-cerrada" in conversation.handoff_labels
    assert "error-tecnico" not in conversation.handoff_labels
    [note] = [m for m in wa.messages() if m.is_internal_note]
    assert "Ventana de 24 h cerrada" in (note.body or "")
    [event] = wa.events("handoff")
    assert event.payload["reason"] == "window_closed"
    # No fallback message was even attempted (nothing failed stored).
    assert [m for m in wa.messages() if m.author == WaAuthor.BOT] == []


# --- Statuses -------------------------------------------------------------------------------


def _sent_message(wa) -> WaMessage:  # type: ignore[no-untyped-def]
    wa.post(incoming(text_message("hola", wa.now)))
    return wa.messages()[-1]


def test_statuses_move_forward_only(make_wa: MakeWa) -> None:
    wa = make_wa([Say(HELLO)])
    sent = _sent_message(wa)
    wamid = sent.wa_message_id
    assert wamid is not None
    later = wa.now + timedelta(seconds=5)

    wa.post(statuses(status(wamid, "delivered", later)))
    assert wa.messages()[-1].status == WaMessageStatus.DELIVERED
    wa.post(statuses(status(wamid, "read", later)))
    wa.post(statuses(status(wamid, "delivered", later)))  # late, out of order
    wa.post(statuses(status(wamid, "sent", later)))

    message = wa.messages()[-1]
    assert message.status == WaMessageStatus.READ
    assert message.status_at == later


def test_played_counts_as_read(make_wa: MakeWa) -> None:
    wa = make_wa([Say(HELLO)])
    wamid = _sent_message(wa).wa_message_id
    assert wamid is not None

    wa.post(statuses(status(wamid, "played", wa.now)))

    assert wa.messages()[-1].status == WaMessageStatus.READ


def test_failed_keeps_the_error(make_wa: MakeWa) -> None:
    wa = make_wa([Say(HELLO)])
    wamid = _sent_message(wa).wa_message_id
    assert wamid is not None

    wa.post(statuses(status(wamid, "failed", wa.now, error=(131030, "Recipient not allowed"))))

    message = wa.messages()[-1]
    assert message.status == WaMessageStatus.FAILED
    assert message.error_code == 131030
    assert message.error_text == "Recipient not allowed — detalle inventado"


def test_a_status_of_an_unknown_message_is_ignored(make_wa: MakeWa) -> None:
    wa = make_wa()

    response = wa.post(statuses(status("wamid.NADIE", "read", wa.now)))

    assert response.status_code == 200
    assert wa.messages() == []


# --- Coexistence: messages from the phone app -----------------------------------------------


def test_an_echo_is_an_operator_message_and_a_human_takes_over(make_wa: MakeWa) -> None:
    wa = make_wa([Say(HELLO)])
    wa.post(incoming(text_message("hola", wa.now)))

    echo = text_message("Hola, soy Marta del estudio.", wa.now, message_id="wamid.ECO1")
    wa.post(echoes(echo))
    wa.post(echoes(echo))  # delivered twice
    wa.post(incoming(text_message("gracias Marta", wa.now)))

    conversation = wa.conversation()
    assert conversation is not None
    assert conversation.status == WaConversationStatus.HUMAN
    operator_messages = [m for m in wa.messages() if m.author == WaAuthor.OPERATOR]
    assert [m.body for m in operator_messages] == ["Hola, soy Marta del estudio."]
    assert operator_messages[0].direction == WaDirection.OUTBOUND
    assert wa.fake.texts() == [HELLO]  # the bot did not answer after the echo
