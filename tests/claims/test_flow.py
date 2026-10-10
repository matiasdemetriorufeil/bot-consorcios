"""Reporting a claim by WhatsApp (app.claims.flow) through the agent: the steps go by the code,
without the model. LLM scripted (only where the model is asked), Postgres test database,
invented data only (fictitious buildings and people, phones 351 555-01xx)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bot.agent import Agent, AgentReply
from app.bot.tools import ToolContext, run_tool
from app.claims import texts
from app.claims.flow import EXPIRES_AFTER, MAX_PHOTOS
from app.claims.service import close_claim
from app.config import Settings
from app.db.models import (
    BotEvent,
    Building,
    BuildingClaimCategory,
    Claim,
    ClaimActor,
    ClaimCategory,
    ClaimDraft,
    ClaimScope,
    ClaimStatus,
    PersonRole,
    Provider,
    Unit,
    WaConversation,
    WaMediaStatus,
)
from app.llm import Message
from tests.admin.wa_data import conversation, message
from tests.bot import factories as f
from tests.claims import factories as cf
from tests.llm.fakes import Call, Say, scripted_provider

TZ = ZoneInfo("America/Argentina/Cordoba")
NOW = datetime(2026, 10, 8, 11, 0, tzinfo=TZ)
SETTINGS = Settings(_env_file=None)
ANA = "+5493515550101"
BETO = "+5493515550102"
TEO = "+5493515550103"  # tenant
MULTI = "+5493515550104"  # units in two buildings
NOBOT = "+5493515550105"  # a building without claims by the bot
UNKNOWN = "+5493515550199"
GAS_SAFETY = "Si sentís olor a gas: no prendas luces y abrí las ventanas (texto inventado)."


@dataclass
class World:
    session: Session
    torre: Building
    unit_a: Unit
    unit_b: Unit
    lift: ClaimCategory
    gas: ClaimCategory
    damp: ClaimCategory
    lifts: Provider
    conversation_id: int
    history: dict[str, list[Message]] = field(default_factory=dict)


@pytest.fixture
def w(db_session: Session) -> World:
    s = db_session
    torre = f.building(s, "001 TORRE INVENTADA")
    torre.claims_bot_enabled = True
    otra = f.building(s, "002 OTRA FICTICIA")
    otra.claims_bot_enabled = True
    nobot = f.building(s, "003 SIN BOT INVENTADO")
    unit_a, unit_b, unit_c = (f.unit(s, torre, label) for label in ("01-A", "02-B", "03-C"))
    unit_d = f.unit(s, torre, "04-D")
    other_unit = f.unit(s, otra, "01-A")
    nobot_unit = f.unit(s, nobot, "05-E")
    f.link(s, unit_a, f.person(s, "Ana Inventada", phone=ANA))
    f.link(s, unit_b, f.person(s, "Beto Ficticio", phone=BETO))
    f.link(s, unit_c, f.person(s, "Teo Inquilino", phone=TEO), PersonRole.TENANT)
    multi = f.person(s, "Mara Varias", phone=MULTI)
    f.link(s, unit_d, multi)
    f.link(s, other_unit, multi)
    f.link(s, nobot_unit, f.person(s, "Nino Sinbot", phone=NOBOT))
    lift = cf.category(s, "No funciona el ascensor inventado", list_title="Ascensor no funciona",
                       sort_order=10)  # fmt: skip
    lift.urgent = True
    lift.follow_up_question = "¿Hay alguien encerrado?"
    gas = cf.category(s, "Siento olor a gas inventado", list_title="Olor a gas", sort_order=20)
    gas.urgent = True
    gas.safety_text = GAS_SAFETY
    gas.emergency_phone = "0800 555 0100"
    damp = cf.category(s, "Humedad inventada", list_title="Humedad o filtración",
                       scope=ClaimScope.UNIT, sort_order=30)  # fmt: skip
    lifts = cf.provider(s, "Ascensores Ficticios SRL", "+5493515550150")
    for building in (torre, otra):
        cf.assign(s, building.id, lift, lifts)
        cf.assign(s, building.id, gas)
        cf.assign(s, building.id, damp)
    cf.assign(s, nobot.id, lift)
    conv = conversation(s, ANA[1:], profile_name="Ana")
    s.commit()
    return World(s, torre, unit_a, unit_b, lift, gas, damp, lifts, conv.id)


def _agent(steps: list[Any] | None = None, now: datetime = NOW) -> tuple[Agent, Any]:
    provider, script = scripted_provider("anthropic", steps or [])
    agent = Agent(provider, settings=SETTINGS, refresh_debt=lambda uid: None, now=lambda: now)  # type: ignore[arg-type]
    return agent, script


def say(
    w: World,
    agent: Agent,
    phone: str,
    text: str,
    photos: tuple[int, ...] = (),
) -> AgentReply:
    """One message of the person, with the conversation's history kept per phone."""
    reply = agent.reply(
        w.session,
        phone,
        text,
        w.history.get(phone, []),
        conversation_id=w.conversation_id if phone == ANA else None,
        attachment_ids=photos,
    )
    w.history[phone] = reply.history
    return reply


