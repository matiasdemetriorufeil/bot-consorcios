"""How the panel looks (5e.3): one state component for every status, the bar at the bottom on
phones with the menu's counters, and «Pendientes» (greeting, everything up to date).
5f.1: one visual base (panel.css :root variables, with their contrast computed here), no
style written by hand in templates, the one empty block, who is in at the foot of the menu,
the compact chart of Métricas and SQLAdmin's forms read aloud. Invented data only."""

import re
from datetime import UTC, date, datetime
from html import escape as html_escape
from html import unescape as html_unescape
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.admin import help, labels
from app.admin.views import per_day_summary
from app.db.models import (
    BotEvent,
    ClaimStatus,
    ReservationStatus,
    VerificationRequestStatus,
    WaConversationStatus,
)
from tests.admin.conftest import USER_PASSWORD, Panel, admin_settings, build_panel, make_user
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
    assert names == ["Vecinos", "Teléfonos", "Verificaciones", "Reservas de SUM", "Guía", "Salir"]
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
    assert "--btn-h-touch: 44px;" in css
    # Every button, also the small ones and the quiet «Eliminar» (btn-link-danger).
    assert (
        ".btn, .btn-sm, .btn-link-danger, .form-control, .form-select, .pagination .page-link "
        "{ min-height: var(--btn-h-touch); }"
    ) in css


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


# --- 5f.1: the visual base ---------------------------------------------------------------------

CSS_FILE = ADMIN_DIR / "static" / "panel.css"
# The only styles a template may write: what is computed per item. The SUM's week (how many
# hours, where each slot goes) and the height of each column of Métricas' chart.
ALLOWED_STYLES = {
    "amenity_week.html": re.compile(
        r"^(--hours: \{\{[^}]+\}\}|top: .+; height: .+; left: .+; width: [^;]+)$"
    ),
    "metrics.html": re.compile(r"^height: \{\{.+\}\}%$"),
}
STYLE_ATTR = re.compile(r'\bstyle="([^"]*)"')
HEX = re.compile(r"#[0-9a-fA-F]{3,8}(?![\w-])")
REQUIRED_VARIABLES = [
    "--c-primary", "--c-primary-hover", "--c-primary-soft", "--c-focus", "--c-focus-on-dark",
    "--c-bg", "--c-surface", "--c-border", "--c-text", "--c-text-2", "--c-placeholder",
    *[f"--c-{kind}" for kind in labels.STATE_KINDS],
    *[f"--c-{kind}-bg" for kind in labels.STATE_KINDS],
    "--c-danger", "--c-danger-hover", "--c-danger-soft", "--c-nav-bg", "--c-nav-text",
    "--c-nav-active-bg", "--font-sans", "--fs-title", "--fs-subtitle", "--fs-body", "--fs-small",
    "--fw-regular", "--fw-bold", "--sp-1", "--sp-2", "--sp-3", "--sp-4", "--sp-5", "--sp-6",
    "--radius-sm", "--radius-md", "--shadow-1", "--shadow-2", "--btn-h", "--btn-h-sm",
    "--btn-h-touch", "--t-fast", "--t-base",
]  # fmt: skip


def _css() -> str:
    return re.sub(r"/\*.*?\*/", "", CSS_FILE.read_text(encoding="utf-8"), flags=re.S)


def _root() -> str:
    """The base: the first :root block, where every color, size and shadow is written."""
    found = re.search(r":root,\s*\[data-bs-theme=light\]\s*\{(.*?)\n\}", _css(), re.S)
    assert found is not None
    return found.group(1)


def _outside_root() -> str:
    return re.sub(r":root[^{]*\{[^{}]*\}", "", _css())


def _colors() -> dict[str, str]:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", _root()))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_no_style_written_by_hand_in_templates(path: Path) -> None:
    for style in STYLE_ATTR.findall(path.read_text(encoding="utf-8")):
        allowed = ALLOWED_STYLES.get(path.name)
        assert allowed is not None and allowed.match(style.strip()), f"{path.name}: {style}"


