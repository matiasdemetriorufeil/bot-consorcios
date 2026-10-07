"""The processor through the WhatsApp channel: office hours in the handoff, LLM failures, a
failed handoff, the ConsorPlus warm-up, debt blocks before the text and the options.
Cloud API faked, LLM scripted, invented data."""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.agent import FALLBACK_REPLY
from app.bot.debt_message import FIRST_GREETING
from app.channels.base import ChannelError
from app.db.models import DebtLine, DebtSnapshot, SyncKind, Unit, WaConversationStatus
from app.llm import LLMError
from app.sync.live import DebtResult
from tests.llm.fakes import Call, Say, Step, last_tool_result
from tests.whatsapp.conftest import NON_PILOT_WA_ID, TZ, MakeWa, Wa
from tests.whatsapp.fakes import FakeWhatsApp, incoming, text_message

SATURDAY_10 = datetime(2026, 10, 3, 10, 0, tzinfo=TZ)
OFFER = Call(
    "offer_choices",
    {"text": "¿Querés que te pase con una persona?", "options": ["Sí, pasame", "No, gracias"]},
)


def _say(wa: Wa, text: str = "hola", wa_id: str | None = None) -> None:
    kwargs = {"wa_id": wa_id} if wa_id else {}
    assert wa.post(incoming(text_message(text, wa.now), **kwargs)).status_code == 200


def _unit_id(session: Session) -> int:
    return session.scalar(select(Unit.id).where(Unit.label == "04-C"))


# --- Office hours -------------------------------------------------------------------------------


def test_handoff_out_of_office_hours_says_next_business_day(make_wa: MakeWa) -> None:
    wa = make_wa(
        [
            Call(
                "handoff_to_human",
                {"reason": "person_requested", "summary": "x", "priority": "normal"},
            ),
            Say("Listo."),
        ]  # fmt: skip
    )
    wa.clock[0] = SATURDAY_10

    _say(wa, "quiero hablar con alguien")

    told = last_tool_result("anthropic", wa.script.requests[1])
    assert "fuera del horario" in told and "el lunes a partir de las 9" in told


def test_non_pilot_message_out_of_hours(make_wa: MakeWa) -> None:
    wa = make_wa()
    wa.clock[0] = SATURDAY_10

    _say(wa, wa_id=NON_PILOT_WA_ID)

    assert "el lunes a partir de las 9" in wa.fake.texts()[0]


# --- Failures -----------------------------------------------------------------------------------


def test_llm_failure_answers_fixed_message_and_hands_off(make_wa: MakeWa) -> None:
    wa = make_wa([LLMError("caído")])

    _say(wa)

    [reply] = wa.fake.texts()
    assert reply.startswith(FALLBACK_REPLY)
    conversation = wa.conversation()
    assert conversation is not None
    assert conversation.status == WaConversationStatus.WAITING_HUMAN
    assert "error-tecnico" in conversation.handoff_labels


def test_failed_handoff_does_not_send_a_second_message(
    make_wa: MakeWa, monkeypatch: pytest.MonkeyPatch
) -> None:
    wa = make_wa(
        [
            Call(
                "handoff_to_human", {"reason": "payment_plan", "summary": "x", "priority": "normal"}
            ),
            Say("Te paso con el estudio."),
        ]  # fmt: skip
    )

    def broken(*args: object) -> None:
        raise ChannelError("no se pudo derivar")

    monkeypatch.setattr(wa.bot.processor.channel, "hand_off", broken)

    _say(wa)

    assert wa.fake.texts() == ["Te paso con el estudio."]
    assert len(wa.events("whatsapp_handoff_failed")) == 1


# --- ConsorPlus warm-up -------------------------------------------------------------------------


def test_identified_pilot_owner_warms_up_the_consorplus_session(make_wa: MakeWa) -> None:
    wa = make_wa([Say("Hola Ana")])

    _say(wa)

    assert wa.warm_ups == [True]


