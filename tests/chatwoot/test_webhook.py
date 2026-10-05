"""Chatwoot webhook and bot: invented payloads, Chatwoot API faked, LLM scripted, real
Postgres test database. All data is invented."""

from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bot.agent import FALLBACK_REPLY, Agent
from app.chatwoot.events import parse_incoming
from app.chatwoot.processor import (
    ATTACHMENT_REPLY,
    NON_PILOT_GREETING,
    UNSUPPORTED_REPLY,
    ChatwootBot,
    resolve_phone,
    web_contact_phone,
)
from app.chatwoot.webhook import get_chatwoot_bot, verify_signature
from app.config import Settings, get_settings
from app.db.models import BotEvent, ChatwootProcessedMessage
from app.db.session import get_session
from app.main import app
from tests.bot import factories as f
from tests.chatwoot.fakes import (
    ACCOUNT_ID,
    SECRET,
    WEB_INBOX,
    WHATSAPP_INBOX,
    FakeChatwoot,
    body_of,
    history_message,
    message_payload,
    signed_headers,
)
from tests.llm.fakes import (
    PROVIDERS,
    Call,
    Say,
    Step,
    last_tool_result,
    last_user_text,
    scripted_provider,
)

TZ = ZoneInfo("America/Argentina/Cordoba")
WEDNESDAY_11 = datetime(2026, 9, 30, 11, 0, tzinfo=TZ)
SATURDAY_10 = datetime(2026, 10, 3, 10, 0, tzinfo=TZ)
OWNER_PHONE = "+5493515550101"
NON_PILOT_PHONE = "+5493515550303"
SETTINGS = Settings(
    _env_file=None,
    chatwoot_webhook_secret=SECRET,
    chatwoot_account_id=ACCOUNT_ID,
    chatwoot_trusted_phone_inbox_ids=[WHATSAPP_INBOX],
)


@dataclass
class Harness:
    session: Session
    chatwoot: FakeChatwoot
    bot: ChatwootBot
    script: Any
    warm_ups: list[bool]

    def handle(self, **payload: Any) -> None:
        self.bot.handle(parse_incoming(message_payload(**payload)))

    @property
    def llm_calls(self) -> int:
        return len(self.script.requests)

    def events(self, event_type: str) -> list[BotEvent]:
        return list(
            self.session.scalars(
                select(BotEvent).where(BotEvent.event_type == event_type).order_by(BotEvent.id)
            )
        )


@pytest.fixture
def people(db_session: Session) -> None:
    pilot = f.building(db_session, "031 RODAS II")
    pilot.pilot = True
    unit = f.unit(db_session, pilot, "04-C")
    f.link(db_session, unit, f.person(db_session, "Ana Prueba", phone=OWNER_PHONE))
    other = f.building(db_session, "040 TORRE NORTE")  # pilot=False
    other_unit = f.unit(db_session, other, "02-B")
    f.link(db_session, other_unit, f.person(db_session, "Beto Prueba", phone=NON_PILOT_PHONE))


def make_harness(
    session: Session,
    steps: list[Step],
    *,
    provider: str = "anthropic",
    chatwoot: FakeChatwoot | None = None,
    now: datetime = WEDNESDAY_11,
) -> Harness:
    llm, script = scripted_provider(provider, steps)
    chatwoot = chatwoot or FakeChatwoot()

    def no_debt(unit_id: int) -> Any:
        raise LookupError(unit_id)

    agent = Agent(llm, settings=SETTINGS, refresh_debt=no_debt, now=lambda: now)
    warm_ups: list[bool] = []
    bot = ChatwootBot(
        chatwoot,  # type: ignore[arg-type]
        lambda: nullcontext(session),
        lambda: agent,
        SETTINGS,
        now=lambda: now,
        warm_up=lambda: warm_ups.append(True),
    )
    return Harness(session, chatwoot, bot, script, warm_ups)


# --- Signature ----------------------------------------------------------------------------


