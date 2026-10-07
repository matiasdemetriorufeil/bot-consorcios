"""Who sees what, checked on the server: an operator against every admin page and action (403,
a page of the panel with its menu, and nothing changes), what she can do, and the menu of each
role. The panel runs with APP_ENV=development, so the test chat exists. Invented data only.

One panel for the whole module (building it and hashing passwords took most of the time):
each test binds its sessionmaker to that test's connection (rolled back afterwards, as
everywhere), creates its user and logs in again.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.admin import setup_admin
from app.admin.amenities import AmenitiesView
from app.admin.auth import AdminAuth, LoginLimiter, hash_password
from app.db.models import (
    Amenity,
    AmenitySlot,
    BotSettings,
    Building,
    BuildingInfo,
    BuildingInfoCategory,
    PanelRole,
    PanelUser,
    QuickReply,
    Reservation,
    ReservationStatus,
    SyncJob,
    SyncKind,
    SyncRun,
    WaTemplate,
)
from tests.admin.conftest import (
    OPERATOR,
    TABLE_ADMIN,
    USER_PASSWORD,
    FakeClock,
    Panel,
    admin_settings,
)
from tests.bot import factories as f
from tests.whatsapp.fakes import FakeWhatsApp

USER_HASH = hash_password(USER_PASSWORD)  # once for the module

CBA = ZoneInfo("America/Argentina/Cordoba")
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=CBA)  # Monday
FRIDAY = date(2026, 10, 9)
ONLY_ADMINS = "Esta sección es solo para administradores."

OPERATOR_MENU = [
    "Conversaciones",
    "Teléfonos",
    "Verificaciones",
    "Reservas de SUM",
    "Guía",
]
ADMIN_SECTION = [
    "Edificios",
    "Información de edificios",
    "Configuración del bot",
    "Plantillas de WhatsApp",
    "Respuestas rápidas",
    "Usuarios",
    "Métricas",
    "Sincronizaciones",
]


@dataclass
class Ids:
    building: int
    info: int
    template: int
    quick_reply: int
    run: int
    user: int
    settings: int
    amenity: int
    slot: int
    unit: int
    building_without_sum: int


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AmenitiesView, "now", lambda self: NOW)


@pytest.fixture
def ids(db_session: Session) -> Ids:
    building = f.building(db_session, "093 TORRE PERMISOS")
    other = f.building(db_session, "094 EDIFICIO SIN SUM")
    unit = f.unit(db_session, building, "01-A")
    info = BuildingInfo(
        building_id=building.id,
        title="Horario inventado",
        content="De 9 a 21.",
        category=BuildingInfoCategory.HORARIOS,
    )
    template = WaTemplate(name="plantilla_inventada", label="Retomar", body="Hola")
    quick = QuickReply(title="Saludo", content="¡Hola!")
    run = SyncRun(kind=SyncKind.NIGHTLY, job=SyncJob.ROSTER, stats={})
    amenity = Amenity(building_id=building.id, max_advance_days=30)
    slot = AmenitySlot(weekday=4, start_time=time(14), end_time=time(18))
    amenity.slots = [slot]
    db_session.add_all([info, template, quick, run, amenity])
    user = _user(db_session, "lucia", PanelRole.OPERATOR)
    db_session.commit()
    settings_id = db_session.scalar(select(BotSettings.id))
    assert settings_id is not None
    return Ids(
        building.id, info.id, template.id, quick.id, run.id, user.id, settings_id,
        amenity.id, slot.id, unit.id, other.id,
    )  # fmt: skip


def _user(session: Session, username: str, role: PanelRole) -> PanelUser:
    user = PanelUser(
        username=username, display_name=username.title(), password_hash=USER_HASH, role=role
    )
    session.add(user)
    session.flush()
    return user


@dataclass
class Shared:
    client: TestClient
    maker: sessionmaker
    clock: FakeClock


@pytest.fixture(scope="module")
def shared(db_engine: Engine) -> Iterator[Shared]:
    """The panel (in development) for every test of the module; its sessionmaker gets each
    test's connection in _login."""
    maker = sessionmaker(join_transaction_mode="create_savepoint")
    clock = FakeClock()
    settings = admin_settings(app_env="development")
    app = FastAPI()
    setup_admin(
        app,
        maker,
        settings,
        auth=AdminAuth(settings, LoginLimiter(clock=clock), clock=clock),
        sender_factory=FakeWhatsApp,
        bot_factory=lambda: pytest.fail("no bot"),
    )
    with TestClient(app, base_url="https://testserver") as client:
        yield Shared(client, maker, clock)