def _titles(reply: AgentReply) -> list[str]:
    return [c.title for c in reply.choices]


def _claims(session: Session) -> list[Claim]:
    session.expire_all()
    return list(session.scalars(select(Claim).order_by(Claim.id)))


def _draft(session: Session, phone: str) -> ClaimDraft | None:
    session.expire_all()
    return session.scalar(select(ClaimDraft).where(ClaimDraft.phone_e164 == phone))


def _photo(w: World, stored: bool = True) -> int:
    row = message(
        w.session,
        w.session.get_one(WaConversation, w.conversation_id),
        None,
        message_type="image",
        media_mime="image/jpeg",
        media_status=WaMediaStatus.STORED if stored else WaMediaStatus.FAILED,
        media_path="x/foto.jpg" if stored else None,
    )  # fmt: skip
    w.session.commit()
    return row.id


def _until_description(w: World, agent: Agent, phone: str = ANA) -> None:
    """From the menu to the description of a claim of humidity (no follow-up question)."""
    reply = say(w, agent, phone, "Registrar reclamo")
    assert "Humedad o filtración" in _titles(reply)
    reply = say(w, agent, phone, "Humedad o filtración")
    assert reply.text.startswith(texts.DESCRIBE)


# --- The whole way ----------------------------------------------------------------------------


def test_from_free_text_to_a_claim_with_a_provider(w: World) -> None:
    agent, script = _agent([Call("start_claim", {"category_hint": "no anda el ascensor"})])

    reply = say(w, agent, ANA, "Hola, no anda el ascensor")
    assert reply.text == "¿Es por *Ascensor no funciona*?"
    assert _titles(reply) == ["Sí", "Elegir otro", "Cancelar"]
    assert reply.debt_messages and "asistente" in reply.debt_messages[0].casefold()  # greeting

    reply = say(w, agent, ANA, "Sí")
    assert reply.text.startswith("¿Hay alguien encerrado?")
    reply = say(w, agent, ANA, "No, nadie")
    assert reply.text.startswith(texts.DESCRIBE)
    reply = say(w, agent, ANA, "Se trabó entre el 2 y el 3")
    assert _titles(reply) == ["Listo", "Sin fotos", "Cancelar"]
    reply = say(w, agent, ANA, "Sin fotos")
    assert "*Edificio:* TORRE INVENTADA" in reply.text
    assert "*Unidad:* todo el edificio" in reply.text
    assert "*Problema:* Ascensor no funciona" in reply.text
    assert "*Qué pasa:* Se trabó entre el 2 y el 3" in reply.text
    assert "*Fotos:* ninguna" in reply.text
    assert _titles(reply) == ["Sí, registrar", "No"]
    assert _claims(w.session) == []  # nothing until the yes

    reply = say(w, agent, ANA, "Sí, registrar")

    [claim] = _claims(w.session)
    assert reply.text == texts.CREATED_WITH_PROVIDER.format(
        number=claim.number, problem="Ascensor no funciona"
    )
    assert claim.status == ClaimStatus.PENDING_SEND and claim.provider_id == w.lifts.id
    assert claim.source == "bot" and claim.wa_conversation_id == w.conversation_id
    assert claim.follow_up_answer == "No, nadie" and claim.unit_id is None
    assert claim.reporter_unit_id == w.unit_a.id and claim.reporter_phone_e164 == ANA
    assert claim.urgent
    assert _draft(w.session, ANA) is None
    assert len(script.requests) == 1  # only the first message went to the model
    actions = [
        e.payload["action"]
        for e in w.session.scalars(select(BotEvent).where(BotEvent.event_type == "claim_flow"))
    ]
    assert actions[0] == "started" and actions[-1] == "registered"
    logged = str([e.payload for e in w.session.scalars(select(BotEvent))])
    assert "Se trabó" not in logged  # never what the person wrote


