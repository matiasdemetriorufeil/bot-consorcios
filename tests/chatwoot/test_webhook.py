"""Chatwoot webhook and bot: invented payloads, Chatwoot API faked, LLM scripted, real
Postgres test database. All data is invented."""

from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bot.agent import FALLBACK_REPLY, Agent
from app.bot.debt_message import FIRST_GREETING
from app.bot.tools import NO_PAYMENT_CODE
from app.chatwoot import outgoing, processor
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
from app.db.models import (
    BotEvent,
    ChatwootProcessedMessage,
    DebtLine,
    DebtSnapshot,
    SyncKind,
    Unit,
)
from app.db.session import get_session
from app.main import app
from app.sync.live import DebtResult
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
    options_attributes,
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
    slept: list[float]

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
    refresh_debt: Callable[[int], DebtResult] | None = None,
) -> Harness:
    llm, script = scripted_provider(provider, steps)
    chatwoot = chatwoot or FakeChatwoot()

    def no_debt(unit_id: int) -> Any:
        raise LookupError(unit_id)

    agent = Agent(llm, settings=SETTINGS, refresh_debt=refresh_debt or no_debt, now=lambda: now)
    warm_ups: list[bool] = []
    slept: list[float] = []  # fake clock: sleeping only moves it
    bot = ChatwootBot(
        chatwoot,  # type: ignore[arg-type]
        lambda: nullcontext(session),
        lambda: agent,
        SETTINGS,
        now=lambda: now,
        warm_up=lambda: warm_ups.append(True),
        sleep=slept.append,
        clock=lambda: sum(slept),
    )
    return Harness(session, chatwoot, bot, script, warm_ups, slept)


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


# --- Debt message -------------------------------------------------------------------------


def test_debt_message_is_sent_before_the_agent_text(db_session: Session, people: None) -> None:
    unit = db_session.scalar(select(Unit).where(Unit.label == "04-C"))
    snapshot = DebtSnapshot(
        unit_id=unit.id,
        fetched_at=datetime(2026, 9, 30, 10, 0, tzinfo=TZ),
        source=SyncKind.LIVE,
        total_amount=Decimal("1234.50"),
        is_up_to_date=False,
        lines=[
            DebtLine(
                concept="Expensas ordinarias",
                period="09/2026",
                concept_amount=Decimal("1234.50"),
                balance_due=Decimal("1234.50"),
                accumulated=Decimal("1234.50"),
            )
        ],
    )
    db_session.add(snapshot)
    db_session.commit()
    h = make_harness(
        db_session,
        [Call("get_debt", {"unit_id": unit.id}), Say("¿Te ayudo con algo más?")],
        refresh_debt=lambda unit_id: DebtResult(snapshot, stale=False),
    )

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, content="¿cuánto debo?")

    # ONE message (Chatwoot does not keep the order of separate ones): the block, then the
    # agent's text. First message of the conversation: the fixed greeting goes first.
    [sent] = h.chatwoot.sent()
    assert sent.startswith(f"{FIRST_GREETING}\n\n*RODAS II 04-C*\nSaldo total: *$1.234,50*")
    assert "Dato al 30/09/2026 a las 10:00." in sent
    assert sent.endswith("\n\n¿Te ayudo con algo más?")


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


# --- Options to tap (offer_choices) -------------------------------------------------------

OFFER = Call(
    "offer_choices",
    {"text": "¿Querés que te pase con una persona?", "options": ["Sí, pasame", "No, gracias"]},
)


@pytest.mark.parametrize("channel", ["Channel::Whatsapp", "Channel::WebWidget"])
def test_options_go_as_buttons_where_chatwoot_shows_them(
    channel: str, db_session: Session, people: None
) -> None:
    h = make_harness(db_session, [OFFER])

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, channel=channel)

    assert h.chatwoot.args_of("send_choices") == [
        (
            7,
            "¿Querés que te pase con una persona?",
            [("Sí, pasame", "Sí, pasame"), ("No, gracias", "No, gracias")],
        )
    ]
    assert h.chatwoot.sent() == []
    assert h.llm_calls == 1  # offer_choices ends the turn


def test_options_go_numbered_in_other_inboxes(db_session: Session, people: None) -> None:
    h = make_harness(db_session, [OFFER])

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, channel="Channel::Api")

    assert "send_choices" not in h.chatwoot.names()
    assert h.chatwoot.sent() == [
        "¿Querés que te pase con una persona?\n\n1. Sí, pasame\n2. No, gracias\n\n"
        "Respondé con el número o escribí la opción."
    ]


def test_options_go_numbered_when_chatwoot_rejects_them(db_session: Session, people: None) -> None:
    h = make_harness(db_session, [OFFER], chatwoot=FakeChatwoot(fail_on={"send_choices"}))

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, channel="Channel::Whatsapp")

    [text] = h.chatwoot.sent()
    assert text.startswith("¿Querés que te pase con una persona?\n\n1. Sí, pasame\n")
    assert h.chatwoot.args_of("send_choices")  # tried first


