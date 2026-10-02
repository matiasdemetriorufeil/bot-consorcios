from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import requests

from app.config import Settings
from app.consorplus.allowlist import BUILDING_SELECT, LOAD_BUTTON, UNIT_SELECT
from app.consorplus.client import ConsorPlusClient, RateLimiter
from app.consorplus.errors import (
    ConsorPlusError,
    ConsorPlusUnavailableError,
    LoginError,
    NotFoundError,
)
from tests.consorplus.conftest import (
    BASE_URL,
    PASSWORD,
    FakeConsorPlus,
    FakeResponse,
    load,
    make_delta,
)

DEBT = "detalledeudaunidad.aspx"


def test_login_flow(server, make_client) -> None:
    client = make_client(server)

    client.login()

    assert server.pages() == [
        ("GET", "login.aspx"),
        ("POST", "login.aspx"),
        ("POST", "loadingcache.aspx"),
        ("GET", "home.aspx"),  # 302 hop, followed by hand
    ]
    login_post = server.calls[1].data
    assert login_post["__EVENTTARGET"] == "lnkIniciarSesion"
    assert login_post["__VIEWSTATE"] == "VS-LOGIN"
    assert server.calls[2].data["__EVENTTARGET"] == ""
    assert "bot-consorcios" in server.headers["User-Agent"]


def test_wrong_password_raises_login_error(server, make_client) -> None:
    client = ConsorPlusClient(BASE_URL, "usuario", "mala", session=server, sleep=lambda s: None)

    with pytest.raises(LoginError):
        client.list_buildings()
    assert ("POST", "loadingcache.aspx") not in server.pages()


def test_missing_credentials() -> None:
    with pytest.raises(LoginError):
        ConsorPlusClient(BASE_URL, "", "")


def test_list_buildings_logs_in_once(server, make_client) -> None:
    client = make_client(server)

    assert [b.code for b in client.list_buildings()] == ["1", "2", "7"]
    assert [b.code for b in client.list_buildings()] == ["1", "2", "7"]
    assert server.pages().count(("GET", "login.aspx")) == 1


def test_list_units(server, make_client) -> None:
    units = make_client(server).list_units("1")

    assert [u.value for u in units] == ["9001", "9002", "9003"]
    post = server.calls[-1]
    assert post.headers["X-MicrosoftAjax"] == "Delta=true"
    assert post.data["__ASYNCPOST"] == "true"
    assert post.data["__EVENTTARGET"] == BUILDING_SELECT
    assert (
        post.data["ctl00$ScriptManager1"]
        == f"ctl00$ContentPlaceHolder1$UpdatePanel1|{BUILDING_SELECT}"
    )


def test_get_debt(server, make_client) -> None:
    debt = make_client(server).get_debt("1", "9001")

    assert len(debt.lines) == 3
    assert debt.total == Decimal("151925.50")
    assert not debt.is_up_to_date
    assert server.pages()[-4:] == [("GET", DEBT), ("POST", DEBT), ("POST", DEBT), ("POST", DEBT)]

    unit_post, button_post = server.calls[-2].data, server.calls[-1].data
    assert unit_post["__EVENTTARGET"] == UNIT_SELECT
    assert unit_post[UNIT_SELECT] == "9001"
    # Each postback carries the __VIEWSTATE returned by the previous delta.
    assert unit_post["__VIEWSTATE"] == f"VS-{len(server.calls) - 2}"
    assert button_post["__EVENTTARGET"] == ""
    assert button_post[LOAD_BUTTON] == "Cargar Periodo"
    assert not [k for k in button_post if "btn" in k and k != LOAD_BUTTON]


def test_get_debt_of_same_building_reuses_the_open_page(server, make_client) -> None:
    client = make_client(server)
    client.get_debt("1", "9001")
    before = len(server.calls)

    debt = client.get_debt("1", "9001")

    assert debt.total == Decimal("151925.50")
    # Only the unit combo and the load button: no GET, no building postback.
    assert server.pages()[before:] == [("POST", DEBT), ("POST", DEBT)]
    assert server.calls[before].data["__EVENTTARGET"] == UNIT_SELECT


