"""Admin panel (SQLAdmin) at /admin, for the studio's employees.

The menu, for everyone: "Pendientes" (what waits for someone of the studio: the first page,
app.admin.pending_view), "Conversaciones" (the WhatsApp inbox, app.admin.conversations),
"Reclamos" (app.admin.claims), "Vecinos" (app.admin.neighbors), "Teléfonos"
(app.admin.phones), "Verificaciones" (app.admin.verifications), "Reservas de SUM"
(app.admin.amenities) and "Guía" (app.admin.guide). Then, under "Administración" and only for
admins: buildings and their information, the claims' set-up (providers, kinds of problem and
who attends each one in each building: app.admin.claims_setup), the bot settings, the WhatsApp
templates and quick replies of the inbox, the panel users (app.admin.users), metrics, sync runs
(read only) and, in development only, the test chat (app.admin.dev_chat). Out of the menu: the
attachments of WhatsApp messages (app.admin.wa_media). /admin/ (and the login) go to
"Pendientes".

Every page asks for the panel's numbers every few seconds (app.admin.pending, panel.js): the
menu's counters (next to Conversaciones, Reclamos, Teléfonos and Verificaciones: the views'
menu_counter), the tab's title and the alerts with sound.

Everything in Spanish and in one style: SQLAdmin's texts through app.admin.i18n, values
through app.admin.labels and app.admin.formatting (also Jinja filters: phone, building, when,
full, day, money), and one style sheet (app/admin/static, served at /admin/static). Help on
every page from app.admin.help: a line under the title, empty pages, confirmations.

Login: the users of panel_users (admin | operator) plus the .env user as rescue admin
(app.admin.auth). Operators neither see nor open (403, a page of the panel) the views marked
AdminOnly, nor the SUM's set-up.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import sqladmin
from fastapi import FastAPI
from jinja2 import pass_context
from sqladmin import Admin, ModelView
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from app.admin import errors, formatting, help, hours, i18n, labels, numbers, pending
from app.admin.amenities import AmenitiesView
from app.admin.auth import AdminAuth, LoginLimiter, current_user_id
from app.admin.claims import ClaimsView, claim_links
from app.admin.claims_setup import (
    BuildingClaimsView,
    ClaimCategoryAdmin,
    ClaimSettingsAdmin,
    ProviderAdmin,
)
from app.admin.conversations import ConversationsView
from app.admin.dev_chat import DevChatView
from app.admin.guide import GUIDE_FILES_DIR, GuideView
from app.admin.inbox import Sender
from app.admin.neighbors import NeighborsView
from app.admin.pending_view import PendingView
from app.admin.phones import PhonesView
from app.admin.static_cache import StaticCache, versions
from app.admin.users import UsersView
from app.admin.verifications import VerificationsView
from app.admin.views import (
    BotSettingsAdmin,
    BuildingAdmin,
    BuildingInfoAdmin,
    ClosedDayAdmin,
    MetricsView,
    QuickReplyAdmin,
    SyncRunAdmin,
    WaTemplateAdmin,
    type_formatters,
)
from app.admin.wa_media import WaMediaView
from app.claims.notify import Notifier
from app.config import Settings
from app.db.models import BotSettings, ClaimSettings, PanelUser
from app.db.session import SessionLocal
from app.whatsapp.bot import WhatsAppBot
from app.whatsapp.client import WhatsAppError
from app.whatsapp.simulator import build_client

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
STATIC_PATH = "/static"
SQLADMIN_STATIC_DIR = Path(sqladmin.__file__).parent / "statics"
BOT_SETTINGS_IDENTITY = "bot-settings"
CLAIM_SETTINGS_IDENTITY = "claim-settings"
# In the menu's order: first what the operators use, then the admin-only ones (AdminOnly,
# under "Administración"; the test chat goes last, in development). The rest is not in the menu.
VIEWS = (
    PendingView,
    ConversationsView,
    ClaimsView,
    NeighborsView,
    PhonesView,
    VerificationsView,
    AmenitiesView,
    GuideView,
    # «Administración», by its blocks (MENU_GROUPS), each in the order of its menu.
    BuildingAdmin,
    BuildingInfoAdmin,
    BuildingClaimsView,
    ProviderAdmin,
    ClaimCategoryAdmin,
    ClaimSettingsAdmin,
    BotSettingsAdmin,
    ClosedDayAdmin,
    QuickReplyAdmin,
    WaTemplateAdmin,
    UsersView,
    MetricsView,
    SyncRunAdmin,
    WaMediaView,
)
# The blocks of «Administración» in the menu (and in «Más» on phones), in this order: each
# admin view says which one is its own (menu_group). They fold; the one of the open page is
# open (templates/sqladmin/_macros.html, panel.js remembers the rest).
MENU_GROUPS = (
    ("buildings", "Edificios"),
    ("claims", "Reclamos"),
    ("bot", "Bot"),
    ("system", "Sistema"),
)


def whatsapp_sender(settings: Settings) -> Callable[[], Sender | None]:
    """The WhatsApp client for the inbox, built on first use (None: its token or phone number
    id are missing, in production; in development what goes to the test chat is simulated:
    app.whatsapp.simulator)."""
    built: list[Sender] = []

    def get() -> Sender | None:
        if not built:
            try:
                built.append(build_client(settings, SessionLocal))
            except WhatsAppError:
                return None
        return built[0]

    return get


def claims_notifier(
    sender: Callable[[], Sender | None], settings: Settings
) -> Callable[[], Notifier | None]:
    """The claims' WhatsApp messages from the panel, with the inbox's client (None without
    WhatsApp)."""

    def get(now: Callable[[], datetime] | None = None) -> Notifier | None:
        client = sender()
        return Notifier(client, settings, now=now) if client is not None else None

    return get


def _default_bot() -> WhatsAppBot:
    from app.whatsapp.webhook import _cached_bot

    return _cached_bot()


async def _home(request: Request) -> Response:
    """The panel's empty home page: straight to «Pendientes» (which asks for the login)."""
    return RedirectResponse(request.url_for("admin:view-pending"), status_code=302)


