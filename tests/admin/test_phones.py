"""The "Teléfonos" page: tabs "A revisar" and "Todos", search, filters, per-row "Aprobar" and
"Desvincular", "Aprobar seleccionados". Invented data only."""

import re

import pytest
from sqlalchemy.orm import Session

from app.admin.formatting import phone
from app.admin.help import CONFIRM
from app.admin.phones import PAGE_SIZE
from app.db.models import DataSource, PersonRole, Phone
from tests.admin.conftest import ADMIN, OPERATOR, Panel
from tests.bot import factories as f

PLAIN = "+5493515550401"
REVIEW = "+5493515550402"
CONFLICT = "+5493515550403"
VERIFIED = "+5493515550404"
REVIEW_2 = "+5493515550405"
ALL = (PLAIN, REVIEW, CONFLICT, VERIFIED, REVIEW_2)
COLUMNS = ["Teléfono", "Persona", "Unidades", "Origen", "Estado", "Fecha"]


@pytest.fixture
def phones(db_session: Session) -> dict[str, int]:
    building = f.building(db_session, "098 EDIFICIO TELEFONOS")
    unit_a = f.unit(db_session, building, "1A")
    unit_b = f.unit(db_session, building, "2B")
    plain = f.person(db_session, "Ana Inventada", phone=PLAIN)
    f.link(db_session, unit_a, plain)
    f.link(db_session, unit_b, plain, PersonRole.TENANT)
    f.person(db_session, "Bruno Revisar", phone=REVIEW, needs_review=True)
    f.person(db_session, "Berta Revisar", phone=REVIEW_2, needs_review=True)
    f.person(db_session, "Carla Conflicto", phone=CONFLICT, conflict=True)
    verified = f.person(db_session, "Dario Verificado")
    db_session.add(
        Phone(person_id=verified.id, e164=VERIFIED, source=DataSource.BOT_VERIFIED, verified=True)
    )
    db_session.commit()
    rows = db_session.query(Phone).filter(Phone.e164.in_(ALL))
    return {p.e164: p.id for p in rows}


def _page(panel: Panel, **params: str) -> str:
    response = panel.client.get("/admin/phones", params=params)
    assert response.status_code == 200
    return response.text


def _shown(page: str) -> set[str]:
    # As the panel shows them: "351 555-0401".
    return {n for n in ALL if phone(n) in page}


def _headers(page: str) -> list[str]:
    thead = page.split("<thead>", 1)[1].split("</thead>", 1)[0]
    return [h.strip() for h in re.findall(r"<th[^>]*>([^<]*)</th>", thead) if h.strip()]


def _phone(panel: Panel, pk: int) -> Phone | None:
    panel.session.expire_all()
    return panel.session.get(Phone, pk)


# --- Tabs and columns -------------------------------------------------------------------------


def test_opens_on_review_with_its_count(logged_in: Panel, phones: dict[str, int]) -> None:
    page = _page(logged_in)
    assert _shown(page) == {REVIEW, REVIEW_2}
    assert re.search(r"A revisar\s*<span[^>]*>2</span>", page)
    assert "Aprobar seleccionados" in page


def test_opens_on_all_when_nothing_to_review(logged_in: Panel, db_session: Session) -> None:
    f.person(db_session, "Ana Inventada", phone=PLAIN)
    db_session.commit()
    page = _page(logged_in)
    assert phone(PLAIN) in page
    assert re.search(r'nav-link active"[^>]*>\s*Todos', page)
    assert "Aprobar seleccionados" not in page


def test_all_tab_shows_every_phone_with_person_units_and_status(
    logged_in: Panel, phones: dict[str, int]
) -> None:
    page = _page(logged_in, tab="all")
    assert _shown(page) == set(ALL)
    assert "Ana Inventada" in page
    assert "1A (propietario)" in page and "2B (inquilino)" in page
    assert "Por WhatsApp" in page and "ConsorPlus" in page
    assert "+549" not in page
    for status in ("Verificado", "A revisar", "En conflicto", "Sin verificar"):
        assert status in page
    assert "Aprobar seleccionados" not in page


@pytest.mark.parametrize("tab", ["review", "all"])
def test_same_columns_in_both_tabs_and_no_id(
    logged_in: Panel, phones: dict[str, int], tab: str
) -> None:
    page = _page(logged_in, tab=tab)
    assert _headers(page) == COLUMNS
    assert ">ID<" not in page and ">id<" not in page


