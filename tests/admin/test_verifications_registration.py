"""Verificaciones for the registrations by WhatsApp: approve as each role (Vecinos' add_person,
one transaction), link to an owner, reject, and the notice to the neighbor (free text in the
24-hour window, the template outside it, a rejection outside it is not sent). Invented data."""

import html
import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.help import REGISTRATION_OWNER_NOTE
from app.bot import identity
from app.bot import registration_tools as reg
from app.bot.identity import can_view_unit_finance, identify_by_phone
from app.db.models import (
    DataSource,
    Person,
    PersonRole,
    Phone,
    UnitPerson,
    VerificationRequest,
    VerificationRequestStatus,
)
from tests.admin.conftest import OPERATOR, Panel
from tests.admin.wa_data import conversation
from tests.bot import factories as f

PHONE = "+5493515550888"


def _setup(
    session: Session,
    role: PersonRole,
    *,
    inbound_hours_ago: float | None = 1,
    with_owner: bool = True,
) -> dict:
    building = f.building(session, "045 TORRE REGISTRO")
    unit = f.unit(session, building, "03-B")
    owner = f.person(session, "Dueno Cargado")
    if with_owner:
        f.link(session, unit, owner)
    created = identity.request_operator_verification(session, PHONE, unit.id, "Nora Nueva", role)
    if inbound_hours_ago is not None:
        conversation(
            session, PHONE.lstrip("+"),
            last_inbound_at=datetime.now(UTC) - timedelta(hours=inbound_hours_ago),
        )  # fmt: skip
    session.commit()
    return {"request": created.request_id, "unit": unit.id, "owner": owner.id}


def _approve(panel: Panel, request_id: int, **data) -> str:
    response = panel.client.post(
        f"/admin/verifications/{request_id}/approve", data=data, follow_redirects=True
    )
    return html.unescape(response.text)


def _request(session: Session, request_id: int) -> VerificationRequest:
    session.expire_all()
    return session.get(VerificationRequest, request_id)


@pytest.mark.parametrize(
    ("role", "text"),
    [("owner", "propietario/a"), ("tenant", "inquilino/a"), ("resident", "familiar u otro")],
)
def test_approve_as_a_new_person_with_each_role(
    operator: Panel, db_session: Session, role: str, text: str
) -> None:
    ids = _setup(db_session, PersonRole(role))

    page = _approve(operator, ids["request"], mode="create", role=role, full_name="Nora Nueva")

    assert "Verificación aprobada: quedó registrado. Se le avisó por WhatsApp." in page
    request = _request(db_session, ids["request"])
    assert (request.status, request.resolved_by) == (VerificationRequestStatus.APPROVED, OPERATOR)
    nora = db_session.get(Person, request.person_id)
    assert nora.full_name == "Nora Nueva"
    link = db_session.get(UnitPerson, (ids["unit"], nora.id, PersonRole(role)))
    assert link.source == DataSource.MANUAL
    phone = db_session.scalar(select(Phone).where(Phone.e164 == PHONE))
    assert (phone.person_id, phone.source, phone.verified) == (nora.id, DataSource.MANUAL, True)
    assert identify_by_phone(db_session, PHONE).person_id == nora.id
    assert can_view_unit_finance(db_session, PHONE, ids["unit"]) is (role == "owner")
    assert operator.whatsapp.texts() == [
        reg.REGISTERED.format(role=text, unit="TORRE REGISTRO 03-B")
    ]
    events = [e["action"] for e in operator.admin_events()]
    assert events == ["neighbor_added", "verification_approved", "registration_notified"]


def test_approve_by_linking_to_an_owner_already_there(operator: Panel, db_session) -> None:
    ids = _setup(db_session, PersonRole.OWNER)

    page = _approve(operator, ids["request"], mode="link", person_id=ids["owner"])

    assert "Verificación aprobada" in page
    assert identify_by_phone(db_session, PHONE).person_id == ids["owner"]
    assert operator.whatsapp.texts() == [
        reg.REGISTERED.format(role="propietario/a", unit="TORRE REGISTRO 03-B")
    ]