def test_from_the_menu_without_a_provider(w: World) -> None:
    agent, script = _agent()
    reply = say(w, agent, BETO, "Registrar reclamo")
    assert _titles(reply) == ["Ascensor no funciona", "Olor a gas", "Humedad o filtración"]
    assert texts.CANCEL_HINT in reply.text
    say(w, agent, BETO, "3")  # the number of the option also works
    say(w, agent, BETO, "Mancha en el techo del baño")
    reply = say(w, agent, BETO, "Listo")
    assert "*Unidad:* 02-B" in reply.text
    reply = say(w, agent, BETO, "si")
    [claim] = _claims(w.session)
    assert reply.text == texts.CREATED_FOR_STUDIO.format(
        number=claim.number, problem="Humedad o filtración"
    )
    assert claim.status == ClaimStatus.STUDIO and claim.unit_id == w.unit_b.id
    assert script.requests == []


def test_a_tenant_can_report(w: World) -> None:
    agent, _ = _agent()
    _until_description(w, agent, TEO)
    say(w, agent, TEO, "Gotea la ventana")
    say(w, agent, TEO, "Sin fotos")
    say(w, agent, TEO, "Sí, registrar")
    [claim] = _claims(w.session)
    assert claim.reporter_name == "Teo Inquilino"


def _report_lift(w: World, agent: Agent, phone: str, description: str = "No anda") -> AgentReply:
    say(w, agent, phone, "Registrar reclamo")
    say(w, agent, phone, "Ascensor no funciona")
    say(w, agent, phone, "No")
    say(w, agent, phone, description)
    say(w, agent, phone, "Sin fotos")
    return say(w, agent, phone, "Sí, registrar")


def test_a_repeated_claim_joins_and_one_already_in_is_told(w: World) -> None:
    agent, _ = _agent()
    _report_lift(w, agent, ANA)
    [claim] = _claims(w.session)

    reply = _report_lift(w, agent, BETO, "Sigue sin andar")
    assert reply.text == texts.JOINED.format(number=claim.number)
    reply = _report_lift(w, agent, BETO, "Ya avisé")
    assert reply.text == texts.ALREADY_JOINED.format(number=claim.number)
    [same] = _claims(w.session)
    assert [r.name for r in same.reporters] == ["Beto Ficticio"]


def test_the_previous_claim_is_mentioned(w: World) -> None:
    agent, _ = _agent()
    _until_description(w, agent)
    say(w, agent, ANA, "Humedad en la pared")
    say(w, agent, ANA, "Sin fotos")
    say(w, agent, ANA, "Sí, registrar")
    [old] = _claims(w.session)
    close_claim(w.session, old, ClaimStatus.SOLVED, "Arreglado", actor=ClaimActor.PANEL,
                now=NOW - timedelta(days=2))  # fmt: skip
    w.session.commit()

    _until_description(w, agent)
    say(w, agent, ANA, "Volvió la humedad")
    say(w, agent, ANA, "Sin fotos")
    reply = say(w, agent, ANA, "Sí, registrar")
    assert reply.text.endswith(texts.PREVIOUS.format(number=old.number))