def test_get_debt_reopens_the_building_when_the_shortcut_fails(server, make_client) -> None:
    client = make_client(server)
    client.get_debt("1", "9001")
    before = len(server.calls)
    original = server.request
    failed = []

    def unit_select_fails_once(method, url, data=None, **kwargs):
        if data and data.get("__EVENTTARGET") == UNIT_SELECT and not failed:
            failed.append(True)
            return FakeResponse(url, make_delta(("error", "500", "Error interno")))
        return original(method, url, data=data, **kwargs)

    server.request = unit_select_fails_once

    debt = client.get_debt("1", "9001")

    assert debt.total == Decimal("151925.50")
    assert failed == [True]  # the failed unit postback is not recorded by the fake
    assert server.pages()[before:] == [("GET", DEBT)] + [("POST", DEBT)] * 3


def test_get_debt_of_another_building_opens_it(server, make_client) -> None:
    client = make_client(server)
    client.get_debt("1", "9001")
    before = len(server.calls)

    with pytest.raises(ConsorPlusError):  # the fake always answers with building 1
        client.get_debt("2", "9001")

    assert server.pages()[before] == ("GET", DEBT)


def test_get_debt_without_table_is_up_to_date(make_client) -> None:
    server = FakeConsorPlus(debt_panel="panel_no_debt.html")
    # The no-debt panel shows unit 9002 selected.
    debt = make_client(server).get_debt("1", "9002")

    assert debt.lines == ()
    assert debt.total == 0
    assert debt.is_up_to_date


def test_get_debt_rejects_panel_for_another_unit(server, make_client) -> None:
    server.debt_panel = "panel_no_debt.html"  # shows unit 9002, we ask for 9001

    with pytest.raises(ConsorPlusError, match="unidad pedida"):
        make_client(server).get_debt("1", "9001")


def test_unknown_building_or_unit(server, make_client) -> None:
    client = make_client(server)

    with pytest.raises(NotFoundError):
        client.get_debt("99", "9001")
    with pytest.raises(NotFoundError):
        client.get_debt("1", "12345")


def test_expired_session_logs_in_again(server, make_client) -> None:
    client = make_client(server)
    client.list_buildings()
    server.session_valid = False  # the server forgot us

    debt = client.get_debt("1", "9001")

    assert debt.total == Decimal("151925.50")
    # First login, the 302 hop to login.aspx?goTo=... and the new login.
    assert server.pages().count(("GET", "login.aspx")) == 3
    assert server.calls[6].url == BASE_URL + "login.aspx?goTo=detalledeudaunidad.aspx"


def test_expired_session_in_async_postback_logs_in_again(server, make_client) -> None:
    client = make_client(server)
    original = server.request
    expired = {"done": False}

    def request(method, url, data=None, headers=None, **kwargs):
        if method == "POST" and data and data.get("__ASYNCPOST") and not expired["done"]:
            expired["done"] = True
            server.session_valid = False
            redirect = ("pageRedirect", "", "/login.aspx?ReturnUrl=%2fDetalleDeudaUnidad.aspx")
            return FakeResponse(url, make_delta(redirect))
        return original(method, url, data=data, headers=headers, **kwargs)

    server.request = request
    assert [u.value for u in client.list_units("1")] == ["9001", "9002", "9003"]
    assert server.pages().count(("GET", "login.aspx")) == 2


def test_retries_with_backoff_then_succeeds(server, make_client, clock) -> None:
    original = server.request
    failures = {"left": 2}

    def flaky(method, url, **kwargs):
        if failures["left"]:
            failures["left"] -= 1
            raise requests.ConnectionError("reset by peer")
        return original(method, url, **kwargs)

    server.request = flaky
    client = make_client(server, min_request_interval=0)

    client.login()

    assert clock.sleeps[:2] == [1.0, 2.0]
    assert server.pages()[0] == ("GET", "login.aspx")