def test_debt_messages_go_before_the_options(db_session: Session, people: None) -> None:
    unit = db_session.scalar(select(Unit).where(Unit.label == "04-C"))
    db_session.add(
        DebtSnapshot(
            unit_id=unit.id,
            fetched_at=datetime(2026, 9, 30, 10, 0, tzinfo=TZ),
            source=SyncKind.LIVE,
            total_amount=Decimal("0"),
            is_up_to_date=True,
            lines=[],
        )
    )
    db_session.commit()
    snapshot = db_session.scalar(select(DebtSnapshot).where(DebtSnapshot.unit_id == unit.id))
    h = make_harness(
        db_session,
        [Call("get_debt", {"unit_id": unit.id}), OFFER],
        refresh_debt=lambda unit_id: DebtResult(snapshot, stale=False),
    )

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, channel="Channel::Whatsapp")

    # The block fits in the text of the message with buttons: one message.
    assert h.chatwoot.sent() == []
    [(_, text, _)] = h.chatwoot.args_of("send_choices")
    assert text.startswith(f"{FIRST_GREETING}\n\n*RODAS II 04-C*\nEstás al día")
    assert text.endswith("\n\n¿Querés que te pase con una persona?")


def test_widget_tap_is_answered_as_the_chosen_title(db_session: Session, people: None) -> None:
    offered = options_attributes(["Sí, pasame", "No, gracias"], chosen="Sí, pasame")
    chatwoot = FakeChatwoot(
        history=[
            history_message(90, "Pagué hace dos semanas y sigue la deuda", 0),
            history_message(
                91,
                "¿Querés que te pase con una persona?",
                1,
                content_type="input_select",
                content_attributes=offered,
            ),
        ]
    )
    h = make_harness(db_session, [Say("Listo")], chatwoot=chatwoot)

    h.handle(
        event="message_updated",
        message_id=91,
        message_type="outgoing",
        content="¿Querés que te pase con una persona?",
        content_type="input_select",
        content_attributes=offered,
        inbox_id=WEB_INBOX,
    )

    # The message with the options is part of the history; the tap is the current message.
    assert h.chatwoot.args_of("get_messages") == [(7, 92)]
    request = str(h.script.requests[0])
    assert "[Opciones: Sí, pasame / No, gracias]" in request
    assert last_user_text("anthropic", h.script.requests[0]).endswith("\nSí, pasame")
    # The choice is the current message, not also a history turn.
    roles = [m["role"] for m in h.script.requests[0]["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert h.chatwoot.sent() == ["Listo"]


def test_webhook_accepts_a_widget_tap_once(client: TestClient, harness: Harness) -> None:
    payload = message_payload(
        event="message_updated",
        message_id=91,
        message_type="outgoing",
        content="¿Qué querés hacer?",
        content_type="input_select",
        content_attributes=options_attributes(["Mi deuda", "Hablar con alguien"], "Mi deuda"),
    )

    assert post(client, payload).json() == {"status": "accepted"}
    # Later updates of the same message (e.g. its status) carry the choice again.
    assert post(client, payload).json() == {"status": "ignored", "reason": "duplicate"}
    assert harness.llm_calls == 1
    assert last_user_text("anthropic", harness.script.requests[0]).endswith("\nMi deuda")


def test_webhook_ignores_updates_without_a_choice(client: TestClient, harness: Harness) -> None:
    not_chosen = message_payload(
        event="message_updated",
        message_type="outgoing",
        content_type="input_select",
        content_attributes=options_attributes(["Mi deuda", "Hablar con alguien"]),
    )
    plain = message_payload(event="message_updated", message_type="outgoing")

    assert post(client, not_chosen).json()["status"] == "ignored"
    assert post(client, plain).json()["status"] == "ignored"
    assert harness.llm_calls == 0


# --- One message per turn (Chatwoot does not keep the order of separate ones) -------------


def _long_debt_harness(db_session: Session, steps: list[Step], lines: int) -> Harness:
    unit = db_session.scalar(select(Unit).where(Unit.label == "04-C"))
    snapshot = DebtSnapshot(
        unit_id=unit.id,
        fetched_at=datetime(2026, 9, 30, 10, 0, tzinfo=TZ),
        source=SyncKind.LIVE,
        total_amount=Decimal("1000") * lines,
        is_up_to_date=False,
        lines=[
            DebtLine(
                concept="Concepto inventado con un nombre bastante largo " * 3,
                period=f"{n % 12 + 1:02d}/2026",
                concept_amount=Decimal("1000"),
                balance_due=Decimal("1000"),
                accumulated=Decimal("1000"),
            )
            for n in range(lines)
        ],
    )
    db_session.add(snapshot)
    db_session.commit()
    return make_harness(
        db_session,
        [Call("get_debt", {"unit_id": unit.id}), *steps],
        refresh_debt=lambda unit_id: DebtResult(snapshot, stale=False),
    )


def test_a_block_too_long_for_the_buttons_goes_first(db_session: Session, people: None) -> None:
    h = _long_debt_harness(db_session, [OFFER], lines=12)

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, channel="Channel::Whatsapp")

    [block] = h.chatwoot.sent()
    assert "Saldo total" in block and len(block) > 1024
    [(_, text, _)] = h.chatwoot.args_of("send_choices")
    assert text == "¿Querés que te pase con una persona?"
    # The block went out to WhatsApp before the buttons were created.
    names = [n for n in h.chatwoot.names() if n != "get_messages"]
    assert names[:3] == ["send_message", "message_sent", "send_choices"]


def test_numbered_fallback_keeps_the_block(db_session: Session, people: None) -> None:
    h = make_harness(
        db_session,
        [Call("get_payment_info", {"unit_id": _unit_id(db_session)}), OFFER],
        chatwoot=FakeChatwoot(fail_on={"send_choices"}),
    )

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, channel="Channel::Whatsapp")

    [sent] = h.chatwoot.sent()
    assert sent.startswith(f"{FIRST_GREETING}\n\n*RODAS II 04-C*\n")
    assert "\n\n¿Querés que te pase con una persona?\n\n1. Sí, pasame\n" in sent


