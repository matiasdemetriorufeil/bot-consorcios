"""Vecinos and the bot, end to end: a number loaded, changed or removed in the panel is what the
next WhatsApp message finds (webhook, processor and channel over the test database, the Cloud
API faked, the LLM scripted, no network). Invented data only."""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin import neighbors_service as svc
from app.db.models import DebtLine, DebtSnapshot, Person, PersonRole, SyncKind, Unit
from app.sync.live import DebtResult
from tests.llm.fakes import Call, Say, last_tool_result, last_user_text
from tests.whatsapp.conftest import TZ, MakeWa, Wa
from tests.whatsapp.fakes import incoming, text_message

NEW_WA_ID = "5493515550777"
NEW_PHONE = f"+{NEW_WA_ID}"
USER = "operadora"


def _say(wa: Wa, text: str) -> None:
    wa.advance(timedelta(minutes=1))
    assert wa.post(incoming(text_message(text, wa.now), wa_id=NEW_WA_ID)).status_code == 200


def _unit_id(session: Session) -> int:
    return session.scalar(select(Unit.id).where(Unit.label == "04-C"))


def _context(wa: Wa, index: int) -> str:
    return last_user_text("anthropic", wa.script.requests[index])


def test_a_number_loaded_in_vecinos_is_known_in_the_next_message(
    make_wa: MakeWa, db_session: Session
) -> None:
    unit_id = _unit_id(db_session)
    debt = Call("get_debt", {"unit_id": unit_id})
    wa = make_wa([Say("Hola"), debt, Say("No puedo."), debt, Say("Tampoco."), Say("Hola de nuevo")])

    _say(wa, "hola")  # nobody yet
    assert "número no registrado" in _context(wa, 0)

    saved = svc.add_person(
        db_session, USER, unit_id, PersonRole.RESIDENT, full_name="Rita Familiar",
        phones=[NEW_PHONE],
    )  # fmt: skip
    _say(wa, "¿cuánto se debe de expensas?")
    context = _context(wa, 1)
    assert "número verificado de Rita Familiar" in context
    assert f"RODAS II 04-C (unit_id {unit_id}, familiar u otro)" in context
    denied = last_tool_result("anthropic", wa.script.requests[2])
    assert "denied" in denied and "tenant" in denied

    svc.change_role(db_session, USER, unit_id, saved.person_id, "resident", "tenant")
    _say(wa, "¿y ahora?")
    assert f"(unit_id {unit_id}, inquilino/a)" in _context(wa, 3)
    denied = last_tool_result("anthropic", wa.script.requests[4])
    assert "denied" in denied and "tenant" in denied
    assert not [t for t in wa.fake.texts() if "Saldo total" in t]  # never a debt message

    assert svc.remove_from_unit(db_session, USER, unit_id, saved.person_id, "tenant")
    _say(wa, "hola")
    assert "número no registrado" in _context(wa, 5)
    assert db_session.get(Person, saved.person_id) is None


def test_an_owner_loaded_by_hand_gets_the_debt(make_wa: MakeWa, db_session: Session) -> None:
    unit_id = _unit_id(db_session)
    snapshot = DebtSnapshot(
        unit_id=unit_id,
        fetched_at=datetime(2026, 9, 30, 10, 0, tzinfo=TZ),
        source=SyncKind.LIVE,
        total_amount=Decimal("1234.50"),
        is_up_to_date=False,
        lines=[
            DebtLine(
                concept="Expensas ordinarias", period="09/2026", concept_amount=Decimal("1234.50"),
                balance_due=Decimal("1234.50"), accumulated=Decimal("1234.50"),
            )
        ],
    )  # fmt: skip
    db_session.add(snapshot)
    db_session.commit()
    svc.add_person(
        db_session, USER, unit_id, PersonRole.OWNER, full_name="Olga Propietaria",
        phones=["351 555-0777"],
    )  # fmt: skip
    wa = make_wa(
        [Call("get_debt", {"unit_id": unit_id}), Say("¿Algo más?")],
        refresh_debt=lambda unit_id: DebtResult(snapshot, stale=False),
    )

    _say(wa, "¿cuánto debo?")

    assert "número verificado de Olga Propietaria" in _context(wa, 0)
    [sent] = wa.fake.texts()
    assert "*RODAS II 04-C*" in sent and "Saldo total: *$1.234,50*" in sent
