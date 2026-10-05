"""Admin panel login: one user from .env (ADMIN_USERNAME, ADMIN_PASSWORD_HASH), sessions
signed with ADMIN_SECRET_KEY, and a temporary lockout after repeated failures.

The password is stored as a salted scrypt hash (hash_password), never in clear text. With
any of the three settings missing, every login is rejected: the panel is never open.
"""

import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings

logger = logging.getLogger(__name__)

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60
SESSION_SECONDS = 8 * 60 * 60
SESSION_COOKIE = "admin_session"

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


class AdminAuth(AuthenticationBackend):
    def __init__(
        self,
        settings: Settings,
        limiter: LoginLimiter | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        secret = settings.admin_secret_key.get_secret_value() if settings.admin_secret_key else ""
        self.configured = bool(settings.admin_username and settings.admin_password_hash and secret)
        if not self.configured:
            logger.warning(
                "Admin panel without ADMIN_USERNAME, ADMIN_PASSWORD_HASH or ADMIN_SECRET_KEY: "
                "every login is rejected"
            )
        super().__init__(
            # A random key when missing: nobody can forge a session (and nobody logs in).
            secret_key=secret or secrets.token_hex(32),
            session_cookie=SESSION_COOKIE,
            max_age=SESSION_SECONDS,
            # Strict: no cookie on requests coming from other sites (sqladmin has no CSRF
            # tokens and its actions are GET links).
            same_site="strict",
            https_only=settings.app_env == "production",
        )
        self.username = settings.admin_username
        self._password_hash = (
            settings.admin_password_hash.get_secret_value() if settings.admin_password_hash else ""
        )
        self.limiter = limiter or LoginLimiter()
        self.clock = clock
        # Set by setup_admin: renders the lockout message on the login page.
        self.templates: Any = None

    async def login(self, request: Request) -> Response | bool:
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        client = request.client.host if request.client else "?"
        key = f"{username.lower()}|{client}"

        if left := self.limiter.seconds_locked(key):
            return await self._locked_response(request, left)
        if self._credentials_ok(username, password):
            self.limiter.success(key)
            request.session.clear()
            request.session.update({"admin_user": self.username, "login_at": self.clock()})
            logger.info("Admin panel: login of %s", self.username)
            return True
        locked = self.limiter.failure(key)
        logger.warning("Admin panel: failed login from %s%s", client, " (locked)" if locked else "")
        if locked:
            return await self._locked_response(request, LOCKOUT_SECONDS)
        return False

    def _credentials_ok(self, username: str, password: str) -> bool:
        if not self.configured:
            verify_password(password, _DUMMY_HASH)
            return False
        user_ok = hmac.compare_digest(username.encode(), self.username.encode())
        password_ok = verify_password(password, self._password_hash if user_ok else _DUMMY_HASH)
        return user_ok and password_ok

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
        if user != self.username or not isinstance(login_at, int | float):
            return False
        if self.clock() - login_at > SESSION_SECONDS:
            request.session.clear()
            return False
        return True

    async def get_user_id(self, request: Request) -> Any:
        return request.session.get("admin_user") if "session" in request.scope else None


def admin_user(request: Request) -> str:
    """The logged-in panel user (for resolved_by and the audit log)."""
    return str(request.session.get("admin_user") or "?")
