"""«Pendientes» and the panel's numbers (app.admin.pending): each card's number, «Mías sin
responder», the admin's row, the one request every page makes, the home page. Invented data
only."""

import html
import re
from datetime import time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.admin import pending
from app.claims.service import Reporter, close_claim, create_claim
from app.db.models import (
    Amenity,
    AmenitySlot,
    ClaimActor,
    ClaimSource,
    ClaimStatus,
    Reservation,
    ReservationSource,
    ReservationStatus,
    SyncJob,
    SyncKind,
    SyncRun,
    SyncStatus,
    VerificationRequest,
    VerificationRequestStatus,
    WaAuthor,
    WaConversationStatus,
)
from tests.admin.conftest import (
    NOW,
    OPERATOR,
    OTHER_OPERATOR,
    USER_PASSWORD,
    Panel,
    make_user,
)
from tests.admin.wa_data import conversation, message
from tests.bot import factories as f
from tests.claims import factories as cf

WAITING = WaConversationStatus.WAITING_HUMAN
HUMAN = WaConversationStatus.HUMAN
TODAY = NOW.date()  # Wednesday 30/09/2026


def _numbers(panel: Panel, **params: Any) -> dict[str, Any]:
    response = panel.client.get("/admin/pending/numbers", params=params)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _cards(page: str) -> dict[str, str]:
    """Each card -> its number, or «Al día»."""
    found = re.findall(
        r'data-card="(\w+)">.*?<span class="(?:pending-number[^"]*|pending-done)">([^<]+)</span>',
        page,
        re.S,
    )
    return {key: html.unescape(value) for key, value in found}


def _text(page: str) -> str:
    """What a person reads: no tags, spaces collapsed."""
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))).replace(" :", ":")


# --- Each card's number ---------------------------------------------------------------------------


def _claims(s: Session) -> None:
    """Open: one urgent, one with an alert, one with neither; closed: an urgent one."""
    building = f.building(s, "001 TORRE INVENTADA")
    lift = cf.category(s, "Ascensor inventado", list_title="Ascensor")
    lift.urgent = True
    damp = cf.category(s, "Humedad inventada", list_title="Humedad")
    gate = cf.category(s, "Portón inventado", list_title="Portón")
    for c in (lift, damp, gate):
        cf.assign(s, building.id, c)

    def claim(category: Any, phone: str) -> Any:
        return create_claim(
            s,
            building_id=building.id,
            category_id=category.id,
            unit_id=None,
            description="No anda",
            reporter=Reporter(name="Vecina Inventada", phone_e164=phone, unit_id=None),
            source=ClaimSource.BOT,
            now=NOW - timedelta(hours=1),
        ).claim

    # Closed first: a new one of the same problem would join an open one.
    closed = create_claim(
        s,
        building_id=building.id,
        category_id=lift.id,
        unit_id=None,
        description="Otra vez",
        reporter=Reporter(name="Otra Inventada", phone_e164="+5493515550304", unit_id=None),
        source=ClaimSource.PANEL,
        now=NOW - timedelta(days=3),
    ).claim
    close_claim(s, closed, ClaimStatus.SOLVED, "Listo", actor=ClaimActor.BOT)
    claim(lift, "+5493515550301")
    with_alert = claim(damp, "+5493515550302")
    with_alert.attention = "no_ack"
    claim(gate, "+5493515550303")
    s.commit()


def _reservations(s: Session) -> None:
    building = f.building(s, "005 EDIFICIO FICTICIO")
    unit = f.unit(s, building, "01-A")
    amenity = Amenity(building_id=building.id)
    slots = [AmenitySlot(weekday=d, start_time=time(14), end_time=time(18)) for d in range(7)]
    late = AmenitySlot(weekday=TODAY.weekday(), start_time=time(19), end_time=time(23))
    amenity.slots = [*slots, late]
    s.add(amenity)
    s.flush()

    def book(slot: AmenitySlot, days: int, status: ReservationStatus) -> None:
        s.add(
            Reservation(
                amenity_id=amenity.id,
                slot_id=slot.id,
                date=TODAY + timedelta(days=days),
                unit_id=unit.id,
                source=ReservationSource.PANEL,
                status=status,
            )
        )

    book(slots[TODAY.weekday()], 0, ReservationStatus.CONFIRMED)
    book(late, 0, ReservationStatus.CONFIRMED)
    book(slots[(TODAY.weekday() + 1) % 7], 1, ReservationStatus.CONFIRMED)
    book(slots[(TODAY.weekday() + 2) % 7], 2, ReservationStatus.CONFIRMED)  # day after tomorrow
    book(slots[(TODAY.weekday() + 3) % 7], 3, ReservationStatus.CANCELLED)
    s.commit()


