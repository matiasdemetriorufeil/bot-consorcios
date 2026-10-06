"""Admin panel login: the users of panel_users (one per employee, role admin or operator)
plus the .env user (ADMIN_USERNAME, ADMIN_PASSWORD_HASH), a fixed rescue admin that is not in
the table and cannot be changed from the panel. Sessions are signed with ADMIN_SECRET_KEY and
there is a temporary lockout after repeated failures.

Passwords are stored as salted scrypt hashes (hash_password), never in clear text. Without
ADMIN_SECRET_KEY every login is rejected: the panel is never open. Each request of a table
user re-reads it: deactivating it or changing its password (session_version) ends its open
sessions right away.
"""

import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings
from app.db.models import PanelRole, PanelUser

logger = logging.getLogger(__name__)

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60
SESSION_SECONDS = 8 * 60 * 60
SESSION_COOKIE = "admin_session"
MIN_PASSWORD_LENGTH = 12

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str, salt: bytes | None = None) -> str:
    """ "scrypt:n:r:p:salt_hex:hash_hex" (what ADMIN_PASSWORD_HASH holds; no "$", which
    Docker Compose would try to expand in .env)."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt:{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}:{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split(":")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(hash_hex)
        digest = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected)


# Checked when the username is wrong, so that both cases take the same time.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


class LoginLimiter:
    """Consecutive failed logins per key (username + client IP). After MAX_FAILED_LOGINS
    the key is locked for LOCKOUT_SECONDS. In memory: the api runs a single process."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def seconds_locked(self, key: str) -> float:
        """Seconds left of the lockout (0 when not locked)."""
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return 0
            left = until - self.clock()
            if left <= 0:
                del self._locked_until[key]
                return 0
            return left

    def failure(self, key: str) -> bool:
        """Record a failed login. True when it locks the key."""
        with self._lock:
            count = self._failures.get(key, 0) + 1
            if count >= MAX_FAILED_LOGINS:
                self._failures.pop(key, None)
                self._locked_until[key] = self.clock() + LOCKOUT_SECONDS
                return True
            self._failures[key] = count
            return False

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


@dataclass(frozen=True)
class PanelLogin:
    """Who logged in (user_id None: the .env user)."""

    username: str
    role: PanelRole
    user_id: int | None = None
    session_version: int = 0


def find_user(session: Session, username: str) -> PanelUser | None:
    return session.scalar(
        select(PanelUser).where(func.lower(PanelUser.username) == username.strip().lower())
    )


