"""The panel section "Reclamos" (app.admin.claims): list, claim page, "Nuevo reclamo", actions
with their audit, the block in the contact card, and that nothing reaches WhatsApp. Invented
data only (fictitious buildings, people and providers, phones 351 555-01xx)."""

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin import help
from app.claims.service import Reporter, close_claim, create_claim, mark_sent
from app.db.models import (
    Building,
    Claim,
    ClaimActor,
    ClaimAttachment,
    ClaimCategory,
    ClaimScope,
    ClaimSource,
    ClaimStatus,
    Provider,
    Unit,
    WaMediaStatus,
    WaMessage,
)
from tests.admin.conftest import Panel
from tests.admin.wa_data import conversation, message
from tests.bot import factories as f
from tests.claims import factories as cf

NOW = datetime.now(UTC)
WA_ANA = "+5493515550101"
WA_BETO = "+5493515550102"
WA_LIFTS = "+5493515550111"


@dataclass
class World:
    building: Building
    other: Building
    unit: Unit
    unit_b: Unit
    lift: ClaimCategory  # whole building, urgent, a provider
    damp: ClaimCategory  # one unit, the studio
    gate: ClaimCategory  # whole building, the studio, only in the other building
    lifts: Provider
    plumber: Provider
    ana_id: int


@pytest.fixture
def w(db_session: Session) -> World:
    s = db_session
    building = f.building(s, "001 TORRE INVENTADA")
    other = f.building(s, "002 OTRA FICTICIA")
    unit, unit_b = f.unit(s, building, "01-A"), f.unit(s, building, "02-B")
    ana = f.person(s, "Ana Inventada", phone=WA_ANA)
    f.link(s, unit, ana)
    f.link(s, unit_b, f.person(s, "Beto Ficticio", phone=WA_BETO))
    lift = cf.category(s, "No funciona el ascensor inventado", list_title="Ascensor", sort_order=1)
    lift.urgent = True
    damp = cf.category(
        s, "Humedad inventada", list_title="Humedad", scope=ClaimScope.UNIT, sort_order=2
    )
    gate = cf.category(s, "Portón inventado", list_title="Portón", sort_order=3)
    lifts = cf.provider(s, "Ascensores Ficticios SRL", WA_LIFTS)
    plumber = cf.provider(s, "Plomería Inventada")
    cf.assign(s, building.id, lift, lifts)
    cf.assign(s, building.id, damp)
    cf.assign(s, other.id, gate)
    s.commit()
    return World(building, other, unit, unit_b, lift, damp, gate, lifts, plumber, ana.id)


def _claim(
    s: Session,
    building: Building,
    category: ClaimCategory,
    name: str = "Ana Inventada",
    phone: str | None = WA_ANA,
    *,
    unit: Unit | None = None,
    ago: timedelta = timedelta(hours=1),
    description: str = "No anda desde la mañana",
) -> Claim:
    result = create_claim(
        s,
        building_id=building.id,
        category_id=category.id,
        unit_id=unit.id if unit else None,
        description=description,
        reporter=Reporter(name=name, phone_e164=phone, unit_id=unit.id if unit else None),
        source=ClaimSource.BOT,
        now=NOW - ago,
    )
    s.commit()
    return result.claim


def _text(page: str) -> str:
    return html.unescape(page)


def _numbers(page: str) -> list[int]:
    return [int(n) for n in re.findall(r'data-claim="(\d+)"', page)]


# --- The list -------------------------------------------------------------------------------