def test_verify_signature() -> None:
    body = b'{"event":"message_created"}'
    headers = signed_headers(body, timestamp=1_000)
    ts, sig = headers["X-Chatwoot-Timestamp"], headers["X-Chatwoot-Signature"]

    assert verify_signature(SECRET, body, ts, sig, now=1_010)
    assert not verify_signature("otro-secreto", body, ts, sig, now=1_010)
    assert not verify_signature(SECRET, body + b" ", ts, sig, now=1_010)
    assert not verify_signature(SECRET, body, ts, sig, now=1_000 + 301)  # too old: replay
    assert not verify_signature(SECRET, body, None, sig, now=1_010)
    assert not verify_signature(SECRET, body, "abc", sig, now=1_010)


# --- Endpoint -----------------------------------------------------------------------------


@pytest.fixture
def harness(db_session: Session, people: None) -> Harness:
    return make_harness(db_session, [Say("¡Hola! Soy el asistente automático.")])


@pytest.fixture
def client(db_session: Session, harness: Harness) -> Iterator[TestClient]:
    def session_override() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_chatwoot_bot] = lambda: harness.bot
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def post(client: TestClient, payload: dict[str, Any], headers: dict | None = None) -> Any:
    body = body_of(payload)
    return client.post("/webhooks/chatwoot", content=body, headers=headers or signed_headers(body))


def test_rejects_bad_or_missing_signature(client: TestClient, harness: Harness) -> None:
    body = body_of(message_payload())
    bad = signed_headers(body, secret="otro-secreto")

    assert client.post("/webhooks/chatwoot", content=body, headers=bad).status_code == 401
    assert client.post("/webhooks/chatwoot", content=body).status_code == 401
    assert harness.chatwoot.calls == []


def test_without_secret_configured_rejects_everything(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)

    assert post(client, message_payload()).status_code == 503


def test_accepts_and_answers_in_background(client: TestClient, harness: Harness) -> None:
    response = post(client, message_payload(content="hola"))

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    # TestClient runs the background task before returning.
    assert harness.chatwoot.sent() == ["¡Hola! Soy el asistente automático."]


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (message_payload(message_type="outgoing"), "not_incoming"),
        (message_payload(message_type="outgoing", private=True), "not_incoming"),
        (message_payload(private=True), "private"),
        (message_payload(status="open"), "not_pending"),
        (message_payload(status="resolved"), "not_pending"),
        (message_payload(event="conversation_status_changed"), "not_message_created"),
    ],
)
def test_ignores_outgoing_private_and_human_conversations(
    client: TestClient, harness: Harness, payload: dict, reason: str
) -> None:
    response = post(client, payload)

    assert response.json() == {"status": "ignored", "reason": reason}
    assert harness.chatwoot.calls == [] and harness.llm_calls == 0


def test_ignores_other_accounts(client: TestClient, harness: Harness) -> None:
    payload = message_payload()
    payload["account"]["id"] = ACCOUNT_ID + 1

    assert post(client, payload).json()["reason"] == "other_account"
    assert harness.llm_calls == 0


def test_same_message_is_processed_once(
    client: TestClient, harness: Harness, db_session: Session
) -> None:
    payload = message_payload(message_id=555)

    first = post(client, payload)
    second = post(client, payload)  # Chatwoot retry: new signature, same message

    assert first.json()["status"] == "accepted"
    assert second.json() == {"status": "ignored", "reason": "duplicate"}
    assert len(harness.chatwoot.sent()) == 1 and harness.llm_calls == 1
    stored = db_session.scalar(select(func.count()).select_from(ChatwootProcessedMessage))
    assert stored == 1


# --- Trusted phone ------------------------------------------------------------------------


