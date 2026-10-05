"""Admin panel login: hashed password from .env, lockout, session expiry."""

import pytest
from sqlalchemy.orm import Session

from app.admin.auth import (
    LOCKOUT_SECONDS,
    MAX_FAILED_LOGINS,
    SESSION_SECONDS,
    hash_password,
    verify_password,
)
from tests.admin.conftest import ADMIN, PASSWORD, Panel, admin_settings, build_panel


def test_password_hash_roundtrip() -> None:
    stored = hash_password("otra-clave-inventada")
    assert stored.startswith("scrypt:") and "otra-clave-inventada" not in stored
    assert "$" not in stored  # Docker Compose would expand it in .env
    assert verify_password("otra-clave-inventada", stored)
    assert not verify_password("otra-clave-inventadA", stored)
    assert not verify_password("otra-clave-inventada", "texto que no es un hash")
    assert hash_password("x") != hash_password("x")  # salted


def test_panel_requires_login(panel: Panel) -> None:
    response = panel.client.get("/admin/building/list", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin/login")


def test_login_logout(panel: Panel) -> None:
    response = panel.login()
    assert response.status_code == 302
    cookie = response.headers["set-cookie"].lower()
    assert "samesite=strict" in cookie and "secure" in cookie and "httponly" in cookie
    assert panel.client.get("/admin/building/list").status_code == 200

    panel.client.get("/admin/logout")
    response = panel.client.get("/admin/building/list", follow_redirects=False)
    assert response.status_code == 302


@pytest.mark.parametrize(
    ("username", "password"), [(ADMIN, "clave-equivocada"), ("otra-persona", PASSWORD)]
)
def test_wrong_credentials(panel: Panel, username: str, password: str) -> None:
    response = panel.login(username, password)
    assert response.status_code == 400
    assert panel.client.get("/admin/building/list", follow_redirects=False).status_code == 302


def test_lockout_after_failed_logins(panel: Panel) -> None:
    for _ in range(MAX_FAILED_LOGINS - 1):
        assert panel.login(password="mal").status_code == 400
    locked = panel.login(password="mal")
    assert locked.status_code == 429
    assert "Demasiados intentos fallidos" in locked.text

    # Even the right password is refused while locked.
    still = panel.login()
    assert still.status_code == 429
    assert panel.client.get("/admin/building/list", follow_redirects=False).status_code == 302

    panel.clock.advance(LOCKOUT_SECONDS + 1)
    assert panel.login().status_code == 302


def test_successful_login_resets_the_failure_count(panel: Panel) -> None:
    for _ in range(MAX_FAILED_LOGINS - 1):
        panel.login(password="mal")
    assert panel.login().status_code == 302
    for _ in range(MAX_FAILED_LOGINS - 1):
        assert panel.login(password="mal").status_code == 400


def test_session_expires(logged_in: Panel) -> None:
    logged_in.clock.advance(SESSION_SECONDS + 1)
    response = logged_in.client.get("/admin/building/list", follow_redirects=False)
    assert response.status_code == 302


@pytest.mark.parametrize("missing", ["admin_username", "admin_password_hash", "admin_secret_key"])
def test_unconfigured_panel_rejects_every_login(db_session: Session, missing: str) -> None:
    panel = build_panel(
        db_session, admin_settings(**{missing: None if missing != "admin_username" else ""})
    )
    with panel.client:
        assert panel.login().status_code == 400
        response = panel.client.get("/admin/building/list", follow_redirects=False)
        assert response.status_code == 302