def _single_row_form(
    session_maker: sessionmaker, model: Any, identity: str
) -> Callable[[Request], Response]:
    """A settings' list with a single row (the bot's, the claims'): the menu (and SQLAdmin's
    "Save" and "Cancel", which go back to the list) open its form instead. The form checks
    login and role."""

    def endpoint(request: Request) -> Response:
        with session_maker() as session:
            settings_id = session.scalar(select(model.id).order_by(model.id).limit(1))
        if settings_id is None:
            raise HTTPException(status_code=404)
        url = request.url_for("admin:edit", identity=identity, pk=settings_id)
        return RedirectResponse(url, status_code=302)

    return endpoint


def _menu_counts(session_maker: sessionmaker) -> Callable[[Request], dict[str, int]]:
    """The menu's counters as the page is drawn (app.admin.pending.counts), read once per
    request; zeros if they cannot be read (panel.js keeps them up to date anyway)."""

    def get(request: Request) -> dict[str, int]:
        cached = getattr(request.state, "menu_counts", None)
        if cached is None:
            try:
                with session_maker() as session:
                    cached = pending.counts(session)
            except Exception:
                cached = {}
            request.state.menu_counts = cached
        return cached

    return get


RESCUE_NAME = "Administrador"


def _panel_user_name(session_maker: sessionmaker) -> Callable[[Request], str]:
    """Who is in, at the foot of the menu (and in «Más» on phones): her name in the panel,
    read once per request. The rescue admin of the .env (no row in the base) is
    "Administrador"; so is anyone whose row cannot be read."""

    def get(request: Request) -> str:
        cached = getattr(request.state, "panel_user_name", None)
        if cached is None:
            cached = RESCUE_NAME
            user_id = current_user_id(request)
            if user_id is not None:
                try:
                    with session_maker() as session:
                        row = session.get(PanelUser, user_id)
                        if row is not None:
                            cached = row.display_name or row.username
                except Exception:
                    cached = RESCUE_NAME
            request.state.panel_user_name = cached
        return cached

    return get


def asset_versions() -> dict[str, dict[str, str]]:
    """The version of every static file, by its mount: a hash of its content, computed once at
    start-up (app.admin.static_cache)."""
    return {"panel-static": versions(STATIC_DIR), "statics": versions(SQLADMIN_STATIC_DIR)}