def _login(shared: Shared, db_session: Session, username: str, role: PanelRole) -> Panel:
    shared.maker.configure(bind=db_session.get_bind())
    _user(db_session, username, role)
    db_session.commit()
    shared.client.cookies.clear()
    response = shared.client.post(
        "/admin/login",
        data={"username": username, "password": USER_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return Panel(client=shared.client, session=db_session, clock=shared.clock)


@pytest.fixture
def op(shared: Shared, db_session: Session) -> Panel:
    """An operator, with the panel in development (the test chat exists)."""
    return _login(shared, db_session, OPERATOR, PanelRole.OPERATOR)


@pytest.fixture
def boss(shared: Shared, db_session: Session) -> Panel:
    """A table admin, with the panel in development."""
    return _login(shared, db_session, TABLE_ADMIN, PanelRole.ADMIN)


def _menu(page: str) -> list[str]:
    nav = page.split('id="navbarSupportedContent"', 1)[1].split("</nav>", 1)[0]
    return re.findall(r'<span class="nav-link-title">([^<]+)</span>', nav)


# --- An operator against every admin page and action -------------------------------------------

SETTINGS_FORM = {"welcome_message": "Cambio de la operadora", "save": "Save"}
BUILDING_FORM = {"name": "X", "address": "Cambio de la operadora", "save": "Save"}
INFO_FORM = {"building": "{building}", "category": "otros", "title": "T", "content": "C"}
TEMPLATE_FORM = {"label": "L", "name": "n", "language": "es", "body": "B", "save": "Save"}
QUICK_FORM = {"title": "T", "content": "C", "sort_order": "1", "save": "Save"}
USER_FORM = {"display_name": "Nueva", "username": "nueva", "role": "admin",
             "password": "clave-inventada-larga", "password2": "clave-inventada-larga"}  # fmt: skip

ADMIN_ACTIONS: list[tuple[str, str, dict[str, str] | None]] = [
    # Buildings and their information
    ("get", "/admin/building/list", None),
    ("get", "/admin/building/details/{building}", None),
    ("get", "/admin/building/edit/{building}", None),
    ("post", "/admin/building/edit/{building}", BUILDING_FORM),
    ("get", "/admin/building-info/list", None),
    ("get", "/admin/building-info/details/{info}", None),
    ("get", "/admin/building-info/create", None),
    ("post", "/admin/building-info/create", INFO_FORM),
    ("get", "/admin/building-info/edit/{info}", None),
    ("post", "/admin/building-info/edit/{info}", INFO_FORM),
    ("delete", "/admin/building-info/delete?pks={info}", None),
    # Bot settings (the list goes to the form: followed)
    ("get", "/admin/bot-settings/list", None),
    ("get", "/admin/bot-settings/details/{settings}", None),
    ("get", "/admin/bot-settings/edit/{settings}", None),
    ("post", "/admin/bot-settings/edit/{settings}", SETTINGS_FORM),
    # WhatsApp templates and quick replies
    ("get", "/admin/wa-template/list", None),
    ("get", "/admin/wa-template/create", None),
    ("post", "/admin/wa-template/create", TEMPLATE_FORM),
    ("get", "/admin/wa-template/edit/{template}", None),
    ("post", "/admin/wa-template/edit/{template}", TEMPLATE_FORM),
    ("get", "/admin/quick-reply/list", None),
    ("get", "/admin/quick-reply/create", None),
    ("post", "/admin/quick-reply/create", QUICK_FORM),
    ("get", "/admin/quick-reply/edit/{quick_reply}", None),
    ("post", "/admin/quick-reply/edit/{quick_reply}", QUICK_FORM),
    ("delete", "/admin/quick-reply/delete?pks={quick_reply}", None),
    # Users, metrics, sync runs
    ("get", "/admin/users", None),
    ("post", "/admin/users", USER_FORM),
    ("get", "/admin/users/{user}", None),
    ("post", "/admin/users/{user}", {"change": "role", "role": "admin"}),
    ("post", "/admin/users/{user}", {"change": "deactivate"}),
    ("get", "/admin/metrics", None),
    ("get", "/admin/sync-run/list", None),
    ("get", "/admin/sync-run/details/{run}", None),
    # Test chat, people search included
    ("get", "/admin/dev-chat", None),
    ("get", "/admin/dev-chat?q=Inventada", None),
    ("get", "/admin/dev-chat?phone=%2B5493515550000", None),
    ("get", "/admin/dev-chat/poll?phone=%2B5493515550000", None),
    ("post", "/admin/dev-chat/send", {"phone": "+5493515550000", "text": "hola"}),
    ("post", "/admin/dev-chat/restart", {"phone": "+5493515550000"}),
    # The SUM's set-up
    ("post", "/admin/amenities/new", {"building_id": "{building_without_sum}"}),
    ("get", "/admin/amenities/{amenity}/config", None),
    ("post", "/admin/amenities/{amenity}/config", {"name": "Cambio", "min_advance_hours": "0"}),
    (
        "post",
        "/admin/amenities/{amenity}/slots",
        {"weekday": "0", "start": "10:00", "end": "12:00"},
    ),  # fmt: skip
    ("post", "/admin/amenities/{amenity}/slots", {"copy_from": "4", "to": "5"}),
    ("post", "/admin/amenities/{amenity}/slots/{slot}/remove", None),
]


def _fill(value: str, ids: Ids) -> str:
    return value.format(**vars(ids))


def _snapshot(session: Session) -> dict[str, Any]:
    session.expire_all()
    count = lambda model: session.scalar(select(func.count()).select_from(model))  # noqa: E731
    settings = session.scalar(select(BotSettings))
    amenity = session.scalar(select(Amenity))
    return {
        "info": count(BuildingInfo),
        "templates": count(WaTemplate),
        "quick": count(QuickReply),
        "users": [(u.username, u.role, u.active) for u in session.scalars(select(PanelUser))],
        "amenities": count(Amenity),
        "slots": [
            (s.weekday, s.start_time, s.active) for s in session.scalars(select(AmenitySlot))
        ],
        "amenity": (amenity.name, amenity.min_advance_hours) if amenity else None,
        "welcome": settings.welcome_message if settings else None,
        "buildings": [(b.name, b.address) for b in session.scalars(select(Building))],
    }


@pytest.mark.parametrize(("method", "url", "data"), ADMIN_ACTIONS)
def test_an_operator_gets_403_inside_the_panel(
    op: Panel, ids: Ids, method: str, url: str, data: dict[str, str] | None
) -> None:
    before = _snapshot(op.session)
    kwargs: dict[str, Any] = {}
    if data is not None:
        kwargs["data"] = {k: _fill(v, ids) for k, v in data.items()}
    response = getattr(op.client, method)(_fill(url, ids), **kwargs)

    assert response.status_code == 403
    assert ONLY_ADMINS in response.text
    # Inside the panel: the operator's menu, to go on.
    assert _menu(response.text) == OPERATOR_MENU
    assert _snapshot(op.session) == before
    assert op.admin_events() == []


def test_the_403_page_of_an_admin_is_not_about_admins(boss: Panel, ids: Ids) -> None:
    # SQLAdmin's own 403 (sync runs are read only).
    response = boss.client.get("/admin/sync-run/create")
    assert response.status_code == 403
    assert ONLY_ADMINS not in response.text and "Esto no se puede hacer" in response.text


def test_an_error_page_without_login_has_no_menu(panel: Panel) -> None:
    response = panel.client.get("/admin/no-existe")
    assert response.status_code == 404
    assert "No se encontró esta página." in response.text
    assert "navbarSupportedContent" not in response.text


# --- What an operator can do --------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/admin/",
        "/admin/conversations",
        "/admin/phones",
        "/admin/phones?tab=all",
        "/admin/verifications",
        "/admin/amenities",
        "/admin/amenities/{amenity}/week",
        "/admin/claims",
    ],
)
def test_an_operator_opens_her_pages(op: Panel, ids: Ids, url: str) -> None:
    assert op.client.get(_fill(url, ids)).status_code == 200