def test_approving_is_all_or_nothing(operator: Panel, db_session: Session) -> None:
    ids = _setup(db_session, PersonRole.TENANT)
    other = f.person(db_session, "Ya Tiene El Numero")  # the number is someone else's now
    db_session.add(Phone(person_id=other.id, e164=PHONE, source=DataSource.MANUAL))
    db_session.commit()

    page = _approve(operator, ids["request"], mode="create", role="tenant", full_name="Nora N")

    assert "Ese número ya es de Ya Tiene El Numero" in page
    assert _request(db_session, ids["request"]).status == VerificationRequestStatus.PENDING
    assert db_session.scalar(select(Person).where(Person.full_name == "Nora N")) is None
    assert operator.whatsapp.sent == [] and operator.admin_events() == []


def test_closed_window_sends_the_template(operator: Panel, db_session: Session) -> None:
    ids = _setup(db_session, PersonRole.TENANT, inbound_hours_ago=30)

    page = _approve(operator, ids["request"], mode="create", role="tenant", full_name="Nora N")

    assert "Se le avisó por WhatsApp." in page
    [(kind, to, name)] = operator.whatsapp.sent
    assert (kind, to, name) == ("template", PHONE.lstrip("+"), ("registro_aprobado", "es_AR"))
    params = [p["text"] for p in operator.whatsapp.components[-1][0]["parameters"]]
    assert params == ["inquilino/a", "TORRE REGISTRO 03-B"]


def test_template_not_approved_yet_says_so(operator: Panel, db_session: Session) -> None:
    ids = _setup(db_session, PersonRole.TENANT, inbound_hours_ago=30)
    operator.whatsapp.fail_on.add("template")  # Meta: not approved (yet)

    page = _approve(operator, ids["request"], mode="create", role="tenant", full_name="Nora N")

    assert "Verificación aprobada: quedó registrado." in page
    assert "No se le pudo avisar por WhatsApp:" in page
    assert operator.whatsapp.sent == []
    assert _request(db_session, ids["request"]).status == VerificationRequestStatus.APPROVED
    [event] = operator.admin_events("registration_notified")
    assert event["outcome"] == "failed"


def test_reject_with_the_window_open_tells_them_kindly(operator: Panel, db_session) -> None:
    ids = _setup(db_session, PersonRole.RESIDENT)

    reply = operator.client.post(
        f"/admin/verifications/{ids['request']}/reject",
        headers={"X-Panel-Fetch": "1", "Accept": "application/json"},
    ).json()

    assert reply["ok"] is True and "Se le avisó por WhatsApp." in reply["message"]
    assert operator.whatsapp.texts() == [reg.NOT_REGISTERED]
    assert "Hablar con alguien" in reg.NOT_REGISTERED


def test_reject_with_the_window_closed_sends_nothing(operator: Panel, db_session) -> None:
    ids = _setup(db_session, PersonRole.RESIDENT, inbound_hours_ago=30)

    reply = operator.client.post(
        f"/admin/verifications/{ids['request']}/reject",
        headers={"X-Panel-Fetch": "1", "Accept": "application/json"},
    ).json()

    assert "pasaron más de 24 h desde su último mensaje" in reply["message"]
    assert operator.whatsapp.sent == []


def test_the_page_shows_the_declared_role_and_the_owner_sentence(
    operator: Panel, db_session: Session
) -> None:
    tenant = _setup(db_session, PersonRole.TENANT)
    listed = html.unescape(operator.client.get("/admin/verifications").text)
    assert "Rol declarado" in listed and "Inquilino/a" in listed

    page = html.unescape(
        operator.client.get(f"/admin/verifications/{tenant['request']}/approve").text
    )
    form = page.split('id="approve-registration"', 1)[1].split(">", 1)[0]
    assert f'data-owner-note="{REGISTRATION_OWNER_NOTE}"' in form
    text = re.search(r'data-confirm-text="([^"]*)"', form).group(1)
    assert REGISTRATION_OWNER_NOTE not in text  # a tenant: no debt
    assert 'value="create" checked' in page and '<option value="tenant" selected>' in page