def test_tabs_with_their_counts(logged_in: Panel, w: World) -> None:
    s = logged_in.session
    open_one = _claim(s, w.building, w.damp, unit=w.unit)
    closed = _claim(s, w.building, w.damp, "Beto Ficticio", WA_BETO, unit=w.unit_b)
    close_claim(s, closed, ClaimStatus.SOLVED, "Listo", actor=ClaimActor.BOT)
    s.commit()

    page = _text(logged_in.client.get("/admin/claims").text)
    assert help.PAGE_HELP["view-claims"] in page
    assert re.search(r"Abiertos <span[^>]*>1</span>", page)
    assert re.search(r"Cerrados <span[^>]*>1</span>", page)
    assert re.search(r"Todos <span[^>]*>2</span>", page)
    assert _numbers(page) == [open_one.number]
    closed_page = logged_in.client.get("/admin/claims?tab=closed").text
    assert _numbers(closed_page) == [closed.number]
    assert sorted(_numbers(logged_in.client.get("/admin/claims?tab=all").text)) == sorted(
        [open_one.number, closed.number]
    )


def test_urgent_open_ones_first_then_the_newest(logged_in: Panel, w: World) -> None:
    s = logged_in.session
    old_urgent = _claim(s, w.building, w.lift, ago=timedelta(days=3))
    newer = _claim(s, w.building, w.damp, unit=w.unit, ago=timedelta(hours=2))
    newest = _claim(s, w.building, w.damp, "Beto Ficticio", WA_BETO, unit=w.unit_b)
    assert _numbers(logged_in.client.get("/admin/claims").text) == [
        old_urgent.number,
        newest.number,
        newer.number,
    ]


def test_the_columns(logged_in: Panel, w: World) -> None:
    s = logged_in.session
    claim = _claim(s, w.building, w.lift, ago=timedelta(hours=3))
    create_claim(
        s,
        building_id=w.building.id,
        category_id=w.lift.id,
        unit_id=None,
        description="Trabado",
        reporter=Reporter(name="Beto Ficticio", phone_e164=WA_BETO),
        source=ClaimSource.BOT,
    )
    s.commit()
    row = _text(logged_in.client.get("/admin/claims").text).split(f'data-claim="{claim.number}"')[1]
    row = row.split("</tr>")[0]
    assert f"#{claim.number}" in row and "Urgente" in row
    assert "TORRE INVENTADA" in row and "001 TORRE" not in row
    assert "Todo el edificio" in row and "Ascensor" in row
    assert "Ana Inventada" in row and "+1" in row
    assert "Ascensores Ficticios SRL" in row
    assert "Falta avisar al proveedor" in row and "claim-open" in row
    assert "hace 3 h" in row


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"building": "{other}"}, ["gate"]),
        ({"category": "{damp}"}, ["damp"]),
        ({"attends": "studio"}, ["damp", "gate"]),
        ({"attends": "{lifts}"}, ["lift"]),
        ({"status": "sent"}, ["lift"]),
        ({"urgent": "yes"}, ["lift"]),
        ({"urgent": "no"}, ["damp", "gate"]),
        ({"q": "Beto"}, ["damp"]),
        ({"q": "02-B"}, ["damp"]),
        ({"q": "555-0102"}, ["damp"]),
        ({"q": "#{lift_number}"}, ["lift"]),
    ],
)
def test_filters_and_search(
    logged_in: Panel, w: World, params: dict[str, str], expected: list[str]
) -> None:
    s = logged_in.session
    claims = {
        "lift": _claim(s, w.building, w.lift),
        "damp": _claim(s, w.building, w.damp, "Beto Ficticio", WA_BETO, unit=w.unit_b),
        "gate": _claim(s, w.other, w.gate, "Carla Inventada", None),
    }
    mark_sent(s, claims["lift"])
    s.commit()
    values = {
        "other": w.other.id, "damp": w.damp.id, "lifts": w.lifts.id,
        "lift_number": claims["lift"].number,
    }  # fmt: skip
    query = {k: v.format(**values) for k, v in params.items()}
    page = logged_in.client.get("/admin/claims", params={"tab": "all", **query}).text
    assert sorted(_numbers(page)) == sorted(claims[k].number for k in expected)