def test_an_operator_books_and_cancels_but_sees_no_set_up(op: Panel, ids: Ids) -> None:
    listing = op.client.get("/admin/amenities").text
    assert "Configuración" not in listing and "Agregar el SUM" not in listing
    week = op.client.get(f"/admin/amenities/{ids.amenity}/week").text
    assert f"/admin/amenities/{ids.amenity}/config" not in week

    book = f"/admin/amenities/{ids.amenity}/book"
    params = {"slot": str(ids.slot), "date": FRIDAY.isoformat()}
    assert op.client.get(book, params=params).status_code == 200
    response = op.client.post(
        book, data={**params, "unit_id": str(ids.unit)}, follow_redirects=False
    )
    assert response.status_code == 302
    op.session.expire_all()
    reservation = op.session.scalar(select(Reservation))
    assert reservation is not None and reservation.status == ReservationStatus.CONFIRMED

    cancel = f"/admin/amenities/{ids.amenity}/reservations/{reservation.id}"
    response = op.client.post(cancel, data={"confirm": "yes"}, follow_redirects=False)
    assert response.status_code == 302
    op.session.expire_all()
    assert op.session.get(Reservation, reservation.id).status == ReservationStatus.CANCELLED
    actions = [(e["action"], e["admin_user"]) for e in op.admin_events()]
    assert actions == [("reservation_created", OPERATOR), ("reservation_cancelled", OPERATOR)]