def test_buttons_in_each_row(logged_in: Panel, phones: dict[str, int]) -> None:
    review = _page(logged_in, tab="review")
    for number in (REVIEW, REVIEW_2):
        pk = phones[number]
        assert f"/admin/phones/{pk}/approve" in review
        assert f"/admin/phones/{pk}/unlink" in review
    every = _page(logged_in, tab="all")
    assert f"/admin/phones/{phones[PLAIN]}/unlink" in every
    # "Aprobar" only for a phone to review.
    assert f"/admin/phones/{phones[PLAIN]}/approve" not in every
    assert f"/admin/phones/{phones[REVIEW]}/approve" in every
    # No SQLAdmin "Actions" menu nor create/delete.
    assert "Actions" not in every and "Delete" not in every and "New " not in every


def test_unlink_confirmation_explains_what_happens(
    logged_in: Panel, phones: dict[str, int]
) -> None:
    page = _page(logged_in, tab="all")
    text = CONFIRM["unlink"]["text"]
    assert "va a tener que volver a identificarse por WhatsApp" in text
    assert "a la mañana siguiente vuelve a aparecer" in text
    # The dialog says which number and whose (panel.js opens it with these).
    assert 'data-confirm-title="¿Desvincular este teléfono?"' in page
    assert "351 555-0401 · Ana Inventada. Se borra este número." in page


# --- Search, filters and pages ----------------------------------------------------------------


@pytest.mark.parametrize("term", ["0351 555-0401", "3515550401", "+54 9 351 555 0401"])
def test_search_by_number(logged_in: Panel, phones: dict[str, int], term: str) -> None:
    assert _shown(_page(logged_in, tab="all", q=term)) == {PLAIN}


def test_search_by_person_name(logged_in: Panel, phones: dict[str, int]) -> None:
    assert _shown(_page(logged_in, tab="all", q="carla")) == {CONFLICT}


def test_search_inside_the_review_tab(logged_in: Panel, phones: dict[str, int]) -> None:
    assert _shown(_page(logged_in, tab="review", q="berta")) == {REVIEW_2}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"status": "verified"}, {VERIFIED}),
        ({"status": "review"}, {REVIEW, REVIEW_2}),
        ({"status": "conflict"}, {CONFLICT}),
        ({"status": "unverified"}, {PLAIN}),
        ({"source": "bot_verified"}, {VERIFIED}),
        ({"source": "consorplus", "status": "unverified"}, {PLAIN}),
    ],
)
def test_filters(
    logged_in: Panel, phones: dict[str, int], params: dict[str, str], expected: set[str]
) -> None:
    page = _page(logged_in, tab="all", **params)
    assert _shown(page) == expected
    assert '<details class="list-filters" open' in page


def test_filters_are_folded_by_default(logged_in: Panel, phones: dict[str, int]) -> None:
    page = _page(logged_in, tab="all")
    assert '<details class="list-filters" >' in page
    assert '<select class="form-select" id="status" name="status">' in page
    assert '<select class="form-select" id="source" name="source">' in page
    # Only those two filters.
    assert page.count('<select class="form-select"') == 2


def test_pages(logged_in: Panel, db_session: Session) -> None:
    for i in range(PAGE_SIZE + 3):
        f.person(db_session, f"Persona {i:03d}", phone=f"+54935155{i:05d}")
    db_session.commit()
    first = _page(logged_in, tab="all")
    assert "página 1 de 2" in first and first.count('<tr data-phone="') == PAGE_SIZE
    second = _page(logged_in, tab="all", page="2")
    assert "página 2 de 2" in second and second.count('<tr data-phone="') == 3


# --- Approve ----------------------------------------------------------------------------------