def test_a_search_without_results_and_an_empty_list(logged_in: Panel, w: World) -> None:
    page = _text(logged_in.client.get("/admin/claims").text)
    assert help.EMPTY["claims:open"] in page
    page = _text(logged_in.client.get("/admin/claims?q=Nadie").text)
    assert help.EMPTY["claims:search"] in page


# --- The claim ------------------------------------------------------------------------------


def test_the_claim_page(logged_in: Panel, w: World) -> None:
    s = logged_in.session
    previous = _claim(s, w.building, w.lift, ago=timedelta(days=5))
    close_claim(
        s,
        previous,
        ClaimStatus.SOLVED,
        "Lo arreglaron",
        actor=ClaimActor.BOT,
        now=NOW - timedelta(days=4),
    )
    claim = create_claim(
        s,
        building_id=w.building.id,
        category_id=w.lift.id,
        unit_id=None,
        description="Se trabó en el 3",
        reporter=Reporter(name="Ana Inventada", phone_e164=WA_ANA, unit_id=w.unit.id),
        source=ClaimSource.BOT,
        follow_up_answer="Sí, una vecina",
    ).claim
    create_claim(
        s,
        building_id=w.building.id,
        category_id=w.lift.id,
        unit_id=None,
        description="Tampoco anda",
        reporter=Reporter(name="Beto Ficticio", phone_e164=WA_BETO, unit_id=w.unit_b.id),
        source=ClaimSource.BOT,
    )
    conv = conversation(s, WA_BETO[1:], profile_name="Beto")
    photo = message(
        s,
        conv,
        None,
        message_type="image",
        media_mime="image/jpeg",
        media_status=WaMediaStatus.STORED,
        media_path="x/foto.jpg",
    )
    s.add(ClaimAttachment(claim_id=claim.id, wa_message_id=photo.id))
    s.commit()

    page = _text(logged_in.client.get(f"/admin/claims/{claim.id}").text)

    assert f"Reclamo #{claim.number}" in page
    assert help.PAGE_HELP["view-claim"] in page
    assert "Se trabó en el 3" in page and "Sí, una vecina" in page
    assert "¿Hay alguien encerrado?" not in page  # the kind has no question here
    reporters = page.split('id="claim-reporters"')[1].split("</div>\n  </div>")[0]
    assert "Ana Inventada" in reporters and "Unidad 01-A" in reporters
    assert "Beto Ficticio" in reporters and "Unidad 02-B" in reporters
    assert f"/admin/conversations/{conv.id}" in reporters  # Beto wrote to WhatsApp
    assert f"/admin/wa/media/{photo.id}" in page
    assert f"#{previous.number}" in page and "Solucionado" in page
    history = page.split('id="claim-history"')[1]
    assert history.index("Reclamo creado") < history.index("Se sumó Beto Ficticio: Tampoco anda")
    assert "El bot" in history
    for button in ("Cambiar quién lo atiende", "Cerrar como solucionado", "Cancelar reclamo"):
        assert button in page
    assert help.CONFIRM["cancel_claim"]["text"] in page


def test_a_closed_claim_only_takes_notes(logged_in: Panel, w: World) -> None:
    s = logged_in.session
    claim = _claim(s, w.building, w.lift)
    close_claim(s, claim, ClaimStatus.CANCELLED, "Estaba repetido", actor=ClaimActor.BOT)
    s.commit()
    page = _text(logged_in.client.get(f"/admin/claims/{claim.id}").text)
    assert "Cancelado" in page and "Reclamo cancelado: Estaba repetido" in page
    assert 'id="claim-actions"' not in page
    assert "Nota interna" in page


def test_an_unknown_claim(logged_in: Panel, w: World) -> None:
    assert logged_in.client.get("/admin/claims/999999").status_code == 404


# --- Actions --------------------------------------------------------------------------------


def _reload(s: Session, claim: Claim) -> Claim:
    s.expire_all()
    found = s.get(Claim, claim.id)
    assert found is not None
    return found