def _unit_id(db_session: Session) -> int:
    return db_session.scalar(select(Unit.id).where(Unit.label == "04-C"))


SPLIT_BLOCK = f"*RODAS II 04-C*\n{NO_PAYMENT_CODE}"  # the unit has no payment code here


def _split_turn(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
    polls_until_sent: int = 0,
    text: str = "¿Algo más?",
) -> Harness:
    """A turn that goes in two parts: the payment block, then the agent's text."""
    limit = len(SPLIT_BLOCK) + 5  # the block fits, the block and the text do not
    monkeypatch.setattr(processor, "pack", lambda parts: outgoing.pack(parts, limit=limit))
    # Not the first message of the conversation: no greeting before the block.
    chatwoot = FakeChatwoot(
        history=[history_message(90, "hola", 0), history_message(91, "¡Hola!", 1)],
        polls_until_sent=polls_until_sent,
    )
    steps = [Call("get_payment_info", {"unit_id": _unit_id(db_session)}), Say(text)]
    h = make_harness(db_session, steps, chatwoot=chatwoot)
    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, channel=channel)
    return h


def test_a_turn_too_long_goes_in_parts_in_order(
    db_session: Session, people: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _split_turn(db_session, monkeypatch, "Channel::Whatsapp")

    # Split between the block and the text; the text is posted once the block went out.
    assert h.chatwoot.sent() == [SPLIT_BLOCK, "¿Algo más?"]
    calls = [n for n in h.chatwoot.names() if n != "get_messages"]
    assert calls[:3] == ["send_message", "message_sent", "send_message"]
    assert h.slept == []


def test_next_part_waits_until_the_previous_went_out(
    db_session: Session, people: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _split_turn(db_session, monkeypatch, "Channel::Whatsapp", polls_until_sent=2)

    assert len(h.chatwoot.args_of("message_sent")) == 3
    assert h.slept == [processor.SENT_POLL_SECONDS] * 2
    assert h.chatwoot.sent() == [SPLIT_BLOCK, "¿Algo más?"]


def test_the_wait_has_a_limit(
    db_session: Session, people: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _split_turn(db_session, monkeypatch, "Channel::Whatsapp", polls_until_sent=1000)

    assert sum(h.slept) == processor.SENT_WAIT_SECONDS  # then the next part goes anyway
    assert h.chatwoot.sent() == [SPLIT_BLOCK, "¿Algo más?"]


def test_the_web_widget_does_not_wait(
    db_session: Session, people: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The widget gets the messages in the order they are created.
    h = _split_turn(db_session, monkeypatch, "Channel::WebWidget")

    assert "message_sent" not in h.chatwoot.names()
    assert h.chatwoot.sent() == [SPLIT_BLOCK, "¿Algo más?"]


def test_a_single_message_does_not_wait(db_session: Session, people: None) -> None:
    steps = [Call("get_payment_info", {"unit_id": _unit_id(db_session)}), Say("¿Algo más?")]
    h = make_harness(db_session, steps)

    h.handle(inbox_id=WHATSAPP_INBOX, phone=OWNER_PHONE, channel="Channel::Whatsapp")

    assert len(h.chatwoot.sent()) == 1
    assert "message_sent" not in h.chatwoot.names()
