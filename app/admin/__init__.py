"""Admin panel (SQLAdmin) at /admin, for the studio's employees.

"Conversaciones" first (the WhatsApp inbox, app.admin.conversations), then buildings and their
information, all phones (search and unlink), phones to review, operator verifications, sync
runs (read only), general bot settings, simple metrics, the SUM reservations
(app.admin.amenities), for now an empty "Reclamos" page, the WhatsApp templates and quick
replies of the inbox, the panel users (app.admin.users) and the attachments of WhatsApp
messages (app.admin.wa_media, not in the menu).

Login: the users of panel_users (admin | operator) plus the .env user as rescue admin
(app.admin.auth). Operators do not see (403) the views marked AdminOnly.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from sqladmin import Admin
from sqlalchemy.orm import sessionmaker

from app.admin.amenities import AmenitiesView, ClaimsView
from app.admin.auth import AdminAuth, LoginLimiter
from app.admin.conversations import ConversationsView
from app.admin.inbox import Sender
from app.admin.users import UsersView
from app.admin.views import (
    BotSettingsAdmin,
    BuildingAdmin,
    BuildingInfoAdmin,
    MetricsView,
    PhoneAdmin,
    PhoneReviewAdmin,
    QuickReplyAdmin,
    SyncRunAdmin,
    VerificationRequestAdmin,
    WaTemplateAdmin,
)
from app.admin.wa_media import WaMediaView
from app.config import Settings
from app.whatsapp.client import WhatsAppClient, WhatsAppError

TEMPLATES_DIR = Path(__file__).parent / "templates"
VIEWS = (
    ConversationsView,
    BuildingAdmin,
    BuildingInfoAdmin,
    PhoneAdmin,
    PhoneReviewAdmin,
    VerificationRequestAdmin,
    SyncRunAdmin,
    BotSettingsAdmin,
    MetricsView,
    AmenitiesView,
    ClaimsView,
    WaTemplateAdmin,
    QuickReplyAdmin,
    UsersView,
    WaMediaView,
)


def whatsapp_sender(settings: Settings) -> Callable[[], Sender | None]:
    """The WhatsApp client for the inbox, built on first use (None: CHANNEL is not whatsapp
    or its token or phone number id are missing)."""
    built: list[Sender] = []

    def get() -> Sender | None:
        if settings.channel != "whatsapp":
            return None
        if not built:
            try:
                built.append(WhatsAppClient.from_settings(settings))
            except WhatsAppError:
                return None
        return built[0]

    return get


def setup_admin(
    app: FastAPI,
    session_maker: sessionmaker,
    settings: Settings,
    *,
    auth: AdminAuth | None = None,
    sender_factory: Callable[[], Sender | None] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Admin:
    """Mount the panel. session_maker must be the panel's own: SQLAdmin reconfigures it
    (autoflush=False), so never pass the bot's SessionLocal. sender_factory and clock: for
    tests (a fake WhatsApp client, a fixed time)."""
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
        VerificationRequestAdmin: {"_timezone": settings.timezone},
        PhoneAdmin: {"_timezone": settings.timezone},
        MetricsView: {"timezone": settings.timezone, "session_maker": session_maker},
        AmenitiesView: {"timezone": settings.timezone, "session_maker": session_maker},
        UsersView: {
            "timezone": settings.timezone,
            "session_maker": session_maker,
            "env_username": settings.admin_username,
        },
        WaMediaView: {"media_dir": settings.whatsapp_media_dir, "session_maker": session_maker},
    }
    for view in VIEWS:
        # A subclass per panel: SQLAdmin stores state on the view class (session_maker...).
        admin.add_view(type(view.__name__, (view,), dict(extra.get(view, {}))))
    return admin
