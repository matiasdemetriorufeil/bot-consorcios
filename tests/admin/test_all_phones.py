"""All phones: search, filters and "Desvincular" (audited delete). Invented data."""

import pytest
from sqlalchemy.orm import Session

from app.db.models import DataSource, PersonRole, Phone
from tests.admin.conftest import ADMIN, Panel
from tests.bot import factories as f

PLAIN = "+5493515550401"
REVIEW = "+5493515550402"
CONFLICT = "+5493515550403"
VERIFIED = "+5493515550404"


@pytest.fixture
def phones(db_session: Session) -> dict[str, int]:
    building = f.building(db_session, "098 EDIFICIO TELEFONOS")
    unit_a = f.unit(db_session, building, "1A")
    unit_b = f.unit(db_session, building, "2B")
    plain = f.person(db_session, "Ana Inventada", phone=PLAIN)
    f.link(db_session, unit_a, plain)
    f.link(db_session, unit_b, plain, PersonRole.TENANT)
    f.person(db_session, "Bruno Revisar", phone=REVIEW, needs_review=True)
    f.person(db_session, "Carla Conflicto", phone=CONFLICT, conflict=True)
    verified = f.person(db_session, "Dario Verificado")
    db_session.add(
        Phone(person_id=verified.id, e164=VERIFIED, source=DataSource.BOT_VERIFIED, verified=True)
    )
    db_session.commit()
    rows = db_session.query(Phone).filter(Phone.e164.in_([PLAIN, REVIEW, CONFLICT, VERIFIED]))
    return {p.e164: p.id for p in rows}


def _list(panel: Panel, **params: str) -> str:
    response = panel.client.get("/admin/phones/list", params=params)
    assert response.status_code == 200
    return response.text


def test_list_shows_every_phone_with_person_and_units(
    logged_in: Panel, phones: dict[str, int]
) -> None:
    page = _list(logged_in)
    for number in (PLAIN, REVIEW, CONFLICT, VERIFIED):
        assert number in page
    assert "Ana Inventada" in page
    assert "1A (propietario)" in page and "2B (inquilino)" in page
    assert "Bot (código por email)" in page


def test_details_page_renders(logged_in: Panel, phones: dict[str, int]) -> None:
    response = logged_in.client.get(f"/admin/phones/details/{phones[PLAIN]}")
    assert response.status_code == 200
    assert PLAIN in response.text and "1A (propietario)" in response.text


@pytest.mark.parametrize("term", ["0351 555-0401", "3515550401", "+54 9 351 555 0401"])
def test_search_by_number(logged_in: Panel, phones: dict[str, int], term: str) -> None:
    page = _list(logged_in, search=term)
    assert PLAIN in page
    assert REVIEW not in page and VERIFIED not in page


def test_search_by_person_name(logged_in: Panel, phones: dict[str, int]) -> None:
    page = _list(logged_in, search="carla")
    assert CONFLICT in page
    assert PLAIN not in page


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"verified": "true"}, {VERIFIED}),
        ({"verified": "false"}, {PLAIN, REVIEW, CONFLICT}),
        ({"needs_review": "true"}, {REVIEW}),
        ({"conflict": "true"}, {CONFLICT}),
        ({"source": "bot_verified"}, {VERIFIED}),
        ({"source": "consorplus", "conflict": "false"}, {PLAIN, REVIEW}),
    ],
)
def test_filters(
    logged_in: Panel, phones: dict[str, int], params: dict[str, str], expected: set[str]
) -> None:
    page = _list(logged_in, **params)
    shown = {n for n in (PLAIN, REVIEW, CONFLICT, VERIFIED) if n in page}
    assert shown == expected


def test_no_create_edit_or_plain_delete(logged_in: Panel, phones: dict[str, int]) -> None:
    pk = phones[PLAIN]
    assert logged_in.client.get("/admin/phones/create").status_code == 403
    assert logged_in.client.get(f"/admin/phones/edit/{pk}").status_code == 403
    logged_in.client.delete(f"/admin/phones/delete?pks={pk}")
    logged_in.session.expire_all()
    assert logged_in.session.get(Phone, pk) is not None
    assert logged_in.admin_events() == []


def test_unlink_deletes_and_audits(logged_in: Panel, phones: dict[str, int]) -> None:
    pk = phones[VERIFIED]
    response = logged_in.client.get(f"/admin/phones/action/unlink?pks={pk}", follow_redirects=False)
    assert response.status_code == 302

    logged_in.session.expire_all()
    assert logged_in.session.get(Phone, pk) is None
    [event] = logged_in.admin_events("phone_unlinked")
    assert event["admin_user"] == ADMIN
    assert event["phone_id"] == pk and event["phone_e164"] == VERIFIED
    assert event["source"] == "bot_verified" and event["verified"] is True
    assert isinstance(event["person_id"], int)
    # The person stays: only the number is unlinked.
    assert "Dario Verificado" not in _list(logged_in)
    assert PLAIN in _list(logged_in)


def test_unlink_several(logged_in: Panel, phones: dict[str, int]) -> None:
    pks = f"{phones[PLAIN]},{phones[CONFLICT]}"
    logged_in.client.get(f"/admin/phones/action/unlink?pks={pks}")
    logged_in.session.expire_all()
    assert logged_in.session.get(Phone, phones[PLAIN]) is None
    assert logged_in.session.get(Phone, phones[CONFLICT]) is None
    assert logged_in.session.get(Phone, phones[REVIEW]) is not None
    assert len(logged_in.admin_events("phone_unlinked")) == 2


def test_unlink_missing_phone_does_nothing(logged_in: Panel, phones: dict[str, int]) -> None:
    logged_in.client.get("/admin/phones/action/unlink?pks=999999")
    assert logged_in.admin_events() == []


def test_unlink_needs_login(panel: Panel, phones: dict[str, int]) -> None:
    pk = phones[PLAIN]
    response = panel.client.get(f"/admin/phones/action/unlink?pks={pk}", follow_redirects=False)
    assert response.status_code == 302
    panel.session.expire_all()
    assert panel.session.get(Phone, pk) is not None
