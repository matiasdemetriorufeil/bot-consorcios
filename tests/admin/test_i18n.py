"""The whole panel in Spanish and with nothing technical in sight: login, SQLAdmin's lists,
records and forms, the sync runs' errors and statistics, the metrics. Invented data only."""

import re
from datetime import datetime, time, timedelta
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    BotEvent,
    BotSettings,
    BuildingInfo,
    BuildingInfoCategory,
    QuickReply,
    SyncJob,
    SyncKind,
    SyncRun,
    SyncStatus,
    WaTemplate,
)
from tests.admin.conftest import ADMIN, PASSWORD, Panel
from tests.bot import factories as f

# The sync run started today at 02:32 in Córdoba (the list says "hoy 02:32" whatever the day).
RUN_AT = datetime.combine(
    datetime.now(ZoneInfo("America/Argentina/Cordoba")).date(),
    time(2, 32, 45, 123456),
    tzinfo=ZoneInfo("America/Argentina/Cordoba"),
)

# SQLAdmin's English, as whole words of what a person reads (texts, placeholders, buttons).
ENGLISH = [
    "Actions", "Search", "Filters", "Showing", "items", "Save", "Cancel", "Go Back", "Delete",
    "Edit", "New", "Logout", "Login", "Username", "Password", "Please confirm", "All", "Yes",
    "View", "Column", "Value", "prev", "next", "Page", "Show", "Forbidden", "Invalid",
    "Number of characters", "Select", "Clear", "Apply", "required", "field",
]  # fmt: skip
# Raw values: seconds, a time zone, E.164, stored codes, the bot's tool names, settings.
TECHNICAL = [
    r"\d{2}:\d{2}:\d{2}",
    r"[+-]\d{2}:\d{2}\b",
    r"\+549\d{8,}",
    r"\bnightly\b",
    r"\broster\b",
    r"\bconsorplus\b",
    r"\bget_debt\b",
    "LLM_PRICE",
    r"\.env\b",
    r"\b[a-z]+\.[a-z_]+\b",  # attribute paths, e.g. "building.name"
]