def test_resolve_phone_trusts_only_listed_inboxes() -> None:
    def resolve(**kwargs: Any) -> tuple[str, bool]:
        return resolve_phone(parse_incoming(message_payload(**kwargs)), [WHATSAPP_INBOX])

    assert resolve(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE) == (OWNER_PHONE, True)
    # Web chat: the phone could be anyone's; an internal per-contact number is used.
    assert resolve(inbox_id=WEB_INBOX, phone=OWNER_PHONE, contact_id=55) == (
        "+549110900005" + "5",
        False,
    )
    assert resolve(inbox_id=WHATSAPP_INBOX, phone=None, contact_id=55)[1] is False
    assert resolve(inbox_id=WHATSAPP_INBOX, phone="123", contact_id=55)[1] is False
    assert resolve(inbox_id=WEB_INBOX, contact_id=10_000_000) == ("", False)


def test_web_contact_phone_is_a_valid_non_real_number() -> None:
    from app.bot.identity import to_e164

    phone = web_contact_phone(42)
    assert phone == "+5491109000042" and to_e164(phone) == phone
    assert web_contact_phone(None) is None and web_contact_phone(0) is None


def test_owner_phone_in_trusted_inbox_is_identified(db_session: Session, people: None) -> None:
    h = make_harness(db_session, [Say("Hola Ana")])

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE)

    assert "número verificado de Ana Prueba" in last_user_text("anthropic", h.script.requests[0])
    [(contact_id, attributes)] = h.chatwoot.args_of("update_contact_attributes")
    assert contact_id == 55
    assert attributes == {"unit": "RODAS II 04-C", "building": "RODAS II", "verified": True}


def test_owner_phone_in_untrusted_inbox_is_unknown(db_session: Session, people: None) -> None:
    h = make_harness(db_session, [Say("Hola")])

    h.handle(inbox_id=WEB_INBOX, phone=OWNER_PHONE)  # anyone can type this in the web chat

    user_turn = last_user_text("anthropic", h.script.requests[0])
    assert "número no registrado" in user_turn and "Ana" not in user_turn
    assert h.chatwoot.args_of("update_contact_attributes") == []


def test_untrusted_inbox_can_verify_by_email(db_session: Session, people: None) -> None:
    """The internal number of a web contact goes through the normal email verification."""
    from app.bot.identity import confirm_email_code, identify_by_phone, start_email_verification

    @dataclass
    class Sender:
        code: str = ""

        def send_verification_code(self, to: str, code: str, valid_minutes: int) -> None:
            self.code = code

    phone = web_contact_phone(55)
    assert phone is not None
    south = f.building(db_session, "050 EDIFICIO SUR")
    unit = f.unit(db_session, south, "01-A")
    f.link(db_session, unit, f.person(db_session, "Caro Prueba", email="caro@example.com"))
    sender = Sender()

    started = start_email_verification(db_session, phone, unit_id=unit.id, sender=sender)
    confirmed = confirm_email_code(db_session, phone, sender.code)

    assert started.status == "codes_sent" and confirmed.status == "verified"
    assert identify_by_phone(db_session, phone).full_name == "Caro Prueba"


# --- Pilot ----------------------------------------------------------------------------------


def test_known_owner_outside_pilot_goes_straight_to_a_human(
    db_session: Session, people: None
) -> None:
    h = make_harness(db_session, [])

    h.handle(inbox_id=WHATSAPP_INBOX, phone=NON_PILOT_PHONE)

    assert h.llm_calls == 0
    [reply] = h.chatwoot.sent()
    assert reply.startswith(NON_PILOT_GREETING) and "persona del estudio" in reply
    assert h.chatwoot.names()[-1] == "update_contact_attributes"
    assert h.chatwoot.args_of("toggle_status") == [(7, "open")]
    assert h.chatwoot.labels == ["fuera-de-piloto", "edificio-torre-norte"]
    [note] = h.chatwoot.notes()
    assert "Beto Prueba" in note and "fuera de la prueba piloto" in note
    assert h.events("handoff")[0].payload["reason"] == "non_pilot"


