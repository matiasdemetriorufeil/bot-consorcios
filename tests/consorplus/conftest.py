"""Fake ConsorPlus server for the client tests. All data here is invented."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from requests.cookies import RequestsCookieJar

from app.consorplus.allowlist import BUILDING_SELECT, LOAD_BUTTON, UNIT_SELECT, page_name
from app.consorplus.client import ConsorPlusClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "consorplus"
BASE_URL = "https://consorplus.example.test/"
USERNAME = "usuario.prueba"
PASSWORD = "clave-de-prueba"  # noqa: S105 - fake credential


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_delta(*nodes: tuple[str, str, str]) -> str:
    """Build an ASP.NET AJAX delta response from (type, id, content) records."""
    out = []
    for node_type, node_id, content in nodes:
        length = len(content.encode("utf-16-le")) // 2
        out.append(f"{length}|{node_type}|{node_id}|{content}|")
    return "".join(out)


def panel_delta(panel_fixture: str, viewstate: str) -> str:
    return panel_delta_html(load(panel_fixture), viewstate)


def panel_delta_html(panel_html: str, viewstate: str) -> str:
    return make_delta(
        ("updatePanel", "ContentPlaceHolder1_UpdatePanel1", panel_html),
        ("hiddenField", "__EVENTTARGET", ""),
        ("hiddenField", "__VIEWSTATE", viewstate),
        ("asyncPostBackControlIDs", "", ""),
        ("pageTitle", "", "Detalle Deuda Unidad"),
    )


@dataclass
class FakeResponse:
    url: str
    text: str
    status_code: int = 200


@dataclass
class Call:
    method: str
    page: str
    data: dict[str, str]
    headers: dict[str, str] | None


@dataclass
class FakeConsorPlus:
    """Stands in for requests.Session and mimics the ConsorPlus page flow."""

    debt_panel: str = "panel_with_debt.html"
    roster_panel: str = "roster_panel.html"
    session_valid: bool = False
    calls: list[Call] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    cookies: RequestsCookieJar = field(default_factory=RequestsCookieJar)

    def request(self, method, url, data=None, headers=None, timeout=None):
        page = page_name(url)
        self.calls.append(Call(method, page, dict(data or {}), dict(headers or {}) or None))

        if page == "login.aspx":
            if method == "POST" and data.get("txtPassword") == PASSWORD:
                # Like the real server: same URL, but the form now posts to LoadingCache.aspx.
                return FakeResponse(BASE_URL + "login.aspx", load("loading_cache.html"))
            return FakeResponse(BASE_URL + "login.aspx", load("login.html"))
        if page == "loadingcache.aspx" and method == "POST":
            self.session_valid = True
            return FakeResponse(BASE_URL + "Home.aspx", load("home.html"))
        if not self.session_valid:
            return FakeResponse(BASE_URL + "login.aspx?ReturnUrl=%2fx", load("login.html"))
        if page == "detalledeudaunidad.aspx" and method == "GET":
            return FakeResponse(url, load("debt_page.html"))
        if page == "detalledeudaunidad.aspx" and method == "POST":
            control = data["ctl00$ScriptManager1"].split("|", 1)[1]
            panel = {
                BUILDING_SELECT: "panel_units.html",
                UNIT_SELECT: "panel_unit_selected.html",
                LOAD_BUTTON: self.debt_panel,
            }[control]
            return FakeResponse(url, panel_delta(panel, f"VS-{len(self.calls)}"))
        if page == "listado2036.aspx" and method == "GET":
            return FakeResponse(url, load("roster_page.html"))
        if page == "listado2036.aspx" and method == "POST":
            control = data["ctl00$ScriptManager1"].split("|", 1)[1]
            assert control == "ctl00$ContentPlaceHolder1$btnListar", control
            return FakeResponse(url, panel_delta(self.roster_panel, f"VS-{len(self.calls)}"))
        raise AssertionError(f"Request inesperada: {method} {url}")

    def pages(self) -> list[tuple[str, str]]:
        return [(c.method, c.page) for c in self.calls]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def server() -> FakeConsorPlus:
    return FakeConsorPlus()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_client(clock: FakeClock):
    def factory(session, **kwargs) -> ConsorPlusClient:
        return ConsorPlusClient(
            BASE_URL, USERNAME, PASSWORD, session=session, sleep=clock.sleep, clock=clock, **kwargs
        )

    return factory