def test_change_who_attends_it(logged_in: Panel, w: World) -> None:
    s = logged_in.session
    claim = _claim(s, w.building, w.lift)
    url = f"/admin/claims/{claim.id}/provider"

    page = logged_in.client.post(url, data={"provider_id": ""}, follow_redirects=True).text
    assert "cambió quién lo atiende" in _text(page)
    assert "Ahora lo atiende el estudio" in _text(page)
    claim = _reload(s, claim)
    assert claim.status == ClaimStatus.STUDIO and claim.provider_id is None

    logged_in.client.post(url, data={"provider_id": str(w.plumber.id)})
    claim = _reload(s, claim)
    assert claim.status == ClaimStatus.PENDING_SEND and claim.provider_id == w.plumber.id

    page = logged_in.client.post(
        url, data={"provider_id": str(w.plumber.id)}, follow_redirects=True
    )
    assert "No cambió quién lo atiende." in _text(page.text)
    [first, second] = logged_in.admin_events("claim_provider_changed")
    assert first["claim_id"] == claim.id and first["claim_number"] == claim.number


def test_close_as_solved_needs_a_reason(logged_in: Panel, w: World) -> None:
    s = logged_in.session
    claim = _claim(s, w.building, w.lift)
    url = f"/admin/claims/{claim.id}/close"

    page = logged_in.client.post(url, data={"reason": " "}, follow_redirects=True).text
    assert "Contá el motivo." in _text(page)
    assert _reload(s, claim).status == ClaimStatus.PENDING_SEND

    page = logged_in.client.post(url, data={"reason": "Cambiaron el motor"}, follow_redirects=True)
    assert "Reclamo cerrado como solucionado." in _text(page.text)
    claim = _reload(s, claim)
    assert claim.status == ClaimStatus.SOLVED and claim.close_reason == "Cambiaron el motor"
    [event] = logged_in.admin_events("claim_solved")
    assert "Cambiaron" not in str(event)  # never the text

    # Closed: it never reopens.
    page = logged_in.client.post(
        f"/admin/claims/{claim.id}/provider", data={"provider_id": ""}, follow_redirects=True
    )
    assert "ya está cerrado" in _text(page.text)
    assert _reload(s, claim).status == ClaimStatus.SOLVED


def test_cancel_and_note(logged_in: Panel, w: World) -> None:
    s = logged_in.session
    claim = _claim(s, w.building, w.damp, unit=w.unit)
    logged_in.client.post(f"/admin/claims/{claim.id}/cancel", data={"reason": "Repetido"})
    claim = _reload(s, claim)
    assert claim.status == ClaimStatus.CANCELLED and claim.closed_at is not None

    page = logged_in.client.post(
        f"/admin/claims/{claim.id}/note",
        data={"text": "Le avisé por teléfono"},
        follow_redirects=True,
    )
    assert "Nota interna: Le avisé por teléfono" in _text(page.text)
    actions = [e["action"] for e in logged_in.admin_events()]
    assert actions == ["claim_cancelled", "claim_note_added"]


# --- "Nuevo reclamo" ------------------------------------------------------------------------


def _new(panel: Panel, w: World, **values: str) -> Any:
    data = {"building_id": str(w.building.id), "description": "Llamó por teléfono", **values}
    return panel.client.post("/admin/claims/new", data=data, follow_redirects=True)


def test_the_form_shows_only_the_building_s_problems(logged_in: Panel, w: World) -> None:
    page = _text(logged_in.client.get("/admin/claims/new").text)
    assert help.PAGE_HELP["view-claim-new"] in page and "Elegí el edificio" in page
    page = _text(logged_in.client.get(f"/admin/claims/new?building_id={w.building.id}").text)
    assert "Ascensor (todo el edificio)" in page and "Humedad (una unidad)" in page
    assert "Portón" not in page  # enabled only in the other building
    assert "Ana Inventada (propietario)" in page and 'label="Unidad 02-B"' in page


