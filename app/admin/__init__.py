"""Admin panel (SQLAdmin) at /admin, for the studio's operators.

Buildings and their information, all phones (search and unlink), phones to review, operator
verifications, sync runs (read only), general bot settings, simple metrics, the SUM
reservations (app.admin.amenities), for now an empty "Reclamos" page, and the attachments
of WhatsApp messages (app.admin.wa_media, not in the menu).
Login with the single user of .env.
"""

from pathlib import Path

from fastapi import FastAPI
from sqladmin import Admin
from sqlalchemy.orm import sessionmaker

from app.admin.amenities import AmenitiesView, ClaimsView
from app.admin.auth import AdminAuth, LoginLimiter
from app.admin.views import (
    BotSettingsAdmin,
    BuildingAdmin,
    BuildingInfoAdmin,
    MetricsView,
    PhoneAdmin,
    PhoneReviewAdmin,
    SyncRunAdmin,
    VerificationRequestAdmin,
)
from app.admin.wa_media import WaMediaView
from app.config import Settings

TEMPLATES_DIR = Path(__file__).parent / "templates"
VIEWS = (
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
    WaMediaView,
)


def setup_admin(
    app: FastAPI,
    session_maker: sessionmaker,
    settings: Settings,
    *,
    auth: AdminAuth | None = None,
) -> Admin:
    """Mount the panel. session_maker must be the panel's own: SQLAdmin reconfigures it
    (autoflush=False), so never pass the bot's SessionLocal."""
    auth = auth or AdminAuth(settings, LoginLimiter())
    admin = Admin(
        app,
        session_maker=session_maker,
        base_url="/admin",
        title="Estudio Diego Rufeil",
        templates_dir=str(TEMPLATES_DIR),
        authentication_backend=auth,
    )
    auth.templates = admin.templates
    extra = {
        VerificationRequestAdmin: {"_timezone": settings.timezone},
        PhoneAdmin: {"_timezone": settings.timezone},
        MetricsView: {"timezone": settings.timezone, "session_maker": session_maker},
        AmenitiesView: {"timezone": settings.timezone, "session_maker": session_maker},
        WaMediaView: {"media_dir": settings.whatsapp_media_dir, "session_maker": session_maker},
    }
    for view in VIEWS:
        # A subclass per panel: SQLAdmin stores state on the view class (session_maker...).
        admin.add_view(type(view.__name__, (view,), dict(extra.get(view, {}))))
    return admin