def _asset_url(known: dict[str, dict[str, str]]) -> Callable[..., str]:
    """asset_url('panel-static', 'panel.css') in the templates: its URL with ?v=<its hash>."""

    @pass_context
    def asset_url(context: Any, mount: str, path: str) -> str:
        url = context["request"].url_for(f"admin:{mount}", path=path)
        return f"{url}?v={known[mount][path]}"

    return asset_url


def panel_env(settings: Settings) -> dict[str, bool]:
    """What the environment shows. The technical detail of an error, only when APP_ENV is
    exactly "development"; the «Desarrollo» label unless APP_ENV is set to "production" (a
    missing APP_ENV also shows it: an alarm, not a silence)."""
    explicit = "app_env" in settings.model_fields_set
    return {
        "show_details": explicit and settings.app_env == "development",
        "dev_label": not (explicit and settings.app_env == "production"),
    }


def _install_texts(env: object, timezone: str) -> None:
    """Spanish for SQLAdmin's templates, and the panel's formats as Jinja filters."""
    i18n.install(env)
    filters = env.filters  # type: ignore[attr-defined]
    filters["phone"] = formatting.phone
    filters["building"] = formatting.building
    filters["when"] = lambda value: formatting.when(value, timezone=timezone)
    filters["full"] = lambda value: formatting.full(value, timezone)
    filters["day"] = lambda value: formatting.day(value, timezone)
    filters["money"] = formatting.money
    filters["reason_short"] = lambda code: (labels.reason(code) or ("", ""))[0]
    filters["claim_links"] = claim_links
    filters["state"] = labels.state
    env.globals.update(help.globals_for_templates())  # type: ignore[attr-defined]
    env.globals["menu_groups"] = MENU_GROUPS  # type: ignore[attr-defined]
    env.globals["hour_options"] = hours.hour_choices  # type: ignore[attr-defined]
    env.globals["number_choices"] = numbers.choices_of  # type: ignore[attr-defined]
    env.globals["error_texts"] = errors.texts  # type: ignore[attr-defined]
    # In every static file's URL (asset_url): a change reaches every browser (no stale cache).
    env.globals["asset_url"] = _asset_url(asset_versions())  # type: ignore[attr-defined]


