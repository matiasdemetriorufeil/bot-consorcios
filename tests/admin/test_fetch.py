"""Actions without reloading (app.admin.fetch): asked by panel.js (X-Panel-Fetch: 1) the same
endpoint answers JSON with what changed; without the header (no JavaScript) it answers as
always, with a Flash and a redirect. Same checks, audit and permissions. Invented data only."""

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.claims.service import Reporter, create_claim
from app.db.models import (
    Claim,
    ClaimSource,
    ClaimStatus,
    Phone,
    VerificationRequest,
    VerificationRequestStatus,
)
from tests.admin.conftest import ADMIN, NOW, OPERATOR, USER_PASSWORD, Panel, make_user
from tests.bot import factories as f
from tests.claims import factories as cf

FETCH = {"X-Panel-Fetch": "1"}
REVIEW = "+5493515550702"
REVIEW_2 = "+5493515550703"
PLAIN = "+5493515550701"


@pytest.fixture
def phones(db_session: Session) -> dict[str, int]:
    f.person(db_session, "Ana Inventada", phone=PLAIN)
    f.person(db_session, "Bruno Revisar", phone=REVIEW, needs_review=True)
    f.person(db_session, "Berta Revisar", phone=REVIEW_2, needs_review=True)
    db_session.commit()
    rows = db_session.query(Phone).filter(Phone.e164.in_([PLAIN, REVIEW, REVIEW_2]))
    return {p.e164: p.id for p in rows}


def _post(panel: Panel, url: str, data: dict[str, Any] | None = None, fetch: bool = True) -> Any:
    return panel.client.post(
        url, data=data or {}, headers=FETCH if fetch else {}, follow_redirects=False
    )


# --- Teléfonos -------------------------------------------------------------------------------


