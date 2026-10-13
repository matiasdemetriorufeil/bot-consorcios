"""How the panel looks (5e.3): one state component for every status, the bar at the bottom on
phones with the menu's counters, and «Pendientes» (greeting, everything up to date).
Invented data only."""

import re
from pathlib import Path

import pytest

from app.admin import labels
from app.db.models import (
    ClaimStatus,
    ReservationStatus,
    VerificationRequestStatus,
    WaConversationStatus,
)
from tests.admin.conftest import Panel
from tests.admin.wa_data import conversation
from tests.bot import factories as f

ADMIN_DIR = Path(__file__).resolve().parents[2] / "app" / "admin"
TEMPLATES = sorted((ADMIN_DIR / "templates").rglob("*.html"))
# The old colors of statuses, one per screen: now .state + .state-<kind> (panel.css).
OLD = re.compile(
    r"bg-(red|orange|green|yellow|azure|purple|teal|blue|secondary|success)-lt|bg-red text-white"
    r'|bg-orange text-white|bg-green text-white|(?<!id=")claim-status(?!-long")'
    r"|claim-(open|progress|closed)\b"
    r"|chat-status-|phone-status-|menu-badge"
)
# SQLAdmin's own list and record pages (copied, not ours to restyle).
SQLADMIN_COPIES = {"list.html", "details.html"}


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_no_template_keeps_the_old_status_colors(path: Path) -> None:
    if path.name in SQLADMIN_COPIES:
        return
    found = OLD.search(path.read_text(encoding="utf-8"))
    assert found is None, f"{path.name}: {found.group(0) if found else ''}"


def test_the_style_sheet_has_only_the_new_component() -> None:
    css = (ADMIN_DIR / "static" / "panel.css").read_text(encoding="utf-8")
    assert OLD.search(css) is None
    for kind in labels.STATE_KINDS:
        assert f".state-{kind} " in css, kind


@pytest.mark.parametrize(
    ("kind", "values"),
    [
        ("conversation", list(WaConversationStatus)),
        ("claim", list(ClaimStatus)),
        ("verification", list(VerificationRequestStatus)),
        ("reservation", list(ReservationStatus)),
        ("phone", list(labels.PHONE_STATUS)),
    ],
)
def test_every_status_has_its_state(kind: str, values: list[object]) -> None:
    assert {labels.state(v, kind) for v in values} <= set(labels.STATE_KINDS)
    assert set(labels.STATE[kind]) == set(values)


def test_the_colors_mean_the_same_everywhere() -> None:
    assert labels.state(WaConversationStatus.WAITING_HUMAN, "conversation") == "waiting"
    assert labels.state("pending_send", "claim") == "waiting"
    assert labels.state("review", "phone") == "waiting"
    assert labels.state(VerificationRequestStatus.PENDING, "verification") == "waiting"
    assert labels.state(ClaimStatus.SOLVED, "claim") == "ok"
    assert labels.state(WaConversationStatus.RESOLVED, "conversation") == "ok"
    assert labels.state(ClaimStatus.SENT, "claim") == "progress"
    assert labels.state(ClaimStatus.CANCELLED, "claim") == "info"


def test_the_pages_use_the_component(logged_in: Panel) -> None:
    s = logged_in.session
    f.person(s, "Bruno Revisar", phone="+5493515550801", needs_review=True)
    conversation(s, "5493515550802", status=WaConversationStatus.WAITING_HUMAN)
    s.commit()
    phones = logged_in.client.get("/admin/phones").text
    assert 'class="state state-waiting">A revisar</span>' in phones
    inbox = logged_in.client.get("/admin/conversations?q=555").text
    assert 'class="state state-waiting">Esperando persona</span>' in inbox


# --- The bar at the bottom (phones) ------------------------------------------------------------


def _nav(page: str) -> str:
    return page.split('<nav class="bottom-nav"', 1)[1].split("</nav>", 1)[0]


def test_the_bottom_bar_with_its_counters(operator: Panel) -> None:
    s = operator.session
    conversation(
        s, "5493515550811", status=WaConversationStatus.WAITING_HUMAN, handoff_priority="urgent"
    )
    f.person(s, "Bruno Revisar", phone="+5493515550812", needs_review=True)
    s.commit()
    page = operator.client.get("/admin/phones").text
    nav = _nav(page)
    labels_ = re.findall(r'<span class="bottom-nav-label">([^<]+)</span>', nav)
    assert labels_ == ["Pendientes", "Conversaciones", "Reclamos", "Más"]
    assert re.search(r'class="count count-urgent" data-counter="waiting" >1<', nav)
    assert 'data-counter="claims"' in nav and 'data-counter="more"' in nav
    assert re.search(r'data-counter="more" >1<', nav)  # a phone to review
    # «Más»: what is not in the bar, with its counters, and Salir; no «Administración».
    sheet = page.split('id="more-sheet"', 1)[1].split("</dialog>", 1)[0]
    names = re.findall(r'<span class="more-item-name">([^<]+)</span>', sheet)
    assert names == ["Teléfonos", "Verificaciones", "Reservas de SUM", "Guía", "Salir"]
    assert 'data-counter="phones"' in sheet and "Administración" not in sheet


def test_the_admin_finds_administracion_in_mas(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/pending").text
    sheet = page.split('id="more-sheet"', 1)[1].split("</dialog>", 1)[0]
    assert '<div class="more-section">Administración</div>' in sheet
    assert "Edificios" in sheet and "Usuarios" in sheet


def test_the_bottom_bar_only_on_small_screens() -> None:
    css = (ADMIN_DIR / "static" / "panel.css").read_text(encoding="utf-8")
    assert re.search(r"\.bottom-nav, \.panel-bell \{ display: none; \}", css)
    phones = css.split("@media (max-width: 991.98px) {\n  :root { --bottom-nav-height", 1)[1]
    assert ".bottom-nav { display: flex;" in phones
    # Nothing hides under it: the page leaves its room, the inbox too.
    assert "body { padding-bottom: calc(var(--bottom-nav-height)" in phones
    assert "var(--bottom-nav-height, 0px)" in css


def test_44_px_to_touch_on_phones() -> None:
    css = (ADMIN_DIR / "static" / "panel.css").read_text(encoding="utf-8")
    assert ".btn, .btn-sm, .form-control, .form-select { min-height: 44px; }" in css


# --- Pendientes --------------------------------------------------------------------------------


def test_the_greeting_goes_above_the_help_line(operator: Panel) -> None:
    page = operator.client.get("/admin/pending").text
    assert page.index('id="pending-greeting"') < page.index('class="page-help')


def test_everything_up_to_date_is_one_line_each(operator: Panel) -> None:
    page = operator.client.get("/admin/pending").text
    assert "Todo al día" in page and 'class="card pending-lines mb-3"' in page
    lines = re.findall(r'<a class="pending-line" href="[^"]+" data-card="(\w+)">', page)
    assert lines == ["waiting", "mine", "claims", "phones", "verifications"]
    assert 'data-card="sum"' in page  # the SUM's card stays whole


def test_with_something_waiting_the_cards_are_whole(operator: Panel) -> None:
    f.person(operator.session, "Bruno Revisar", phone="+5493515550821", needs_review=True)
    operator.session.commit()
    page = operator.client.get("/admin/pending").text
    assert "pending-line" not in page and 'class="pending-grid"' in page