def setup_admin(
    app: FastAPI,
    session_maker: sessionmaker,
    settings: Settings,
    *,
    auth: AdminAuth | None = None,
    sender_factory: Callable[[], Sender | None] | None = None,
    clock: Callable[[], datetime] | None = None,
    bot_factory: Callable[[], WhatsAppBot] | None = None,
) -> Admin:
    """Mount the panel. session_maker must be the panel's own: SQLAdmin reconfigures it
    (autoflush=False), so never pass the bot's SessionLocal. sender_factory, clock and
    bot_factory (the test chat's bot): for tests (a fake WhatsApp client, a fixed time)."""
    auth = auth or AdminAuth(settings, LoginLimiter())
    auth.session_maker = auth.session_maker or session_maker
    admin = Admin(
        app,
        session_maker=session_maker,
        base_url="/admin",
        title="Estudio Diego Rufeil",
        favicon_url=f"/admin{STATIC_PATH}/logo.svg",
        templates_dir=str(TEMPLATES_DIR),
        authentication_backend=auth,
    )
    _install_texts(admin.templates.env, settings.timezone)
    admin.templates.env.globals["menu_counts"] = _menu_counts(session_maker)
    admin.templates.env.globals["panel_user_name"] = _panel_user_name(session_maker)
    env_flags = panel_env(settings)
    admin.templates.env.globals["panel_env"] = env_flags
    auth.templates = admin.templates
    # Errors in Spanish and in the panel's style (app.admin.errors): SQLAdmin's HTTPException
    # handler replaced; any other error caught inside the session (the page keeps its menu).
    admin.admin.exception_handlers[HTTPException] = errors.http_error_handler(
        admin.templates, env_flags["show_details"]
    )
    admin.admin.user_middleware.append(
        Middleware(
            errors.ServerErrors, templates=admin.templates, show_details=env_flags["show_details"]
        )
    )
    sender = sender_factory or whatsapp_sender(settings)
    conversations: dict[str, object] = {
        "timezone": settings.timezone,
        "session_maker": session_maker,
        "sender_factory": staticmethod(sender),
    }
    claims: dict[str, object] = {
        "timezone": settings.timezone,
        "session_maker": session_maker,
        "notifier_factory": staticmethod(claims_notifier(sender, settings)),
    }
    pending_view: dict[str, object] = {
        "timezone": settings.timezone,
        "session_maker": session_maker,
    }
    # «Días cerrados»: which year is the current one, and which holidays already passed.
    closed_days: dict[str, object] = {"timezone": settings.timezone}
    if clock is not None:
        conversations["clock"] = staticmethod(clock)
        claims["clock"] = staticmethod(clock)
        pending_view["clock"] = staticmethod(clock)
        closed_days["clock"] = staticmethod(clock)
    extra: dict[type, dict[str, object]] = {
        PendingView: pending_view,
        ConversationsView: conversations,
        VerificationsView: {
            "timezone": settings.timezone,
            "session_maker": session_maker,
            "notifier_factory": claims["notifier_factory"],
        },
        NeighborsView: {"timezone": settings.timezone, "session_maker": session_maker},
        PhonesView: {"timezone": settings.timezone, "session_maker": session_maker},
        MetricsView: {"timezone": settings.timezone, "session_maker": session_maker},
        AmenitiesView: {"timezone": settings.timezone, "session_maker": session_maker},
        ClaimsView: claims,
        UsersView: {
            "timezone": settings.timezone,
            "session_maker": session_maker,
            "env_username": settings.admin_username,
        },
        BuildingClaimsView: {"session_maker": session_maker},
        ClosedDayAdmin: closed_days,
        WaMediaView: {"media_dir": settings.whatsapp_media_dir, "session_maker": session_maker},
    }
    views: list[type] = list(VIEWS)
    if settings.app_env == "development":
        dev_chat: dict[str, object] = {
            "enabled": True,
            "timezone": settings.timezone,
            "session_maker": session_maker,
            "bot_factory": staticmethod(bot_factory or _default_bot),
            "notifier_factory": claims["notifier_factory"],
        }
        if clock is not None:
            dev_chat["clock"] = staticmethod(clock)
        extra[DevChatView] = dev_chat
        views.insert(views.index(SyncRunAdmin) + 1, DevChatView)
    listed, detail = type_formatters(settings.timezone)
    for view in views:
        attributes = dict(extra.get(view, {}))
        if issubclass(view, ModelView):
            attributes["column_type_formatters"] = listed
            attributes["column_type_formatters_detail"] = detail
            attributes["form_base_class"] = i18n.SpanishForm
        # A subclass per panel: SQLAdmin stores state on the view class (session_maker...).
        admin.add_view(type(view.__name__, (view,), attributes))
    # Replaces SQLAdmin's empty index (same name: the logo links to it).
    routes = admin.admin.router.routes
    routes[:] = [r for r in routes if getattr(r, "name", None) != "index"]
    routes.insert(0, Route("/", endpoint=_home, name="index"))
    # Static files with a long cache when versioned, gzip only for text (app.admin.static_cache).
    routes[:] = [
        Mount(r.path, StaticCache(r.app), name="statics")
        if isinstance(r, Mount) and r.name == "statics"
        else r
        for r in routes
    ]
    routes.insert(
        0, Mount(STATIC_PATH, StaticCache(StaticFiles(directory=STATIC_DIR)), name="panel-static")
    )
    # The guide's pictures (invented data only: they are in the repository).
    routes.insert(
        0,
        Mount(
            "/guide-files", StaticCache(StaticFiles(directory=GUIDE_FILES_DIR)), name="guide-files"
        ),
    )
    # Before SQLAdmin's "/{identity}/list".
    for identity, model in (
        (BOT_SETTINGS_IDENTITY, BotSettings),
        (CLAIM_SETTINGS_IDENTITY, ClaimSettings),
    ):
        routes.insert(
            1,
            Route(
                f"/{identity}/list",
                endpoint=_single_row_form(session_maker, model, identity),
                name=f"{identity}-form",
            ),
        )
    return admin
