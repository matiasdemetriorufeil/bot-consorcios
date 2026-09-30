"""The allowlist must block anything outside the read-only flow BEFORE any request is sent."""

from unittest.mock import patch

import pytest
import requests

from app.consorplus.allowlist import (
    CONTENT_PREFIX,
    LOAD_BUTTON,
    UNIT_SELECT,
    check_get,
    check_postback,
    page_name,
)
from app.consorplus.errors import ForbiddenActionError
from tests.consorplus.conftest import BASE_URL

DEBT_URL = BASE_URL + "DetalleDeudaUnidad.aspx"


@pytest.fixture
def logged_in_client(server, make_client):
    """Client sitting on the debt page with a unit selected (all buttons visible)."""
    client = make_client(server)
    client.list_units("1")
    client._async_postback(UNIT_SELECT, {UNIT_SELECT: "9001"})
    return client


@pytest.fixture
def no_network():
    """Any real HTTP request fails the test."""
    with patch.object(requests.Session, "request", side_effect=AssertionError("red")) as mock:
        yield mock


@pytest.mark.parametrize("button", ["btnElim", "btnImprimir", "btnListar", "btnExcel", "btnSalir"])
def test_forbidden_button_click_is_blocked_without_request(
    logged_in_client, server, no_network, button
) -> None:
    calls_before = len(server.calls)

    with pytest.raises(ForbiddenActionError):
        logged_in_client._async_postback(CONTENT_PREFIX + button, {}, button=True)

    assert len(server.calls) == calls_before
    no_network.assert_not_called()


@pytest.mark.parametrize(
    "control", ["ctl00$lnkCerrarSesion", "ctl00$lnkHideMenu", CONTENT_PREFIX + "btnElim"]
)
def test_forbidden_full_postback_is_blocked_without_request(
    logged_in_client, server, no_network, control
) -> None:
    calls_before = len(server.calls)

    with pytest.raises(ForbiddenActionError):
        logged_in_client._postback(control)

    assert len(server.calls) == calls_before
    no_network.assert_not_called()


def test_smuggling_a_button_through_fields_is_blocked(logged_in_client, server) -> None:
    calls_before = len(server.calls)

    with pytest.raises(ForbiddenActionError, match="Campos no permitidos"):
        logged_in_client._async_postback(UNIT_SELECT, {CONTENT_PREFIX + "btnExcel": "Excel"})

    assert len(server.calls) == calls_before


def test_forbidden_get_is_blocked_without_request(logged_in_client, server) -> None:
    calls_before = len(server.calls)

    for page in ("EliminarUnidad.aspx", "DetalleDeudaUnidad.aspx?accion=borrar"):
        with pytest.raises(ForbiddenActionError):
            logged_in_client._get(page)

    assert len(server.calls) == calls_before


def test_other_host_is_blocked(logged_in_client, server) -> None:
    calls_before = len(server.calls)

    with pytest.raises(ForbiddenActionError, match="Host"):
        logged_in_client._send("GET", "https://otro.example.test/DetalleDeudaUnidad.aspx")

    assert len(server.calls) == calls_before


def test_check_postback_allows_the_read_only_flow() -> None:
    check_postback(BASE_URL + "login.aspx", "lnkIniciarSesion", {}, {})
    check_postback(BASE_URL + "LoadingCache.aspx?goTo=Home.aspx", "", {"__EVENTTARGET": ""}, {})
    check_postback(DEBT_URL, UNIT_SELECT, {"__EVENTTARGET": UNIT_SELECT}, {LOAD_BUTTON: "x"})
    check_postback(
        DEBT_URL, LOAD_BUTTON, {"__EVENTTARGET": "", LOAD_BUTTON: "Cargar"}, {LOAD_BUTTON: "x"}
    )


def test_check_postback_rejects_allowed_control_on_another_page() -> None:
    with pytest.raises(ForbiddenActionError):
        check_postback(BASE_URL + "ModificarUnidad.aspx", UNIT_SELECT, {}, {})


def test_check_postback_rejects_event_target_mismatch() -> None:
    with pytest.raises(ForbiddenActionError, match="__EVENTTARGET"):
        check_postback(DEBT_URL, UNIT_SELECT, {"__EVENTTARGET": "ctl00$lnkCerrarSesion"}, {})


@pytest.mark.parametrize("key", ["BTN", "BTN.x"])
def test_check_postback_rejects_other_button_in_body(key: str) -> None:
    button = CONTENT_PREFIX + "btnListar"
    data = {"__EVENTTARGET": UNIT_SELECT, key.replace("BTN", button): "1"}

    with pytest.raises(ForbiddenActionError, match="Botón"):
        check_postback(DEBT_URL, UNIT_SELECT, data, {button: "Listar PDF"})


def test_check_get() -> None:
    check_get(BASE_URL + "login.aspx")
    check_get(BASE_URL + "detalledeudaunidad.ASPX")
    with pytest.raises(ForbiddenActionError):
        check_get(BASE_URL + "Home.aspx?logout=1")


def test_page_name() -> None:
    assert page_name("https://x.test/a/DetalleDeudaUnidad.aspx?x=1") == "detalledeudaunidad.aspx"
    assert page_name("/login.aspx?ReturnUrl=%2fHome.aspx") == "login.aspx"
