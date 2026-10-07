"""Agent tool loop and tools, with BOTH providers mocked, against the Postgres test database.
All data is invented."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import identity, tools
from app.bot.agent import FALLBACK_REPLY, MAX_ROUNDS, Agent, trim_history
from app.bot.bot_config import invalidate_bot_config
from app.bot.choices import Choice, with_options
from app.bot.debt_message import FIRST_GREETING
from app.bot.identity import identify_by_phone
from app.bot.prompts import SYSTEM_PROMPT, describe_identity
from app.bot.tools import ToolContext, run_tool
from app.config import Settings
from app.db.models import (
    BotEvent,
    BotSettings,
    DebtLine,
    DebtSnapshot,
    PersonRole,
    SyncKind,
    Unit,
)
from app.llm import (
    AssistantMessage,
    LLMError,
    Prices,
    ToolCall,
    ToolResult,
    ToolResultsMessage,
    UserMessage,
)
from app.notify.email import EmailError
from app.sync.live import DebtResult
from tests.bot import factories as f
from tests.llm.fakes import (
    PROVIDERS,
    Call,
    Say,
    last_tool_result,
    last_user_text,
    scripted_provider,
    system_of,
)

OWNER_PHONE = "+5493515550101"
TENANT_PHONE = "+5493515550202"
UNKNOWN_PHONE = "+5493515550999"
PAYMENT_CODE = "0000111122223333444"  # invented, 19 digits
FETCHED = datetime(2026, 9, 30, 17, 5, tzinfo=UTC)  # 14:05 in Córdoba
NOW = datetime(2026, 9, 30, 11, 0, tzinfo=ZoneInfo("America/Argentina/Cordoba"))
SETTINGS = Settings(_env_file=None)


@dataclass
class World:
    session: Session
    unit_id: int
    other_unit_id: int
    snapshot: DebtSnapshot
    refreshed: list[int] = field(default_factory=list)
    stale: bool = False

    def refresh(self, unit_id: int) -> DebtResult:
        self.refreshed.append(unit_id)
        return DebtResult(self.snapshot, stale=self.stale)

    def ctx(self, phone: str) -> ToolContext:
        return ToolContext(session=self.session, phone=phone, refresh_debt=self.refresh)

    def events(self, event_type: str) -> list[BotEvent]:
        return list(
            self.session.scalars(
                select(BotEvent).where(BotEvent.event_type == event_type).order_by(BotEvent.id)
            )
        )


@pytest.fixture
def world(db_session: Session) -> World:
    rodas = f.building(db_session, "031 RODAS II")
    unit = f.unit(db_session, rodas, "04-C")
    unit.payment_code = PAYMENT_CODE
    other = f.unit(db_session, rodas, "05-A")
    owner = f.person(db_session, "Ana Ficticia", email="ana@example.com", phone=OWNER_PHONE)
    tenant = f.person(db_session, "Tito Inventado", phone=TENANT_PHONE)
    f.link(db_session, unit, owner)
    f.link(db_session, unit, tenant, PersonRole.TENANT)
    snapshot = DebtSnapshot(
        unit_id=unit.id,
        fetched_at=FETCHED,
        source=SyncKind.LIVE,
        total_amount=Decimal("165060.00"),
        is_up_to_date=False,
        lines=[
            DebtLine(
                concept="Expensas ordinarias",
                period="09/2026",
                concept_amount=Decimal("165060.00"),
                balance_due=Decimal("165060.00"),
                accumulated=Decimal("165060.00"),
            )
        ],
    )
    db_session.add(snapshot)
    db_session.commit()
    return World(db_session, unit.id, other.id, snapshot)


def make_agent(name: str, steps: list[Any], world: World) -> tuple[Agent, Any]:
    provider, script = scripted_provider(name, steps)
    agent = Agent(
        provider,
        prices=Prices(input=1, output=5, cache_read=0.1, cache_write=1.25),
        settings=SETTINGS,
        refresh_debt=world.refresh,
        now=lambda: NOW,
    )
    return agent, script


# --- Tool loop, both providers -----------------------------------------------------------


@pytest.mark.parametrize("name", PROVIDERS)
def test_tool_loop_gets_debt_for_verified_owner(name: str, world: World) -> None:
    steps = [
        Call("get_debt", {"unit_id": world.unit_id}),
        Say("Tu deuda es *$165.060,00*."),
    ]
    agent, script = make_agent(name, steps, world)

    reply = agent.reply(world.session, OWNER_PHONE, "Hola, ¿cuánto debo?")

    assert reply.text == "Tu deuda es *$165.060,00*."
    assert reply.error is None and not reply.handed_off
    assert world.refreshed == [world.unit_id]
    assert reply.tools_called == [("get_debt", "ok")]
    result = last_tool_result(name, script.requests[1])
    assert "$165.060,00" in result
    assert PAYMENT_CODE in result
    assert "30/09/2026 a las 14:05" in result
    # Fixed system prompt; the variable context goes in the user message.
    first = script.requests[0]
    assert system_of(name, first) == SYSTEM_PROMPT
    user_text = last_user_text(name, first)
    assert "30/09/2026 11:00" in user_text
    assert "Primer mensaje de la conversación: sí" in user_text
    assert "Ana Ficticia" in user_text and "propietario" in user_text
    assert user_text.endswith("Hola, ¿cuánto debo?")
    # History stores the plain text, and the tool exchange.
    assert reply.history[0] == UserMessage("Hola, ¿cuánto debo?")
    assert isinstance(reply.history[2], ToolResultsMessage)
    # Events: one tool call, one usage per model call, one turn summary with cost.
    [call] = world.events("tool_call")
    assert call.payload["tool"] == "get_debt" and call.payload["status"] == "ok"
    usage = world.events("llm_usage")
    assert [u.payload["provider"] for u in usage] == [name, name]
    [turn] = world.events("agent_turn")
    assert turn.payload["rounds"] == 2
    assert turn.payload["input_tokens"] == 2000
    assert turn.payload["cost_usd"] > 0


@pytest.mark.parametrize("name", PROVIDERS)
def test_debt_message_goes_before_the_agent_text(name: str, world: World) -> None:
    steps = [Call("get_debt", {"unit_id": world.unit_id}), Say("¿Te ayudo con algo más?")]
    agent, script = make_agent(name, steps, world)

    reply = agent.reply(world.session, OWNER_PHONE, "¿cuánto debo?")

    [message] = reply.debt_messages
    # First message of the conversation: the fixed greeting goes before the block, and the
    # model is told so (it must not introduce itself again).
    assert message.startswith(f"{FIRST_GREETING}\n\n*RODAS II 04-C*\nSaldo total: *$165.060,00*")
    assert tools.GREETED_STEP.strip() in last_tool_result(name, script.requests[1])
    assert reply.text == "¿Te ayudo con algo más?"
    # The history keeps what the person saw, as the channel gives it back next time.
    assert reply.history[-1] == AssistantMessage(f"{message}\n\n¿Te ayudo con algo más?")
    assert world.events("debt_amount_mismatch") == []


@pytest.mark.parametrize("name", PROVIDERS)
def test_no_greeting_after_the_first_message(name: str, world: World) -> None:
    steps = [Call("get_debt", {"unit_id": world.unit_id}), Say("Listo.")]
    agent, script = make_agent(name, steps, world)
    past = [UserMessage("hola"), AssistantMessage("¡Hola! ¿En qué te ayudo?")]

    reply = agent.reply(world.session, OWNER_PHONE, "¿cuánto debo?", past)

    [message] = reply.debt_messages
    assert message.startswith("*RODAS II 04-C*\n")
    assert FIRST_GREETING not in message
    assert tools.GREETED_STEP.strip() not in last_tool_result(name, script.requests[1])


def test_one_debt_message_per_unit_in_call_order(world: World) -> None:
    unit = world.session.get(Unit, world.unit_id)
    garage = f.unit(world.session, unit.building, "COC.3")
    owner = f.person(world.session, "Otra Ficticia", phone="+5493515550111")
    f.link(world.session, unit, owner)
    f.link(world.session, garage, owner)
    world.session.commit()
    steps = [
        Call("get_debt", {"unit_id": garage.id}),
        Call("get_debt", {"unit_id": world.unit_id}),
        Say("Listo, ahí tenés las dos."),
    ]
    agent, _ = make_agent("anthropic", steps, world)

    reply = agent.reply(world.session, "+5493515550111", "la deuda de mis dos unidades")

    first, second = reply.debt_messages
    # Only the first one carries the greeting (first message of the conversation).
    assert first.startswith(f"{FIRST_GREETING}\n\n*RODAS II COC.3*\n")
    assert second.startswith("*RODAS II 04-C*\n")


@pytest.mark.parametrize(
    ("text", "logged"),
    [
        ("Debés *$165.060,00* en total.", None),  # repeats an amount of the message: fine
        ("Debés $160.000,00 en total.", ["$160.000,00"]),
    ],
)
def test_amount_not_in_the_debt_message_is_logged(
    text: str, logged: list[str] | None, world: World
) -> None:
    steps = [Call("get_debt", {"unit_id": world.unit_id}), Say(text)]
    agent, _ = make_agent("anthropic", steps, world)

    reply = agent.reply(world.session, OWNER_PHONE, "¿cuánto debo?")

    assert reply.text == text  # never blocked: the debt message already has the right data
    events = world.events("debt_amount_mismatch")
    if logged is None:
        assert events == []
    else:
        [event] = events
        assert event.payload == {"amounts": logged, "debt_messages": 1}
        assert event.phone_e164 == OWNER_PHONE


def test_no_debt_message_when_the_turn_fails(world: World) -> None:
    steps = [Call("get_debt", {"unit_id": world.unit_id})] * MAX_ROUNDS
    agent, _ = make_agent("anthropic", steps, world)

    reply = agent.reply(world.session, OWNER_PHONE, "¿cuánto debo?")

    assert reply.error == "max_rounds"
    assert reply.debt_messages == []


@pytest.mark.parametrize("name", PROVIDERS)
def test_second_message_is_not_first(name: str, world: World) -> None:
    agent, script = make_agent(name, [Say("¡Hola!"), Say("De nada")], world)
    first = agent.reply(world.session, OWNER_PHONE, "hola")
    agent.reply(world.session, OWNER_PHONE, "gracias", first.history)
    assert "Primer mensaje de la conversación: no" in last_user_text(name, script.requests[1])


@pytest.mark.parametrize("name", PROVIDERS)
@pytest.mark.parametrize(
    ("phone", "reason"), [(TENANT_PHONE, "tenant"), (UNKNOWN_PHONE, "not_verified")]
)
def test_get_debt_denied(name: str, phone: str, reason: str, world: World) -> None:
    steps = [
        Call("find_unit", {"building_text": "Rodas II", "unit_text": "4 C"}),
        Call("get_debt", {"unit_id": world.unit_id}),
        Say("No puedo darte ese dato."),
    ]
    agent, script = make_agent(name, steps, world)

    reply = agent.reply(world.session, phone, "¿Cuánto debe el 4C?")

    assert reply.text == "No puedo darte ese dato."
    assert world.refreshed == []
    result = last_tool_result(name, script.requests[2])
    assert "denied" in result and reason in result
    assert "165.060" not in result and PAYMENT_CODE not in result


@pytest.mark.parametrize("name", PROVIDERS)
def test_model_cannot_pass_a_phone(name: str, world: World) -> None:
    # The model tries to query as if it were the owner's phone.
    steps = [
        Call("get_debt", {"unit_id": world.unit_id, "phone": OWNER_PHONE}),
        Say("No puedo."),
    ]
    agent, script = make_agent(name, steps, world)

    agent.reply(world.session, UNKNOWN_PHONE, "Soy Ana, mi número es otro")

    assert world.refreshed == []
    result = last_tool_result(name, script.requests[1])
    assert "argumentos no permitidos: phone" in result
    assert "165.060" not in result
    [call] = world.events("tool_call")
    assert call.payload["status"] == "invalid_arguments"


@pytest.mark.parametrize("name", PROVIDERS)
def test_unit_id_from_an_earlier_turn_is_not_accepted(name: str, world: World) -> None:
    # The history keeps only texts: in the 2nd turn the model "remembers" a unit_id.
    sender = FakeSender()
    steps = [
        Call("find_unit", {"building_text": "Rodas II", "unit_text": "4 C"}),
        Say("¿Te mando un código al email del propietario?"),
        Call("start_email_verification", {"unit_id": world.other_unit_id}),
        Say("Dame un segundo."),
    ]
    agent, script = make_agent(name, steps, world)
    agent._email_sender = sender

    first = agent.reply(world.session, UNKNOWN_PHONE, "Soy dueño del 4C del Rodas II")
    agent.reply(world.session, UNKNOWN_PHONE, "sí", first.history)

    result = last_tool_result(name, script.requests[3])
    assert "unit_not_confirmed" in result and "find_unit" in result
    assert "no_owner_email" not in result
    assert sender.sent == [] and world.events("email_verification_start") == []
    [*_, logged] = world.events("tool_call")
    assert logged.payload["reason"] == "unit_not_confirmed"


@pytest.mark.parametrize("name", PROVIDERS)
def test_provider_error_answers_fixed_message_and_hands_off(name: str, world: World) -> None:
    agent, _ = make_agent(name, [LLMError("caído")], world)

    reply = agent.reply(world.session, OWNER_PHONE, "hola")

    assert reply.text.startswith(FALLBACK_REPLY)
    assert reply.handed_off and reply.error.startswith("provider_error")
    [handoff] = world.events("handoff")
    assert handoff.payload["reason"] == "technical_error"
    assert world.events("agent_error")


@pytest.mark.parametrize("name", PROVIDERS)
def test_real_sdk_error_also_falls_back(name: str, world: World) -> None:
    import anthropic
    import httpx2
    from google.genai import errors

    error = (
        anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x"))
        if name == "anthropic"
        else errors.ServerError(503, {"error": {"message": "x", "status": "UNAVAILABLE"}})
    )
    agent, _ = make_agent(name, [error], world)
    assert agent.reply(world.session, OWNER_PHONE, "hola").text.startswith(FALLBACK_REPLY)


@pytest.mark.parametrize("name", PROVIDERS)
def test_loop_exhausted(name: str, world: World) -> None:
    steps = [Call("get_building_info", {"question": "?"})] * (MAX_ROUNDS + 1)
    agent, script = make_agent(name, steps, world)

    reply = agent.reply(world.session, OWNER_PHONE, "¿Se puede tener perro?")

    assert reply.text.startswith(FALLBACK_REPLY) and reply.error == "max_rounds"
    assert len(reply.tools_called) == MAX_ROUNDS
    assert len(script.requests) == MAX_ROUNDS
    assert len(world.events("handoff")) == 1
    assert reply.history[-1] == AssistantMessage(reply.text)


@pytest.mark.parametrize("name", PROVIDERS)
def test_model_handoff_is_not_duplicated(name: str, world: World) -> None:
    steps = [
        Call("handoff_to_human", {"reason": "payment_plan", "summary": "x", "priority": "normal"}),
        Say("Te paso con una persona del estudio."),
    ]
    agent, _ = make_agent(name, steps, world)
    reply = agent.reply(world.session, OWNER_PHONE, "quiero un plan de pagos")
    assert reply.handed_off and reply.error is None
    assert len(world.events("handoff")) == 1


@pytest.mark.parametrize("name", PROVIDERS)
def test_urgent_handoff_tells_the_emergency_contact_out_of_hours(name: str, world: World) -> None:
    steps = [
        Call("handoff_to_human", {"reason": "emergency", "summary": "x", "priority": "urgent"}),
        Say("Cerrá la llave de paso."),
    ]
    provider, script = scripted_provider(name, steps)
    saturday_night = datetime(2026, 10, 3, 22, 0, tzinfo=ZoneInfo("America/Argentina/Cordoba"))
    agent = Agent(
        provider,
        settings=SETTINGS.model_copy(update={"emergency_contact_text": "llamá al 351 000-0000."}),
        refresh_debt=world.refresh,
        now=lambda: saturday_night,
    )
    agent.reply(world.session, UNKNOWN_PHONE, "se inunda el baño")
    told = last_tool_result(name, script.requests[1])
    assert "el lunes a partir de las 9" in told and "llamá al 351 000-0000." in told


@pytest.mark.parametrize("name", PROVIDERS)
def test_admin_panel_settings_reach_the_agent(name: str, world: World) -> None:
    """Values of the admin panel (bot_settings) win over .env, without restarting."""
    row = world.session.get(BotSettings, 1)
    row.welcome_message = "¡Hola! Te atiende el asistente del estudio inventado."
    row.payment_code_how_to = "Pagalo con el código en el banco inventado."
    row.autogestion_url = "https://autogestion.example.com"
    row.out_of_hours_text = "Te contestamos el próximo día hábil."
    world.session.commit()
    invalidate_bot_config()
    steps = [
        Call("get_debt", {"unit_id": world.unit_id}),
        Call("handoff_to_human", {"reason": "payment_plan", "summary": "x", "priority": "normal"}),
        Say("Listo."),
    ]
    provider, script = scripted_provider(name, steps)
    saturday_night = datetime(2026, 10, 3, 22, 0, tzinfo=ZoneInfo("America/Argentina/Cordoba"))
    agent = Agent(
        provider,
        settings=SETTINGS.model_copy(update={"payment_code_how_to": "texto de .env"}),
        refresh_debt=world.refresh,
        now=lambda: saturday_night,
    )

    agent.reply(world.session, OWNER_PHONE, "hola, ¿cuánto debo?")

    first_turn = last_user_text(name, script.requests[0])
    assert "Mensaje de bienvenida del estudio: ¡Hola! Te atiende el asistente" in first_turn
    debt = last_tool_result(name, script.requests[1])
    assert "Pagalo con el código en el banco inventado." in debt
    assert "texto de .env" not in debt
    assert "https://autogestion.example.com" in debt
    told = last_tool_result(name, script.requests[2])
    assert "Te contestamos el próximo día hábil." in told
    assert "el lunes a partir de las 9" not in told


def test_get_debt_without_autogestion_url_does_not_mention_it(world: World) -> None:
    result = run_tool(world.ctx(OWNER_PHONE), "get_debt", {"unit_id": world.unit_id})
    assert "autogestion_url" not in result


def test_normal_handoff_does_not_get_the_urgent_notice(world: World) -> None:
    ctx = world.ctx(OWNER_PHONE)
    ctx.handoff_notice, ctx.urgent_handoff_notice = "normal", "urgente"
    args = {"reason": "payment_plan", "summary": "x", "priority": "normal"}
    assert run_tool(ctx, "handoff_to_human", args)["tell_person"] == "normal"
    args = {"reason": "emergency", "summary": "x", "priority": "urgent"}
    assert run_tool(ctx, "handoff_to_human", args)["tell_person"] == "urgente"


# --- Tools directly ------------------------------------------------------------------------


def test_get_debt_owner_builds_the_debt_message(world: World) -> None:
    ctx = world.ctx(OWNER_PHONE)
    result = run_tool(ctx, "get_debt", {"unit_id": world.unit_id})
    [message] = ctx.debt_messages.values()
    assert message.splitlines()[:4] == [
        "*RODAS II 04-C*",
        "Saldo total: *$165.060,00*",
        "• 09/2026 Expensas ordinarias: $165.060,00",
        "Dato al 30/09/2026 a las 14:05.",
    ]
    assert f"Código de pago Siro: *{PAYMENT_CODE}*" in message
    assert "Pago Mis Cuentas" in message
    assert "último dato guardado" not in message
    # The model learns that it went out and is told not to repeat it.
    assert result["status"] == "ok" and result["unit"] == "RODAS II 04-C"
    assert result["already_sent"] == message
    assert result["next_step"] == tools.DEBT_SENT_STEP
    assert not {"total_debt", "detail", "payment_code", "payment_how_to"} & set(result)


def test_get_debt_stale_and_without_payment_code(world: World) -> None:
    world.stale = True
    world.session.get(Unit, world.unit_id).payment_code = None
    ctx = world.ctx(OWNER_PHONE)
    run_tool(ctx, "get_debt", {"unit_id": world.unit_id})
    [message] = ctx.debt_messages.values()
    assert "No se pudo actualizar ahora: es el último dato guardado." in message
    assert "Código de pago" not in message
    assert message.endswith(tools.NO_PAYMENT_CODE)


def test_get_debt_cuts_the_detail_and_says_how_many_more(world: World) -> None:
    world.snapshot.lines = [
        DebtLine(
            concept="Expensas ordinarias",
            period=f"{month:02d}/2025",
            concept_amount=Decimal("1000"),
            balance_due=Decimal("1000"),
            accumulated=Decimal("1000"),
        )
        for month in range(1, 13)
    ] + [
        DebtLine(
            concept="Fondo de reserva",
            period=f"{month:02d}/2026",
            concept_amount=Decimal("500"),
            balance_due=Decimal("500"),
            accumulated=Decimal("500"),
        )
        for month in range(1, 4)
    ]
    world.session.commit()
    ctx = world.ctx(OWNER_PHONE)
    run_tool(ctx, "get_debt", {"unit_id": world.unit_id})
    [message] = ctx.debt_messages.values()
    bullets = [line for line in message.splitlines() if line.startswith("• ")]
    assert len(bullets) == tools.MAX_DEBT_LINES + 1
    assert bullets[-1] == "• y 3 más"


def test_get_debt_twice_for_the_same_unit_sends_one_message(world: World) -> None:
    ctx = world.ctx(OWNER_PHONE)
    run_tool(ctx, "get_debt", {"unit_id": world.unit_id})
    run_tool(ctx, "get_debt", {"unit_id": world.unit_id})
    assert list(ctx.debt_messages) == [world.unit_id]


def test_get_debt_denied_builds_no_message(world: World) -> None:
    ctx = world.ctx(TENANT_PHONE)
    assert run_tool(ctx, "get_debt", {"unit_id": world.unit_id})["status"] == "denied"
    assert ctx.debt_messages == {}


def test_get_debt_owner_of_another_unit(world: World) -> None:
    ctx = world.ctx(OWNER_PHONE)
    run_tool(ctx, "find_unit", {"building_text": "Rodas II", "unit_text": "5 A"})
    result = run_tool(ctx, "get_debt", {"unit_id": world.other_unit_id})
    assert result == {
        "status": "denied",
        "reason": "not_owner",
        "next_step": result["next_step"],
    }
    assert world.refreshed == []


@pytest.mark.parametrize(
    ("amount", "text"),
    [("165060", "$165.060,00"), ("0", "$0,00"), ("1234567.5", "$1.234.567,50"), ("-10", "-$10,00")],
)
def test_format_money(amount: str, text: str) -> None:
    assert tools.format_money(Decimal(amount)) == text


def test_find_unit_several_buildings_asks_building_first(world: World) -> None:
    f.unit(world.session, f.building(world.session, "032 RODAS I"), "04-C")
    result = run_tool(world.ctx(UNKNOWN_PHONE), "find_unit", {"building_text": "Rodas 4C"})
    assert result["status"] == "ambiguous"
    assert set(result["buildings"]) == {"RODAS I", "RODAS II"}
    assert "edificio" in result["next_step"]
    dumped = json.dumps(result, ensure_ascii=False)
    assert "Ana" not in dumped and "Tito" not in dumped


def test_find_unit_found(world: World) -> None:
    result = run_tool(
        world.ctx(UNKNOWN_PHONE), "find_unit", {"building_text": "Rodas II", "unit_text": "4 C"}
    )
    assert result["status"] == "found"
    assert result["unit"]["unit_id"] == world.unit_id
    assert set(result["unit"]) == {"unit_id", "building", "unit"}


@dataclass
class FakeSender:
    sent: list[tuple[str, str]] = field(default_factory=list)

    def send_verification_code(self, to: str, code: str, valid_minutes: int) -> None:
        self.sent.append((to, code))


def test_email_verification_flow_never_logs_the_code(world: World) -> None:
    sender = FakeSender()
    ctx = world.ctx(UNKNOWN_PHONE)
    ctx.email_sender = sender

    run_tool(ctx, "find_unit", {"building_text": "Rodas II", "unit_text": "4 C"})
    started = run_tool(ctx, "start_email_verification", {"unit_id": world.unit_id})
    assert started["status"] == "codes_sent"
    assert started["unit"] == "RODAS II 04-C"
    assert started["masked_emails"] == ["a***@example.com"]
    assert "a***@example.com" in started["say"] and "RODAS II 04-C" in started["say"]
    code = sender.sent[0][1]

    confirmed = run_tool(ctx, "confirm_email_code", {"code": code})
    assert confirmed["status"] == "verified"
    assert confirmed["units"][0]["unit_id"] == world.unit_id

    logged = json.dumps([e.payload for e in world.events("tool_call")])
    assert code not in logged and "[omitido]" in logged
    # Verified now: the same number can see the debt.
    assert run_tool(ctx, "get_debt", {"unit_id": world.unit_id})["status"] == "ok"


def test_start_verification_without_email(world: World) -> None:
    ctx = world.ctx(UNKNOWN_PHONE)
    run_tool(ctx, "find_unit", {"building_text": "Rodas II", "unit_text": "5 A"})
    result = run_tool(ctx, "start_email_verification", {"unit_id": world.other_unit_id})
    assert result["status"] == "not_sent"
    assert result["reason"] == "no_owner_email"
    assert result["unit"] == "RODAS II 05-A"
    assert "La unidad RODAS II 05-A no tiene un email" in result["say"]
    [*_, logged] = world.events("tool_call")
    assert logged.payload["reason"] == "no_owner_email"
    requested = run_tool(
        ctx,
        "request_operator_verification",
        {"unit_id": world.other_unit_id, "claimed_name": "Juan Inventado"},
    )
    assert requested["status"] == "created"


def test_unit_id_not_returned_by_find_unit_is_rejected(world: World) -> None:
    sender = FakeSender()
    ctx = world.ctx(UNKNOWN_PHONE)
    ctx.email_sender = sender

    for tool, args in [
        ("start_email_verification", {"unit_id": world.unit_id}),
        ("get_debt", {"unit_id": world.unit_id}),
        ("request_operator_verification", {"unit_id": world.unit_id, "claimed_name": "X Y"}),
    ]:
        result = run_tool(ctx, tool, args)
        assert result["reason"] == "unit_not_confirmed", tool
    assert sender.sent == [] and world.events("email_verification_start") == []
    assert world.refreshed == []

    run_tool(ctx, "find_unit", {"building_text": "Rodas II", "unit_text": "4 C"})
    result = run_tool(ctx, "start_email_verification", {"unit_id": world.unit_id})
    assert result["status"] == "codes_sent"


def test_start_verification_rate_limited_says_when_to_retry(world: World) -> None:
    sender = FakeSender()
    ctx = world.ctx(UNKNOWN_PHONE)
    ctx.email_sender = sender
    run_tool(ctx, "find_unit", {"building_text": "Rodas II", "unit_text": "4 C"})
    for _ in range(identity.MAX_STARTS_PER_DAY):
        started = run_tool(ctx, "start_email_verification", {"unit_id": world.unit_id})
        assert started["status"] == "codes_sent"

    result = run_tool(ctx, "start_email_verification", {"unit_id": world.unit_id})

    assert result["status"] == "not_sent" and result["reason"] == "rate_limited"
    assert result["unit"] == "RODAS II 04-C"
    assert result["say"] == (
        "Ya enviamos varios códigos desde este número hoy; probá de nuevo en 24 horas o te "
        "paso con una persona."
    )
    assert len(sender.sent) == identity.MAX_STARTS_PER_DAY


def test_start_verification_send_failed(world: World) -> None:
    class BrokenSender:
        def send_verification_code(self, to: str, code: str, valid_minutes: int) -> None:
            raise EmailError("smtp caído")

    ctx = world.ctx(UNKNOWN_PHONE)
    ctx.email_sender = BrokenSender()
    run_tool(ctx, "find_unit", {"building_text": "Rodas II", "unit_text": "4 C"})

    result = run_tool(ctx, "start_email_verification", {"unit_id": world.unit_id})

    assert result["status"] == "not_sent" and result["reason"] == "send_failed"
    assert "no tiene" not in result["say"]


@pytest.mark.parametrize(
    ("minutes", "text"),
    [(0.2, "1 minuto"), (40, "40 minutos"), (60, "1 hora"), (61, "2 horas"), (1440, "24 horas")],
)
def test_wait_text(minutes: float, text: str) -> None:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert tools.wait_text(now + timedelta(minutes=minutes), now) == text


def test_already_verified_owner_gets_no_code(world: World) -> None:
    result = run_tool(
        world.ctx(OWNER_PHONE), "start_email_verification", {"unit_id": world.unit_id}
    )
    assert result["status"] == "already_verified"


def test_handoff_is_recorded(world: World) -> None:
    ctx = world.ctx(OWNER_PHONE)
    handoff = run_tool(
        ctx, "handoff_to_human", {"reason": "emergency", "summary": "pérdida de agua",
                                  "priority": "urgent"}
    )  # fmt: skip
    assert handoff["status"] == "ok"
    [event] = world.events("handoff")
    assert event.payload["priority"] == "urgent"
    assert event.phone_e164 == OWNER_PHONE


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ({}, "faltan argumentos: unit_id"),
        ({"unit_id": "3"}, "entero"),
        ({"unit_id": True}, "entero"),
    ],
)
def test_invalid_arguments(world: World, args: dict[str, Any], error: str) -> None:
    result = run_tool(world.ctx(OWNER_PHONE), "get_debt", args)
    assert result["status"] == "error" and error in result["error"]


def test_invalid_priority_and_unknown_tool(world: World) -> None:
    ctx = world.ctx(OWNER_PHONE)
    bad = run_tool(ctx, "handoff_to_human", {"reason": "x", "summary": "y", "priority": "max"})
    assert bad["status"] == "error"
    assert run_tool(ctx, "delete_everything", {})["status"] == "error"


def test_integer_as_float_is_accepted(world: World) -> None:
    result = run_tool(world.ctx(OWNER_PHONE), "get_debt", {"unit_id": float(world.unit_id)})
    assert result["status"] == "ok"


# --- History -------------------------------------------------------------------------------


def test_trim_history_keeps_last_messages_and_tool_exchanges() -> None:
    history: list[Any] = []
    for i in range(15):
        history.append(UserMessage(f"u{i}"))
        call = ToolCall(f"c{i}", "find_unit", {"building_text": "x"})
        history.append(AssistantMessage(tool_calls=(call,)))
        history.append(ToolResultsMessage((ToolResult(f"c{i}", "find_unit", {}),)))
        history.append(AssistantMessage(f"a{i}"))
    trimmed = trim_history(history, 20)
    assert trimmed[0] == UserMessage("u5")
    texts = [m for m in trimmed if isinstance(m, UserMessage) or (
        isinstance(m, AssistantMessage) and not m.tool_calls)]  # fmt: skip
    assert len(texts) == 20
    assert isinstance(trimmed[2], ToolResultsMessage)


def test_trim_history_never_starts_with_a_tool_result() -> None:
    history = [
        UserMessage("u0"),
        AssistantMessage("a0"),
        UserMessage("u1"),
        AssistantMessage(tool_calls=(ToolCall("c", "x", {}),)),
        ToolResultsMessage((ToolResult("c", "x", {}),)),
        AssistantMessage("a1"),
    ]
    assert trim_history(history, 1) == []
    assert trim_history(history, 2)[0] == UserMessage("u1")


def test_context_lists_the_unit_id_of_own_units(world: World) -> None:
    text = describe_identity(identify_by_phone(world.session, OWNER_PHONE))
    assert f"RODAS II 04-C (unit_id {world.unit_id}, propietario)" in text


# --- Options to tap (offer_choices) --------------------------------------------------------

YES_NO = {"text": "¿Querés que te pase con una persona?", "options": ["Sí, pasame", "No, gracias"]}


@pytest.mark.parametrize("name", PROVIDERS)
def test_offer_choices_ends_the_turn(name: str, world: World) -> None:
    agent, script = make_agent(name, [Call("offer_choices", YES_NO)], world)

    reply = agent.reply(world.session, OWNER_PHONE, "pagué hace dos semanas y sigue la deuda")

    assert len(script.requests) == 1  # no model call after it
    assert reply.text == "¿Querés que te pase con una persona?"
    assert reply.choices == (
        Choice("Sí, pasame", "Sí, pasame"),
        Choice("No, gracias", "No, gracias"),
    )
    assert reply.tools_called == [("offer_choices", "ok")]
    assert not reply.handed_off and reply.error is None
    # The history keeps the titles offered, so the next turn knows what "Sí, pasame" answers.
    assert reply.history[-1] == AssistantMessage(
        with_options(reply.text, ["Sí, pasame", "No, gracias"])
    )


@pytest.mark.parametrize("name", PROVIDERS)
def test_options_that_do_not_fit_go_back_to_the_model(name: str, world: World) -> None:
    too_long = {"text": "¿Querés?", "options": ["Sí, pasame con una persona", "No"]}
    steps = [Call("offer_choices", too_long), Call("offer_choices", YES_NO)]
    agent, script = make_agent(name, steps, world)

    reply = agent.reply(world.session, OWNER_PHONE, "hola")

    told = last_tool_result(name, script.requests[1])
    assert "invalid_options" in told and "20 caracteres" in told
    assert [c.title for c in reply.choices] == ["Sí, pasame", "No, gracias"]
    assert reply.tools_called == [("offer_choices", "error"), ("offer_choices", "ok")]


def test_offer_choices_after_a_handoff_or_twice_is_rejected(world: World) -> None:
    ctx = world.ctx(OWNER_PHONE)
    run_tool(ctx, "offer_choices", YES_NO)
    assert run_tool(ctx, "offer_choices", YES_NO)["reason"] == "already_offered"

    ctx = world.ctx(OWNER_PHONE)
    args = {"reason": "person_requested", "summary": "x", "priority": "normal"}
    run_tool(ctx, "handoff_to_human", args)
    assert run_tool(ctx, "offer_choices", YES_NO)["reason"] == "handed_off"
    assert ctx.offer is None


def test_offer_choices_needs_a_list_of_texts(world: World) -> None:
    result = run_tool(world.ctx(OWNER_PHONE), "offer_choices", {"text": "¿?", "options": "Sí"})
    assert result == {"status": "error", "error": "options tiene que ser una lista de textos"}


def test_debt_message_with_options_after_it(world: World) -> None:
    steps = [Call("get_debt", {"unit_id": world.unit_id}), Call("offer_choices", YES_NO)]
    agent, _ = make_agent("anthropic", steps, world)
    past = [UserMessage("hola"), AssistantMessage("¡Hola!")]

    reply = agent.reply(world.session, OWNER_PHONE, "pagué y sigue la deuda", past)

    [message] = reply.debt_messages
    assert reply.history[-1] == AssistantMessage(
        f"{message}\n\n{with_options(reply.text, ['Sí, pasame', 'No, gracias'])}"
    )


# --- get_payment_info and the self-service link ---------------------------------------------


def test_get_payment_info_sends_the_code_without_the_balance(world: World) -> None:
    ctx = world.ctx(OWNER_PHONE)
    ctx.autogestion_url = "https://autogestion.example.com"

    result = run_tool(ctx, "get_payment_info", {"unit_id": world.unit_id})

    [message] = ctx.debt_messages.values()
    assert message.splitlines() == [
        "*RODAS II 04-C*",
        f"Código de pago Siro: *{PAYMENT_CODE}*",
        tools.PAYMENT_CODE_HOW_TO,
        "Expensas y comprobantes: https://autogestion.example.com",
    ]
    assert "$" not in message and "Saldo" not in message
    assert world.refreshed == []  # no ConsorPlus refresh for this
    assert result["status"] == "ok" and result["already_sent"] == message
    assert result["next_step"] == tools.PAYMENT_SENT_STEP


@pytest.mark.parametrize(
    ("phone", "reason"), [(TENANT_PHONE, "tenant"), (UNKNOWN_PHONE, "not_verified")]
)
def test_get_payment_info_has_the_permissions_of_get_debt(
    world: World, phone: str, reason: str
) -> None:
    ctx = world.ctx(phone)
    run_tool(ctx, "find_unit", {"building_text": "Rodas II", "unit_text": "4 C"})

    result = run_tool(ctx, "get_payment_info", {"unit_id": world.unit_id})

    assert result["status"] == "denied" and result["reason"] == reason
    assert ctx.debt_messages == {}


def test_get_payment_info_after_get_debt_sends_one_message(world: World) -> None:
    ctx = world.ctx(OWNER_PHONE)
    run_tool(ctx, "get_debt", {"unit_id": world.unit_id})
    run_tool(ctx, "get_payment_info", {"unit_id": world.unit_id})

    [message] = ctx.debt_messages.values()
    assert "Saldo total" in message


def test_get_debt_puts_the_self_service_link_in_the_message(world: World) -> None:
    ctx = world.ctx(OWNER_PHONE)
    ctx.autogestion_url = "https://autogestion.example.com"

    result = run_tool(ctx, "get_debt", {"unit_id": world.unit_id})

    [message] = ctx.debt_messages.values()
    assert message.endswith("\nExpensas y comprobantes: https://autogestion.example.com")
    assert "autogestion_url" not in result  # the model does not handle it


def test_debt_block_greeting_uses_the_panel_welcome_message(world: World) -> None:
    row = world.session.get(BotSettings, 1)
    row.welcome_message = "¡Hola! Te atiende el asistente del estudio inventado."
    world.session.commit()
    invalidate_bot_config()
    steps = [Call("get_debt", {"unit_id": world.unit_id}), Say("¿Algo más?")]
    agent, _ = make_agent("anthropic", steps, world)

    reply = agent.reply(world.session, OWNER_PHONE, "¿cuánto debo?")

    [message] = reply.debt_messages
    assert message.startswith(
        "¡Hola! Te atiende el asistente del estudio inventado.\n\n*RODAS II 04-C*\n"
    )
    assert FIRST_GREETING not in message
    invalidate_bot_config()