def test_new_claim_for_a_person_of_the_roster(logged_in: Panel, w: World) -> None:
    response = _new(
        logged_in,
        w,
        category_id=str(w.damp.id),
        unit_id=str(w.unit.id),
        reporter="roster",
        person=f"{w.unit.id}-{w.ana_id}",
    )
    claim = logged_in.session.scalar(select(Claim))
    assert claim is not None
    assert f"Reclamo #{claim.number} cargado." in _text(response.text)
    assert (claim.reporter_person_id, claim.reporter_phone_e164) == (w.ana_id, WA_ANA)
    assert claim.unit_id == w.unit.id and claim.source == ClaimSource.PANEL
    assert claim.status == ClaimStatus.STUDIO and claim.created_by_user == "operadora"
    assert [e["action"] for e in logged_in.admin_events()] == ["claim_created"]


def test_new_claim_typed_by_hand(logged_in: Panel, w: World) -> None:
    _new(
        logged_in,
        w,
        category_id=str(w.lift.id),
        reporter="other",
        name="Vecina Inventada",
        phone="0351 15 555-0109",
    )
    claim = logged_in.session.scalar(select(Claim))
    assert claim is not None and claim.reporter_name == "Vecina Inventada"
    assert claim.reporter_phone_e164 == "+5493515550109" and claim.unit_id is None
    assert claim.status == ClaimStatus.PENDING_SEND


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ({"category": "damp", "reporter": "other", "name": "X"}, "Elegí la unidad"),
        ({"category": "lift", "reporter": "other", "name": ""}, "Escribí el nombre"),
        ({"category": "lift", "reporter": "other", "name": "X", "phone": "12"}, "no es válido"),
        ({"category": "lift", "reporter": "roster", "person": ""}, "Elegí la persona"),
        (
            {"category": "damp", "unit": "b", "reporter": "roster", "person": "ana"},
            "unidad elegida",
        ),
        ({"category": "gate", "reporter": "other", "name": "X"}, "no se puede reclamar"),
    ],
)
def test_new_claim_errors(logged_in: Panel, w: World, values: dict[str, str], error: str) -> None:
    data = {
        "category_id": str(getattr(w, values.pop("category")).id),
        "unit_id": str(w.unit_b.id) if values.pop("unit", "") == "b" else "",
    }
    if values.get("person") == "ana":
        values["person"] = f"{w.unit.id}-{w.ana_id}"
    response = _new(logged_in, w, **data, **values)
    assert response.status_code == 400
    assert error in _text(response.text)
    assert logged_in.session.scalar(select(func.count()).select_from(Claim)) == 0


def test_a_repeated_claim_opens_the_open_one(logged_in: Panel, w: World) -> None:
    first = _claim(logged_in.session, w.building, w.lift)
    response = _new(
        logged_in, w, category_id=str(w.lift.id), reporter="other", name="Vecina Inventada"
    )
    page = _text(response.text)
    assert f"Ya había un reclamo abierto (#{first.number}): se sumó a ese." in page
    assert f"Reclamo #{first.number}" in page and "Se sumó Vecina Inventada" in page
    assert logged_in.session.scalar(select(func.count()).select_from(Claim)) == 1
    assert [e["action"] for e in logged_in.admin_events()] == ["claim_joined"]


# --- Conversaciones, operators, WhatsApp ----------------------------------------------------


def test_the_contact_card_shows_its_claims(logged_in: Panel, w: World) -> None:
    claim = _claim(logged_in.session, w.building, w.lift)
    conv = conversation(logged_in.session, WA_ANA[1:], profile_name="Ana")
    logged_in.session.commit()
    page = _text(logged_in.client.get(f"/admin/conversations/{conv.id}").text)
    card = page.split('class="contact-card"')[1]
    assert "Reclamos" in card and f"#{claim.number} · Ascensor" in card
    assert f"/admin/claims/{claim.id}" in card and "Falta avisar al proveedor" in card


