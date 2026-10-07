"""Admin panel (SQLAdmin) at /admin, for the studio's employees.

The menu, for everyone: "Conversaciones" (the WhatsApp inbox, app.admin.conversations),
"Teléfonos" (app.admin.phones), "Verificaciones" (app.admin.verifications) and "Reservas de
SUM" (app.admin.amenities). Then, under "Administración" and only for admins: buildings and
their information, the bot settings, the WhatsApp templates and quick replies of the inbox,
the panel users (app.admin.users), metrics, sync runs (read only) and, in development only,
the test chat (app.admin.dev_chat). Out of the menu: "Reclamos" (an empty page for now) and
the attachments of WhatsApp messages (app.admin.wa_media). /admin/ goes to "Conversaciones".

Login: the users of panel_users (admin | operator) plus the .env user as rescue admin
(app.admin.auth). Operators neither see nor open (403, a page of the panel) the views marked
AdminOnly, nor the SUM's set-up.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from sqladmin import Admin
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from app.admin.amenities import AmenitiesView, ClaimsView
from app.admin.auth import AdminAuth, LoginLimiter
from app.admin.conversations import ConversationsView
from app.admin.dev_chat import DevChatView
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
)
from app.admin.wa_media import WaMediaView
from app.config import Settings
from app.db.models import BotSettings
from app.db.session import SessionLocal
from app.whatsapp.bot import WhatsAppBot
from app.whatsapp.client import WhatsAppError
from app.whatsapp.simulator import build_client

TEMPLATES_DIR = Path(__file__).parent / "templates"
BOT_SETTINGS_IDENTITY = "bot-settings"
# In the menu's order: first what the operators use, then the admin-only ones (AdminOnly,
# under "Administración"; the test chat goes last, in development). The rest is not in the menu.
VIEWS = (
    ConversationsView,
    PhonesView,
    VerificationsView,
    AmenitiesView,
    BuildingAdmin,
    BuildingInfoAdmin,
    BotSettingsAdmin,
    WaTemplateAdmin,
    QuickReplyAdmin,
    UsersView,
    MetricsView,
    SyncRunAdmin,
    ClaimsView,
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
        templates_dir=str(TEMPLATES_DIR),
        authentication_backend=auth,
    )
    auth.templates = admin.templates
    conversations: dict[str, object] = {
        "timezone": settings.timezone,
        "session_maker": session_maker,
        "sender_factory": staticmethod(sender_factory or whatsapp_sender(settings)),
    }
    if clock is not None:
        conversations["clock"] = staticmethod(clock)
    extra: dict[type, dict[str, object]] = {
        ConversationsView: conversations,
        VerificationsView: {"timezone": settings.timezone, "session_maker": session_maker},
        PhonesView: {"timezone": settings.timezone, "session_maker": session_maker},
        MetricsView: {"timezone": settings.timezone, "session_maker": session_maker},
        AmenitiesView: {"timezone": settings.timezone, "session_maker": session_maker},
        UsersView: {
            "timezone": settings.timezone,
            "session_maker": session_maker,
            "env_username": settings.admin_username,
        },
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
    for view in views:
        # A subclass per panel: SQLAdmin stores state on the view class (session_maker...).
        admin.add_view(type(view.__name__, (view,), dict(extra.get(view, {}))))
    # Replaces SQLAdmin's empty index (same name: the logo links to it).
    routes = admin.admin.router.routes
    routes[:] = [r for r in routes if getattr(r, "name", None) != "index"]
    routes.insert(0, Route("/", endpoint=_home, name="index"))
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