def test_several_units_choose_first(w: World) -> None:
    agent, _ = _agent()
    reply = say(w, agent, MULTI, "Registrar reclamo")
    assert reply.text == texts.CHOOSE_UNIT
    assert _titles(reply)[:2] == ["04-D", "01-A"]  # by building: TORRE, then OTRA
    reply = say(w, agent, MULTI, "04-D")
    assert "Ascensor no funciona" in _titles(reply)


def test_a_building_without_claims_by_the_bot(w: World) -> None:
    agent, _ = _agent()
    reply = say(w, agent, NOBOT, "Registrar reclamo")
    assert reply.text == texts.NOT_AVAILABLE
    assert _titles(reply) == ["Sí, pasame", "No, gracias"]
    assert _draft(w.session, NOBOT) is None

    agent, script = _agent([Call("start_claim", {"category_hint": "no anda el ascensor"}),
                            Say("Te paso con una persona.")])  # fmt: skip
    say(w, agent, NOBOT, "No anda el ascensor")
    result = script.requests[1]["messages"][-1]["content"][0]["content"]
    assert "not_available" in str(result)


def test_more_options_with_twelve_kinds(w: World, db_session: Session) -> None:
    big = f.building(db_session, "009 TORRE GRANDE")
    big.claims_bot_enabled = True
    unit = f.unit(db_session, big, "01-A")
    phone = "+5493515550109"
    f.link(db_session, unit, f.person(db_session, "Gina Grande", phone=phone))
    for n in range(1, 13):
        category = cf.category(db_session, f"Problema inventado {n:02}", sort_order=n)
        cf.assign(db_session, big.id, category)
    db_session.commit()
    agent, _ = _agent()

    reply = say(w, agent, phone, "Registrar reclamo")
    assert len(reply.choices) == 10 and _titles(reply)[-1] == texts.MORE_OPTIONS
    assert _titles(reply)[8] == "Problema inventado 09"
    reply = say(w, agent, phone, texts.MORE_OPTIONS)
    assert _titles(reply) == [f"Problema inventado {n}" for n in (10, 11, 12)]
    reply = say(w, agent, phone, "Problema inventado 11")
    assert reply.text.startswith(texts.DESCRIBE)


# --- Unknown numbers and safety ---------------------------------------------------------------


def test_gas_to_an_unknown_number_gets_the_safety_text_first(w: World) -> None:
    agent, script = _agent([
        Call("start_claim", {"category_hint": "hay olor a gas"}),
        Say("Te paso con una persona del estudio."),
    ])  # fmt: skip
    reply = say(w, agent, UNKNOWN, "Hay olor a gas en el pasillo")
    assert reply.debt_messages
    assert GAS_SAFETY in reply.debt_messages[0]
    assert "Teléfono de emergencias: 0800 555 0100" in reply.debt_messages[0]
    result = str(script.requests[1]["messages"][-1]["content"])
    assert "not_verified" in result and "urgent" in result


def test_an_unknown_number_verifies_and_goes_on(w: World, db_session: Session) -> None:
    newcomer = f.person(db_session, "Nadia Nueva", email="nadia@example.com")
    unit = f.unit(db_session, w.torre, "06-F")
    f.link(db_session, unit, newcomer)
    db_session.commit()

    @dataclass
    class Sender:
        codes: list[str] = field(default_factory=list)

        def send_verification_code(self, to: str, code: str, valid_minutes: int) -> None:
            self.codes.append(code)

    sender = Sender()
    agent, _ = _agent([Call("start_claim", {"category_hint": "humedad"}), Say("Verifiquemos.")])
    say(w, agent, UNKNOWN, "Tengo humedad en el techo")
    assert _draft(w.session, UNKNOWN).step == "waiting_identity"

    ctx = ToolContext(session=db_session, phone=UNKNOWN, refresh_debt=lambda uid: None,
                      email_sender=sender, now=NOW)  # fmt: skip
    run_tool(ctx, "find_unit", {"building_text": "Torre Inventada", "unit_text": "06-F"})
    run_tool(ctx, "start_email_verification", {"unit_id": unit.id})
    confirmed = run_tool(ctx, "confirm_email_code", {"code": sender.codes[0]})

    assert confirmed["status"] == "verified" and "no escribas" in confirmed["next_step"]
    assert ctx.offer is not None and ctx.offer.text == "¿Es por *Humedad o filtración*?"
    assert _draft(w.session, UNKNOWN).step == "confirm_category"