def test_no_color_size_radius_nor_shadow_outside_the_base() -> None:
    rest = _outside_root()
    assert HEX.findall(rest) == []
    assert re.findall(r"\brgba?\(", rest) == []
    for value in re.findall(r"font-size:\s*([^;}]+)", rest):
        assert value.strip().startswith("var(--fs-") or value.strip() in {"0", "inherit"}, value
    for value in re.findall(r"border-radius:\s*([^;}]+)", rest):
        assert set(re.sub(r"var\(--radius-[\w-]+\)", "", value).split()) <= {"0"}, value
    for value in re.findall(r"box-shadow:\s*([^;}]+)", rest):
        value = value.strip()
        if value == "none" or re.fullmatch(r"var\(--shadow-\d\)", value):
            continue
        # A colored line inside (the active menu item, an urgent row): its color is a variable.
        assert re.fullmatch(r"inset( -?\d+(px)?){3} var\(--c-[\w-]+\)", value), value


def test_the_base_has_its_variables() -> None:
    root = _root()
    for name in REQUIRED_VARIABLES:
        assert f"{name}:" in root, name
    # The four sizes give 15 / 13 / 17 / 22 px with the html's 16 px (measured in the browser).
    sizes = dict(re.findall(r"--(fs-\w+):\s*([\d.]+)rem", root))
    px = {name: round(float(rem) * 16, 2) for name, rem in sizes.items()}
    assert (px["fs-body"], px["fs-small"], px["fs-subtitle"], px["fs-title"]) == (15, 13, 17, 22)
    # Nothing from the internet: the system's own font, no @import.
    assert "@import" not in _css() and "Inter" not in root


def test_motion_is_short_and_goes_away_when_asked() -> None:
    css = _css()
    assert "@media (prefers-reduced-motion: reduce)" in css
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1].split("\n}", 1)[0]
    assert "--t-fast: 0ms" in reduced and "--t-base: 0ms" in reduced
    durations = re.findall(r"--t-\w+:\s*(\d+)ms", _root())
    assert durations and all(150 <= int(ms) <= 200 for ms in durations)


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_one_set_of_icons(path: Path) -> None:
    assert "ti ti-" not in path.read_text(encoding="utf-8")


# The contrast (WCAG 2): text 4.5:1, the focus ring 3:1.
def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(one: str, other: str) -> float:
    a, b = sorted((_luminance(one), _luminance(other)), reverse=True)
    return (a + 0.05) / (b + 0.05)


TEXT_PAIRS = [
    # Pages and cards.
    *[("text", bg) for bg in ("bg", "surface", "surface-2", "surface-3", "primary-soft")],
    *[("text-2", bg) for bg in ("bg", "surface", "surface-2", "surface-3", "primary-soft")],
    ("placeholder", "surface"),
    ("primary", "surface"), ("primary", "bg"), ("primary", "surface-2"),
    ("primary", "primary-soft"),
    ("on-primary", "primary"), ("on-primary", "primary-hover"),
    # The states and counters.
    ("ok", "ok-bg"), ("progress", "progress-bg"), ("waiting", "waiting-bg"),
    ("urgent", "urgent-bg"), ("info", "info-bg"), ("count-waiting", "count-waiting-bg"),
    ("on-primary", "ok"), ("ok", "surface"), ("ok", "surface-2"), ("waiting", "surface"),
    # What cannot be undone: the button, and «Eliminar» as a red link on white, on a row
    # with the mouse over it, and its own hover.
    ("on-primary", "danger"), ("on-primary", "danger-hover"), ("danger", "surface"),
    ("danger", "surface-2"), ("danger-hover", "danger-soft"), ("danger", "danger-soft"),
    # The menu.
    ("nav-text", "nav-bg"), ("nav-text", "nav-active-bg"), ("nav-text-2", "nav-bg"),
    ("nav-text-2", "nav-active-bg"),
    # The chat.
    *[("text", bg) for bg in ("msg-bot", "msg-operator", "msg-system", "msg-note", "msg-handoff")],
    *[("chat-text-2", bg) for bg in (
        "chat-bg", "surface", "msg-bot", "msg-operator", "msg-system", "msg-note", "msg-day")],
    ("msg-who", "surface"), ("msg-who", "msg-operator"), ("msg-who-bot", "msg-bot"),
    ("msg-who-bot", "surface"), ("msg-who-note", "msg-note"), ("msg-who-note", "msg-handoff"),
    ("phone-choice", "surface"), ("on-primary", "phone-top"),
    # The SUM's week.
    ("slot-free-text", "slot-free"), ("slot-free-text", "slot-free-hover"),
    ("slot-blocked-text", "slot-blocked"), ("primary", "grid-today"),
    # Notices.
    ("notice-text", "notice-bg"), ("text", "notice-bg"), ("text", "note-bg"),
    ("toast-text", "toast-bg"), ("env", "env-bg"),
]  # fmt: skip
FOCUS_PAIRS = [
    ("focus", "bg"), ("focus", "surface"), ("focus", "surface-2"), ("focus", "primary-soft"),
    ("focus-on-dark", "nav-bg"), ("focus-on-dark", "nav-active-bg"),
]  # fmt: skip


