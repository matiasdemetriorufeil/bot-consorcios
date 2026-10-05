"""Operator verifications (units without owner email): approve choosing the owner, or
reject. Invented data only."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import identity
from app.db.models import (
    BotEvent,
    DataSource,
    Phone,
    VerificationCode,
    VerificationRequest,
    VerificationRequestStatus,
)
from tests.admin.conftest import ADMIN, Panel
from tests.bot import factories as f

PHONE = "+5493515550401"
CODE_HASH = "sal0inventada$" + "ab" * 32


@dataclass
class Pending:
    request_id: int
    owner_id: int
    other_owner_id: int
    stranger_id: int


@pytest.fixture
def pending(db_session: Session) -> Pending:
    building = f.building(db_session, "077 TORRE INVENTADA")
    unit = f.unit(db_session, building, "03-B")
    owner = f.person(db_session, "Dueña Inventada")
    other = f.person(db_session, "Codueño Inventado")
    stranger = f.person(db_session, "Persona Ajena")
    f.link(db_session, unit, owner)
    f.link(db_session, unit, other)
    created = identity.request_operator_verification(db_session, PHONE, unit.id, "Dueña Inventada")
    # A code of the same phone: its hash must never show up in the panel.
    now = datetime.now(UTC)
    db_session.add(
        VerificationCode(
            verification_id="v" * 32,
            phone_e164=PHONE,
            unit_id=unit.id,
            person_id=owner.id,
            code_hash=CODE_HASH,
            created_at=now,
            expires_at=now + timedelta(minutes=15),
        )
    )
    db_session.commit()
    assert created.request_id is not None
    return Pending(created.request_id, owner.id, other.id, stranger.id)


def _request(panel: Panel, request_id: int) -> VerificationRequest:
    panel.session.expire_all()
    request = panel.session.get(VerificationRequest, request_id)
    assert request is not None
    return request


def test_list_shows_unit_building_name_and_date(logged_in: Panel, pending: Pending) -> None:
    page = logged_in.client.get("/admin/verification-request/list").text
    for text in ("TORRE INVENTADA", "03-B", "Dueña Inventada", PHONE, "Nombre declarado", "Fecha"):
        assert text in page
    assert CODE_HASH not in page and "sal0inventada" not in page


def test_approve_page_lists_the_owners(logged_in: Panel, pending: Pending) -> None:
    response = logged_in.client.get(
        f"/admin/verification-request/action/approve?pks={pending.request_id}"
    )
    assert response.status_code == 200
    assert "Codueño Inventado" in response.text and "Dueña Inventada" in response.text
    assert "Persona Ajena" not in response.text
    assert "sal0inventada" not in response.text


def test_approve_links_the_phone_as_manual(logged_in: Panel, pending: Pending) -> None:
    response = logged_in.client.post(
        f"/admin/verification-request/approve/{pending.request_id}",
        data={"person_id": str(pending.other_owner_id)},
        follow_redirects=False,
    )
    assert response.status_code == 302

    request = _request(logged_in, pending.request_id)
    assert request.status == VerificationRequestStatus.APPROVED
    assert request.person_id == pending.other_owner_id
    assert request.resolved_by == ADMIN
    phone = logged_in.session.scalar(select(Phone).where(Phone.e164 == PHONE))
    assert phone is not None
    assert phone.person_id == pending.other_owner_id
    assert phone.source == DataSource.MANUAL and phone.verified
    [event] = logged_in.admin_events("verification_approved")
    assert event == {
        "admin_user": ADMIN,
        "action": "verification_approved",
        "phone_e164": PHONE,
        "request_id": pending.request_id,
        "unit_id": request.unit_id,
        "person_id": pending.other_owner_id,
    }
    resolved = logged_in.session.scalar(
        select(BotEvent).where(BotEvent.event_type == "operator_verification_resolved")
    )
    assert resolved is not None and resolved.payload["resolved_by"] == ADMIN
    assert "Dueña Inventada" not in logged_in.client.get("/admin/verification-request/list").text


def test_approve_with_a_non_owner_changes_nothing(logged_in: Panel, pending: Pending) -> None:
    response = logged_in.client.post(
        f"/admin/verification-request/approve/{pending.request_id}",
        data={"person_id": str(pending.stranger_id)},
    )
    assert response.status_code == 400
    assert "no es propietaria" in response.text
    assert _request(logged_in, pending.request_id).status == VerificationRequestStatus.PENDING
    assert logged_in.session.scalar(select(Phone).where(Phone.e164 == PHONE)) is None
    assert logged_in.admin_events() == []


def test_approve_twice_fails(logged_in: Panel, pending: Pending) -> None:
    url = f"/admin/verification-request/approve/{pending.request_id}"
    logged_in.client.post(url, data={"person_id": str(pending.owner_id)})
    response = logged_in.client.post(url, data={"person_id": str(pending.other_owner_id)})
    assert response.status_code == 400
    assert _request(logged_in, pending.request_id).person_id == pending.owner_id


def test_reject(logged_in: Panel, pending: Pending) -> None:
    response = logged_in.client.get(
        f"/admin/verification-request/action/reject?pks={pending.request_id}",
        follow_redirects=False,
    )
    assert response.status_code == 302

    request = _request(logged_in, pending.request_id)
    assert request.status == VerificationRequestStatus.REJECTED
    assert request.resolved_by == ADMIN
    assert logged_in.session.scalar(select(Phone).where(Phone.e164 == PHONE)) is None
    [event] = logged_in.admin_events("verification_rejected")
    assert event["admin_user"] == ADMIN and event["request_id"] == pending.request_id


def test_approve_page_needs_login(panel: Panel, pending: Pending) -> None:
    response = panel.client.post(
        f"/admin/verification-request/approve/{pending.request_id}",
        data={"person_id": str(pending.owner_id)},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert _request(panel, pending.request_id).status == VerificationRequestStatus.PENDING
