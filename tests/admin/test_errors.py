"""The panel's error pages (app.admin.errors): in Spanish and in its style, «Volver a Pendientes»,
no technical detail unless APP_ENV is exactly development, JSON with the same status code for
panel.js, and an unexpected error logged once. Invented data only."""

import logging
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.admin import pending
from tests.admin.conftest import Panel, admin_settings, build_panel, make_user

FETCH = {"X-Panel-Fetch": "1"}
SECRET = "detalle-interno-inventado"


def _boom(*args: object, **kwargs: object) -> object:
    raise RuntimeError(SECRET)


@pytest.fixture
def production(db_session: Session) -> Iterator[Panel]:
    built = build_panel(db_session, admin_settings(app_env="production"))
    with built.client:
        assert built.login().status_code == 302
        yield built


@pytest.fixture
def development(db_session: Session) -> Iterator[Panel]:
    built = build_panel(db_session, admin_settings(app_env="development"))
    with built.client:
        assert built.login().status_code == 302
        yield built


# --- 404 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["/admin/no-existe", "/admin/claims/999999", "/admin/users/999999"])
def test_a_404_in_the_panel_style(logged_in: Panel, url: str) -> None:
    response = logged_in.client.get(url)
    assert response.status_code == 404
    page = response.text
    assert '<h1 class="error-title">No encontramos esa página</h1>' in page
    assert ">Volver a Pendientes</a>" in page and 'href="https://testserver/admin/pending"' in page
    assert 'class="card-body empty-state"' in page  # the panel's empty block
    assert 'id="menu-user"' in page  # with a session: the menu, to go on
    assert "<title>No encontramos esa página · Panel · Estudio Diego Rufeil</title>" in page
    assert "inexistente" not in page and "Not Found" not in page


def test_without_a_session_no_menu(panel: Panel) -> None:
    response = panel.client.get("/admin/no-existe")
    assert response.status_code == 404
    assert "No encontramos esa página" in response.text
    assert 'id="menu-user"' not in response.text and "bottom-nav" not in response.text
    assert '<html lang="es">' in response.text


# --- 500 ---------------------------------------------------------------------------------------


def test_a_500_says_nothing_internal_in_production(
    production: Panel, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(pending, "pending_numbers", _boom)
    with caplog.at_level(logging.ERROR):
        response = production.client.get("/admin/pending")
    assert response.status_code == 500
    page = response.text
    assert "Algo salió mal. Probá de nuevo; si sigue, avisale a un administrador." in page
    assert ">Volver a Pendientes</a>" in page
    assert SECRET not in page and "RuntimeError" not in page and "Traceback" not in page
    # In the log, once, with its traceback; no "response already started".
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None and SECRET in str(errors[0].exc_info[1])
    assert "already started" not in caplog.text


def test_a_500_shows_its_detail_only_in_development(
    development: Panel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pending, "pending_numbers", _boom)
    response = development.client.get("/admin/pending")
    assert response.status_code == 500
    assert f"RuntimeError: {SECRET}" in response.text


def test_without_app_env_no_detail(logged_in: Panel, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture's settings do not set APP_ENV: no detail (and the «Desarrollo» label)."""
    monkeypatch.setattr(pending, "pending_numbers", _boom)
    response = logged_in.client.get("/admin/pending")
    assert response.status_code == 500 and SECRET not in response.text


# --- panel.js: JSON as always, the same status codes -------------------------------------------


def test_fetch_gets_json_with_the_same_code(
    production: Panel, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = production.client.get("/admin/claims/999999", headers=FETCH)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["ok"] is False
    monkeypatch.setattr(pending, "pending_numbers", _boom)
    response = production.client.get("/admin/pending", headers=FETCH)
    assert response.status_code == 500 and response.json()["level"] == "error"
    assert SECRET not in response.text


def test_fetch_of_an_operator_on_an_admin_page_is_still_a_403(operator: Panel) -> None:
    """panel.js decides by the code (401/403/the login: the session); it stays a 403."""
    response = operator.client.post("/admin/users", headers=FETCH, data={})
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["ok"] is False
    assert response.json()["message"] == "Esta acción es solo para administradores."


def test_fetch_without_a_session_goes_to_the_login_as_before(panel: Panel) -> None:
    for method, url in (("get", "/admin/pending/numbers"), ("post", "/admin/claims/1/close")):
        response = getattr(panel.client, method)(url, headers=FETCH, follow_redirects=False)
        assert response.status_code in (302, 303), url
        assert response.headers["location"].endswith("/admin/login"), url


def test_the_operator_403_keeps_its_text(panel: Panel) -> None:
    make_user(panel.session, "marta")
    assert panel.login("marta", "clave-de-usuaria-inventada").status_code == 302
    response = panel.client.get("/admin/users")
    assert response.status_code == 403
    assert "Esta sección es solo para administradores." in response.text
    assert ">Volver a Pendientes</a>" in response.text
