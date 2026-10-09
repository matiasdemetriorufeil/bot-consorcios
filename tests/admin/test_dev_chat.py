"""The panel's test chat (development only): a message typed there is answered by the same bot
as WhatsApp's, the conversation is one more in "Conversaciones", and what an operator answers
from the inbox shows up in the chat without reaching Meta. Invented data only."""

import html
import secrets
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.bot.agent import Agent
from app.channels.processor import BotProcessor
from app.db.models import WaContact, WaConversation, WaConversationStatus
from app.whatsapp.bot import WhatsAppBot
from app.whatsapp.channel import WhatsAppChannel
from app.whatsapp.simulator import DevClient
from tests.admin.conftest import (
    NOW,
    OPERATOR,
    OPERATOR_NAME,
    USER_PASSWORD,
    Panel,
    admin_settings,
    build_panel,
    make_user,
)
from tests.bot import factories as f
from tests.llm.fakes import Say, scripted_provider
from tests.whatsapp.fakes import FakeWhatsApp

OWNER_PHONE = "+5493515550101"  # invented
INVENTED_PHONE = "+5493515550000"


class DevPanel(Panel):
    script: Any
    real: FakeWhatsApp

    def send(self, text: str, phone: str = OWNER_PHONE, *, tapped: bool = False) -> Any:
        return self.client.post(
            "/admin/dev-chat/send",
            data={"phone": phone, "text": text, "tapped": "1" if tapped else "0"},
        )

    def poll(self, phone: str = OWNER_PHONE, after: int = 0) -> dict[str, Any]:
        response = self.client.get("/admin/dev-chat/poll", params={"phone": phone, "after": after})
        assert response.status_code == 200
        return response.json()

    def conversation(self, phone: str = OWNER_PHONE) -> WaConversation:
        self.session.expire_all()
        conversation = self.session.scalar(
            select(WaConversation)
            .join(WaContact, WaContact.id == WaConversation.contact_id)
            .where(WaContact.phone_e164 == phone)
        )
        assert conversation is not None
        return conversation


@pytest.fixture
def dev(db_session: Session) -> Iterator[DevPanel]:
    pilot = f.building(db_session, "031 RODAS II")
    pilot.pilot = True
    unit = f.unit(db_session, pilot, "04-C")
    f.link(db_session, unit, f.person(db_session, "Ana Prueba", phone=OWNER_PHONE))
    settings = admin_settings(app_env="development")
    llm, script = scripted_provider(
        "anthropic", [Say("¡Hola Ana! ¿En qué te ayudo?"), Say("¡De nada!")]
    )
    agent = Agent(llm, settings=settings, now=lambda: NOW)
    # Like the panel's: each one a SAVEPOINT of the test connection, so their commits nest.
    sessions = sessionmaker(bind=db_session.get_bind(), join_transaction_mode="create_savepoint")
    real = FakeWhatsApp()  # what would reach Meta
    client = DevClient(real, sessions)  # type: ignore[arg-type]
    channel = WhatsAppChannel(client, sessions, now=lambda: NOW)  # type: ignore[arg-type]
    processor = BotProcessor(channel, sessions, lambda: agent, settings, now=lambda: NOW)
    bot = WhatsAppBot(processor, sessions, None, now=lambda: NOW)
    built = build_panel(db_session, settings, sender=client, bot_factory=lambda: bot)
    panel = DevPanel(**vars(built))
    panel.script, panel.real = script, real
    with panel.client:
        assert panel.login().status_code == 302
        yield panel


def test_a_test_message_is_answered_by_the_bot_and_never_reaches_meta(dev: DevPanel) -> None:
    page = dev.client.get("/admin/dev-chat", params={"phone": OWNER_PHONE})
    assert page.status_code == 200
    assert "Escribís como 351 555-0101 (Ana Prueba)" in page.text

    assert dev.send("hola").status_code == 200

    data = dev.poll()
    assert "hola" in data["html"] and "¡Hola Ana! ¿En qué te ayudo?" in data["html"]
    assert data["status"] == "Con el bot"
    assert dev.real.sent == []
    # One more conversation of the inbox, with the person identified by the phone.
    listed = dev.client.get("/admin/conversations", params={"tab": "bot"})
    assert "Ana Prueba" in listed.text


