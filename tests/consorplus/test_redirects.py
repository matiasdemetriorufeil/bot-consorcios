"""Redirects are followed by hand and each hop is checked BEFORE its GET is sent."""

from unittest.mock import MagicMock

import pytest

from app.consorplus.allowlist import MAX_REDIRECTS, check_redirect
from app.consorplus.errors import ForbiddenActionError, UnexpectedRedirectError
from tests.consorplus.conftest import BASE_URL, FakeResponse, redirect

DEBT = "detalledeudaunidad.aspx"


def test_login_follows_loading_cache_to_home(server, make_client) -> None:
    make_client(server).login()

    assert server.pages()[-2:] == [("POST", "loadingcache.aspx"), ("GET", "home.aspx")]
    assert server.calls[-1].url == BASE_URL + "Home.aspx"
    assert server.calls[-1].data == {}  # the hop is a plain GET, the POST body is not re-sent


def test_redirect_to_login_is_followed_and_triggers_relogin(server, make_client) -> None:
    client = make_client(server)
    client.login()
    server.session_valid = False

    assert [b.code for b in client.list_buildings()] == ["1", "2", "7"]
    assert ("GET", "login.aspx") in server.pages()[5:]
    assert client._logged_in


def test_redirect_in_async_postback_to_login_triggers_relogin(server, make_client) -> None:
    client = make_client(server)
    original = server.request
    expired = {"done": False}

    def request(method, url, data=None, **kwargs):
        if method == "POST" and data and data.get("__ASYNCPOST") and not expired["done"]:
            expired["done"] = True
            server.session_valid = False  # the AJAX POST gets a 302 to login.aspx
        return original(method, url, data=data, **kwargs)

    server.request = request

    assert [u.value for u in client.list_units("1")] == ["9001", "9002", "9003"]
    hop = server.calls[6]
    assert (hop.method, hop.url) == ("GET", BASE_URL + f"login.aspx?goTo={DEBT}")
    assert hop.headers is None  # the AJAX headers are not carried to the hop


def test_redirect_to_another_page_raises_without_get(server, make_client) -> None:
    client = make_client(server)
    client.login()
    server.redirects[DEBT] = "/Avisos.aspx"
    calls_before = len(server.calls)

    with pytest.raises(UnexpectedRedirectError, match="avisos.aspx") as excinfo:
        client.list_buildings()

    assert isinstance(excinfo.value, ForbiddenActionError)
    assert server.pages()[calls_before:] == [("GET", DEBT)]  # Avisos.aspx never requested


def test_home_redirect_is_only_allowed_after_loading_cache(server, make_client) -> None:
    client = make_client(server)
    client.login()
    server.redirects[DEBT] = "/Home.aspx"
    calls_before = len(server.calls)

    with pytest.raises(UnexpectedRedirectError):
        client.list_buildings()

    assert server.pages()[calls_before:] == [("GET", DEBT)]


def test_redirect_to_another_host_raises_without_get(server, make_client) -> None:
    client = make_client(server)
    client.login()
    server.redirects[DEBT] = "https://otro.example.test/login.aspx"
    calls_before = len(server.calls)

    with pytest.raises(ForbiddenActionError, match="Host"):
        client.list_buildings()

    assert len(server.calls) == calls_before + 1


def test_too_many_redirects_raise(server, make_client) -> None:
    server.redirects["login.aspx"] = "/login.aspx?goTo=Home.aspx"  # endless loop

    with pytest.raises(UnexpectedRedirectError, match="Más de 5"):
        make_client(server).login()

    assert len(server.calls) == 1 + MAX_REDIRECTS


def test_307_after_post_is_not_followed(make_client) -> None:
    session = MagicMock()
    session.headers = {}
    session.request.side_effect = [
        FakeResponse(BASE_URL + "login.aspx", "<form action='./LoadingCache.aspx'></form>"),
        redirect(BASE_URL + "LoadingCache.aspx", "/Home.aspx", status_code=307),
    ]
    client = make_client(session, min_request_interval=0)
    client._get("login.aspx")

    with pytest.raises(UnexpectedRedirectError, match="307"):
        client._postback("")

    assert session.request.call_count == 2


def test_redirect_without_location_raises(make_client) -> None:
    session = MagicMock()
    session.headers = {}
    session.request.return_value = FakeResponse(BASE_URL + "login.aspx", "", status_code=302)

    with pytest.raises(Exception, match="sin Location"):
        make_client(session)._get("login.aspx")


def test_requests_never_follow_redirects_by_themselves(make_client) -> None:
    session = MagicMock()
    session.headers = {}
    session.request.return_value = FakeResponse(BASE_URL + "login.aspx", "<form></form>")

    make_client(session)._get("login.aspx")

    assert session.request.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize(
    ("method", "source", "status", "target"),
    [
        ("POST", "LoadingCache.aspx?goTo=Home.aspx", 302, "/Home.aspx"),
        ("GET", "DetalleDeudaUnidad.aspx", 302, "/login.aspx?goTo=DetalleDeudaUnidad.aspx"),
        ("POST", "DetalleDeudaUnidad.aspx", 302, "/login.aspx?ReturnUrl=%2f"),
        ("GET", "Listado2036.aspx", 307, "/login.aspx"),
    ],
)
def test_check_redirect_allows(method, source, status, target) -> None:
    check_redirect(method, BASE_URL + source, status, BASE_URL + target.lstrip("/"))


@pytest.mark.parametrize(
    ("method", "source", "status", "target"),
    [
        ("GET", "DetalleDeudaUnidad.aspx", 302, "Avisos.aspx"),
        ("GET", "LoadingCache.aspx", 302, "Home.aspx"),  # only after the login POST
        ("POST", "login.aspx", 302, "Home.aspx"),
        ("POST", "LoadingCache.aspx", 307, "Home.aspx"),  # would re-send the POST
        ("POST", "DetalleDeudaUnidad.aspx", 308, "login.aspx"),
        ("GET", "DetalleDeudaUnidad.aspx", 302, "EliminarUnidad.aspx"),
    ],
)
def test_check_redirect_rejects(method, source, status, target) -> None:
    with pytest.raises(UnexpectedRedirectError):
        check_redirect(method, BASE_URL + source, status, BASE_URL + target)
