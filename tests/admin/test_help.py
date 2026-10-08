"""The panel's help: a line under each title, empty pages that say what to do, and the
confirmations before what cannot be undone. Invented data only."""

import html
import re
from datetime import time

import pytest
from sqlalchemy.orm import Session
from starlette.routing import Mount

from app.admin import help
from app.db.models import Amenity, AmenitySlot, Reservation, ReservationSource, WaConversationStatus
from tests.admin.conftest import Panel
from tests.admin.wa_data import conversation
from tests.bot import factories as f

# Words an employee would need explained (the help is for them, not for whoever built it).
JARGON = [
    "característica", "sincroniz", "e.164", "needs_review", "ventana de 24", r"\bmeta\b",
    "bot_verified", "admin_action", "endpoint", "deriv", "plantilla", "consorplus_code",
]  # fmt: skip
# Routes of the panel that are not pages a person reads (JSON, redirects, files, actions).
NOT_PAGES = {
    "view-conversations-poll",
    "view-dev-chat-poll",
    "view-claims-poll",
    "view-wa-media",
}


def _all_texts() -> list[str]:
    texts = [*help.PAGE_HELP.values(), *help.EMPTY.values(), help.EMPTY_DEFAULT, help.TOUR_MISSING]
    texts += [t for item in help.CONFIRM.values() for t in (item["title"], item["text"])]
    for step in help.TOUR:
        texts += [step["title"], step["text"], step.get("missing", "")]
    return texts


@pytest.mark.parametrize("word", JARGON)
def test_the_help_needs_no_explanation(word: str) -> None:
    for text in _all_texts():
        assert not re.search(word, text, re.IGNORECASE), f"{word!r} in: {text}"


def test_every_page_of_the_panel_has_its_line(logged_in: Panel) -> None:
    [admin_mount] = [r for r in logged_in.client.app.routes if isinstance(r, Mount)]
    pages = {
        route.name
        for route in admin_mount.routes
        if route.name.startswith("view-")
        and "GET" in (getattr(route, "methods", None) or set())
        and route.name not in NOT_PAGES
    }
    assert pages, "no pages found"
    missing = sorted(p for p in pages if p not in help.PAGE_HELP)
    assert missing == [], f"pages without a help line in app.admin.help.PAGE_HELP: {missing}"


def _line(page: str) -> str:
    match = re.search(r'<p class="page-help mb-0">(.*?)</p>', page, re.S)
    assert match, "no help line"
    return html.unescape(match.group(1))


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("/admin/conversations", "view-conversations"),
        ("/admin/phones", "view-phones"),
        ("/admin/claims", "view-claims"),
        ("/admin/claims/new", "view-claim-new"),
        ("/admin/verifications", "view-verifications"),
        ("/admin/amenities", "view-amenities"),
        ("/admin/guide", "view-guide"),
        ("/admin/building/list", "list:building"),
        ("/admin/building-info/list", "list:building-info"),
        ("/admin/building-info/create", "create:building-info"),
        ("/admin/provider/list", "list:provider"),
        ("/admin/provider/create", "create:provider"),
        ("/admin/claim-category/list", "list:claim-category"),
        ("/admin/claim-category/create", "create:claim-category"),
        ("/admin/building-claims", "view-building-claims"),
        ("/admin/bot-settings/list", "edit:bot-settings"),
        ("/admin/wa-template/list", "list:wa-template"),
        ("/admin/wa-template/create", "list:wa-template"),
        ("/admin/quick-reply/list", "list:quick-reply"),
        ("/admin/sync-run/list", "list:sync-run"),
        ("/admin/users", "view-users"),
        ("/admin/metrics", "view-metrics"),
    ],
)
def test_the_line_under_the_title(logged_in: Panel, url: str, key: str) -> None:
    assert _line(logged_in.client.get(url).text) == help.PAGE_HELP[key]


def test_an_operator_sees_the_lines_too(operator: Panel) -> None:
    for url, key in (
        ("/admin/phones", "view-phones"),
        ("/admin/verifications", "view-verifications"),
    ):
        assert _line(operator.client.get(url).text) == help.PAGE_HELP[key]


# --- Empty pages -----------------------------------------------------------------------------


@pytest.mark.parametrize("tab", ["waiting", "mine", "bot", "resolved"])
def test_empty_conversation_tabs(operator: Panel, tab: str) -> None:
    page = html.unescape(operator.client.get("/admin/conversations", params={"tab": tab}).text)
    assert help.EMPTY[f"conversations:{tab}"] in page


def test_a_search_without_results(operator: Panel) -> None:
    conversation(operator.session)
    operator.session.commit()
    page = html.unescape(operator.client.get("/admin/conversations", params={"q": "zzz"}).text)
    assert help.EMPTY["conversations:search"] in page