def test_approve_in_review_removes_the_row(logged_in: Panel, phones: dict[str, int]) -> None:
    pk = phones[REVIEW]
    response = _post(logged_in, f"/admin/phones/{pk}/approve", {"back": "tab=review"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["ok"] is True and data["level"] == "success"
    assert data["message"] == "Teléfono aprobado: quedó verificado."
    assert data["remove"] == [f'[data-phone="{pk}"]'] and data["replace"] == {}
    assert data["texts"] == {"[data-review-count]": "1", "[data-phones-total]": "1"}
    assert len(logged_in.admin_events("phone_approved")) == 1


def test_approve_in_all_redraws_the_row(logged_in: Panel, phones: dict[str, int]) -> None:
    pk = phones[REVIEW]
    data = _post(logged_in, f"/admin/phones/{pk}/approve", {"back": "tab=all"}).json()
    assert data["remove"] == []
    [html] = data["replace"].values()
    assert f'<tr data-phone="{pk}">' in html and "state-ok" in html and "Verificado" in html
    assert "Aprobar</button>" not in html  # no longer to review


def test_approve_selected_and_unlink(logged_in: Panel, phones: dict[str, int]) -> None:
    data = _post(
        logged_in,
        "/admin/phones/approve",
        {"pk": [str(phones[REVIEW]), str(phones[REVIEW_2])], "back": "tab=review"},
    ).json()
    assert data["message"] == "Teléfonos aprobados: 2."
    assert sorted(data["remove"]) == sorted(
        f'[data-phone="{phones[n]}"]' for n in (REVIEW, REVIEW_2)
    )
    assert data["texts"]["[data-review-count]"] == "0"

    data = _post(logged_in, f"/admin/phones/{phones[PLAIN]}/unlink", {"back": "tab=all"}).json()
    assert data["ok"] and data["remove"] == [f'[data-phone="{phones[PLAIN]}"]']
    assert len(logged_in.admin_events("phone_unlinked")) == 1


def test_a_phone_no_longer_to_review_is_an_error(logged_in: Panel, phones: dict[str, int]) -> None:
    data = _post(logged_in, f"/admin/phones/{phones[PLAIN]}/approve", {"back": ""}).json()
    assert data["ok"] is False and data["level"] == "error"
    assert data["message"] == "No se aprobó: ese teléfono ya no estaba a revisar."
    assert logged_in.admin_events() == []


def test_without_javascript_it_redirects_as_always(
    logged_in: Panel, phones: dict[str, int]
) -> None:
    pk = phones[REVIEW]
    response = _post(logged_in, f"/admin/phones/{pk}/approve", {"back": "tab=review"}, fetch=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin/phones?tab=review")
    page = logged_in.client.get(response.headers["location"]).text
    assert "Teléfono aprobado: quedó verificado." in page and 'class="panel-toast' in page


def test_the_operator_too_and_never_without_login(panel: Panel, phones: dict[str, int]) -> None:
    anonymous = _post(panel, f"/admin/phones/{phones[REVIEW]}/approve")
    assert anonymous.status_code in (302, 303) and anonymous.headers["location"].endswith("/login")
    make_user(panel.session, OPERATOR)
    panel.session.commit()
    panel.login(OPERATOR, USER_PASSWORD)
    data = _post(panel, f"/admin/phones/{phones[REVIEW]}/approve", {"back": ""}).json()
    assert data["ok"] is True
    assert panel.admin_events("phone_approved")[0]["admin_user"] == OPERATOR


# --- Verificaciones --------------------------------------------------------------------------


@pytest.fixture
def verification(db_session: Session) -> int:
    unit = f.unit(db_session, f.building(db_session, "077 TORRE INVENTADA"), "03-B")
    row = VerificationRequest(
        phone_e164="+5493515550711", unit_id=unit.id, claimed_name="Dueña Inventada"
    )
    db_session.add(row)
    db_session.commit()
    return row.id


def test_reject_removes_the_row(logged_in: Panel, verification: int) -> None:
    data = _post(logged_in, f"/admin/verifications/{verification}/reject").json()
    assert data["ok"] and data["message"] == (
        "Verificación rechazada: el número no se registró. No se le pudo avisar: pasaron más "
        "de 24 h desde su último mensaje."
    )  # it never wrote: nothing is sent
    assert data["remove"] == [f'[data-verification="{verification}"]']
    assert data["texts"] == {"[data-pending-count]": "0"}
    row = logged_in.session.get(VerificationRequest, verification)
    logged_in.session.refresh(row)
    assert row.status == VerificationRequestStatus.REJECTED
    actions = [e["action"] for e in logged_in.admin_events()]
    assert actions == ["verification_rejected", "registration_notified"]
    again = _post(logged_in, f"/admin/verifications/{verification}/reject").json()
    assert again["ok"] is False and again["remove"] == []


def test_reject_without_javascript(logged_in: Panel, verification: int) -> None:
    response = _post(logged_in, f"/admin/verifications/{verification}/reject", fetch=False)
    assert response.status_code == 302 and response.headers["location"].endswith("/verifications")


# --- The claim's page ------------------------------------------------------------------------


@pytest.fixture
def claim(db_session: Session) -> Claim:
    building = f.building(db_session, "001 TORRE INVENTADA")
    damp = cf.category(db_session, "Humedad inventada", list_title="Humedad")
    cf.assign(db_session, building.id, damp)  # the studio attends it
    found = create_claim(
        db_session,
        building_id=building.id,
        category_id=damp.id,
        unit_id=None,
        description="Mancha en el pasillo",
        reporter=Reporter(name="Ana Inventada", phone_e164=None),
        source=ClaimSource.PANEL,
        now=NOW,
    ).claim
    db_session.commit()
    return found


def test_closing_redraws_the_claim(logged_in: Panel, claim: Claim) -> None:
    data = _post(logged_in, f"/admin/claims/{claim.id}/close", {"reason": "Se pintó"}).json()
    assert data["ok"] is True and data["message"].startswith("Reclamo cerrado como solucionado.")
    [(selector, html)] = data["replace"].items()
    assert selector == "#claim-page"
    assert '<div class="col-12" id="claim-page">' in html
    assert "Solucionado" in html and 'id="claim-actions"' not in html
    logged_in.session.expire_all()
    assert logged_in.session.get(Claim, claim.id).status == ClaimStatus.SOLVED
    assert len(logged_in.admin_events("claim_solved")) == 1


def test_an_empty_reason_is_an_error_and_changes_nothing(logged_in: Panel, claim: Claim) -> None:
    data = _post(logged_in, f"/admin/claims/{claim.id}/cancel", {"reason": "  "}).json()
    assert (
        data["ok"] is False and data["level"] == "error" and data["message"] == "Contá el motivo."
    )
    assert 'id="claim-actions"' in data["replace"]["#claim-page"]
    logged_in.session.expire_all()
    assert logged_in.session.get(Claim, claim.id).status == ClaimStatus.STUDIO


def test_a_note_and_the_claim_without_javascript(logged_in: Panel, claim: Claim) -> None:
    data = _post(logged_in, f"/admin/claims/{claim.id}/note", {"text": "Llamé al encargado"})
    assert data.json()["ok"] and "Llamé al encargado" in data.json()["replace"]["#claim-page"]
    response = _post(logged_in, f"/admin/claims/{claim.id}/note", {"text": "Otra"}, fetch=False)
    assert response.status_code == 302 and response.headers["location"].endswith(
        f"/claims/{claim.id}"
    )
    notes = logged_in.admin_events("claim_note_added")
    assert [e["admin_user"] for e in notes] == [ADMIN, ADMIN]