@pytest.fixture
def busy(db_session: Session) -> None:
    s = db_session
    conversation(s, "5493515550201", status=WAITING, handed_off_at=NOW - timedelta(minutes=25))
    conversation(
        s,
        "5493515550202",
        status=WAITING,
        handed_off_at=NOW - timedelta(minutes=5),
        handoff_priority="urgent",
    )
    conversation(s, "5493515550203")  # with the bot
    _claims(s)
    f.person(s, "Persona Inventada", phone="+5493515550211", needs_review=True)
    f.person(s, "Otra Inventada", phone="+5493515550212", needs_review=True)
    f.person(s, "Verificada Inventada", phone="+5493515550213")
    unit = f.unit(s, f.building(s, "009 TORRE FICTICIA"), "02-B")
    for days, status in ((5, "pending"), (2, "pending"), (9, "approved")):
        s.add(
            VerificationRequest(
                phone_e164="+5493515550221",
                unit_id=unit.id,
                claimed_name="Dueña Inventada",
                status=VerificationRequestStatus(status),
                created_at=NOW - timedelta(days=days),
            )
        )
    _reservations(s)
    s.commit()


def test_each_card_counts_what_waits(logged_in: Panel, busy: None) -> None:
    data = _numbers(logged_in)

    assert data["counts"] == {
        "waiting": 2,
        "waiting_urgent": 1,
        "mine": 0,
        "claims": 2,  # the urgent one and the one with an alert
        "claims_urgent": 1,
        "phones": 2,
        "verifications": 2,
    }
    assert data["title_count"] == 3  # waiting + urgent claims
    page = logged_in.client.get("/admin/pending").text
    assert _cards(page) == {
        "waiting": "2",
        "mine": "Al día",
        "claims": "2",
        "phones": "2",
        "verifications": "2",
        "sum": "3",  # today 2, tomorrow 1 (not the day after nor the cancelled one)
    }
    text = _text(page)
    assert "1 urgente · la más vieja espera hace 25 min" in text
    assert "la más vieja es del viernes 25/09" in text
    assert "Hoy: EDIFICIO FICTICIO (2)" in text and "Mañana: EDIFICIO FICTICIO (1)" in text
    assert "Todo al día" not in text
    # Each button goes to its list, already filtered.
    for href in (
        "/admin/conversations?tab=waiting",
        "/admin/conversations?tab=mine",
        "/admin/claims?tab=open&amp;attention=yes",
        "/admin/phones?tab=review",
        "/admin/verifications",
        "/admin/amenities",
    ):
        assert f'href="https://testserver{href}"' in page or f'href="{href}"' in page, href
    assert "pending-card-urgent" in page


def test_the_cards_follow_the_numbers(logged_in: Panel, busy: None) -> None:
    """Only «Pendientes» asks for the cards, redrawn with the same numbers."""
    assert "cards_html" not in _numbers(logged_in)
    cards = _numbers(logged_in, cards="1")["cards_html"]
    assert _cards(cards)["waiting"] == "2"


def test_all_clear(operator: Panel) -> None:
    page = operator.client.get("/admin/pending").text

    assert "Todo al día" in page
    cards = _cards(page)
    assert {cards[k] for k in ("waiting", "mine", "claims", "phones", "verifications")} == {
        "Al día"
    }
    assert cards["sum"] == "0" and "No hay reservas del SUM para hoy ni para mañana." in page
    assert "Buen día, Marta · miércoles 30/09" in _text(page)


def test_the_sum_alone_is_not_something_waiting(operator: Panel, db_session: Session) -> None:
    _reservations(db_session)
    assert "Todo al día" in operator.client.get("/admin/pending").text


# --- «Mías sin responder» -------------------------------------------------------------------------


