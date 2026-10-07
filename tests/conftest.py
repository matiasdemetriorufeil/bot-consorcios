import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.bot.bot_config import invalidate_bot_config
from app.config import Settings, get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Fail instead of waiting forever: Postgres unreachable (e.g. Docker Desktop paused), or a
# lock held by another session on the test database (e.g. a test run stopped in a debugger).
TEST_CONNECT_TIMEOUT_SECONDS = 10
TEST_LOCK_TIMEOUT = "10s"
# pg advisory lock key: only one pytest run at a time per test database. Windows and the
# api container share the Postgres server, and each run drops the test schema.
TEST_RUN_LOCK_KEY = 0x7E57_DB


def _test_database_url() -> str:
    """TEST_DATABASE_URL, or DATABASE_URL with the database name suffixed with `_test`.

    Adds a connect timeout and a lock timeout (libpq parameters, so Alembic gets them too).
    """
    if explicit := os.environ.get("TEST_DATABASE_URL"):
        url = make_url(explicit)
    else:
        url = make_url(Settings().database_url)
        url = url.set(database=f"{url.database}_test")
    url = url.update_query_dict(
        {
            "connect_timeout": str(TEST_CONNECT_TIMEOUT_SECONDS),
            "options": f"-c lock_timeout={TEST_LOCK_TIMEOUT}",
            **url.query,
        }
    )
    return url.render_as_string(hide_password=False)


# Read once, before _isolated_settings hides the environment from the tests.
TEST_DATABASE_URL = _test_database_url()


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Settings() in a test sees only its defaults: neither `.env` nor the environment
    (the api container gets `.env` as environment variables)."""
    fields = set(Settings.model_fields)
    for name in list(os.environ):
        if name.lower() in fields:
            monkeypatch.delenv(name)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    # The admin panel's bot settings are cached for a minute: no test sees another's row.
    invalidate_bot_config()
    yield
    get_settings.cache_clear()
    invalidate_bot_config()


class NetworkAccessError(RuntimeError):
    """A test tried to reach a host other than this machine."""


# Plus the test Postgres host ("db" in the api container): psycopg resolves it in Python.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", make_url(TEST_DATABASE_URL).host}


def _check_local(host: Any) -> None:
    if isinstance(host, bytes):
        host = host.decode()
    if host not in _LOCAL_HOSTS:
        raise NetworkAccessError(f"Los tests no pueden salir a la red (host: {host!r})")


@pytest.fixture(autouse=True, scope="session")
def _no_network() -> Iterator[None]:
    """No test may reach ConsorPlus, Meta, the LLM providers or SMTP.

    Only Python sockets are guarded; the test Postgres host is allowed.
    """
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        _check_local(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check_local(address[0])
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check_local(address[0])
        return real_connect_ex(self, address)

    patch = pytest.MonkeyPatch()
    patch.setattr(socket, "getaddrinfo", getaddrinfo)
    patch.setattr(socket.socket, "connect", connect)
    patch.setattr(socket.socket, "connect_ex", connect_ex)
    yield
    patch.undo()


def _ensure_database_exists(url: str) -> None:
    target = make_url(url)
    admin_engine = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin_engine.dispose()


def _alembic_config(url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """Fresh test database built with the real Alembic migrations (not create_all)."""
    url = TEST_DATABASE_URL
    try:
        _ensure_database_exists(url)
    except OperationalError as exc:
        message = f"Postgres de test no disponible ({make_url(url).host}): {exc.orig}"
        # In CI a missing database is a broken pipeline, never a silent skip.
        if os.environ.get("CI"):
            pytest.fail(message, pytrace=False)
        pytest.skip(message)

    engine = create_engine(url)
    # Held for the whole run on its own connection; released when it closes (or dies).
    run_lock = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    if not run_lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": TEST_RUN_LOCK_KEY}):
        run_lock.close()
        engine.dispose()
        pytest.fail(
            f"Otra corrida de pytest está usando la base {make_url(url).database} "
            "(¿en Windows y en el contenedor a la vez?). Esperá a que termine.",
            pytrace=False,
        )

    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))

        config = _alembic_config(url)
        # Round-trip to make sure the downgrade also works.
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")

        yield engine
    finally:
        run_lock.close()
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """Session inside an outer transaction that is rolled back after each test.

    Code under test may call commit(); it only releases a SAVEPOINT.
    """
    with db_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