class _Visible(HTMLParser):
    """The text a person reads: no scripts nor styles, plus placeholders, titles and the
    labels of buttons."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        values = dict(attrs)
        for name in ("placeholder", "title"):
            if values.get(name):
                self.parts.append(str(values[name]))
        if tag == "input" and values.get("type") in ("submit", "button") and values.get("value"):
            self.parts.append(str(values["value"]))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def visible(page: str) -> str:
    parser = _Visible()
    parser.feed(page)
    return " ".join(" ".join(parser.parts).split())


def english_in(page: str) -> list[str]:
    text = visible(page)
    return [word for word in ENGLISH if re.search(rf"\b{re.escape(word)}\b", text)]


def technical_in(page: str) -> list[str]:
    text = visible(page)
    return [pattern for pattern in TECHNICAL if re.search(pattern, text)]


@pytest.fixture
def data(db_session: Session) -> dict[str, int]:
    building = f.building(db_session, "095 TORRE IDIOMA")
    info = BuildingInfo(
        building_id=building.id,
        title="Horario de pileta",
        content="De 9 a 21.",
        category=BuildingInfoCategory.HORARIOS,
    )
    template = WaTemplate(name="retomar", label="Retomar la charla", body="Hola")
    quick = QuickReply(title="Saludo", content="¡Hola!")
    run = SyncRun(
        kind=SyncKind.NIGHTLY,
        job=SyncJob.ROSTER,
        status=SyncStatus.PARTIAL,
        started_at=RUN_AT,
        finished_at=RUN_AT + timedelta(minutes=12),
        units_ok=2450,
        units_failed=2,
        error_summary="Edificio 095: no disponible\nEdificio 096: página distinta",
        stats={
            "units": 2450,
            "phones_valid": 1800,
            "elapsed_seconds": 720.4,
            "by_unit_type": {"Cochera": {"units": 3, "with_owner_phone": 1}},
            "errors_by_type": {"ConsorPlusUnavailableError": 2},
            "algo_nuevo": 7,
        },
    )
    db_session.add_all([info, template, quick, run])
    db_session.flush()
    for tool in ("get_debt", "get_debt", "find_unit"):
        db_session.add(BotEvent(conversation_id=1, event_type="tool_call", payload={"tool": tool}))
    db_session.add(BotEvent(conversation_id=1, event_type="llm_usage", payload={"cost_usd": "0.2"}))
    db_session.commit()
    settings_id = db_session.scalar(select(BotSettings.id))
    assert settings_id is not None
    return {
        "building": building.id,
        "info": info.id,
        "template": template.id,
        "quick": quick.id,
        "run": run.id,
        "settings": settings_id,
    }


ADMIN_PAGES = [
    "/admin/building/list",
    "/admin/building/details/{building}",
    "/admin/building/edit/{building}",
    "/admin/building-info/list",
    "/admin/building-info/details/{info}",
    "/admin/building-info/edit/{info}",
    "/admin/building-info/create",
    "/admin/bot-settings/edit/{settings}",
    "/admin/bot-settings/details/{settings}",
    "/admin/wa-template/list",
    "/admin/wa-template/details/{template}",
    "/admin/wa-template/edit/{template}",
    "/admin/wa-template/create",
    "/admin/quick-reply/list",
    "/admin/quick-reply/edit/{quick}",
    "/admin/quick-reply/create",
    "/admin/sync-run/list",
    "/admin/sync-run/details/{run}",
    "/admin/users",
    "/admin/metrics",
    "/admin/conversations",
    "/admin/phones",
    "/admin/verifications",
    "/admin/amenities",
    "/admin/claims",
    "/admin/claims/new",
    "/admin/no-existe",
]


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_every_page_in_spanish_and_nothing_technical(
    logged_in: Panel, data: dict[str, int], url: str
) -> None:
    response = logged_in.client.get(url.format(**data))
    assert response.status_code in (200, 404), url
    assert english_in(response.text) == [], url
    assert technical_in(response.text) == [], url
    # One style sheet for everything, no page with its own.
    assert "/admin/static/panel.css" in response.text and "<style" not in response.text


@pytest.mark.parametrize(
    ("url", "title"),
    [
        ("/admin/building/list", "Edificios"),
        ("/admin/building-info/list", "Información de edificios"),
        ("/admin/wa-template/list", "Plantillas de WhatsApp"),
        ("/admin/quick-reply/list", "Respuestas rápidas"),
        ("/admin/sync-run/list", "Sincronizaciones"),
        ("/admin/phones", "Teléfonos"),
        ("/admin/verifications", "Verificaciones"),
        ("/admin/users", "Usuarios del panel"),
    ],
)
def test_every_list_has_a_page_title(
    logged_in: Panel, data: dict[str, int], url: str, title: str
) -> None:
    page = logged_in.client.get(url).text
    assert f'<h2 class="page-title">{title}</h2>' in page


# --- Login ------------------------------------------------------------------------------------


def test_the_login_page(panel: Panel) -> None:
    page = panel.client.get("/admin/login").text
    for text in ("Ingresar al panel del Estudio Diego Rufeil", "Usuario", "Contraseña"):
        assert text in page
    assert re.search(r">\s*Ingresar\s*</button>", page)
    assert english_in(page) == []


def test_wrong_credentials_in_spanish_with_the_same_status(panel: Panel) -> None:
    response = panel.login(ADMIN, "otra-clave-inventada")
    assert response.status_code == 400
    assert "Usuario o contraseña incorrectos." in response.text
    assert "Invalid credentials" not in response.text


def test_login_and_logout_work_as_before(panel: Panel) -> None:
    response = panel.login(ADMIN, PASSWORD)
    assert response.status_code == 302
    page = panel.client.get("/admin/conversations")
    assert page.status_code == 200 and "Salir" in page.text
    panel.client.get("/admin/logout")
    again = panel.client.get("/admin/conversations", follow_redirects=False)
    assert again.status_code == 302 and "/admin/login" in again.headers["location"]


# --- SQLAdmin's lists and forms -----------------------------------------------------------------


def test_save_buttons_keep_working(logged_in: Panel, data: dict[str, int]) -> None:
    url = f"/admin/quick-reply/edit/{data['quick']}"
    page = logged_in.client.get(url).text
    # Spanish label, SQLAdmin's English value (it compares it to choose where to go).
    assert re.search(r'value="Save and continue editing"[^>]*>\s*Guardar y seguir editando', page)
    form = {"title": "Saludo", "content": "¡Hola!", "sort_order": "0", "active": "y"}

    stay = logged_in.client.post(
        url, data={**form, "save": "Save and continue editing"}, follow_redirects=False
    )
    assert stay.status_code == 302 and stay.headers["location"].endswith(url)
    back = logged_in.client.post(url, data={**form, "save": "Save"}, follow_redirects=False)
    assert back.status_code == 302 and back.headers["location"].endswith("/admin/quick-reply/list")


def test_actions_only_where_there_are_batch_actions(logged_in: Panel, data: dict[str, int]) -> None:
    for url in ("/admin/building/list", "/admin/wa-template/list", "/admin/sync-run/list"):
        page = logged_in.client.get(url).text
        assert "Acciones" not in page and 'id="dropdownMenuButton"' not in page, url
    # Information and quick replies can be deleted in batch.
    page = logged_in.client.get("/admin/quick-reply/list").text
    assert "Acciones" in page and "Borrar los elegidos" in page


def test_row_buttons_have_their_text(logged_in: Panel, data: dict[str, int]) -> None:
    page = visible(logged_in.client.get("/admin/quick-reply/list").text)
    assert "Ver" in page and "Editar" in page and "Borrar" in page


def test_buildings_without_their_code_except_in_its_own_column(
    logged_in: Panel, data: dict[str, int]
) -> None:
    buildings = visible(logged_in.client.get("/admin/building/list").text)
    # (?<!\d): the code column ("9095", the factories' counter) may come right before the name.
    assert "TORRE IDIOMA" in buildings and not re.search(r"(?<!\d)095 TORRE IDIOMA", buildings)
    assert "Código ConsorPlus" in buildings
    for url in ("/admin/building-info/list", f"/admin/building-info/edit/{data['info']}"):
        page = visible(logged_in.client.get(url).text)
        assert "TORRE IDIOMA" in page and "095 TORRE IDIOMA" not in page, url


def test_information_categories_and_filters_in_spanish(
    logged_in: Panel, data: dict[str, int]
) -> None:
    listing = visible(logged_in.client.get("/admin/building-info/list").text)
    assert "Horarios Horario de pileta" in listing  # the category in Spanish, not "horarios"
    assert "Todos" in listing and "Reglamento" in listing
    url = f"/admin/building-info/edit/{data['info']}"
    form = logged_in.client.get(url).text
    assert '<option selected value="horarios">Horarios</option>' in form
    assert 'title="Obligatorio"' in form


def test_saving_information_keeps_its_category(
    logged_in: Panel, data: dict[str, int], db_session: Session
) -> None:
    # SQLAdmin used to compare the category by its enum name: saving reset it to the first.
    url = f"/admin/building-info/edit/{data['info']}"
    form = {"building": str(data["building"]), "category": "horarios", "title": "Horario",
            "content": "De 9 a 21.", "save": "Save"}  # fmt: skip
    assert logged_in.client.post(url, data=form, follow_redirects=False).status_code == 302
    db_session.expire_all()
    info = db_session.get(BuildingInfo, data["info"])
    assert info is not None and info.category == BuildingInfoCategory.HORARIOS


def test_form_errors_in_spanish(logged_in: Panel, data: dict[str, int]) -> None:
    url = f"/admin/quick-reply/edit/{data['quick']}"
    page = logged_in.client.post(url, data={"title": "", "content": "", "save": "Save"}).text
    assert "Este campo es obligatorio." in page
    assert "This field is required" not in page


def test_sync_runs_readable(logged_in: Panel, data: dict[str, int]) -> None:
    listing = visible(logged_in.client.get("/admin/sync-run/list").text)
    for text in ("Nocturna", "Propietarios y unidades", "Con errores", "hoy 02:32", "Bien"):
        assert text in listing, text
    page = logged_in.client.get(f"/admin/sync-run/details/{data['run']}").text
    text = visible(page)
    for expected in (
        "Unidades", "2.450", "Teléfonos válidos", "1.800", "Duración", "12 min",
        "Por tipo de unidad", "Cochera", "Con teléfono del propietario",
        "Errores por tipo", "ConsorPlus no disponible", "Algo nuevo", f"{RUN_AT:%d/%m/%Y} 02:32",
    ):  # fmt: skip
        assert expected in text, expected
    assert "<li>Edificio 095: no disponible</li>" in page
    assert "{" not in text and "units_" not in text and "_" not in text.split("Estadísticas")[1]


def test_metrics_in_spanish(logged_in: Panel, data: dict[str, int]) -> None:
    page = visible(logged_in.client.get("/admin/metrics").text)
    assert "Consultar deuda" in page and "Buscar unidad" in page
    assert "US$ 0,20" in page
    assert "get_debt" not in page and "LLM_PRICE" not in page


def test_the_style_sheet_logo_and_favicon(logged_in: Panel) -> None:
    for name in ("panel.css", "panel.js", "logo.svg"):
        assert logged_in.client.get(f"/admin/static/{name}").status_code == 200
    page = logged_in.client.get("/admin/conversations").text
    assert '<link rel="icon" href="/admin/static/logo.svg">' in page