@pytest.mark.parametrize(("text", "background"), TEXT_PAIRS, ids="/".join)
def test_text_contrast_is_aa(text: str, background: str) -> None:
    colors = _colors()
    ratio = _ratio(colors[f"c-{text}"], colors[f"c-{background}"])
    assert ratio >= 4.5, f"{text} on {background}: {ratio:.2f}"


@pytest.mark.parametrize(("ring", "background"), FOCUS_PAIRS, ids="/".join)
def test_the_focus_ring_is_seen(ring: str, background: str) -> None:
    colors = _colors()
    ratio = _ratio(colors[f"c-{ring}"], colors[f"c-{background}"])
    assert ratio >= 3, f"{ring} on {background}: {ratio:.2f}"


def test_the_focus_is_visible_and_light_on_the_dark_menu() -> None:
    css = _css()
    assert "outline: 3px solid var(--c-focus); outline-offset: 2px;" in css
    assert ".navbar-vertical a:focus-visible" in css
    assert "{ outline-color: var(--c-focus-on-dark); }" in css
    assert "--tblr-focus-ring-color: transparent;" in _root()


# --- 5f.1: the pages ---------------------------------------------------------------------------


def _empty_block(page: str) -> str:
    return page.split('<div class="empty-state">', 1)[1].split("</div>", 1)[0]


def test_one_empty_block_everywhere(operator: Panel) -> None:
    for url, text in [
        ("/admin/conversations?tab=waiting", help.EMPTY["conversations:waiting"]),
        ("/admin/claims", help.EMPTY["claims:open"]),
        ("/admin/phones", help.EMPTY["phones:all"]),
    ]:
        page = operator.client.get(url).text
        block = _empty_block(page)
        assert 'class="fa-solid' in block and 'aria-hidden="true"' in block, url
        assert f'<p class="empty-help">{html_escape(text, quote=False)}</p>' in block, url