def test_take_answer_and_return_with_two_users(dev: DevPanel) -> None:
    dev.send("hola")
    conversation_id = dev.conversation().id
    after = dev.poll()["last_id"]
    make_user(dev.session, OPERATOR, display_name=OPERATOR_NAME)
    operator = TestClient(dev.client.app, base_url="https://testserver")
    with operator:
        login = {"username": OPERATOR, "password": USER_PASSWORD}
        assert operator.post("/admin/login", data=login, follow_redirects=False).status_code == 302
        action = f"/admin/conversations/{conversation_id}/action"

        operator.post(action, data={"action": "take"})
        assert dev.conversation().status == WaConversationStatus.HUMAN
        # With a person: the bot does not answer the test chat any more.
        dev.send("¿hay alguien?")
        assert len(dev.script.requests) == 1

        token = secrets.token_urlsafe(8)
        operator.post(
            action, data={"action": "reply", "text": "Hola, soy Marta", "form_token": token}
        )
        data = dev.poll(after=after)
        assert "Hola, soy Marta" in data["html"] and OPERATOR_NAME in data["html"]
        assert data["status"] == "Con una persona"

        operator.post(action, data={"action": "return"})
    dev.send("gracias")

    assert "¡De nada!" in dev.poll(after=after)["html"]
    assert len(dev.script.requests) == 2
    assert dev.real.sent == []  # neither the bot nor the operator reached Meta


def test_an_option_tapped_goes_as_its_title(dev: DevPanel) -> None:
    dev.send("Sí, pasame", tapped=True)

    assert "Sí, pasame" in dev.poll()["html"]
    assert dev.script.requests  # answered by the bot


def test_restart_and_search(dev: DevPanel) -> None:
    dev.send("hola", phone=INVENTED_PHONE)
    assert dev.conversation(INVENTED_PHONE).last_inbound_at is not None

    dev.client.post("/admin/dev-chat/restart", data={"phone": INVENTED_PHONE})

    assert dev.poll(INVENTED_PHONE)["html"] == ""
    assert dev.conversation(INVENTED_PHONE).last_inbound_at is None
    found = dev.client.get("/admin/dev-chat", params={"q": "ana"})
    assert "Ana Prueba" in found.text and "351 555-0101" in found.text


def test_invalid_phone_or_empty_text(dev: DevPanel) -> None:
    assert dev.send("hola", phone="123").status_code == 400
    assert dev.send("   ").status_code == 400
    assert "no es válido" in dev.client.get("/admin/dev-chat", params={"phone": "abc"}).text


def test_there_is_no_test_chat_in_production(logged_in: Panel) -> None:
    assert logged_in.client.get("/admin/dev-chat").status_code == 404
    assert logged_in.client.post("/admin/dev-chat/send", data={}).status_code in (404, 405)
    assert "Chat de prueba" not in logged_in.client.get("/admin/conversations").text


def test_the_home_page_goes_to_conversations(panel: Panel) -> None:
    anonymous = panel.client.get("/admin/", follow_redirects=False)
    assert anonymous.status_code == 302
    assert anonymous.headers["location"].endswith("/admin/conversations")

    panel.login()
    page = panel.client.get("/admin/")
    assert page.status_code == 200
    assert str(page.url).endswith("/admin/conversations")


# --- The whole claims circuit, without a phone ---------------------------------------------------

PROVIDER_PHONE = "+5493515550150"  # invented


@pytest.fixture
def circuit(db_session: Session) -> Iterator[DevPanel]:
    from app.claims.notify import Notifier
    from tests.claims import factories as cf

    building = f.building(db_session, "031 RODAS II")
    building.pilot = True
    building.claims_bot_enabled = True
    unit = f.unit(db_session, building, "04-C")
    f.link(db_session, unit, f.person(db_session, "Ana Prueba", phone=OWNER_PHONE))
    damp = cf.category(db_session, "Humedad inventada", list_title="Humedad o filtración")
    plumber = cf.provider(db_session, "Plomería Ficticia", PROVIDER_PHONE)
    cf.assign(db_session, building.id, damp, plumber)
    settings = admin_settings(app_env="development", claims_payload_secret="secreto-inventado")
    llm, script = scripted_provider("anthropic", [])
    sessions = sessionmaker(bind=db_session.get_bind(), join_transaction_mode="create_savepoint")
    real = FakeWhatsApp()  # what would reach Meta
    client = DevClient(real, sessions)  # type: ignore[arg-type]
    notifier = Notifier(client, settings, now=lambda: NOW)
    agent = Agent(llm, settings=settings, now=lambda: NOW, notifier=notifier)
    channel = WhatsAppChannel(client, sessions, now=lambda: NOW)  # type: ignore[arg-type]
    processor = BotProcessor(
        channel, sessions, lambda: agent, settings, now=lambda: NOW, notifier=notifier
    )
    bot = WhatsAppBot(processor, sessions, None, now=lambda: NOW)
    built = build_panel(db_session, settings, sender=client, bot_factory=lambda: bot)
    panel = DevPanel(**vars(built))
    panel.script, panel.real = script, real
    with panel.client:
        assert panel.login().status_code == 302
        yield panel