def test_an_operator_does_everything_in_claims(operator: Panel, w: World) -> None:
    claim = _claim(operator.session, w.building, w.lift)
    assert operator.client.get("/admin/claims").status_code == 200
    assert operator.client.get(f"/admin/claims/{claim.id}").status_code == 200
    assert operator.client.get(f"/admin/claims/new?building_id={w.building.id}").status_code == 200
    base = f"/admin/claims/{claim.id}"
    for url, data in (
        (f"{base}/provider", {"provider_id": ""}),
        (f"{base}/note", {"text": "Nota"}),
        (f"{base}/close", {"reason": "Listo"}),
    ):
        assert operator.client.post(url, data=data, follow_redirects=False).status_code == 302
    other = _claim(operator.session, w.building, w.damp, unit=w.unit)
    operator.client.post(f"/admin/claims/{other.id}/cancel", data={"reason": "Error"})
    _new(operator, w, category_id=str(w.lift.id), reporter="other", name="Vecina Inventada")
    actions = [(e["action"], e["admin_user"]) for e in operator.admin_events()]
    assert actions == [
        ("claim_provider_changed", "marta"),
        ("claim_note_added", "marta"),
        ("claim_solved", "marta"),
        ("claim_cancelled", "marta"),
        ("claim_created", "marta"),
    ]
    # The set-up of 8.1 stays for admins.
    assert operator.client.get("/admin/building-claims").status_code == 403


def test_urgent_open_claims_for_the_alerts_and_the_menu(logged_in: Panel, w: World) -> None:
    s = logged_in.session
    lift = _claim(s, w.building, w.lift)  # urgent
    _claim(s, w.building, w.damp, unit=w.unit)  # not urgent
    closed = _claim(s, w.other, w.gate, "Carla Inventada", None)
    closed.urgent = True
    close_claim(s, closed, ClaimStatus.SOLVED, "Listo", actor=ClaimActor.BOT)
    s.commit()

    data = logged_in.client.get("/admin/claims/poll").json()
    assert data["urgent_claims"] == [
        {"id": lift.id, "number": lift.number, "problem": "Ascensor", "building": "TORRE INVENTADA"}
    ]
    polled = logged_in.client.get("/admin/conversations/poll").json()
    assert polled["urgent_claims"] == data["urgent_claims"]

    page = logged_in.client.get("/admin/claims").text
    badge = re.search(r"<span[^>]*data-urgent-claims[^>]*>(\d+)</span>", page)
    assert badge and badge.group(1) == "1" and "hidden" not in badge.group(0)
    assert "data-urgent-poll" in page and 'id="enable-alerts"' in page
    # Elsewhere too (the menu is on every page).
    other = logged_in.client.get("/admin/phones").text
    assert re.search(r"data-urgent-claims[^>]*>1</span>", other)


def test_no_urgent_claims_hides_the_counter(logged_in: Panel, w: World) -> None:
    page = logged_in.client.get("/admin/claims").text
    badge = re.search(r"<span[^>]*data-urgent-claims[^>]*>", page)
    assert badge and "hidden" in badge.group(0)


def test_nothing_reaches_whatsapp(logged_in: Panel, w: World) -> None:
    claim = _claim(logged_in.session, w.building, w.lift)
    base = f"/admin/claims/{claim.id}"
    _new(
        logged_in, w, category_id=str(w.damp.id), unit_id=str(w.unit.id), reporter="other", name="X"
    )
    logged_in.client.post(f"{base}/provider", data={"provider_id": str(w.plumber.id)})
    logged_in.client.post(f"{base}/note", data={"text": "Nota"})
    logged_in.client.post(f"{base}/close", data={"reason": "Listo"})
    assert logged_in.whatsapp.sent == []
    assert logged_in.session.scalar(select(func.count()).select_from(WaMessage)) == 0