def test_an_owner_approval_says_they_will_see_the_debt(operator: Panel, db_session) -> None:
    ids = _setup(db_session, PersonRole.OWNER)
    page = html.unescape(operator.client.get(f"/admin/verifications/{ids['request']}/approve").text)
    form = page.split('id="approve-registration"', 1)[1].split(">", 1)[0]
    assert REGISTRATION_OWNER_NOTE in re.search(r'data-confirm-text="([^"]*)"', form).group(1)
    assert 'value="link" checked' in page  # an owner already there: linking comes first


def test_without_login_nothing_is_approved(panel: Panel, db_session: Session) -> None:
    ids = _setup(db_session, PersonRole.TENANT)
    response = panel.client.post(
        f"/admin/verifications/{ids['request']}/approve",
        data={"mode": "create", "role": "tenant", "full_name": "Nora N"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303) and "/admin/login" in response.headers["location"]
    assert _request(db_session, ids["request"]).status == VerificationRequestStatus.PENDING


# --- «¿Cómo queda registrado?»: only the chosen option's fields show, are sent and count ------


def _page(panel: Panel, request_id: int) -> str:
    return html.unescape(panel.client.get(f"/admin/verifications/{request_id}/approve").text)


def _block(page: str, mode: str) -> tuple[str, str]:
    """(the opening tag's attributes, the fields) of one option's block."""
    rest = page.split(f'data-mode-fields="{mode}"', 1)[1]
    attrs, body = rest.split(">", 1)
    return attrs, re.split(r'<label class="form-check">|</fieldset>', body, maxsplit=1)[0]


def _shown(page: str, mode: str) -> bool:
    attrs, body = _block(page, mode)
    controls = re.findall(r"<(?:input|select)\b[^>]*>", body)
    assert controls and "required" not in body  # a hidden required one would block the form
    disabled = ["disabled" in tag for tag in controls]
    assert disabled == [disabled[0]] * len(disabled)
    assert ("hidden" in attrs) == disabled[0]  # hidden and disabled go together
    return "hidden" not in attrs


def test_a_tenant_starts_as_a_new_person_with_no_owner_chosen(operator: Panel, db_session) -> None:
    ids = _setup(db_session, PersonRole.TENANT)
    page = _page(operator, ids["request"])

    assert 'value="create" checked' in page and 'value="link" checked' not in page
    assert "Es un propietario ya cargado, que escribe desde otro número" in page
    assert "Es una persona nueva en la unidad" in page
    assert not _shown(page, "link") and _shown(page, "create")
    _, owner_fields = _block(page, "link")
    assert "Elegí el propietario" in owner_fields and "Dueno Cargado" in owner_fields
    assert '<option value="" selected>Elegí…</option>' in owner_fields
    assert f'<option value="{ids["owner"]}" selected' not in owner_fields
    _, new_fields = _block(page, "create")
    assert 'value="Nora Nueva"' in new_fields and '<option value="tenant" selected>' in new_fields


def test_an_owner_starts_linking_with_no_owner_chosen(operator: Panel, db_session) -> None:
    ids = _setup(db_session, PersonRole.OWNER)
    page = _page(operator, ids["request"])

    assert 'value="link" checked' in page and 'value="create" checked' not in page
    assert _shown(page, "link") and not _shown(page, "create")
    _, owner_fields = _block(page, "link")
    assert '<option value="" selected>Elegí…</option>' in owner_fields
    assert f'<option value="{ids["owner"]}" >Dueno Cargado</option>' in owner_fields
    _, new_fields = _block(page, "create")
    assert 'value="Nora Nueva"' in new_fields and '<option value="owner" selected>' in new_fields


def test_a_unit_without_owners_has_no_link_option(operator: Panel, db_session) -> None:
    ids = _setup(db_session, PersonRole.OWNER, with_owner=False)
    page = _page(operator, ids["request"])

    assert 'value="link"' not in page and 'data-mode-fields="link"' not in page
    assert "Elegí el propietario" not in page
    assert 'value="create" checked' in page and _shown(page, "create")

    response = operator.client.post(
        f"/admin/verifications/{ids['request']}/approve",
        data={"mode": "link", "person_id": str(ids["owner"])},
    )
    assert response.status_code == 400
    assert "Esta unidad no tiene propietarios cargados" in html.unescape(response.text)
    assert _request(db_session, ids["request"]).status == VerificationRequestStatus.PENDING
    assert db_session.scalar(select(Phone).where(Phone.e164 == PHONE)) is None
    assert operator.whatsapp.sent == [] and operator.admin_events() == []


@pytest.mark.parametrize("person_id", ["", None])
def test_linking_without_choosing_the_owner_approves_nothing(
    operator: Panel, db_session: Session, person_id: str | None
) -> None:
    ids = _setup(db_session, PersonRole.OWNER)
    data = {"mode": "link"} | ({} if person_id is None else {"person_id": person_id})

    response = operator.client.post(f"/admin/verifications/{ids['request']}/approve", data=data)

    assert response.status_code == 400
    page = html.unescape(response.text)
    assert "Elegí el propietario." in page
    assert 'value="link" checked' in page and _shown(page, "link")  # back as she left it
    assert _request(db_session, ids["request"]).status == VerificationRequestStatus.PENDING
    assert db_session.scalar(select(Phone).where(Phone.e164 == PHONE)) is None
    assert operator.whatsapp.sent == [] and operator.admin_events() == []


@pytest.mark.parametrize("mode", [None, "", "other"])
def test_without_a_valid_option_nothing_is_approved(
    operator: Panel, db_session: Session, mode: str | None
) -> None:
    ids = _setup(db_session, PersonRole.OWNER)
    data = {"person_id": str(ids["owner"])} | ({} if mode is None else {"mode": mode})

    response = operator.client.post(f"/admin/verifications/{ids['request']}/approve", data=data)

    assert response.status_code == 400
    assert "Elegí cómo queda registrado." in html.unescape(response.text)
    assert _request(db_session, ids["request"]).status == VerificationRequestStatus.PENDING
    assert operator.admin_events() == []


def test_a_new_person_ignores_the_owner_field(operator: Panel, db_session: Session) -> None:
    ids = _setup(db_session, PersonRole.TENANT)

    page = _approve(
        operator, ids["request"],
        mode="create", role="tenant", full_name="Nora Nueva", person_id=ids["owner"],
    )  # fmt: skip

    assert "Verificación aprobada" in page
    request = _request(db_session, ids["request"])
    assert request.person_id != ids["owner"]
    assert db_session.get(Person, request.person_id).full_name == "Nora Nueva"
    assert identify_by_phone(db_session, PHONE).person_id == request.person_id


def test_linking_ignores_the_new_person_fields(operator: Panel, db_session: Session) -> None:
    ids = _setup(db_session, PersonRole.TENANT)

    page = _approve(
        operator, ids["request"],
        mode="link", person_id=ids["owner"], full_name="Otra Persona", role="tenant",
    )  # fmt: skip

    assert "Verificación aprobada" in page
    assert _request(db_session, ids["request"]).person_id == ids["owner"]
    assert db_session.scalar(select(Person).where(Person.full_name == "Otra Persona")) is None
    assert identify_by_phone(db_session, PHONE).person_id == ids["owner"]