def test_mine_unanswered_only_mine_with_the_contact_last(operator: Panel) -> None:
    s = operator.session
    make_user(s, OTHER_OPERATOR)
    asked = conversation(s, "5493515550231", status=HUMAN, assigned_to=OPERATOR)
    last = message(s, asked, "¿y?", at=NOW - timedelta(minutes=3))
    noted = conversation(s, "5493515550232", status=HUMAN, assigned_to=OPERATOR)
    noted_last = message(s, noted, "hola", at=NOW - timedelta(minutes=9))
    message(  # an internal note is not an answer
        s,
        noted,
        "lo veo después",
        author=WaAuthor.OPERATOR,
        operator=OPERATOR,
        is_internal_note=True,
        message_type="note",
        at=NOW - timedelta(minutes=2),
    )
    answered = conversation(s, "5493515550233", status=HUMAN, assigned_to=OPERATOR)
    message(s, answered, "hola", at=NOW - timedelta(minutes=9))
    message(s, answered, "ya te ayudo", author=WaAuthor.OPERATOR, at=NOW - timedelta(minutes=8))
    others = conversation(s, "5493515550234", status=HUMAN, assigned_to=OTHER_OPERATOR)
    message(s, others, "hola", at=NOW - timedelta(minutes=9))
    waiting = conversation(s, "5493515550235", status=WAITING, handed_off_at=NOW)
    message(s, waiting, "hola", at=NOW - timedelta(minutes=9))
    s.commit()

    assert pending.mine_unanswered(s, OPERATOR) == [(asked.id, last.id), (noted.id, noted_last.id)]
    data = _numbers(operator)
    assert data["counts"]["mine"] == 2
    assert data["alerts"]["mine"] == [
        {"id": asked.id, "key": f"mine:{asked.id}:{last.id}"},
        {"id": noted.id, "key": f"mine:{noted.id}:{noted_last.id}"},
    ]
    assert _cards(operator.client.get("/admin/pending").text)["mine"] == "2"


# --- The admin's row ------------------------------------------------------------------------------


@pytest.fixture
def admin_things(db_session: Session) -> dict[str, int]:
    s = db_session
    building = f.building(s, "003 TORRE INVENTADA")
    gate = cf.category(s, "Portón inventado", list_title="Portón")
    lift = cf.category(s, "Ascensor inventado", list_title="Ascensor")
    damp = cf.category(s, "Humedad inventada", list_title="Humedad")
    cf.assign(s, building.id, gate, cf.provider(s, "Herrería Sin WhatsApp"))
    cf.assign(s, building.id, lift, cf.provider(s, "Ascensores Con WhatsApp", "+5493515550241"))
    cf.assign(s, building.id, damp, cf.provider(s, "Desactivado Inventado", active=False))
    cf.provider(s, "Sin Edificios Inventado")
    s.add_all(
        [
            SyncRun(
                kind=SyncKind.NIGHTLY,
                job=SyncJob.DEBT,
                status=SyncStatus.OK,
                started_at=NOW - timedelta(hours=8),
            ),
            SyncRun(
                kind=SyncKind.NIGHTLY,
                job=SyncJob.ROSTER,
                status=SyncStatus.PARTIAL,
                started_at=NOW - timedelta(hours=9),
            ),
        ]
    )
    this_week = conversation(
        s, "5493515550251", status=HUMAN, handoff_reason="no_answer",
        handed_off_at=NOW - timedelta(days=1),
    )  # fmt: skip
    conversation(
        s, "5493515550252", status=WaConversationStatus.RESOLVED, handoff_reason="no_answer",
        handed_off_at=NOW - timedelta(days=4),  # last week (Saturday)
    )  # fmt: skip
    conversation(s, "5493515550253", status=WAITING, handoff_reason="emergency", handed_off_at=NOW)
    s.commit()
    return {"this_week": this_week.id}


def test_the_admin_row(logged_in: Panel, admin_things: dict[str, int]) -> None:
    assert _numbers(logged_in)["admin"] == {
        "sync": "Con errores (hoy 02:00)",
        "sync_problem": True,
        "providers_without_whatsapp": 1,
        "no_answer_week": 1,
    }
    page = logged_in.client.get("/admin/pending").text
    row = _text(page.split('id="pending-admin"', 1)[1])
    assert "Última sincronización: Con errores (hoy 02:00)" in row
    assert "Proveedores sin WhatsApp con edificios: 1" in row
    assert "Preguntas que el bot no supo responder esta semana: 1" in row
    assert "/admin/sync-run/list" in page and "/admin/provider/list" in page

    # Its link: Conversaciones with only those.
    listed = logged_in.client.get("/admin/conversations?reason=no_answer").text
    assert re.findall(r"/admin/conversations/(\d+)\?", listed) == [str(admin_things["this_week"])]
    assert "Que el bot no supo responder esta semana" in listed and "Quitar filtro" in listed


def test_no_sync_last_night_is_a_problem(logged_in: Panel) -> None:
    admin = _numbers(logged_in)["admin"]
    assert admin["sync"] == "No hubo sincronización anoche" and admin["sync_problem"] is True


def test_the_operator_never_gets_the_admin_row(
    operator: Panel, admin_things: dict[str, int]
) -> None:
    assert "admin" not in _numbers(operator)
    page = operator.client.get("/admin/pending").text
    assert 'id="pending-admin"' not in page and "sincronización" not in page.lower()