def test_gives_up_after_max_retries(clock, make_client) -> None:
    session = MagicMock()
    session.headers = {}
    session.request.side_effect = requests.Timeout("slow")
    client = make_client(session, min_request_interval=0)

    with pytest.raises(ConsorPlusUnavailableError):
        client.login()

    assert session.request.call_count == 4  # 1 attempt + 3 retries
    assert clock.sleeps == [1.0, 2.0, 4.0]


def test_retries_on_server_errors(clock, make_client) -> None:
    session = MagicMock()
    session.headers = {}
    session.request.side_effect = [
        FakeResponse(BASE_URL + "login.aspx", "busy", status_code=503),
        FakeResponse(BASE_URL + "login.aspx", load("login.html")),
    ]
    client = make_client(session, min_request_interval=0)

    client._get("login.aspx")

    assert session.request.call_count == 2


def test_client_error_is_not_retried(make_client) -> None:
    session = MagicMock()
    session.headers = {}
    session.request.return_value = FakeResponse(BASE_URL + "login.aspx", "", status_code=404)

    with pytest.raises(ConsorPlusError, match="404"):
        make_client(session)._get("login.aspx")
    assert session.request.call_count == 1


def test_requests_use_timeout(make_client) -> None:
    session = MagicMock()
    session.headers = {}
    session.request.return_value = FakeResponse(BASE_URL + "login.aspx", load("login.html"))

    make_client(session, timeout=(3, 7))._get("login.aspx")

    assert session.request.call_args.kwargs["timeout"] == (3, 7)


def test_rate_limiter(clock) -> None:
    limiter = RateLimiter(0.5, clock=clock, sleep=clock.sleep)

    limiter.wait()
    limiter.wait()
    clock.now += 2
    limiter.wait()
    limiter.wait()

    assert clock.sleeps == [0.5, 0.5]


def test_client_is_rate_limited(server, make_client, clock) -> None:
    make_client(server, min_request_interval=0.5).login()

    assert clock.sleeps == [0.5, 0.5, 0.5]  # 4 requests (with the Home hop), 3 waits


def test_from_settings_and_repr_hide_password() -> None:
    settings = Settings(
        _env_file=None,
        consorplus_base_url=BASE_URL,
        consorplus_user="usuario",
        consorplus_password=PASSWORD,
        consorplus_min_request_interval=1.5,
    )

    client = ConsorPlusClient.from_settings(settings)

    assert client._rate_limiter._min_interval == 1.5
    assert PASSWORD not in repr(client)


def test_needs_login_and_ensure_session(server, make_client, clock) -> None:
    client = make_client(server, session_idle_seconds=900)
    assert client.needs_login

    client.ensure_session()
    requests_after_login = len(server.calls)
    client.ensure_session()  # session ready: nothing is sent

    assert not client.needs_login and len(server.calls) == requests_after_login
    clock.now += 901
    assert client.needs_login  # idle longer than the server keeps sessions


def test_idle_session_logs_in_before_the_operation(server, make_client, clock) -> None:
    client = make_client(server, session_idle_seconds=900)
    client.list_buildings()
    clock.now += 901
    server.session_valid = False  # the server forgot us meanwhile

    debt = client.get_debt("1", "9001")

    assert debt.total == Decimal("151925.50")
    # Logs in first: no wasted request bounced to login.aspx?goTo=...
    assert not any("login.aspx?goTo" in c.url for c in server.calls)
    assert server.pages().count(("GET", "login.aspx")) == 2


def test_without_idle_limit_only_bounces_trigger_a_login(server, make_client, clock) -> None:
    client = make_client(server)
    client.list_buildings()
    clock.now += 10_000

    assert not client.needs_login
    client.list_buildings()
    assert server.pages().count(("GET", "login.aspx")) == 1


@pytest.mark.parametrize(("minutes", "seconds"), [(15, 900), (0, None)])
def test_from_settings_session_idle(minutes: float, seconds: float | None) -> None:
    settings = Settings(
        _env_file=None,
        consorplus_user="usuario",
        consorplus_password=PASSWORD,
        consorplus_session_idle_minutes=minutes,
    )

    assert ConsorPlusClient.from_settings(settings)._session_idle_seconds == seconds