def test_an_admin_sees_the_set_up_of_the_sum(boss: Panel, ids: Ids) -> None:
    listing = boss.client.get("/admin/amenities").text
    assert f"/admin/amenities/{ids.amenity}/config" in listing and "Agregar el SUM" in listing
    assert boss.client.get(f"/admin/amenities/{ids.amenity}/config").status_code == 200


# --- The menu of each role -----------------------------------------------------------------------


def test_the_operator_menu(op: Panel) -> None:
    page = op.client.get("/admin/conversations").text
    assert _menu(page) == OPERATOR_MENU
    assert "Administración" not in page
    assert "Reclamos" not in page and "Chat de prueba" not in page


def test_the_admin_menu_in_development(boss: Panel) -> None:
    page = boss.client.get("/admin/conversations").text
    assert _menu(page) == [*OPERATOR_MENU, *ADMIN_SECTION, "Chat de prueba"]
    # The "Administración" heading, between the operators' part and the admins'.
    nav = page.split('id="navbarSupportedContent"', 1)[1]
    assert nav.index("Reservas de SUM") < nav.index("Administración") < nav.index("Edificios")
    assert "Reclamos" not in nav


def test_the_admin_menu_in_production(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/conversations").text
    assert _menu(page) == [*OPERATOR_MENU, *ADMIN_SECTION]
    assert logged_in.client.get("/admin/dev-chat").status_code == 404
