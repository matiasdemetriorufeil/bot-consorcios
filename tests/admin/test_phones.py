"""Phones to review (area code assumed on import): approve or delete. Invented data."""

import pytest
from sqlalchemy.orm import Session

from app.db.models import Phone
from tests.admin.conftest import ADMIN, Panel
from tests.bot import factories as f

REVIEW = "+5493515550301"
FINE = "+5493515550302"


@pytest.fixture
def phones(db_session: Session) -> tuple[int, int]:
    f.person(db_session, "Persona Revisar", phone=REVIEW, needs_review=True)
    f.person(db_session, "Persona Normal", phone=FINE)
    db_session.commit()
    ids = {p.e164: p.id for p in db_session.query(Phone).filter(Phone.e164.in_([REVIEW, FINE]))}
    return ids[REVIEW], ids[FINE]


def test_list_shows_only_phones_to_review(logged_in: Panel, phones: tuple[int, int]) -> None:
    page = logged_in.client.get("/admin/phone/list").text
    assert REVIEW in page and "Persona Revisar" in page
    assert FINE not in page


def test_approve_phone(logged_in: Panel, phones: tuple[int, int]) -> None:
    review_id, _ = phones
    response = logged_in.client.get(
        f"/admin/phone/action/approve?pks={review_id}", follow_redirects=False
    )
    assert response.status_code == 302

    phone = logged_in.session.get(Phone, review_id)
    logged_in.session.refresh(phone)
    assert phone.verified and not phone.needs_review
    assert logged_in.admin_events("phone_approved") == [
        {
            "admin_user": ADMIN,
            "action": "phone_approved",
            "phone_id": review_id,
            "phone_e164": REVIEW,
        }
    ]
    assert REVIEW not in logged_in.client.get("/admin/phone/list").text


def test_approve_ignores_phones_not_to_review(logged_in: Panel, phones: tuple[int, int]) -> None:
    _, fine_id = phones
    logged_in.client.get(f"/admin/phone/action/approve?pks={fine_id}")
    phone = logged_in.session.get(Phone, fine_id)
    logged_in.session.refresh(phone)
    assert not phone.verified
    assert logged_in.admin_events() == []


def test_delete_phone(logged_in: Panel, phones: tuple[int, int]) -> None:
    review_id, _ = phones
    response = logged_in.client.delete(f"/admin/phone/delete?pks={review_id}")
    assert response.status_code == 200

    logged_in.session.expire_all()
    assert logged_in.session.get(Phone, review_id) is None
    [event] = logged_in.admin_events("phone_deleted")
    assert event["admin_user"] == ADMIN and event["phone_id"] == review_id


def test_cannot_delete_a_phone_not_to_review(logged_in: Panel, phones: tuple[int, int]) -> None:
    _, fine_id = phones
    logged_in.client.delete(f"/admin/phone/delete?pks={fine_id}")
    logged_in.session.expire_all()
    assert logged_in.session.get(Phone, fine_id) is not None
    assert logged_in.admin_events() == []


def test_actions_need_login(panel: Panel, phones: tuple[int, int]) -> None:
    review_id, _ = phones
    response = panel.client.get(
        f"/admin/phone/action/approve?pks={review_id}", follow_redirects=False
    )
    assert response.status_code == 302
    phone = panel.session.get(Phone, review_id)
    panel.session.refresh(phone)
    assert phone.needs_review