def _buttons(html: str) -> dict[str, str]:
    import html as html_lib
    import re

    found = re.findall(r'data-title="([^"]*)" data-payload="([^"]*)"', html)
    return {html_lib.unescape(t): html_lib.unescape(p) for t, p in found}


def test_speak_as_a_provider_and_close_the_circuit(circuit: DevPanel) -> None:
    from app.db.models import Claim, ClaimStatus, Provider

    plumber = circuit.session.scalar(select(Provider))
    page = circuit.client.get("/admin/dev-chat").text
    assert "Hablar como proveedor" in page and "Plomería Ficticia" in page
    response = circuit.client.post(
        "/admin/dev-chat/provider", data={"provider_id": str(plumber.id)}, follow_redirects=False
    )
    assert response.status_code == 302 and "5550150" in response.headers["location"]

    for text, tapped in [
        ("Registrar reclamo", True), ("Humedad o filtración", True),
        ("Mancha en el techo", False), ("Sin fotos", True), ("Sí, registrar", True),
    ]:  # fmt: skip
        circuit.send(text, tapped=tapped)
    provider_side = circuit.poll(PROVIDER_PHONE)["html"]
    assert "Nuevo reclamo #" in provider_side and "Humedad o filtración" in provider_side
    buttons = _buttons(provider_side)
    assert set(buttons) == {"Recibido", "No puedo atenderlo"}

    circuit.client.post(
        "/admin/dev-chat/send",
        data={"phone": PROVIDER_PHONE, "text": "Recibido", "tapped": "1",
              "payload": buttons["Recibido"]},
    )  # fmt: skip
    assert "La empresa ya confirmó" in circuit.poll()["html"]
    solved = _buttons(circuit.poll(PROVIDER_PHONE)["html"])["Ya está solucionado"]
    circuit.client.post(
        "/admin/dev-chat/send",
        data={"phone": PROVIDER_PHONE, "text": "Ya está solucionado", "tapped": "1",
              "payload": solved},
    )  # fmt: skip
    assert "ya está solucionado, así que lo cerramos" in circuit.poll()["html"]
    circuit.session.expire_all()
    assert circuit.session.scalar(select(Claim)).status == ClaimStatus.SOLVED
    assert circuit.real.sent == []  # nothing reached Meta
    assert circuit.script.requests == []


def test_run_the_claim_jobs_as_if_it_were_later(circuit: DevPanel) -> None:
    """NOW is a Wednesday at 11:00: the claim goes at once; "como si fueran las" 15:05 the
    provider gets the reminder (4 hours of the providers' hours), at 19:05 the studio is told
    (8 hours). Only once each."""
    from app.db.models import Claim, ClaimAttention, ClaimEventKind, Provider

    plumber = circuit.session.scalar(select(Provider))
    circuit.client.post("/admin/dev-chat/provider", data={"provider_id": str(plumber.id)})
    for text, tapped in [
        ("Registrar reclamo", True), ("Humedad o filtración", True),
        ("Mancha en el techo", False), ("Sin fotos", True), ("Sí, registrar", True),
    ]:  # fmt: skip
        circuit.send(text, tapped=tapped)
    page = circuit.client.get("/admin/dev-chat").text
    assert 'id="claim-jobs"' in page and "Correr tareas de reclamos ahora" in page

    def run(at: str) -> str:
        response = circuit.client.post(
            "/admin/dev-chat/claim-jobs", data={"at": at, "phone": PROVIDER_PHONE},
            follow_redirects=True,
        )  # fmt: skip
        return html.unescape(response.text)

    assert "0 recordatorio(s)" in run("2026-09-30T14:55")
    page = run("2026-09-30T15:05")
    assert "como si fueran las 30/09/2026 15:05" in page and "1 recordatorio(s)" in page
    assert "Recordatorio: el reclamo #" in circuit.poll(PROVIDER_PHONE)["html"]
    assert "0 recordatorio(s)" in run("2026-09-30T15:10")  # only once
    assert "1 aviso(s) al estudio" in run("2026-09-30T19:05")
    assert "0 aviso(s) al estudio" in run("2026-09-30T19:10")

    circuit.session.expire_all()
    claim = circuit.session.scalar(select(Claim))
    assert claim.attention == ClaimAttention.NO_ACK
    kinds = [e.kind for e in claim.events]
    assert kinds.count(ClaimEventKind.REMINDED) == 1 and kinds.count(ClaimEventKind.ALERT) == 1
    assert circuit.real.sent == []  # nothing reached Meta
    assert "no se entiende" in run("ayer a la tarde")


def test_the_claim_jobs_button_only_in_development(logged_in: Panel) -> None:
    response = logged_in.client.post("/admin/dev-chat/claim-jobs", data={})
    assert response.status_code in (404, 405)