def test_sqladmin_lists_use_the_same_empty_block(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/wa-template/list").text
    block = _empty_block(page)
    text = html_escape(help.empty_for("wa-template"), quote=False)
    assert f'<p class="empty-help">{text}</p>' in block
    # Its «+ Agregar» beside the page's title, the title not repeated in the card.
    header = page.split('class="page-header', 1)[1].split('class="page-body', 1)[0]
    assert "+ Agregar Plantilla de WhatsApp" in header
    assert '<h3 class="card-title">Plantillas de WhatsApp</h3>' not in page


def _menu_user(page: str) -> str:
    return page.split('id="menu-user">', 1)[1].split("</li>", 1)[0]


def _sheet(page: str) -> str:
    return page.split('id="more-sheet"', 1)[1].split("</dialog>", 1)[0]


def _more_user(page: str) -> str:
    return _sheet(page).split('<div class="more-user">', 1)[1].split("</div>", 1)[0]


def test_who_is_in_at_the_foot_of_the_menu(operator: Panel) -> None:
    page = operator.client.get("/admin/pending").text
    assert "<span>Marta Inventada</span>" in _menu_user(page)
    # The menu's own «Salir» right after the name.
    assert page.index('id="menu-user"') < page.index("/admin/logout")
    # Phones: inside «Más», right above Salir.
    assert "<span>Marta Inventada</span>" in _more_user(page)
    assert _sheet(page).index('class="more-user"') < _sheet(page).index("more-logout")


def test_the_rescue_admin_is_administrador(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/pending").text
    assert "<span>Administrador</span>" in _menu_user(page)
    assert "<span>Administrador</span>" in _more_user(page)


def test_the_name_is_escaped(panel: Panel) -> None:
    make_user(panel.session, "ana", display_name="<b>Ana</b> Inventada")
    panel.session.commit()
    assert panel.login("ana", USER_PASSWORD).status_code == 302
    page = panel.client.get("/admin/pending").text
    assert "&lt;b&gt;Ana&lt;/b&gt; Inventada" in _menu_user(page)
    assert "<b>Ana</b>" not in page


def test_metrics_draws_the_days_in_one_compact_chart(logged_in: Panel) -> None:
    logged_in.session.add(
        BotEvent(
            conversation_id=1, event_type="message_in", payload={}, created_at=datetime.now(UTC)
        )
    )
    logged_in.session.commit()
    page = logged_in.client.get("/admin/metrics").text
    assert page.count('<div class="spark"') == 1
    chart = page.split('<div class="spark"', 1)[1].split('<div class="spark-axis"', 1)[0]
    assert chart.count('class="spark-bar') == 30
    assert chart.count('aria-hidden="true"') == 30
    found = re.search(r'role="img" aria-label="([^"]+)"', chart)
    assert found is not None
    label = found.group(1)
    assert "últimos 30 días" in label and "en total" in label
    assert "El día con más" in label and "El día con menos" in label
    # Every number, folded underneath; no more 30 bars of Tabler.
    days = page.split('<details class="metrics-days" id="per-day">', 1)[1].split("</details>")[0]
    assert "<summary>Ver por día</summary>" in days and days.count("<tr>") == 31
    assert "progress-bar" not in page


def test_the_chart_summary() -> None:
    per_day = [(date(2026, 10, 1), 3), (date(2026, 10, 2), 0), (date(2026, 10, 3), 7)]
    assert per_day_summary(per_day) == (
        "Conversaciones por día, últimos 3 días: 10 en total. "
        "El día con más: 03/10, con 7. El día con menos: 02/10, con 0."
    )
    assert per_day_summary([]) == "Conversaciones por día: sin datos."


def test_an_invalid_sqladmin_form_reads_its_error(logged_in: Panel) -> None:
    response = logged_in.client.post(
        "/admin/provider/create",
        data={"name": "Ascensores Ficticios SRL", "whatsapp_e164": "555-0101", "save": "Save"},
    )
    assert response.status_code == 400
    page = response.text
    found = re.search(r"<input[^>]*\bid=\"whatsapp_e164\"[^>]*>", page)
    assert found is not None
    field = found.group(0)
    assert 'aria-invalid="true"' in field
    described = re.search(r'aria-describedby="([^"]+)"', field)
    assert described is not None and "whatsapp_e164-error" in described.group(1).split()
    error = page.split('id="whatsapp_e164-error">', 1)[1].split("</div>\n", 1)[0]
    assert "código de área" in html_unescape(error)
    # The label on top (no column on its left), the required mark the same in every form.
    assert "col-sm-2" not in page
    label = re.search(r'<label[^>]*for="name"[^>]*>', page)
    assert label is not None
    assert "required-label" in label.group(0) and 'title="Obligatorio"' in label.group(0)


# --- 5f.2: titles, structure, «Agregar a inicio», environment, static files ------------------

MAIN_PAGES = [
    ("/admin/pending", "Pendientes"),
    ("/admin/conversations", "Conversaciones"),
    ("/admin/claims", "Reclamos"),
    ("/admin/neighbors", "Vecinos"),
    ("/admin/phones", "Teléfonos"),
    ("/admin/verifications", "Verificaciones"),
    ("/admin/amenities", "Reservas de SUM"),
    ("/admin/guide", "Guía"),
    ("/admin/building/list", "Edificios"),
    ("/admin/metrics", "Métricas"),
]
SUFFIX = " · Panel · Estudio Diego Rufeil"


def _title(page: str) -> str:
    found = re.search(r"<title>(.*?)</title>", page, re.S)
    assert found is not None
    return html_unescape(found.group(1).strip())


@pytest.mark.parametrize(("url", "screen"), MAIN_PAGES)
def test_every_page_has_its_tab_title_and_structure(
    logged_in: Panel, url: str, screen: str
) -> None:
    page = logged_in.client.get(url).text
    assert _title(page) == f"{screen}{SUFFIX}"
    assert f'data-title="{screen}{SUFFIX}"' in page  # what panel.js keeps up to date
    assert '<html lang="es">' in page
    # «Saltar al contenido» is the first thing in the body, to <main id="contenido">.
    body = page.split("<body>", 1)[1].lstrip()
    assert body.startswith('<a class="skip-link" href="#contenido">Saltar al contenido</a>')
    assert page.count('<main id="contenido"') == 1 and "</main>" in page
    assert '<nav class="navbar' in page and 'aria-label="Menú"' in page
    assert len(re.findall(r"<h1[\s>]", page)) == 1
    assert f'<h1 class="page-title">{html_escape(screen, quote=False)}</h1>' in page


def test_the_tab_title_counts_what_waits(logged_in: Panel) -> None:
    conversation(logged_in.session, "5493515550831", status=WaConversationStatus.WAITING_HUMAN)
    logged_in.session.commit()
    page = logged_in.client.get("/admin/claims").text
    assert _title(page) == f"(1) Reclamos{SUFFIX}"


def test_the_login_and_error_titles(panel: Panel) -> None:
    assert _title(panel.client.get("/admin/login").text) == f"Ingresar{SUFFIX}"
    assert _title(panel.client.get("/admin/no-existe").text) == f"No encontramos esa página{SUFFIX}"


# The login.
def test_the_login_helps_the_browser_and_the_keyboard(panel: Panel) -> None:
    page = panel.client.get("/admin/login").text
    user = re.search(r'<input id="login-username"[^>]*>', page)
    password = re.search(r'<input id="login-password"[^>]*>', page)
    assert user is not None and password is not None
    assert 'autocomplete="username"' in user.group(0) and "autofocus" in user.group(0)
    assert 'autocomplete="current-password"' in password.group(0)
    assert 'type="submit"' in page  # Enter sends
    assert "¿Te olvidaste la contraseña? Pedile a un administrador que te la cambie." in page
    assert '<h1 class="login-title">Estudio Diego Rufeil</h1>' in page


def test_a_wrong_login_never_says_which_field(panel: Panel) -> None:
    make_user(panel.session, "marta")
    panel.session.commit()
    wrong_user = panel.login("nadie-inventado", USER_PASSWORD)
    wrong_password = panel.login("marta", "otra-clave-inventada")
    assert wrong_user.status_code == wrong_password.status_code == 400

    def error_part(page: str) -> str:
        return page.split('<form class="card login-card"', 1)[1].split("</form>", 1)[0]

    assert error_part(wrong_user.text) == error_part(wrong_password.text)
    form = error_part(wrong_user.text)
    assert '<p class="login-error" id="login-error" role="alert">' in form
    assert "is-invalid" not in form and "aria-invalid" not in form
    assert form.index('id="login-error"') < form.index('type="submit"')


# «Agregar a inicio».
def test_the_manifest_and_the_icons(panel: Panel) -> None:
    response = panel.client.get("/admin/static/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert manifest["name"] == "Panel · Estudio Diego Rufeil"
    assert manifest["short_name"] == "Estudio Rufeil"
    assert manifest["start_url"] == "/admin/" and manifest["display"] == "standalone"
    colors = _colors()
    assert manifest["theme_color"] == colors["c-nav-bg"]
    assert manifest["background_color"] == colors["c-bg"]
    purposes = {(icon["sizes"], icon.get("purpose")) for icon in manifest["icons"]}
    assert {("192x192", "maskable"), ("512x512", "maskable"), ("512x512", "any")} <= purposes
    for icon in manifest["icons"]:
        got = panel.client.get(f"/admin/static/{icon['src']}")
        assert got.status_code == 200 and got.headers["content-type"] == icon["type"], icon
    for name in ("icons/icon-32.png", "icons/apple-touch-icon-180.png"):
        assert panel.client.get(f"/admin/static/{name}").headers["content-type"] == "image/png"
    # The page links them all, and the bar's color is the menu's.
    page = panel.client.get("/admin/login").text
    for rel in ('rel="manifest"', 'rel="apple-touch-icon"', 'rel="icon"'):
        assert rel in page
    assert f'<meta name="theme-color" content="{colors["c-nav-bg"]}">' in page
    assert "serviceWorker" not in (ADMIN_DIR / "static" / "panel.js").read_text(encoding="utf-8")


# The environment.
@pytest.mark.parametrize(
    ("overrides", "label", "chat"),
    [
        ({"app_env": "production"}, False, False),
        ({"app_env": "development"}, True, True),
        ({}, True, False),  # APP_ENV missing: the label as an alarm, never the test chat
    ],
)
def test_development_is_never_seen_in_production(
    db_session: Session, overrides: dict[str, str], label: bool, chat: bool
) -> None:
    built = build_panel(db_session, admin_settings(**overrides))
    with built.client:
        assert built.login().status_code == 302
        page = built.client.get("/admin/pending").text
        assert ('class="env-badge"' in page) is label
        assert ("Chat de prueba" in page) is chat
        assert (built.client.get("/admin/dev-chat").status_code == 200) is chat


def test_panel_js_says_nothing_to_the_console() -> None:
    assert "console." not in (ADMIN_DIR / "static" / "panel.js").read_text(encoding="utf-8")


# Static files.
ASSET = re.compile(r'(?:href|src)="([^"]+\.(?:css|js|svg|png|webmanifest)(?:\?[^"]*)?)"')


@pytest.mark.parametrize("url", ["/admin/pending", "/admin/building/list", "/admin/login"])
def test_static_files_from_the_server_with_their_version(logged_in: Panel, url: str) -> None:
    page = logged_in.client.get(url).text
    assets = ASSET.findall(page)
    assert assets
    for asset in assets:
        assert asset.startswith("https://testserver/admin/"), asset  # never a CDN
        assert re.search(r"\?v=[0-9a-f]{10}$", asset), asset
    assert "tabler-icons" not in page
    assert "select2" not in page and "flatpickr" not in page  # only in SQLAdmin's forms


def test_select2_only_in_sqladmin_forms(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/building-info/create").text
    assert "select2.full.min.js?v=" in page and "flatpickr.min.css?v=" in page


def test_a_versioned_file_is_cached_for_long_and_text_is_compressed(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/pending").text
    css = re.search(r'href="https://testserver(/admin/static/panel\.css\?v=\w+)"', page)
    assert css is not None
    response = logged_in.client.get(css.group(1), headers={"Accept-Encoding": "gzip"})
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["content-encoding"] == "gzip"
    # Without its version: revalidate (the ETag) instead.
    plain = logged_in.client.get("/admin/static/panel.css")
    assert plain.headers["cache-control"] == "no-cache"
    sqladmin_css = logged_in.client.get("/admin/statics/css/tabler.min.css?v=x")
    assert sqladmin_css.headers["cache-control"].startswith("public, max-age=31536000")
    # A picture is not compressed again; a page of the panel never is (BREACH).
    png = logged_in.client.get(
        "/admin/static/icons/icon-512.png?v=x", headers={"Accept-Encoding": "gzip"}
    )
    assert "content-encoding" not in png.headers
    html_page = logged_in.client.get("/admin/pending", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in html_page.headers


def test_the_version_comes_from_the_content(tmp_path: Path) -> None:
    from app.admin.static_cache import versions

    (tmp_path / "panel.css").write_text("body { color: red; }", encoding="utf-8")
    before = versions(tmp_path)["panel.css"]
    (tmp_path / "panel.css").write_text("body { color: blue; }", encoding="utf-8")
    after = versions(tmp_path)["panel.css"]
    assert before != after and len(before) == 10


# What was left of 5f.1.
def test_metrics_with_nothing_shows_the_empty_block(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/metrics").text
    assert html_escape(help.EMPTY["metrics:per-day"], quote=False) in page
    assert '<div class="spark"' not in page
    section = page.split('class="col-12 section-title"', 1)[1].split("</div>", 1)[0]
    assert section.index("Reclamos del mes") < section.index("Desde el")


def test_repeated_danger_buttons_are_outlined() -> None:
    for name in ("verifications.html", "_phone_row.html"):
        text = (ADMIN_DIR / "templates" / name).read_text(encoding="utf-8")
        assert "btn-outline-danger" in text and "btn btn-sm btn-danger" not in text, name
    assert ".rows-table .state { white-space: nowrap; flex-shrink: 0; }" in _css()


def test_a_long_reason_never_widens_the_claims_list() -> None:
    """«El proveedor no puede atenderlo» and the like: from 1280 px at most 13rem with «…» (its
    title says it whole), under that in two lines, on a phone whole (survey.py measures no
    horizontal bar at 1440 and 1024 px)."""
    css = _css()
    assert (
        "@media (min-width: 768px) { .claim-row-attention.cell-cut { max-width: 13rem; "
        "white-space: normal; overflow: visible; } }"
    ) in css
    assert (
        "@media (min-width: 1280px) { .claim-row-attention.cell-cut { white-space: nowrap; "
        "overflow: hidden; } }"
    ) in css
    assert (
        ".rows-table .cell-cut { white-space: normal; overflow: visible; max-width: none; }" in css
    )
    rows = (ADMIN_DIR / "templates" / "_claims_list.html").read_text(encoding="utf-8")
    assert 'class="claim-row-attention cell-cut" title="{{ row.attention }}"' in rows


# --- 5f.4: no table scrolls sideways inside its card --------------------------------------------


def _prop_name(column: object) -> str:
    return column if isinstance(column, str) else column.key  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "view",
    [
        "BuildingAdmin",
        "BuildingInfoAdmin",
        "SyncRunAdmin",
        "WaTemplateAdmin",
        "ProviderAdmin",
        "ClaimCategoryAdmin",
    ],
)
def test_secondary_columns_are_listed_and_kept_in_the_record(view: str) -> None:
    from app.admin import claims_setup, views

    cls = getattr(views, view, None) or getattr(claims_setup, view)
    listed = {_prop_name(c) for c in cls.column_list}
    detail = {_prop_name(c) for c in cls.column_details_list}
    assert cls.column_secondary and set(cls.column_secondary) <= listed
    assert set(cls.column_secondary) <= detail  # hidden in the list, never lost


def test_a_list_marks_its_secondary_columns(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/sync-run/list").text
    assert re.search(r'<th class="col-secondary">\s*(<a [^>]*>)?\s*Tipo', page)
    assert 'class="table-responsive table-scroll-hint"' in page
    assert '<div class="list-layout">' in page and 'class="filter-sidebar-col"' in page


def test_buildings_have_their_own_list(logged_in: Panel) -> None:
    """5g: the search and the Todos / Sí / No filters in the card, «Acciones» on the right."""
    f.building(logged_in.session, "001 TORRE INVENTADA")
    logged_in.session.commit()
    page = logged_in.client.get("/admin/building/list?active=true").text
    assert 'id="search-input"' in page and 'id="search-button"' in page
    assert "filter-sidebar-col" not in page and page.count('class="segmented"') == 3
    assert re.search(r'class="segmented-item active"[^>]*>Sí</a>', page)
    assert re.search(r'<th class="col-secondary">\s*Dirección', page)
    assert ">Acciones</th>" in page and "1 edificio<" in page


def test_building_info_has_the_same_list(logged_in: Panel) -> None:
    """5g: like «Edificios», the filters as selects in one form with the search."""
    page = logged_in.client.get("/admin/building-info/list?category=horarios").text
    assert '<form class="building-search" method="get"' in page and 'name="search"' in page
    assert "filter-sidebar-col" not in page and page.count('class="building-select"') == 2
    assert '<option value="horarios" selected>' in page and "Borrar la búsqueda" in page
    assert re.search(r'<th class="col-secondary">\s*(<a [^>]*>)?\s*Actualizado', page)
    assert "Agregar información de edificio" in page and 'id="modal-delete"' in page


def test_providers_have_the_same_list(logged_in: Panel) -> None:
    """5g: like «Edificios»: the contact under the company, the state as a label."""
    page = logged_in.client.get("/admin/provider/list").text
    assert 'id="search-input"' in page and page.count('class="segmented"') == 1
    assert "filter-sidebar-col" not in page and "Agregar proveedor" in page
    assert re.search(r'<th class="text-center col-secondary">Edificios</th>', page)
    assert "Mostrando 0–0 de 0 proveedores" in page


def test_kinds_of_problem_have_the_same_list(logged_in: Panel) -> None:
    """5g: the title of WhatsApp's list in bold, the whole problem under it, labels."""
    page = logged_in.client.get("/admin/claim-category/list").text
    assert 'id="search-input"' in page and page.count('class="segmented"') == 1
    assert "filter-sidebar-col" not in page and "Agregar tipo de problema" in page
    assert "Título en la lista · " in page and "en que el propietario ve la lista" in page


def test_verifications_fit_at_1024(operator: Panel) -> None:
    """«Estado» waits under 1280 px (every row is «Pendiente»), long texts cut with the whole on
    hover, and less air per cell between 768 and 1279 px."""
    from app.bot import identity

    s = operator.session
    building = f.building(s, "078 TORRE CON UN NOMBRE MUY LARGO INVENTADO")
    unit = f.unit(s, building, "04-C")
    f.link(s, unit, f.person(s, "Dueña Inventada"))
    identity.request_operator_verification(s, "+5493515550602", unit.id, "Dueña Inventada")
    s.commit()
    page = operator.client.get("/admin/verifications").text
    assert '<th class="col-secondary">Estado</th>' in page
    assert 'class="col-secondary"><span class="state state-waiting">' in page
    assert '<span class="cell-cut" title="TORRE CON UN NOMBRE MUY LARGO INVENTADO">' in page
    assert '<span class="cell-cut" title="Dueña Inventada">' in page
    assert "#verifications > :not(caption) > * > * { padding-left: var(--sp-2);" in _css()


def test_the_rules_that_keep_tables_inside_their_card() -> None:
    css = _css()
    assert (
        "@media (max-width: 1279.98px) {\n  .col-secondary { display: none !important; }\n}" in css
    )
    assert ".list-layout { flex-direction: row; }" in css  # filters beside it only from 1280 px
    assert ".rows-table.card-table td.row-actions .btn { white-space: nowrap; }" in css
    assert "position: sticky; left: 0;" in css and ".table-scroll-hint" in css
    js = (ADMIN_DIR / "static" / "panel.js").read_text(encoding="utf-8")
    assert 'matchMedia("(min-width: 1280px)")' in js and "panel-filters-open:" in js


@pytest.mark.parametrize(
    ("name", "table_id"),
    [
        ("users.html", "users"),
        ("building_claims_list.html", "building-claims"),
        ("building_claims.html", "building-claims-table"),
    ],
)
def test_more_lists_are_row_cards_on_phones(name: str, table_id: str) -> None:
    text = (ADMIN_DIR / "templates" / name).read_text(encoding="utf-8")
    table = re.search(rf'<table class="([^"]+)" id="{table_id}">', text)
    assert table is not None and "rows-table" in table.group(1)
    body = text.split(f'id="{table_id}">', 1)[1].split("</table>", 1)[0]
    cells = re.findall(r"<td(?: [^>]*)?>", body)
    labelled = [c for c in cells if "data-label" in c or "row-actions" in c or "colspan" in c]
    assert cells and labelled == cells