def test_unknown_phone_is_answered_even_outside_pilot(db_session: Session, people: None) -> None:
    h = make_harness(db_session, [Say("Hola, ¿en qué te ayudo?")])

    h.handle(inbox_id=WHATSAPP_INBOX, phone="+5493515559999")

    assert h.llm_calls == 1 and h.chatwoot.sent() == ["Hola, ¿en qué te ayudo?"]
    assert h.chatwoot.args_of("toggle_status") == []


# --- Handoff --------------------------------------------------------------------------------


def test_agent_handoff_sends_reply_then_note_labels_and_open(
    db_session: Session, people: None
) -> None:
    h = make_harness(
        db_session,
        [
            Call(
                "handoff_to_human",
                {
                    "reason": "emergency",
                    "summary": "Pérdida de gas en el palier",
                    "priority": "urgent",
                },
            ),  # fmt: skip
            Say("Te paso con el estudio. Llamá también al 100."),
        ],
    )

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, content="hay olor a gas")

    assert h.chatwoot.names() == [
        "get_messages",
        "send_message",
        "note",
        "add_labels",
        "toggle_status",
        "update_contact_attributes",
    ]
    assert h.chatwoot.labels == ["emergencia", "edificio-rodas-ii", "urgente"]
    [note] = h.chatwoot.notes()
    assert "URGENTE" in note and "Pérdida de gas en el palier" in note and "Ana Prueba" in note
    assert h.chatwoot.args_of("toggle_status") == [(7, "open")]
    assert "a la brevedad" in last_tool_result("anthropic", h.script.requests[1])


def test_handoff_out_of_office_hours_says_next_business_day(
    db_session: Session, people: None
) -> None:
    h = make_harness(
        db_session,
        [
            Call(
                "handoff_to_human",
                {"reason": "person_requested", "summary": "x", "priority": "normal"},
            ),
            Say("Listo."),
        ],  # fmt: skip
        now=SATURDAY_10,
    )

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, content="quiero hablar con alguien")

    told = last_tool_result("anthropic", h.script.requests[1])
    assert "fuera del horario" in told and "el lunes a partir de las 9" in told


def test_non_pilot_message_out_of_hours(db_session: Session, people: None) -> None:
    h = make_harness(db_session, [], now=SATURDAY_10)

    h.handle(inbox_id=WHATSAPP_INBOX, phone=NON_PILOT_PHONE)

    assert "el lunes a partir de las 9" in h.chatwoot.sent()[0]


def test_operator_takes_conversation_while_bot_thinks(db_session: Session, people: None) -> None:
    chatwoot = FakeChatwoot(statuses=["pending", "open"])
    h = make_harness(db_session, [Say("respuesta tardía")], chatwoot=chatwoot)

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE)

    assert h.chatwoot.sent() == [] and h.chatwoot.args_of("toggle_status") == []
    assert len(h.events("chatwoot_reply_dropped")) == 1


def test_already_open_conversation_is_not_answered(db_session: Session, people: None) -> None:
    h = make_harness(db_session, [Say("no")], chatwoot=FakeChatwoot(statuses=["open"]))

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE)

    assert h.llm_calls == 0 and h.chatwoot.names() == []


def test_llm_failure_answers_fixed_message_and_hands_off(db_session: Session, people: None) -> None:
    from app.llm import LLMError

    h = make_harness(db_session, [LLMError("caído")])

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE)

    [reply] = h.chatwoot.sent()
    assert reply.startswith(FALLBACK_REPLY)
    assert "error-tecnico" in h.chatwoot.labels
    assert h.chatwoot.args_of("toggle_status") == [(7, "open")]


def test_unexpected_error_still_hands_off(db_session: Session, people: None) -> None:
    h = make_harness(db_session, [])

    def broken_agent() -> Agent:
        raise RuntimeError("falta GEMINI_API_KEY")

    h.bot._agent_factory = broken_agent

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE)

    assert h.chatwoot.sent()[0].startswith(FALLBACK_REPLY)
    assert h.chatwoot.args_of("toggle_status") == [(7, "open")]