@pytest.mark.parametrize("wa_id", ["5493515559999", NON_PILOT_WA_ID])
def test_no_warm_up_for_unknown_or_non_pilot(make_wa: MakeWa, wa_id: str) -> None:
    wa = make_wa([Say("Hola")])

    _say(wa, wa_id=wa_id)

    assert wa.warm_ups == []


# --- Debt blocks and options --------------------------------------------------------------------


def _snapshot(session: Session, lines: int = 1, amount: str = "1234.50") -> DebtSnapshot:
    snapshot = DebtSnapshot(
        unit_id=_unit_id(session),
        fetched_at=datetime(2026, 9, 30, 10, 0, tzinfo=TZ),
        source=SyncKind.LIVE,
        total_amount=Decimal(amount) * lines,
        is_up_to_date=False,
        lines=[
            DebtLine(
                concept="Expensas ordinarias" if lines == 1 else "Concepto inventado largo " * 6,
                period=f"{n % 12 + 1:02d}/2026",
                concept_amount=Decimal(amount),
                balance_due=Decimal(amount),
                accumulated=Decimal(amount),
            )
            for n in range(lines)
        ],
    )
    session.add(snapshot)
    session.commit()
    return snapshot


def _with_debt(make_wa: MakeWa, session: Session, steps: list[Step], **kwargs: object) -> Wa:
    snapshot = _snapshot(session, **kwargs)  # type: ignore[arg-type]
    return make_wa(
        [Call("get_debt", {"unit_id": snapshot.unit_id}), *steps],
        refresh_debt=lambda unit_id: DebtResult(snapshot, stale=False),
    )


def test_debt_message_goes_before_the_agent_text_in_one_message(
    make_wa: MakeWa, db_session: Session
) -> None:
    wa = _with_debt(make_wa, db_session, [Say("¿Te ayudo con algo más?")])

    _say(wa, "¿cuánto debo?")

    # ONE message: the block, then the agent's text. First message of the conversation: the
    # fixed greeting goes first.
    [sent] = wa.fake.texts()
    assert sent.startswith(f"{FIRST_GREETING}\n\n*RODAS II 04-C*\nSaldo total: *$1.234,50*")
    assert "Dato al 30/09/2026 a las 10:00." in sent
    assert sent.endswith("\n\n¿Te ayudo con algo más?")


def test_debt_messages_go_in_the_text_of_the_buttons(make_wa: MakeWa, db_session: Session) -> None:
    wa = _with_debt(make_wa, db_session, [OFFER])

    _say(wa, "¿cuánto debo?")

    assert wa.fake.kinds() == ["choices"]
    [(_, _, (text, titles))] = wa.fake.sent
    assert text.startswith(f"{FIRST_GREETING}\n\n*RODAS II 04-C*\nSaldo total")
    assert text.endswith("\n\n¿Querés que te pase con una persona?")
    assert titles == ["Sí, pasame", "No, gracias"]


def test_a_block_too_long_for_the_buttons_goes_first(make_wa: MakeWa, db_session: Session) -> None:
    wa = _with_debt(make_wa, db_session, [OFFER], lines=12, amount="1000")

    _say(wa, "¿cuánto debo?")

    assert wa.fake.kinds() == ["text", "choices"]
    [block] = wa.fake.texts()
    assert "Saldo total" in block and len(block) > 1024
    assert wa.fake.sent[1][2][0] == "¿Querés que te pase con una persona?"


def test_numbered_fallback_keeps_the_block(make_wa: MakeWa, db_session: Session) -> None:
    wa = make_wa(
        [Call("get_payment_info", {"unit_id": _unit_id(db_session)}), OFFER],
        fake=FakeWhatsApp(fail_on={"choices"}),
    )

    _say(wa, "¿cómo pago?")

    [sent] = wa.fake.texts()
    assert sent.startswith(f"{FIRST_GREETING}\n\n*RODAS II 04-C*\n")
    assert "\n\n¿Querés que te pase con una persona?\n\n1. Sí, pasame\n" in sent
