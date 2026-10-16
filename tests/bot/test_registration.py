"""Registration by WhatsApp (request_registration) and what a pending one can do. Invented data."""

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.bot import registration_tools as reg
from app.bot.identity import identify_by_phone, pending_registration
from app.bot.prompts import describe_identity
from app.bot.tools import ToolContext, run_tool
from app.db.models import (
    Amenity,
    PersonRole,
    VerificationRequest,
    VerificationRequestStatus,
)
from tests.bot import factories as f
from tests.claims import factories as cf

NEW_PHONE = "+5493515550777"
OWNER_PHONE = "+5493515550101"


@pytest.fixture
def units(db_session: Session) -> dict[str, int]:
    rodas = f.building(db_session, "031 RODAS FICTICIO")
    with_email = f.unit(db_session, rodas, "04-C")
    without_email = f.unit(db_session, rodas, "05-A")
    f.link(db_session, with_email, f.person(db_session, "Ana Duena", email="ana@example.com",
                                             phone=OWNER_PHONE))  # fmt: skip
    f.link(db_session, without_email, f.person(db_session, "Raul Sinmail"))
    db_session.add(Amenity(building_id=rodas.id))
    db_session.commit()
    return {"with_email": with_email.id, "without_email": without_email.id}


def _ctx(session: Session, phone: str = NEW_PHONE, **values: Any) -> ToolContext:
    def no_debt(unit_id: int) -> Any:
        raise LookupError(unit_id)

    ctx = ToolContext(session=session, phone=phone, refresh_debt=no_debt)
    for key, value in values.items():
        setattr(ctx, key, value)
    return ctx


def _register(session: Session, unit_id: int, role: str, name: str = "Nora Nueva") -> dict:
    """The two messages: the summary with its buttons, then «Sí, enviar» to it."""
    args = {"unit_id": unit_id, "role": role, "full_name": name}
    first = _ctx(session)
    run_tool(first, "find_unit", {"building_text": "Rodas Ficticio", "unit_text": "4 C"})
    run_tool(first, "find_unit", {"building_text": "Rodas Ficticio", "unit_text": "5 A"})
    asked = run_tool(first, "request_registration", args)
    if asked["status"] != "confirmation_requested":
        return asked
    assert first.offer is not None
    assert [c.title for c in first.offer.choices] == ["Sí, enviar", "Corregir"]
    second = _ctx(session, user_text="Sí, enviar", last_bot_text=first.offer.text)
    run_tool(second, "find_unit", {"building_text": "Rodas Ficticio", "unit_text": "4 C"})
    run_tool(second, "find_unit", {"building_text": "Rodas Ficticio", "unit_text": "5 A"})
    return run_tool(second, "request_registration", args)


@pytest.mark.parametrize(
    ("role", "text"), [("tenant", "inquilino/a"), ("resident", "familiar u otro")]
)
def test_a_tenant_or_relative_registers_without_email(
    db_session: Session, units, role: str, text: str
) -> None:
    first = _ctx(db_session)
    run_tool(first, "find_unit", {"building_text": "Rodas Ficticio", "unit_text": "4 C"})
    asked = run_tool(
        first, "request_registration",
        {"unit_id": units["with_email"], "role": role, "full_name": " nora   nueva "},
    )  # fmt: skip
    assert asked["status"] == "confirmation_requested"
    assert first.offer.text == (
        f"Te registro como {text} de RODAS FICTICIO 04-C, a nombre de nora nueva.\n¿Está bien?"
    )
    assert pending_registration(db_session, NEW_PHONE) is None  # nothing until «Sí, enviar»

    created = _register(db_session, units["with_email"], role)

    assert created == {"status": "created", "say": reg.CREATED}
    request = pending_registration(db_session, NEW_PHONE)
    assert (request.declared_role, request.claimed_name, request.unit_id) == (
        PersonRole(role), "Nora Nueva", units["with_email"],
    )  # fmt: skip