def test_the_numbers_carry_no_personal_data(logged_in: Panel, busy: None) -> None:
    raw = logged_in.client.get("/admin/pending/numbers").text
    for personal in ("Inventada", "555", "01-A", "02-B", "Vecina"):
        assert personal not in raw, personal


# --- Every page asks for the numbers --------------------------------------------------------------

OPERATOR_PAGES = [
    "/admin/pending",
    "/admin/conversations",
    "/admin/claims",
    "/admin/claims/new",
    "/admin/phones",
    "/admin/verifications",
    "/admin/amenities",
    "/admin/guide",
]
ADMIN_PAGES = [
    "/admin/building/list",
    "/admin/provider/list",
    "/admin/building-claims",
    "/admin/users",
    "/admin/metrics",
    "/admin/sync-run/list",
    "/admin/quick-reply/list",
]


def _has_the_bar(page: str) -> None:
    assert 'data-pending-url="https://testserver/admin/pending/numbers"' in page
    assert page.count('id="enable-alerts"') == 1
    assert 'id="alerts-off"' in page and 'id="session-gone"' in page
    for key in ("waiting", "claims", "phones", "verifications"):
        assert f'data-counter="{key}"' in page, key


@pytest.mark.parametrize("url", OPERATOR_PAGES)
def test_every_operator_page_has_the_numbers(operator: Panel, url: str) -> None:
    response = operator.client.get(url)
    assert response.status_code == 200
    _has_the_bar(response.text)
    assert "counts" in _numbers(operator)


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_every_admin_page_has_the_numbers(logged_in: Panel, url: str) -> None:
    response = logged_in.client.get(url)
    assert response.status_code == 200
    _has_the_bar(response.text)


def test_the_menu_counters_as_the_page_is_drawn(operator: Panel, busy: None) -> None:
    page = operator.client.get("/admin/phones").text

    def badge(key: str) -> str:
        found = re.search(rf'<span class="([^"]*)" data-counter="{key}"[^>]*>(\d+)</span>', page)
        assert found, key
        return f"{found.group(2)} {'urgent' if 'menu-badge-urgent' in found.group(1) else '-'}"

    assert badge("waiting") == "2 urgent"
    assert badge("claims") == "2 urgent"
    assert badge("phones") == "2 -"
    assert badge("verifications") == "2 -"
    assert 'data-title-count="3"' in page


def test_the_old_polls_are_gone(operator: Panel) -> None:
    assert operator.client.get("/admin/claims/poll").status_code == 404
    page = operator.client.get("/admin/claims").text
    assert "data-urgent-poll" not in page


# --- Without a session ----------------------------------------------------------------------------


def test_without_login_the_numbers_go_to_the_login(panel: Panel) -> None:
    response = panel.client.get("/admin/pending/numbers", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert response.headers["location"].endswith("/admin/login")
    followed = panel.client.get("/admin/pending/numbers")
    assert "json" not in followed.headers["content-type"]
    assert followed.url.path == "/admin/login"  # what panel.js takes as a lost session


def test_a_server_error_is_not_the_login(operator: Panel, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 500 never looks like a lost session (panel.js retries instead of reloading)."""

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("se cayó la base inventada")

    monkeypatch.setattr(pending, "pending_numbers", broken)
    client = TestClient(
        operator.client.app,
        base_url="https://testserver",
        raise_server_exceptions=False,
        cookies=operator.client.cookies,
    )
    response = client.get("/admin/pending/numbers", follow_redirects=False)
    assert response.status_code == 500
    assert "location" not in response.headers


# --- The first page -------------------------------------------------------------------------------


def test_the_home_is_pending(logged_in: Panel) -> None:
    response = logged_in.client.get("/admin/", follow_redirects=False)
    assert response.headers["location"].endswith("/admin/pending")
    page = logged_in.client.get("/admin/").text
    assert '<h2 class="page-title">Pendientes</h2>' in page


def test_the_login_opens_pending_and_the_tour_still_waits_in_conversations(
    panel: Panel,
) -> None:
    make_user(panel.session, OPERATOR, display_name="Marta Inventada", tour_seen=False)
    panel.session.commit()
    login = panel.login(OPERATOR, USER_PASSWORD)
    assert login.status_code == 302

    landed = panel.client.get(login.headers["location"])
    assert landed.url.path == "/admin/pending"
    assert "Buen día, Marta" in landed.text

    first = panel.client.get("/admin/conversations", follow_redirects=False)
    assert "/admin/conversations/example" in first.headers["location"]