def test_approve_one(logged_in: Panel, phones: dict[str, int]) -> None:
    pk = phones[REVIEW]
    response = logged_in.client.post(
        f"/admin/phones/{pk}/approve", data={"back": "tab=review&q=bruno"}, follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin/phones?tab=review&q=bruno")

    phone = _phone(logged_in, pk)
    assert phone is not None and phone.verified and not phone.needs_review
    assert logged_in.admin_events("phone_approved") == [
        {"admin_user": ADMIN, "action": "phone_approved", "phone_id": pk, "phone_e164": REVIEW}
    ]
    assert REVIEW not in _page(logged_in, tab="review")


def test_approve_ignores_a_phone_not_to_review(logged_in: Panel, phones: dict[str, int]) -> None:
    pk = phones[PLAIN]
    logged_in.client.post(f"/admin/phones/{pk}/approve")
    phone = _phone(logged_in, pk)
    assert phone is not None and not phone.verified
    assert logged_in.admin_events() == []


def test_approve_selected(logged_in: Panel, phones: dict[str, int]) -> None:
    response = logged_in.client.post(
        "/admin/phones/approve",
        data={"pk": [str(phones[REVIEW]), str(phones[REVIEW_2]), str(phones[PLAIN])]},
        follow_redirects=False,
    )
    assert response.status_code == 302
    for number in (REVIEW, REVIEW_2):
        phone = _phone(logged_in, phones[number])
        assert phone is not None and phone.verified and not phone.needs_review
    plain = _phone(logged_in, phones[PLAIN])
    assert plain is not None and not plain.verified
    assert len(logged_in.admin_events("phone_approved")) == 2
    assert "Teléfonos aprobados: 2." in _page(logged_in, tab="all")


def test_approve_selected_with_nothing_chosen(logged_in: Panel, phones: dict[str, int]) -> None:
    logged_in.client.post("/admin/phones/approve", data={})
    assert logged_in.admin_events() == []


# --- Unlink -----------------------------------------------------------------------------------


def test_unlink_deletes_and_audits(logged_in: Panel, phones: dict[str, int]) -> None:
    pk = phones[VERIFIED]
    response = logged_in.client.post(
        f"/admin/phones/{pk}/unlink", data={"back": "tab=all"}, follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin/phones?tab=all")

    assert _phone(logged_in, pk) is None
    [event] = logged_in.admin_events("phone_unlinked")
    assert event["admin_user"] == ADMIN
    assert event["phone_id"] == pk and event["phone_e164"] == VERIFIED
    assert event["source"] == "bot_verified" and event["verified"] is True
    assert isinstance(event["person_id"], int)
    page = _page(logged_in, tab="all")
    assert "Dario Verificado" not in page and phone(PLAIN) in page


def test_unlink_a_phone_to_review(logged_in: Panel, phones: dict[str, int]) -> None:
    logged_in.client.post(f"/admin/phones/{phones[REVIEW]}/unlink")
    assert _phone(logged_in, phones[REVIEW]) is None
    assert len(logged_in.admin_events("phone_unlinked")) == 1


def test_unlink_missing_phone_does_nothing(logged_in: Panel, phones: dict[str, int]) -> None:
    logged_in.client.post("/admin/phones/999999/unlink")
    assert logged_in.admin_events() == []


@pytest.mark.parametrize(
    "back", ["https://otro-sitio.example/", "//otro-sitio.example/x", "/admin/users"]
)
def test_back_never_leaves_the_list(logged_in: Panel, phones: dict[str, int], back: str) -> None:
    response = logged_in.client.post(
        f"/admin/phones/{phones[PLAIN]}/unlink", data={"back": back}, follow_redirects=False
    )
    assert response.headers["location"].endswith("/admin/phones")


# --- Operators, login and the old pages -------------------------------------------------------


def test_an_operator_approves_and_unlinks(operator: Panel, phones: dict[str, int]) -> None:
    assert _page(operator, tab="all")
    operator.client.post(f"/admin/phones/{phones[REVIEW]}/approve")
    operator.client.post("/admin/phones/approve", data={"pk": str(phones[REVIEW_2])})
    operator.client.post(f"/admin/phones/{phones[PLAIN]}/unlink")
    actions = [(e["action"], e["admin_user"]) for e in operator.admin_events()]
    assert actions == [
        ("phone_approved", OPERATOR),
        ("phone_approved", OPERATOR),
        ("phone_unlinked", OPERATOR),
    ]


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/admin/phones"),
        ("post", "/admin/phones/{review}/approve"),
        ("post", "/admin/phones/approve"),
        ("post", "/admin/phones/{plain}/unlink"),
    ],
)
def test_needs_login(panel: Panel, phones: dict[str, int], method: str, url: str) -> None:
    url = url.format(review=phones[REVIEW], plain=phones[PLAIN])
    data = {"pk": str(phones[REVIEW])} if method == "post" else None
    kwargs = {"data": data} if data else {}
    response = getattr(panel.client, method)(url, follow_redirects=False, **kwargs)
    assert response.status_code == 302 and "/admin/login" in response.headers["location"]
    review = _phone(panel, phones[REVIEW])
    assert review is not None and review.needs_review
    assert _phone(panel, phones[PLAIN]) is not None


@pytest.mark.parametrize(
    "url",
    [
        "/admin/phone/list",
        "/admin/phones/list",
        "/admin/phones/create",
        "/admin/phone/action/approve?pks=1",
        "/admin/phones/action/unlink?pks=1",
    ],
)
def test_the_old_sqladmin_pages_are_gone(logged_in: Panel, url: str) -> None:
    assert logged_in.client.get(url).status_code == 404