def test_failed_handoff_does_not_send_a_second_message(db_session: Session, people: None) -> None:
    chatwoot = FakeChatwoot(fail_on={"toggle_status"})
    h = make_harness(
        db_session,
        [
            Call(
                "handoff_to_human", {"reason": "payment_plan", "summary": "x", "priority": "normal"}
            ),
            Say("Te paso con el estudio."),
        ],  # fmt: skip
        chatwoot=chatwoot,
    )

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE)

    assert h.chatwoot.sent() == ["Te paso con el estudio."]
    assert len(h.events("chatwoot_handoff_failed")) == 1


# --- Unsupported messages -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"content": None, "attachments": ("audio",)}, UNSUPPORTED_REPLY),
        ({"content": None, "attachments": ("location",)}, UNSUPPORTED_REPLY),
        (
            {"content": None, "content_type": "sticker", "attachments": ("image",)},
            UNSUPPORTED_REPLY,
        ),
        ({"content": "", "attachments": ("image",)}, ATTACHMENT_REPLY),
    ],
)
def test_attachments_without_text_get_a_fixed_reply(
    db_session: Session, people: None, kwargs: dict, expected: str
) -> None:
    h = make_harness(db_session, [])

    h.handle(inbox_id=WEB_INBOX, **kwargs)

    assert h.llm_calls == 0 and h.chatwoot.sent() == [expected]


def test_text_with_image_goes_to_the_agent_with_a_note(db_session: Session, people: None) -> None:
    h = make_harness(db_session, [Say("Gracias")])

    h.handle(inbox_id=WEB_INBOX, content="te mando el comprobante", attachments=("image",))

    user_turn = last_user_text("anthropic", h.script.requests[0])
    assert "te mando el comprobante" in user_turn and "adjunto (image)" in user_turn


# --- History ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", PROVIDERS)
def test_history_comes_from_chatwoot(name: str, db_session: Session, people: None) -> None:
    chatwoot = FakeChatwoot(
        history=[
            history_message(90, "Hola, ¿cuánto debo?", 0),
            history_message(91, "¿De qué edificio sos?", 1),
            history_message(92, "nota interna del operador", 1, private=True),
            history_message(93, "Conversation was reopened", 2),
            history_message(101, "este es el mensaje actual", 0),
        ]
    )
    h = make_harness(db_session, [Say("Perfecto")], provider=name, chatwoot=chatwoot)

    h.handle(message_id=101, inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, content="Rodas 2")

    assert h.chatwoot.args_of("get_messages") == [(7, 101)]
    request = str(h.script.requests[0])
    assert "Hola, ¿cuánto debo?" in request and "¿De qué edificio sos?" in request
    assert "nota interna" not in request and "reopened" not in request
    assert "Primer mensaje de la conversación: no" in last_user_text(name, h.script.requests[0])


def test_works_without_history_token(db_session: Session, people: None) -> None:
    h = make_harness(db_session, [Say("Hola")], chatwoot=FakeChatwoot(can_read_history=False))

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE)

    assert h.chatwoot.sent() == ["Hola"] and "get_messages" not in h.chatwoot.names()
    assert "update_contact_attributes" not in h.chatwoot.names()


# --- ConsorPlus warm-up -------------------------------------------------------------------


def test_identified_pilot_owner_warms_up_the_consorplus_session(
    db_session: Session, people: None
) -> None:
    h = make_harness(db_session, [Say("Hola Ana")])

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE)

    assert h.warm_ups == [True]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"inbox_id": WEB_INBOX, "phone": OWNER_PHONE},  # unknown: no debt without verifying
        {"inbox_id": WHATSAPP_INBOX, "phone": NON_PILOT_PHONE},  # straight to a human
    ],
)
def test_no_warm_up_for_unknown_or_non_pilot(
    db_session: Session, people: None, kwargs: dict
) -> None:
    h = make_harness(db_session, [Say("Hola")])

    h.handle(**kwargs)

    assert h.warm_ups == []