def test_empty_phones_and_verifications(operator: Panel) -> None:
    review = html.unescape(operator.client.get("/admin/phones", params={"tab": "review"}).text)
    assert help.EMPTY["phones:review"] in review
    every = html.unescape(operator.client.get("/admin/phones", params={"tab": "all"}).text)
    assert help.EMPTY["phones:all"] in every
    search = operator.client.get("/admin/phones", params={"tab": "all", "q": "zzz"}).text
    assert help.EMPTY["phones:search"] in html.unescape(search)
    pending = html.unescape(operator.client.get("/admin/verifications").text)
    assert help.EMPTY["verifications"] in pending


def test_empty_sum_says_who_adds_it(operator: Panel) -> None:
    as_operator = html.unescape(operator.client.get("/admin/amenities").text)
    assert help.EMPTY["amenities:operator"] in as_operator


def test_empty_sum_for_an_admin(logged_in: Panel) -> None:
    page = html.unescape(logged_in.client.get("/admin/amenities").text)
    assert help.EMPTY["amenities:admin"] in page


def test_an_empty_list_of_sqladmin(logged_in: Panel) -> None:
    page = html.unescape(logged_in.client.get("/admin/quick-reply/list").text)
    assert help.EMPTY["list:quick-reply"] in page
    assert help.empty_for("algo-nuevo") == help.EMPTY_DEFAULT


# --- Confirmations -----------------------------------------------------------------------------


def _confirm(page: str, key: str) -> str:
    """The data-confirm-text of the form asking for confirmation `key`."""
    title = re.escape(html.escape(help.CONFIRM[key]["title"]))
    match = re.search(rf'data-confirm-text="([^"]*)"[^>]*data-confirm-title="{title}"', page)
    match = match or re.search(
        rf'data-confirm-title="{title}"[^>]*data-confirm-text="([^"]*)"', page
    )
    assert match, f"no confirmation {key}"
    return html.unescape(match.group(1))


def test_the_dialog_is_on_every_page(operator: Panel) -> None:
    for url in ("/admin/conversations", "/admin/phones", "/admin/amenities"):
        assert 'id="confirm-dialog"' in operator.client.get(url).text


def test_reject_says_nothing_reaches_the_person(operator: Panel, db_session: Session) -> None:
    from app.bot import identity

    building = f.building(db_session, "077 TORRE AYUDA")
    unit = f.unit(db_session, building, "03-B")
    f.link(db_session, unit, f.person(db_session, "Dueña Inventada"))
    identity.request_operator_verification(db_session, "+5493515550601", unit.id, "Dueña")
    db_session.commit()
    text = _confirm(operator.client.get("/admin/verifications").text, "reject")
    assert text.startswith("Dueña · TORRE AYUDA 03-B.")
    assert "A la persona no le llega ningún aviso" in text


def test_resolve_says_what_happens(operator: Panel) -> None:
    conv = conversation(operator.session, status=WaConversationStatus.WAITING_HUMAN)
    operator.session.commit()
    page = operator.client.get(f"/admin/conversations/{conv.id}").text
    assert "Si vuelve a escribir, la atiende el bot." in _confirm(page, "resolve")
    assert 'data-confirm-kind="primary"' in page


def test_sum_confirmations(logged_in: Panel, db_session: Session) -> None:
    building = f.building(db_session, "092 TORRE AYUDA")
    unit = f.unit(db_session, building, "01-A")
    amenity = Amenity(building_id=building.id)
    slot = AmenitySlot(weekday=4, start_time=time(14), end_time=time(18))
    amenity.slots = [slot]
    db_session.add(amenity)
    db_session.flush()
    from datetime import date

    reservation = Reservation(
        amenity_id=amenity.id,
        slot_id=slot.id,
        date=date(2030, 1, 4),
        unit_id=unit.id,
        source=ReservationSource.PANEL,
    )
    db_session.add(reservation)
    db_session.commit()

    page = logged_in.client.get(f"/admin/amenities/{amenity.id}/reservations/{reservation.id}")
    text = _confirm(page.text, "cancel_reservation")
    assert text.startswith("Unidad 01-A, Viernes 04/01/2030, 14:00 a 18:00")
    assert "A la unidad no le llega ningún aviso" in text
    config = logged_in.client.get(f"/admin/amenities/{amenity.id}/config").text
    assert _confirm(config, "remove_slot").startswith("Viernes, 14:00 a 18:00.")


def test_deactivate_user_confirmation(logged_in: Panel, db_session: Session) -> None:
    from tests.admin.conftest import make_user

    user = make_user(db_session, "marta", display_name="Marta Inventada")
    db_session.commit()
    page = logged_in.client.get(f"/admin/users/{user.id}").text
    assert _confirm(page, "deactivate_user").startswith("Marta Inventada. No va a poder entrar")
