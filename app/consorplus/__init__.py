"""The ONLY entry point to ConsorPlus. Read-only: see allowlist.py and CLAUDE.md."""

from app.consorplus.client import ConsorPlusClient
from app.consorplus.errors import (
    ConsorPlusError,
    ConsorPlusUnavailableError,
    ForbiddenActionError,
    LoginError,
    NotFoundError,
    ParseError,
    SessionExpiredError,
)
from app.consorplus.models import (
    Building,
    DebtLine,
    RosterContact,
    RosterRow,
    Unit,
    UnitDebt,
)

__all__ = [
    "Building",
    "ConsorPlusClient",
    "ConsorPlusError",
    "ConsorPlusUnavailableError",
    "DebtLine",
    "ForbiddenActionError",
    "LoginError",
    "NotFoundError",
    "ParseError",
    "RosterContact",
    "RosterRow",
    "SessionExpiredError",
    "Unit",
    "UnitDebt",
]