# --- Leaving the flow ---------------------------------------------------------------------------


def test_cancel_at_any_step(w: World) -> None:
    agent, _ = _agent()
    _until_description(w, agent)
    reply = say(w, agent, ANA, "cancelar")
    assert reply.text == texts.CANCELLED
    assert _draft(w.session, ANA) is None and _claims(w.session) == []


def test_another_question_drops_the_draft_and_goes_to_the_model(w: World) -> None:
    agent, script = _agent([Say("Tu consulta, respondida.")])
    first = say(w, agent, ANA, "Registrar reclamo")
    # The first answer that fits no option: the same list once more.
    again = say(w, agent, ANA, "¿Cuánto debo de expensas?")
    assert again.text.startswith(texts.REPROMPT) and first.text in again.text
    assert _titles(again) == _titles(first) and script.requests == []
    # The second: the draft goes and the model answers.
    reply = say(w, agent, ANA, "¿Cuánto debo de expensas?")
    assert reply.text == "Tu consulta, respondida."
    assert reply.debt_messages[0] == texts.DROPPED
    assert _draft(w.session, ANA) is None and len(script.requests) == 1


def test_an_expired_draft(w: World) -> None:
    agent, _ = _agent()
    say(w, agent, ANA, "Registrar reclamo")
    later, script = _agent([Say("Hola de nuevo.")], now=NOW + EXPIRES_AFTER + timedelta(minutes=1))
    reply = say(w, later, ANA, "Humedad o filtración")
    assert reply.debt_messages[0] == texts.EXPIRED and reply.text == "Hola de nuevo."
    assert _draft(w.session, ANA) is None and len(script.requests) == 1


def test_no_means_not_registered(w: World) -> None:
    agent, _ = _agent()
    _until_description(w, agent)
    say(w, agent, ANA, "Humedad")
    say(w, agent, ANA, "Sin fotos")
    reply = say(w, agent, ANA, "No")
    assert reply.text == texts.NOT_REGISTERED and _claims(w.session) == []


# --- Photos -------------------------------------------------------------------------------------


def test_photos_with_the_limit(w: World) -> None:
    agent, _ = _agent()
    _until_description(w, agent)
    first = _photo(w)
    reply = say(w, agent, ANA, "", (first,))  # a photo instead of the description
    assert reply.text.startswith(texts.PHOTO_SAVED) and texts.DESCRIBE in reply.text
    reply = say(w, agent, ANA, "Mancha en la pared")
    assert _titles(reply) == ["Listo", "Cancelar"]  # one photo already: no "Sin fotos"
    for count in range(2, MAX_PHOTOS + 1):
        reply = say(w, agent, ANA, "", (_photo(w),))
        assert reply.text == texts.PHOTO_RECEIVED.format(count=count, limit=MAX_PHOTOS)
    reply = say(w, agent, ANA, "", (_photo(w),))
    assert reply.text == texts.PHOTO_LIMIT.format(limit=MAX_PHOTOS)
    reply = say(w, agent, ANA, "", (_photo(w, stored=False),))
    assert reply.text == texts.PHOTO_NOT_SAVED

    reply = say(w, agent, ANA, "Empezó ayer")  # a text in the photos step: to the description
    assert reply.text == texts.ADDED_TO_DESCRIPTION
    reply = say(w, agent, ANA, "Listo")
    assert "*Fotos:* 5" in reply.text and "Mancha en la pared\nEmpezó ayer" in reply.text
    say(w, agent, ANA, "Sí, registrar")
    [claim] = _claims(w.session)
    assert len(claim.attachments) == MAX_PHOTOS and claim.attachments[0].wa_message_id == first


