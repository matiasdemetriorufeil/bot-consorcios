"""Agent tool loop and tools, with BOTH providers mocked, against the Postgres test database.
All data is invented."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import tools
from app.bot.agent import FALLBACK_REPLY, MAX_ROUNDS, Agent, trim_history
from app.bot.prompts import SYSTEM_PROMPT
from app.bot.tools import ToolContext, run_tool
from app.config import Settings
from app.db.models import BotEvent, DebtLine, DebtSnapshot, PersonRole, SyncKind, Unit
from app.llm import (
    AssistantMessage,
    LLMError,
    Prices,
    ToolCall,
    ToolResult,
    ToolResultsMessage,
    UserMessage,
)
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
    result = last_tool_result(name, script.requests[1])
    assert "$165.060,00" in result
    assert PAYMENT_CODE in result
    assert "30/09/2026 14:05" in result
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
    steps = [Call("get_debt", {"unit_id": world.unit_id}), Say("No puedo darte ese dato.")]
    agent, script = make_agent(name, steps, world)

    reply = agent.reply(world.session, phone, "¿Cuánto debe el 4C?")

    assert reply.text == "No puedo darte ese dato."
    assert world.refreshed == []
    result = last_tool_result(name, script.requests[1])
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
def test_provider_error_answers_fixed_message_and_hands_off(name: str, world: World) -> None:
    agent, _ = make_agent(name, [LLMError("caído")], world)

    reply = agent.reply(world.session, OWNER_PHONE, "hola")

    assert reply.text == FALLBACK_REPLY
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
    assert agent.reply(world.session, OWNER_PHONE, "hola").text == FALLBACK_REPLY


@pytest.mark.parametrize("name", PROVIDERS)
def test_loop_exhausted(name: str, world: World) -> None:
    steps = [Call("get_building_info", {"building_id": 1, "question": "?"})] * (MAX_ROUNDS + 1)
    agent, script = make_agent(name, steps, world)

    reply = agent.reply(world.session, OWNER_PHONE, "¿Se puede tener perro?")

    assert reply.text == FALLBACK_REPLY and reply.error == "max_rounds"
    assert len(script.requests) == MAX_ROUNDS
    assert len(world.events("handoff")) == 1
    assert reply.history[-1] == AssistantMessage(FALLBACK_REPLY)


@pytest.mark.parametrize("name", PROVIDERS)
def test_model_handoff_is_not_duplicated(name: str, world: World) -> None:
    steps = [
        Call("handoff_to_human", {"reason": "plan de pago", "summary": "x", "priority": "normal"}),
        Say("Te paso con una persona del estudio."),
    ]
    agent, _ = make_agent(name, steps, world)
    reply = agent.reply(world.session, OWNER_PHONE, "quiero un plan de pagos")
    assert reply.handed_off and reply.error is None
    assert len(world.events("handoff")) == 1


# --- Tools directly ------------------------------------------------------------------------


def test_get_debt_owner_formats_everything(world: World) -> None:
    result = run_tool(world.ctx(OWNER_PHONE), "get_debt", {"unit_id": world.unit_id})
    assert result["status"] == "ok"
    assert result["unit"] == "RODAS II 04-C"
    assert result["total_debt"] == "$165.060,00"
    assert result["data_date"] == "30/09/2026 14:05"
    assert result["detail"] == ["09/2026 Expensas ordinarias: $165.060,00"]
    assert result["payment_code"] == PAYMENT_CODE
    assert "Pago Mis Cuentas" in result["payment_how_to"]
    assert "note" not in result


def test_get_debt_stale_and_without_payment_code(world: World) -> None:
    world.stale = True
    world.session.get(Unit, world.unit_id).payment_code = None
    result = run_tool(world.ctx(OWNER_PHONE), "get_debt", {"unit_id": world.unit_id})
    assert "último dato guardado" in result["note"]
    assert result["payment_code"] is None
    assert "pedilo a la administración" in result["payment_how_to"]


def test_get_debt_owner_of_another_unit(world: World) -> None:
    result = run_tool(world.ctx(OWNER_PHONE), "get_debt", {"unit_id": world.other_unit_id})
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
    assert {b["name"] for b in result["buildings"]} == {"RODAS I", "RODAS II"}
    assert "edificio" in result["next_step"]
    dumped = json.dumps(result, ensure_ascii=False)
    assert "Ana" not in dumped and "Tito" not in dumped


def test_find_unit_found(world: World) -> None:
    result = run_tool(
        world.ctx(UNKNOWN_PHONE), "find_unit", {"building_text": "Rodas II", "unit_text": "4 C"}
    )
    assert result["status"] == "found"
    assert result["unit"]["unit_id"] == world.unit_id
    assert set(result["unit"]) == {"unit_id", "building_id", "building", "unit"}


@dataclass
class FakeSender:
    sent: list[tuple[str, str]] = field(default_factory=list)

    def send_verification_code(self, to: str, code: str, valid_minutes: int) -> None:
        self.sent.append((to, code))


def test_email_verification_flow_never_logs_the_code(world: World) -> None:
    sender = FakeSender()
    ctx = world.ctx(UNKNOWN_PHONE)
    ctx.email_sender = sender

    started = run_tool(ctx, "start_email_verification", {"unit_id": world.unit_id})
    assert started["status"] == "codes_sent"
    assert started["masked_emails"] == ["a***@example.com"]
    code = sender.sent[0][1]

    confirmed = run_tool(ctx, "confirm_email_code", {"code": code})
    assert confirmed["status"] == "verified"
    assert confirmed["units"][0]["unit_id"] == world.unit_id

    logged = json.dumps([e.payload for e in world.events("tool_call")])
    assert code not in logged and "[omitido]" in logged
    # Verified now: the same number can see the debt.
    assert run_tool(ctx, "get_debt", {"unit_id": world.unit_id})["status"] == "ok"


def test_start_verification_without_email(world: World) -> None:
    result = run_tool(
        world.ctx(UNKNOWN_PHONE), "start_email_verification", {"unit_id": world.other_unit_id}
    )
    assert result["status"] == "sin_email"
    requested = run_tool(
        world.ctx(UNKNOWN_PHONE),
        "request_operator_verification",
        {"unit_id": world.other_unit_id, "claimed_name": "Juan Inventado"},
    )
    assert requested["status"] == "created"


def test_already_verified_owner_gets_no_code(world: World) -> None:
    result = run_tool(
        world.ctx(OWNER_PHONE), "start_email_verification", {"unit_id": world.unit_id}
    )
    assert result["status"] == "already_verified"


def test_building_info_and_handoff_placeholders(world: World) -> None:
    ctx = world.ctx(OWNER_PHONE)
    info = run_tool(ctx, "get_building_info", {"building_id": 1, "question": "¿Mascotas?"})
    assert info["status"] == "no_info"
    handoff = run_tool(
        ctx, "handoff_to_human", {"reason": "urgencia", "summary": "pérdida de agua",
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