class AdminAuth(AuthenticationBackend):
    def __init__(
        self,
        settings: Settings,
        limiter: LoginLimiter | None = None,
        clock: Callable[[], float] = time.time,
        session_maker: sessionmaker | None = None,
    ) -> None:
        secret = settings.admin_secret_key.get_secret_value() if settings.admin_secret_key else ""
        self.configured = bool(secret)
        if not self.configured:
            logger.warning("Admin panel without ADMIN_SECRET_KEY: every login is rejected")
        super().__init__(
            # A random key when missing: nobody can forge a session (and nobody logs in).
            secret_key=secret or secrets.token_hex(32),
            session_cookie=SESSION_COOKIE,
            max_age=SESSION_SECONDS,
            # Strict: no cookie on requests coming from other sites (the panel has no CSRF
            # tokens and sqladmin's actions are GET links).
            same_site="strict",
            https_only=settings.app_env == "production",
        )
        self.username = settings.admin_username
        self._password_hash = (
            settings.admin_password_hash.get_secret_value() if settings.admin_password_hash else ""
        )
        # The .env rescue admin needs both values.
        self.env_user_configured = bool(self.username and self._password_hash)
        self.limiter = limiter or LoginLimiter()
        self.clock = clock
        # Set by setup_admin (the panel's own sessionmaker): the users of panel_users.
        self.session_maker = session_maker
        # Set by setup_admin: renders the lockout message on the login page.
        self.templates: Any = None

    def is_env_user(self, username: str) -> bool:
        return bool(self.username) and hmac.compare_digest(
            username.strip().lower().encode(), self.username.lower().encode()
        )

    async def login(self, request: Request) -> Response | bool:
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        client = request.client.host if request.client else "?"
        key = f"{username.lower()}|{client}"

        if left := self.limiter.seconds_locked(key):
            return await self._locked_response(request, left)
        who = self._check_credentials(username, password)
        if who is not None:
            self.limiter.success(key)
            request.session.clear()
            request.session.update(
                {
                    "admin_user": who.username,
                    "role": who.role.value,
                    "user_id": who.user_id,
                    "session_version": who.session_version,
                    "login_at": self.clock(),
                }
            )
            logger.info("Admin panel: login of %s", who.username)
            return True
        locked = self.limiter.failure(key)
        logger.warning("Admin panel: failed login from %s%s", client, " (locked)" if locked else "")
        if locked:
            return await self._locked_response(request, LOCKOUT_SECONDS)
        return False

    def _check_credentials(self, username: str, password: str) -> PanelLogin | None:
        """Who logs in, or None. A hash is always checked, so that a wrong username takes as
        long as a wrong password."""
        if not self.configured or not username:
            verify_password(password, _DUMMY_HASH)
            return None
        if self.is_env_user(username):
            stored = self._password_hash if self.env_user_configured else _DUMMY_HASH
            if verify_password(password, stored) and self.env_user_configured:
                return PanelLogin(self.username, PanelRole.ADMIN)
            return None
        if self.session_maker is None:
            verify_password(password, _DUMMY_HASH)
            return None
        with self.session_maker() as session:
            user = find_user(session, username)
            ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
            if user is None or not ok or not user.active:
                return None
            user.last_login_at = datetime.now(UTC)
            session.commit()
            return PanelLogin(user.username, user.role, user.id, user.session_version)

    async def _locked_response(self, request: Request, seconds: float) -> Response:
        minutes = max(1, round(seconds / 60))
        error = f"Demasiados intentos fallidos. Probá de nuevo en {minutes} minutos."
        return await self.templates.TemplateResponse(
            request, "sqladmin/login.html", {"error": error}, status_code=429
        )

    async def logout(self, request: Request) -> Response | bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> Response | bool:
        if not self.configured:
            return False
        user = request.session.get("admin_user")
        login_at = request.session.get("login_at")
        if not isinstance(user, str) or not isinstance(login_at, int | float):
            return False
        if self.clock() - login_at > SESSION_SECONDS:
            request.session.clear()
            return False
        user_id = request.session.get("user_id")
        if user_id is None:
            if not (self.env_user_configured and user == self.username):
                request.session.clear()
                return False
            request.state.panel_role = PanelRole.ADMIN.value
            return True
        role = self._current_role(user_id, user, request.session.get("session_version"))
        if role is None:
            request.session.clear()
            return False
        request.session["role"] = role  # an admin may have changed it
        request.state.panel_role = role
        return True

    def _current_role(self, user_id: Any, username: str, version: Any) -> str | None:
        """The user's role if its session is still valid (active, same password)."""
        if self.session_maker is None or not isinstance(user_id, int):
            return None
        with self.session_maker() as session:
            row = session.get(PanelUser, user_id)
            if (
                row is None
                or not row.active
                or row.username != username
                or row.session_version != version
            ):
                return None
            return PanelRole(row.role).value

    async def get_user_id(self, request: Request) -> Any:
        return request.session.get("admin_user") if "session" in request.scope else None


def admin_user(request: Request) -> str:
    """The logged-in panel user's username (for the audit log, assigned_to and operator)."""
    return str(request.session.get("admin_user") or "?")


def user_role(request: Request) -> str | None:
    """The role authenticate checked in this request (else the session's)."""
    role = getattr(request.state, "panel_role", None)
    if role is None and "session" in request.scope:
        role = request.session.get("role")
    return role


def is_admin(request: Request) -> bool:
    return user_role(request) == PanelRole.ADMIN.value


def current_user_id(request: Request) -> int | None:
    value = request.session.get("user_id") if "session" in request.scope else None
    return value if isinstance(value, int) else None


def display_names(session: Session) -> dict[str, str]:
    """username -> the name shown in conversations (the .env user shows its username)."""
    rows = session.execute(select(PanelUser.username, PanelUser.display_name))
    return {username: name for username, name in rows}


class AdminOnly:
    """Mixin (first base) for panel views only admins see and open: 403 for operators."""

    def is_visible(self, request: Request) -> bool:
        return is_admin(request)

    def is_accessible(self, request: Request) -> bool:
        return is_admin(request)