def test_a_photo_after_listo_joins_the_summary(w: World) -> None:
    """The photo was still downloading when the person tapped *Listo*: it is processed with
    the summary already shown, joins the claim and the summary comes again."""
    agent, _ = _agent()
    _until_description(w, agent)
    say(w, agent, ANA, "Mancha en la pared")
    reply = say(w, agent, ANA, "Sin fotos")
    assert "*Fotos:* ninguna" in reply.text

    reply = say(w, agent, ANA, "", (_photo(w),))
    assert reply.text.startswith(texts.CONFIRM_PHOTO_ADDED + "\n\n")
    assert "*Fotos:* 1" in reply.text
    assert _titles(reply) == [texts.REGISTER, texts.DONT_REGISTER]
    say(w, agent, ANA, "Sí, registrar")
    [claim] = _claims(w.session)
    assert len(claim.attachments) == 1


def test_a_photo_in_the_summary_keeps_the_limit(w: World) -> None:
    agent, _ = _agent()
    _until_description(w, agent)
    say(w, agent, ANA, "Mancha en la pared")
    for _ in range(MAX_PHOTOS):
        say(w, agent, ANA, "", (_photo(w),))
    say(w, agent, ANA, "Listo")

    reply = say(w, agent, ANA, "", (_photo(w),))
    assert reply.text.startswith(texts.CONFIRM_PHOTO_LIMIT.format(limit=MAX_PHOTOS))
    assert f"*Fotos:* {MAX_PHOTOS}" in reply.text
    reply = say(w, agent, ANA, "", (_photo(w, stored=False),))
    assert reply.text.startswith(texts.CONFIRM_PHOTO_LIMIT.format(limit=MAX_PHOTOS))
    say(w, agent, ANA, "Sí, registrar")
    [claim] = _claims(w.session)
    assert len(claim.attachments) == MAX_PHOTOS


def test_a_photo_in_the_summary_that_was_not_stored(w: World) -> None:
    agent, _ = _agent()
    _until_description(w, agent)
    say(w, agent, ANA, "Mancha en la pared")
    say(w, agent, ANA, "Sin fotos")
    reply = say(w, agent, ANA, "", (_photo(w, stored=False),))
    assert reply.text.startswith(texts.CONFIRM_PHOTO_NOT_SAVED)
    assert "*Fotos:* ninguna" in reply.text


# --- "Mis reclamos" and the status of one --------------------------------------------------------


def test_my_claims_and_a_claim_s_status(w: World) -> None:
    agent, _ = _agent()
    _report_lift(w, agent, ANA)
    [claim] = _claims(w.session)

    reply = say(w, agent, ANA, "Mis reclamos")
    assert reply.text.startswith(texts.MY_CLAIMS)
    assert f"*#{claim.number}* Ascensor no funciona (TORRE INVENTADA)" in reply.text
    assert texts.STATUS[ClaimStatus.PENDING_SEND] in reply.text

    ctx = ToolContext(session=w.session, phone=ANA, refresh_debt=lambda uid: None, now=NOW)
    assert run_tool(ctx, "claim_status", {"number": claim.number})["status"] == "ok"
    assert ctx.blocks[-1].startswith(f"Reclamo *#{claim.number}* (Ascensor no funciona")
    other = ToolContext(session=w.session, phone=TEO, refresh_debt=lambda uid: None, now=NOW)
    run_tool(other, "claim_status", {"number": claim.number})
    assert other.blocks == [texts.CLAIM_NOT_FOUND]  # not hers: as if it did not exist
    run_tool(other, "claim_status", {"number": 999999})
    assert other.blocks[-1] == texts.CLAIM_NOT_FOUND
    run_tool(other, "my_claims", {})
    assert other.blocks[-1] == texts.MY_CLAIMS_NONE


def test_my_claims_of_an_unknown_number_goes_to_the_model(w: World) -> None:
    agent, script = _agent([Say("Primero verifiquemos tu número.")])
    reply = say(w, agent, UNKNOWN, "Mis reclamos")
    assert reply.text == "Primero verifiquemos tu número." and len(script.requests) == 1


def test_nothing_was_counted_as_a_model_call(w: World) -> None:
    agent, script = _agent()
    _report_lift(w, agent, ANA)
    assert script.requests == []
    assert w.session.scalar(select(func.count()).select_from(Claim)) == 1


