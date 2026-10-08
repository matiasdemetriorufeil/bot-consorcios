"""Admin panel (SQLAdmin) at /admin, for the studio's employees.

The menu, for everyone: "Conversaciones" (the WhatsApp inbox, app.admin.conversations),
"Reclamos" (app.admin.claims), "Teléfonos" (app.admin.phones), "Verificaciones"
(app.admin.verifications), "Reservas de SUM" (app.admin.amenities) and "Guía"
(app.admin.guide). Then, under "Administración" and only for admins: buildings and their
information, the claims' set-up (providers, kinds of problem and who attends each one in each
building: app.admin.claims_setup), the bot settings, the WhatsApp templates and quick replies of
the inbox, the panel users (app.admin.users), metrics, sync runs (read only) and, in development
only, the test chat (app.admin.dev_chat). Out of the menu: the attachments of WhatsApp messages
(app.admin.wa_media). /admin/ goes to "Conversaciones".

Everything in Spanish and in one style: SQLAdmin's texts through app.admin.i18n, values
through app.admin.labels and app.admin.formatting (also Jinja filters: phone, building, when,
full, day, money), and one style sheet (app/admin/static, served at /admin/static). Help on
every page from app.admin.help: a line under the title, empty pages, confirmations.

Login: the users of panel_users (admin | operator) plus the .env user as rescue admin
(app.admin.auth). Operators neither see nor open (403, a page of the panel) the views marked
AdminOnly, nor the SUM's set-up.
"""

import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from sqladmin import Admin, ModelView
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from app.admin import formatting, help, i18n, labels
from app.admin.amenities import AmenitiesView
from app.admin.auth import AdminAuth, LoginLimiter
from app.admin.claims import ClaimsView, urgent_open_count
from app.admin.claims_setup import BuildingClaimsView, ClaimCategoryAdmin, ProviderAdmin
from app.admin.conversations import ConversationsView
from app.admin.dev_chat import DevChatView
from app.admin.guide import GUIDE_FILES_DIR, GuideView
from app.admin.inbox import Sender
from app.admin.phones import PhonesView
from app.admin.users import UsersView
from app.admin.verifications import VerificationsView
from app.admin.views import (
    BotSettingsAdmin,
    BuildingAdmin,
    BuildingInfoAdmin,
    MetricsView,
    QuickReplyAdmin,
    SyncRunAdmin,
    WaTemplateAdmin,
    type_formatters,
)
from app.admin.wa_media import WaMediaView
from app.claims.notify import Notifier
from app.config import Settings
from app.db.models import BotSettings
from app.db.session import SessionLocal
from app.whatsapp.bot import WhatsAppBot
from app.whatsapp.client import WhatsAppError
from app.whatsapp.simulator import build_client

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
STATIC_PATH = "/static"
BOT_SETTINGS_IDENTITY = "bot-settings"
# In the menu's order: first what the operators use, then the admin-only ones (AdminOnly,
# under "Administración"; the test chat goes last, in development). The rest is not in the menu.
VIEWS = (
    ConversationsView,
    ClaimsView,
    PhonesView,
    VerificationsView,
    AmenitiesView,
    GuideView,
    BuildingAdmin,
    BuildingInfoAdmin,
    ProviderAdmin,
    ClaimCategoryAdmin,
    BuildingClaimsView,
    BotSettingsAdmin,
    WaTemplateAdmin,
    QuickReplyAdmin,
    UsersView,
    MetricsView,
    SyncRunAdmin,
    WaMediaView,
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

    def get() -> Notifier | None:
        client = sender()
        return Notifier(client, settings) if client is not None else None

    return get


def _default_bot() -> WhatsAppBot:
    from app.whatsapp.webhook import _cached_bot

    return _cached_bot()


async def _home(request: Request) -> Response:
    """The panel's empty home page: straight to the inbox (which asks for the login)."""
    return RedirectResponse(request.url_for("admin:view-conversations"), status_code=302)


def _bot_settings_form(session_maker: sessionmaker) -> Callable[[Request], Response]:
    """The bot settings' list has a single row: the menu (and SQLAdmin's "Save" and "Cancel",
    which go back to the list) open its form instead. The form checks login and role."""

    def endpoint(request: Request) -> Response:
        with session_maker() as session:
            settings_id = session.scalar(select(BotSettings.id).order_by(BotSettings.id).limit(1))
        if settings_id is None:
            raise HTTPException(status_code=404)
        url = request.url_for("admin:edit", identity=BOT_SETTINGS_IDENTITY, pk=settings_id)
        return RedirectResponse(url, status_code=302)

    return endpoint


def _urgent_claims_counter(session_maker: sessionmaker) -> Callable[[], int]:
    """The menu's counter of urgent open claims ("Reclamos"); 0 if it cannot be read."""

    def count() -> int:
        try:
            with session_maker() as session:
                return urgent_open_count(session)
        except Exception:
            return 0

    return count


def static_version() -> str:
    """A short hash of the panel's style sheet and script."""
    digest = hashlib.sha256()
    for name in ("panel.css", "panel.js"):
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:10]


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
    env.globals.update(help.globals_for_templates())  # type: ignore[attr-defined]
    # In the style sheet's and script's URLs: a change reaches every browser (no stale cache).
    env.globals["static_version"] = static_version()  # type: ignore[attr-defined]


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
    admin.templates.env.globals["urgent_claims_count"] = _urgent_claims_counter(session_maker)
    auth.templates = admin.templates
    sender = sender_factory or whatsapp_sender(settings)
    conversations: dict[str, object] = {
        "timezone": settings.timezone,
        "session_maker": session_maker,
        "sender_factory": staticmethod(sender),
    }
    if clock is not None:
        conversations["clock"] = staticmethod(clock)
    extra: dict[type, dict[str, object]] = {
        ConversationsView: conversations,
        VerificationsView: {"timezone": settings.timezone, "session_maker": session_maker},
        PhonesView: {"timezone": settings.timezone, "session_maker": session_maker},
        MetricsView: {"timezone": settings.timezone, "session_maker": session_maker},
        AmenitiesView: {"timezone": settings.timezone, "session_maker": session_maker},
        ClaimsView: {
            "timezone": settings.timezone,
            "session_maker": session_maker,
            "notifier_factory": staticmethod(claims_notifier(sender, settings)),
        },
        UsersView: {
            "timezone": settings.timezone,
            "session_maker": session_maker,
            "env_username": settings.admin_username,
        },
        BuildingClaimsView: {"session_maker": session_maker},
        WaMediaView: {"media_dir": settings.whatsapp_media_dir, "session_maker": session_maker},
    }
    views: list[type] = list(VIEWS)
    if settings.app_env == "development":
        dev_chat: dict[str, object] = {
            "enabled": True,
            "timezone": settings.timezone,
            "session_maker": session_maker,
            "bot_factory": staticmethod(bot_factory or _default_bot),
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
    routes.insert(0, Mount(STATIC_PATH, StaticFiles(directory=STATIC_DIR), name="panel-static"))
    # The guide's pictures (invented data only: they are in the repository).
    routes.insert(
        0, Mount("/guide-files", StaticFiles(directory=GUIDE_FILES_DIR), name="guide-files")
    )
    # Before SQLAdmin's "/{identity}/list".
    routes.insert(
        1,
        Route(
            f"/{BOT_SETTINGS_IDENTITY}/list",
            endpoint=_bot_settings_form(session_maker),
            name="bot-settings-form",
        ),
    )
    return admin
