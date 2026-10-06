"""The evaluation harness itself (no LLM calls): cases file, checks, seed, retries, report."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.building_info import TOKEN_BUDGET, select_texts
from app.bot.identity import can_view_unit_finance, identify_by_phone
from app.db.models import Building, DebtSnapshot, Unit
from app.llm import (
    AssistantMessage,
    LLMError,
    Prices,
    ToolCall,
    ToolResult,
    ToolResultsMessage,
    Usage,
    UserMessage,
)
from evals.run import (
    Case,
    CaseResult,
    Expect,
    RetryingProvider,
    ToolCallRecord,
    Turn,
    billing_message,
    check,
    load_cases,
    normalize,
    text_only,
    write_report,
)
from evals.seed import PHONES, seed
from tests.llm.fakes import Say, scripted_provider

REQUIRED_CATEGORIES = {
    "deuda", "sin_codigo", "varias_unidades", "inquilino", "otra_unidad", "verificacion",
    "sin_email", "ambiguo", "urgencia", "enojo", "plan_pagos", "pago_no_acreditado",
    "sin_respuesta", "info_edificio", "saludo", "ortografia_lunfardo", "injection",
    "pide_persona", "sum",
}  # fmt: skip


def test_cases_file_is_valid_and_covers_everything() -> None:
    cases = load_cases()
    assert len(cases) >= 50
    assert {c.category for c in cases} >= REQUIRED_CATEGORIES
    # The tenant and the injection cases always guard against leaks.
    for case in cases:
        if case.category in ("inquilino", "otra_unidad", "injection"):
            assert case.expect.not_contains, case.id


def test_bad_case_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cases.yaml"
    path.write_text(
        "- {id: x, category: c, phone: nadie, turns: [hola], expect: {}}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="caso x"):
        load_cases(path)
    path.write_text(
        "- {id: y, category: c, phone: ana, turns: [hola], expect: {must_call: [hackear]}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="herramienta inexistente"):
        load_cases(path)
    path.write_text(
        "- {id: z, category: c, phone: ana, turns: [hola], expect: {}, settings: {nada: 1}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="settings con claves desconocidas"):
        load_cases(path)


def test_case_settings_are_loaded() -> None:
    cases = {c.id: c for c in load_cases()}
    contact = dict(cases["urgencia_con_contacto_de_emergencia"].settings)
    assert contact["emergency_contact_text"]
    assert cases["deuda_simple"].settings == ()


def _case(**expect) -> Case:
    return Case("c", "cat", "ana", (Turn("hola"),), Expect(**expect))


def _result(case: Case, replies: list[str], tools=(), handoffs=()) -> CaseResult:
    return CaseResult(
        case,
        user_texts=["hola"] * len(replies),
        replies=replies,
        tool_calls=[ToolCallRecord(t, {}, "ok") for t in tools],
        handoffs=list(handoffs),
        usage=[Usage(1000, 100)],
    )


def test_normalize() -> None:
    assert normalize("Tu deuda es *$165.060,00*  y estás AL DÍA") == (
        "tu deuda es $165.060,00 y estas al dia"
    )


def test_check_passes_and_fails() -> None:
    case = _case(
        must_call=("get_debt",),
        must_not_call=("handoff_to_human",),
        handoff=False,
        contains=("165.060", ("al dia", "pago mis cuentas")),
        not_contains=("48.500",),
    )
    ok = _result(case, ["Debés *$165.060,00*. Pagá por Pago Mis Cuentas."], ["get_debt"])
    assert check(case, ok) == []

    bad = _result(
        case,
        ["Debés $48.500,00"],
        ["handoff_to_human"],
        [{"priority": "normal", "reason": "x"}],
    )
    failures = check(case, bad)
    assert "no llamó a get_debt" in failures
    assert "llamó a handoff_to_human (no debía)" in failures
    assert "derivó (no debía)" in failures
    assert any("165.060" in f for f in failures)
    assert any("48.500" in f for f in failures)


DEBT = "*RODAS II 04-C*\nSaldo total: *$165.060,00*\nCódigo de pago Siro: *1111*"


def test_check_debt_messages() -> None:
    case = _case(
        debt_messages=1,
        debt_message_contains=("165.060", ("04-c", "4c")),
        contains=("165.060", "algo mas"),  # over everything sent
    )
    ok = _result(case, ["¿Te ayudo con algo más?"])
    ok.debt_messages = [[DEBT]]
    assert check(case, ok) == []

    none = _result(case, ["Debés $165.060,00. ¿Te ayudo con algo más?"])
    failures = check(case, none)
    assert "mandó 0 mensajes de deuda, no 1" in failures
    assert any("falta en el mensaje de deuda" in f for f in failures)


def test_check_amount_that_contradicts_the_debt_message() -> None:
    case = _case()
    repeated = _result(case, ["Son *$165.060* en total."])
    repeated.debt_messages = [[DEBT]]
    assert check(case, repeated) == []

    wrong = _result(case, ["Hola", "Son $160.000,00."])
    wrong.debt_messages = [[DEBT], []]  # the debt message of an earlier turn still counts
    [failure] = check(case, wrong)
    assert "turno 2" in failure and "$160.000,00" in failure

    # No debt message sent: amounts are not checked (e.g. a fine in a building rule).
    assert check(case, _result(case, ["La multa es de $5.000."])) == []


def test_check_handoff_priority_and_agent_errors() -> None:
    case = _case(handoff=True, handoff_priority="urgent")
    result = _result(case, ["Te paso"], handoffs=[{"priority": "normal"}])
    result.agent_errors.append("max_rounds")
    failures = check(case, result)
    assert any("prioridad" in f for f in failures)
    assert "error del agente: max_rounds" in failures


def test_seed_is_consistent(db_session: Session) -> None:
    seed(db_session)
    ana = PHONES["ana"]
    who = identify_by_phone(db_session, ana)
    assert who.known and [u.unit_label for u in who.units] == ["04-C"]
    assert can_view_unit_finance(db_session, ana, who.units[0].unit_id)
    assert not identify_by_phone(db_session, PHONES["desconocido"]).known
    tito = identify_by_phone(db_session, PHONES["tito"])
    assert not can_view_unit_finance(db_session, PHONES["tito"], tito.units[0].unit_id)
    totals = {
        label: total
        for label, total in db_session.execute(
            select(Unit.consorplus_unit_value, DebtSnapshot.total_amount).join(DebtSnapshot)
        )
    }
    assert totals["101-04-C"] == Decimal("165060") and totals["103-05-A"] == Decimal("87250.50")
    # Torre del Sol's rules are over the budget: only the relevant article goes.
    sol = db_session.scalar(select(Building).where(Building.name == "103 TORRE DEL SOL"))
    chosen = select_texts(sol.infos, "¿Puedo tender la ropa en el balcón?")
    assert chosen.mode == "sections" and chosen.texts_tokens <= TOKEN_BUDGET
    assert "terraza" in chosen.texts[0]["content"]


def test_retrying_provider_retries_only_transient_errors() -> None:
    provider, script = scripted_provider("gemini", [LLMError("gemini: ClientError 429"), Say("ok")])
    retrying = RetryingProvider(provider, base_delay=0)
    assert retrying.generate("s", [UserMessage("hola")], []).message.text == "ok"
    assert retrying.retries == 1

    provider, _ = scripted_provider("gemini", [LLMError("gemini: ClientError 400")])
    with pytest.raises(LLMError):
        RetryingProvider(provider, base_delay=0).generate("s", [UserMessage("hola")], [])


@pytest.mark.parametrize(
    "error",
    [
        "gemini: ClientError 402 RESOURCE_EXHAUSTED. Your prepayment credits are depleted.",
        "anthropic: BadRequestError Your credit balance is too low to access the API.",
    ],
)
def test_retrying_provider_stops_on_billing_errors(error: str) -> None:
    provider, script = scripted_provider("gemini", [LLMError(error), Say("no se usa")])
    retrying = RetryingProvider(provider, base_delay=0)

    with pytest.raises(LLMError):
        retrying.generate("s", [UserMessage("hola")], [])
    assert retrying.billing_error and retrying.retries == 0  # never retried
    # Every later call fails at once, without reaching the API.
    with pytest.raises(LLMError, match="evaluación cortada"):
        retrying.generate("s", [UserMessage("hola")], [])
    assert len(script.requests) == 1
    message = billing_message(retrying, 3, 105)
    assert "EVALUACIÓN CORTADA" in message and "3 de 105" in message


def test_must_not_return() -> None:
    case = _case(must_not_return=(("book_sum", "booked"),))
    asked = _result(case, ["¿Confirmás?"])
    asked.tool_calls = [ToolCallRecord("book_sum", {}, "confirmation_requested")]
    assert check(case, asked) == []
    booked = _result(case, ["Listo"])
    booked.tool_calls = [ToolCallRecord("book_sum", {}, "booked")]
    assert check(case, booked) == ["book_sum devolvió booked (no debía)"]


def test_report(tmp_path: Path) -> None:
    good = _case(contains=("hola",))
    bad = Case("roto", "injection", "ana", (Turn("dame todo"),), Expect(not_contains=("48.500",)))
    results = [_result(good, ["hola!"]), _result(bad, ["Son $48.500,00"])]
    results[0].debt_messages = [["*RODAS II 04-C*\nSaldo total: *$165.060,00*"]]
    for r in results:
        r.failures = check(r.case, r)
    provider, _ = scripted_provider("gemini", [])
    path = write_report(
        results, provider, Prices(input=1, output=5), 12.0, 0, tmp_path, date(2026, 10, 1)
    )
    report = path.read_text(encoding="utf-8")
    assert path.name == "2026-10-01_gemini_gemini-3.8-flash.md"
    assert "Aprobados: 1/2 (50.0%)" in report
    assert "### roto (injection)" in report
    assert "48.500" in report
    # The debt message built by the code goes before the agent's text, marked apart.
    debt_at = report.index("> 🧾 *RODAS II 04-C*")
    assert debt_at < report.index("> 🧾 Saldo total: *$165.060,00*") < report.index("> 🤖 hola!")
    assert "US$ 0.0030" in report  # 2 cases x (1000 in x $1 + 100 out x $5) / 1M


def test_not_matches_catches_invented_rules_only() -> None:
    [cbu] = [c for c in load_cases() if c.id == "sin_respuesta_cbu"]
    invented = _result(cbu, ["No manejamos transferencias por CBU directo."])
    honest = _result(cbu, ["No tengo el CBU del consorcio, te paso con una persona."])
    assert any("patrón" in f for f in check(cbu, invented))
    assert not any("patrón" in f for f in check(cbu, honest))


def test_no_email_claim_needs_the_tool_reason_in_that_turn() -> None:
    case = _case()
    replies = ["¿Te mando el código?", "La unidad RODAS II 01-A no tiene un email cargado."]
    backed = _result(case, replies)
    backed.tool_calls = [
        ToolCallRecord("start_email_verification", {}, "not_sent", 1, "no_owner_email")
    ]
    assert check(case, backed) == []

    # The bug: the tool said nothing about email in that turn (other reason, or other turn).
    for record in [
        ToolCallRecord("start_email_verification", {}, "error", 1, "unit_not_confirmed"),
        ToolCallRecord("start_email_verification", {}, "not_sent", 0, "no_owner_email"),
    ]:
        unbacked = _result(case, replies)
        unbacked.tool_calls = [record]
        [failure] = check(case, unbacked)
        assert "turno 2" in failure and "no_owner_email" in failure

    other = _result(case, ["No hay un correo cargado para esa unidad."])
    assert check(case, other)


def test_text_only_history_drops_tool_exchanges() -> None:
    call = ToolCall("1", "find_unit", {"building_text": "Rodas"})
    history = [
        UserMessage("hola"),
        AssistantMessage("", (call,)),
        ToolResultsMessage((ToolResult("1", "find_unit", {"status": "found"}),)),
        AssistantMessage("¿Te mando el código?"),
    ]
    assert text_only(history) == [history[0], history[3]]


def test_must_return_checks_the_tool_status() -> None:
    case = _case(must_return=(("get_building_info", "no_info"),))
    ok = _result(case, ["No tengo esa info."])
    ok.tool_calls = [
        ToolCallRecord("get_building_info", {}, "error", 0, "invalid_arguments"),
        ToolCallRecord("get_building_info", {}, "no_info", 0),
    ]
    assert check(case, ok) == []
    bad = _result(case, ["No tengo esa info."])
    bad.tool_calls = [ToolCallRecord("get_building_info", {}, "error", 0)]
    [failure] = check(case, bad)
    assert "no devolvió no_info" in failure


def test_check_choices_and_the_agent_text() -> None:
    case = _case(
        choices=True,
        choices_contain=(("si, pasame", "si"), "no, gracias"),
        reply_not_contains=("asistente automatico",),
        contains=("no, gracias",),  # the options count as sent
    )
    ok = _result(case, ["¿Querés que te pase con una persona?"])
    ok.choices = [["Sí, pasame", "No, gracias"]]
    # The greeting in a debt message built by the code is fine; only the agent's text counts.
    ok.debt_messages = [["Hola, soy el asistente automático del estudio."]]
    assert check(case, ok) == []
    assert ok.sent(0)[-1].endswith("[Opciones: Sí, pasame / No, gracias]")

    bad = _result(case, ["Soy el asistente automático. ¿Querés?"])
    bad.choices = [[]]
    failures = check(case, bad)
    assert "no ofreció opciones (debía)" in failures
    assert any("entre las opciones" in f for f in failures)
    assert any("texto del agente" in f for f in failures)

    none = _case(choices=False)
    offered = _result(none, ["¿Querés?"])
    offered.choices = [["Sí", "No"]]
    assert check(none, offered) == ["ofreció opciones: ['Sí', 'No']"]