def test_the_hint_prefers_the_list_title() -> None:
    from app.claims.flow import match_category

    def kind(name: str, title: str) -> ClaimCategory:
        return ClaimCategory(name=name, list_title=title, scope=ClaimScope.BUILDING)

    water = kind("No hay agua", "No hay agua")
    damp = kind("Tengo o hay humedad o filtración de agua", "Humedad o filtración")
    lift = kind("No funciona el o los ascensores", "Ascensor no funciona")
    kinds = [water, damp, lift]
    assert match_category(kinds, "en el edificio no hay agua desde la mañana") is water
    lights = kind("No hay luz en todo el edificio", "Sin luz en el edificio")
    assert match_category([*kinds, lights], "no hay agua en el edificio desde las 8") is water
    assert match_category([*kinds, lights], "se cortó la luz") is lights
    assert match_category(kinds, "hay una filtración en el techo") is damp
    assert match_category(kinds, "no anda el ascensor") is lift
    assert match_category(kinds, "hola") is None


def test_a_new_claim_goes_to_the_provider_and_a_repeated_one_does_not(w: World) -> None:
    from app.claims.notify import Notifier
    from tests.whatsapp.fakes import FakeWhatsApp

    fake = FakeWhatsApp()
    settings = Settings(_env_file=None, claims_payload_secret="secreto-inventado")
    provider, _ = scripted_provider("anthropic", [])
    agent = Agent(
        provider, settings=settings, refresh_debt=lambda uid: None, now=lambda: NOW,
        notifier=Notifier(fake, settings, now=lambda: NOW),
    )  # type: ignore[arg-type]  # fmt: skip

    reply = _report_lift(w, agent, ANA)
    [claim] = _claims(w.session)
    assert claim.status == ClaimStatus.SENT
    assert reply.text == texts.CREATED_AND_SENT.format(
        number=claim.number, problem="Ascensor no funciona"
    )
    assert [kind for kind, _, _ in fake.sent] == ["template"]

    reply = _report_lift(w, agent, BETO, "Sigue sin andar")
    assert reply.text == texts.JOINED.format(number=claim.number)
    assert [kind for kind, _, _ in fake.sent] == ["template"]  # nothing new to the provider


def test_out_of_the_providers_hours_the_neighbor_is_told_when(w: World) -> None:
    """A claim that is not urgent, a Friday at 21:00: it goes to the company on Saturday at
    8:00 (app.claims.jobs) and the neighbor is told so."""
    from app.claims.notify import Notifier
    from tests.whatsapp.fakes import FakeWhatsApp

    row = w.session.scalar(
        select(BuildingClaimCategory).where(
            BuildingClaimCategory.building_id == w.torre.id,
            BuildingClaimCategory.category_id == w.damp.id,
        )
    )
    row.provider_id = w.lifts.id
    w.session.commit()
    friday_night = datetime(2026, 10, 9, 21, 0, tzinfo=TZ)
    fake = FakeWhatsApp()
    settings = Settings(_env_file=None, claims_payload_secret="secreto-inventado")
    provider, _ = scripted_provider("anthropic", [])
    agent = Agent(
        provider, settings=settings, refresh_debt=lambda uid: None, now=lambda: friday_night,
        notifier=Notifier(fake, settings, now=lambda: friday_night),
    )  # type: ignore[arg-type]  # fmt: skip

    for text in ("Registrar reclamo", "Humedad o filtración", "Mancha en el techo", "Sin fotos"):
        say(w, agent, ANA, text)
    reply = say(w, agent, ANA, "Sí, registrar")
    [claim] = _claims(w.session)
    assert claim.status == ClaimStatus.PENDING_SEND
    assert claim.send_after == datetime(2026, 10, 10, 8, 0, tzinfo=TZ)
    assert reply.text == (
        f"Listo, registramos tu reclamo *#{claim.number}* (Humedad o filtración). Le avisamos "
        "a la empresa mañana a las 8:00."
    )
    assert fake.sent == []