def test_an_owner_with_email_gets_the_code_instead(db_session: Session, units) -> None:
    result = _register(db_session, units["with_email"], "owner")
    assert result["status"] == "use_email"
    assert pending_registration(db_session, NEW_PHONE) is None


def test_an_owner_without_email_registers(db_session: Session, units) -> None:
    assert _register(db_session, units["without_email"], "owner")["status"] == "created"
    assert pending_registration(db_session, NEW_PHONE).role == PersonRole.OWNER


def test_corregir_or_another_answer_registers_nothing(db_session: Session, units) -> None:
    args = {"unit_id": units["with_email"], "role": "tenant", "full_name": "Nora Nueva"}
    first = _ctx(db_session)
    run_tool(first, "find_unit", {"building_text": "Rodas Ficticio", "unit_text": "4 C"})
    run_tool(first, "request_registration", args)
    second = _ctx(db_session, user_text="Corregir", last_bot_text=first.offer.text)
    run_tool(second, "find_unit", {"building_text": "Rodas Ficticio", "unit_text": "4 C"})

    assert run_tool(second, "request_registration", args)["status"] == "confirmation_requested"
    assert pending_registration(db_session, NEW_PHONE) is None


def test_name_and_surname_are_needed(db_session: Session, units) -> None:
    assert _register(db_session, units["with_email"], "tenant", "Nora")["status"] == "missing_name"


def test_a_known_number_does_not_register(db_session: Session, units) -> None:
    ctx = _ctx(db_session, phone=OWNER_PHONE)
    run_tool(ctx, "find_unit", {"building_text": "Rodas Ficticio", "unit_text": "4 C"})
    result = run_tool(
        ctx, "request_registration",
        {"unit_id": units["with_email"], "role": "tenant", "full_name": "Ana Duena"},
    )  # fmt: skip
    assert result["status"] == "already_registered"


def test_one_pending_request_per_number(db_session: Session, units) -> None:
    assert _register(db_session, units["with_email"], "tenant")["status"] == "created"

    again = _register(db_session, units["without_email"], "resident")

    assert again == {"status": "already_pending", "say": reg.ALREADY_PENDING}
    assert len(db_session.scalars(select(VerificationRequest)).all()) == 1
    # And the database says so too.
    db_session.add(
        VerificationRequest(phone_e164=NEW_PHONE, unit_id=units["without_email"],
                            claimed_name="Otra", status=VerificationRequestStatus.PENDING)
    )  # fmt: skip
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_while_pending_no_debt_sum_nor_claims(db_session: Session, units) -> None:
    _register(db_session, units["with_email"], "tenant")
    request = pending_registration(db_session, NEW_PHONE)
    assert "solicitud de registro pendiente de aprobación del estudio (inquilino/a de RODAS " \
        "FICTICIO 04-C)" in describe_identity(identify_by_phone(db_session, NEW_PHONE),
                                             reg.describe_pending(request))  # fmt: skip

    ctx = _ctx(db_session)
    run_tool(ctx, "find_unit", {"building_text": "Rodas Ficticio", "unit_text": "4 C"})
    for tool, args in [
        ("get_debt", {"unit_id": units["with_email"]}),
        ("get_payment_info", {"unit_id": units["with_email"]}),
        ("my_sum_reservations", {}),
        ("my_claims", {}),
        ("start_claim", {"category_hint": "pérdida de agua en el baño"}),
    ]:
        result = run_tool(ctx, tool, args)
        assert result["reason"] == "pending_registration", tool
        assert result["say"] == reg.PENDING_SAY, tool


def test_an_urgency_while_pending_still_goes_to_a_person(db_session: Session, units) -> None:
    gas = cf.category(db_session, "Siento olor a gas", list_title="Olor a gas")
    gas.urgent = True
    db_session.commit()
    _register(db_session, units["with_email"], "tenant")

    result = run_tool(_ctx(db_session), "start_claim", {"category_hint": "olor a gas"})

    assert result["status"] == "not_verified" and result["urgent"] is True
    assert "handoff_to_human (priority urgent)" in result["next_step"]
