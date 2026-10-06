"""Messages a restart left unanswered: answered if recent, marked unanswered if old, and
claimed by one worker only. Invented data only."""

import logging
from datetime import timedelta

import pytest

from app.db.models import WaConversationStatus, WaMessage
from app.whatsapp import store
from app.whatsapp.bot import UNANSWERED_EVENT
from app.whatsapp.events import WaIncoming
from tests.llm.fakes import Say
from tests.whatsapp.conftest import MakeWa, Wa
from tests.whatsapp.fakes import CONTACT_WA_ID, wamid

MAX_AGE = timedelta(minutes=15)


def _stored(wa: Wa, text: str, minutes_ago: float, wa_id: str = CONTACT_WA_ID) -> int:
    """An incoming message stored by the webhook whose background task never ran."""
    at = wa.now - timedelta(minutes=minutes_ago)
    message_id = store.record_incoming(
        wa.session, WaIncoming(wamid(), wa_id, "text", text=text, timestamp=at), at
    )
    assert message_id is not None
    message = wa.session.get(WaMessage, message_id)
    assert message is not None
    message.created_at = at
    wa.session.commit()
    return message_id


def test_recent_messages_are_answered_once(make_wa: MakeWa) -> None:
    wa = make_wa([Say("¡Hola! Perdón la demora.")])
    message_id = _stored(wa, "hola", minutes_ago=3)

    first = wa.bot.recover_unanswered(MAX_AGE)
    second = wa.bot.recover_unanswered(MAX_AGE)

    assert first.answered == [message_id]
    assert second.answered == []
    assert wa.fake.texts() == ["¡Hola! Perdón la demora."]
    assert wa.session.get(WaMessage, message_id).processed_at == wa.now  # type: ignore[union-attr]


def test_old_messages_are_marked_unanswered_and_logged(
    make_wa: MakeWa, caplog: pytest.LogCaptureFixture
) -> None:
    wa = make_wa()
    message_id = _stored(wa, "hola", minutes_ago=40)

    with caplog.at_level(logging.WARNING, logger="app.whatsapp.bot"):
        result = wa.bot.recover_unanswered(MAX_AGE)

    assert result.stale == [message_id] and result.answered == []
    assert wa.fake.sent == []
    message = wa.session.get(WaMessage, message_id)
    assert message is not None
    assert (message.processed_at, message.processing_note) == (wa.now, "stale_after_restart")
    [event] = wa.events(UNANSWERED_EVENT)
    assert event.payload == {"message_id": message_id, "reason": "stale"}
    assert any("left unanswered" in r.message for r in caplog.records)
    assert "hola" not in caplog.text  # never the text of the message


def test_messages_of_a_conversation_with_a_human_are_not_answered(make_wa: MakeWa) -> None:
    wa = make_wa()
    message_id = _stored(wa, "hola", minutes_ago=2)
    conversation = wa.conversation()
    assert conversation is not None
    conversation.status = WaConversationStatus.WAITING_HUMAN
    wa.session.commit()

    result = wa.bot.recover_unanswered(MAX_AGE)

    assert result.not_with_bot == [message_id]
    assert wa.fake.sent == []
    message = wa.session.get(WaMessage, message_id)
    assert message is not None and message.processing_note == "not_with_bot"


def test_already_processed_or_being_processed_is_left_alone(make_wa: MakeWa) -> None:
    wa = make_wa()
    done = _stored(wa, "uno", minutes_ago=2)
    busy = _stored(wa, "dos", minutes_ago=2)
    wa.session.get(WaMessage, done).processed_at = wa.now  # type: ignore[union-attr]
    wa.session.get(WaMessage, busy).processing_started_at = wa.now - timedelta(minutes=1)  # type: ignore[union-attr]
    wa.session.commit()

    result = wa.bot.recover_unanswered(MAX_AGE)

    assert (result.answered, result.stale, result.not_with_bot) == ([], [], [])
    assert wa.fake.sent == []


def test_a_claim_of_a_worker_that_died_is_taken_again(make_wa: MakeWa) -> None:
    wa = make_wa([Say("¡Hola!")])
    message_id = _stored(wa, "hola", minutes_ago=10)
    wa.session.get(WaMessage, message_id).processing_started_at = wa.now - timedelta(minutes=9)  # type: ignore[union-attr]
    wa.session.commit()

    result = wa.bot.recover_unanswered(MAX_AGE)

    assert result.answered == [message_id]


def test_a_message_is_claimed_once(make_wa: MakeWa) -> None:
    wa = make_wa([Say("¡Hola!")])
    message_id = _stored(wa, "hola", minutes_ago=1)

    assert wa.bot.process(message_id) is True
    assert wa.bot.process(message_id) is False  # e.g. the recovery of another worker
    assert wa.fake.texts() == ["¡Hola!"]
