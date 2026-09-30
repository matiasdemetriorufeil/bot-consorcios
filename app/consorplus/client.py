"""HTTP client for ConsorPlus (ASP.NET WebForms, no API). READ-ONLY.

Every request goes through `_send`, which checks the allowlist (allowlist.py) before
anything leaves the process.
"""

import logging
import re
import time
from collections.abc import Callable, Mapping
from urllib.parse import unquote, urljoin, urlsplit

import requests

from app.config import Settings
from app.consorplus import parsers
from app.consorplus.allowlist import (
    BUILDING_SELECT,
    DEBT_PAGE,
    HOME_PAGE,
    LOAD_BUTTON,
    LOADING_PAGE,
    LOGIN_LINK,
    LOGIN_PAGE,
    PASSWORD_FIELD,
    ROSTER_FROM_SELECT,
    ROSTER_QUERY_BUTTON,
    ROSTER_TO_SELECT,
    UNIT_SELECT,
    USERNAME_FIELD,
    check_control,
    check_get,
    check_input_fields,
    check_postback,
    page_name,
)
from app.consorplus.delta import DeltaNode, apply_delta, parse_delta
from app.consorplus.errors import (
    ConsorPlusError,
    ConsorPlusUnavailableError,
    ForbiddenActionError,
    LoginError,
    NotFoundError,
    ParseError,
    SessionExpiredError,
)
from app.consorplus.models import Building, RosterRow, Unit, UnitDebt

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    # Starts with "Mozilla/5.0" so ASP.NET's browser detection keeps partial rendering on.
    "Mozilla/5.0 (compatible; bot-consorcios/0.1; Estudio Diego Rufeil; solo lectura)"
)
SCRIPT_MANAGER = "ctl00$ScriptManager1"
UPDATE_PANEL = "ctl00$ContentPlaceHolder1$UpdatePanel1"
UPDATE_PANEL_ID = "ContentPlaceHolder1_UpdatePanel1"
AJAX_HEADERS = {"X-MicrosoftAjax": "Delta=true", "X-Requested-With": "XMLHttpRequest"}
ROSTER_PAGE_PATH = "Listado2036.aspx"
RETRY_STATUS_CODES = frozenset({500, 502, 503, 504})
RETRY_EXCEPTIONS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def _label_code(label: str) -> str | None:
    """'050 EDIFICIO X' -> '050'."""
    match = re.match(r"\s*(\d+)(?!\S)", label)
    return match.group(1) if match else None


def _same_code(a: str, b: str) -> bool:
    """The same building code may come as "1" or as "001"."""
    a, b = a.strip(), b.strip()
    if a.isdigit() and b.isdigit():
        return int(a) == int(b)
    return a == b


class RateLimiter:
    """Guarantees at least `min_interval` seconds between consecutive requests."""

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self._min_interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


class ConsorPlusClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        min_request_interval: float = 0.5,
        timeout: tuple[float, float] = (10.0, 60.0),
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        user_agent: str = DEFAULT_USER_AGENT,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not username or not password:
            raise LoginError("Faltan el usuario o la clave de ConsorPlus")
        self._base_url = base_url if base_url.endswith("/") else base_url + "/"
        self._netloc = urlsplit(self._base_url).netloc.lower()
        self._username = username
        self._password = password
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._rate_limiter = RateLimiter(min_request_interval, clock=clock, sleep=sleep)
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._logged_in = False
        self._url = ""
        self._html = ""

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> "ConsorPlusClient":
        password = settings.consorplus_password
        return cls(
            settings.consorplus_base_url,
            settings.consorplus_user,
            password.get_secret_value() if password else "",
            min_request_interval=settings.consorplus_min_request_interval,
            **kwargs,
        )

    def __repr__(self) -> str:
        return f"ConsorPlusClient(base_url={self._base_url!r}, logged_in={self._logged_in})"

    # --- Public API --------------------------------------------------------------------

    def login(self) -> None:
        self._logged_in = False
        self._session.cookies.clear()
        self._get(LOGIN_PAGE)
        self._postback(LOGIN_LINK, {USERNAME_FIELD: self._username, PASSWORD_FIELD: self._password})
        # On success the answer is still at login.aspx, but its form now points to
        # LoadingCache.aspx, which the browser auto-submits before landing on Home.aspx.
        if LOADING_PAGE in (page_name(self._url), self._form_target_page()):
            self._postback("")
        elif page_name(self._url) == LOGIN_PAGE:
            raise LoginError("ConsorPlus rechazó el login (¿usuario o clave incorrectos?)")
        if page_name(self._url) != HOME_PAGE:
            raise LoginError(f"Login incompleto: terminó en {page_name(self._url)!r}")
        self._logged_in = True
        logger.info("ConsorPlus login OK")

    def list_buildings(self) -> list[Building]:
        return self._with_session(lambda: parsers.parse_buildings(self._get(DEBT_PAGE)))

    def list_units(self, building_code: str) -> list[Unit]:
        def operation() -> list[Unit]:
            self._open_building(building_code)
            return parsers.parse_units(self._html)

        return self._with_session(operation)

    def get_debt(self, building_code: str, unit_value: str) -> UnitDebt:
        def operation() -> UnitDebt:
            self._open_building(building_code)
            if unit_value not in {unit.value for unit in parsers.parse_units(self._html)}:
                raise NotFoundError(f"La unidad {unit_value!r} no existe en {building_code!r}")
            selection = {BUILDING_SELECT: building_code, UNIT_SELECT: unit_value}
            self._async_postback(UNIT_SELECT, selection)
            self._async_postback(LOAD_BUTTON, selection, button=True)
            # Guard against reading a stale or foreign panel as "no debt".
            if (
                parsers.selected_value(self._html, BUILDING_SELECT) != building_code
                or parsers.selected_value(self._html, UNIT_SELECT) != unit_value
            ):
                raise ConsorPlusError("La pantalla de deuda no quedó en la unidad pedida")
            lines = parsers.parse_debt_lines(self._html)
            return UnitDebt(building_code=building_code, unit_value=unit_value, lines=tuple(lines))

        return self._with_session(operation)

    def list_roster(self, building_code: str) -> list[RosterRow]:
        """Units of ONE building with owner/tenant contact data ('List. Todos Los Datos')."""

        def operation() -> list[RosterRow]:
            self._get(ROSTER_PAGE_PATH)
            options = parsers.parse_select_options(self._html, ROSTER_FROM_SELECT)
            option = next((o for o in options if o.value == building_code), None)
            if option is None:
                raise NotFoundError(f"El edificio {building_code!r} no existe")
            selection = {ROSTER_FROM_SELECT: building_code, ROSTER_TO_SELECT: building_code}
            self._async_postback(ROSTER_QUERY_BUTTON, selection)
            rows = parsers.parse_roster(self._html)
            # Guard against reading a stale panel or another building's units. The combo value
            # is an internal id; the visible code ("050 ...") is what the grid shows.
            expected = _label_code(option.label) or building_code
            if any(not _same_code(row.building_code, expected) for row in rows):
                raise ConsorPlusError("El listado trajo unidades de otro edificio")
            return rows

        return self._with_session(operation)

    def _form_target_page(self) -> str:
        try:
            return page_name(urljoin(self._url, parsers.parse_form(self._html).action))
        except ParseError:
            return ""

    # --- Page flows --------------------------------------------------------------------

    def _with_session[T](self, operation: Callable[[], T]) -> T:
        if not self._logged_in:
            self.login()
        try:
            return operation()
        except SessionExpiredError:
            logger.info("ConsorPlus session expired, logging in again")
            self.login()
            return operation()

    def _open_building(self, building_code: str) -> None:
        self._get(DEBT_PAGE)
        if building_code not in {b.code for b in parsers.parse_buildings(self._html)}:
            raise NotFoundError(f"El edificio {building_code!r} no existe")
        self._async_postback(BUILDING_SELECT, {BUILDING_SELECT: building_code})

    # --- Requests ----------------------------------------------------------------------

    def _get(self, page: str) -> str:
        url = urljoin(self._base_url, page)
        response = self._send("GET", url)
        self._url, self._html = response.url, response.text
        if page_name(self._url) == LOGIN_PAGE and page_name(url) != LOGIN_PAGE:
            self._logged_in = False
            raise SessionExpiredError("Redirigido a login.aspx")
        return self._html

    def _postback(self, control: str, fields: Mapping[str, str] | None = None) -> None:
        """Full postback (the whole page reloads)."""
        form = parsers.parse_form(self._html)
        url = urljoin(self._url, form.action)
        check_control(url, control)
        check_input_fields(fields or {})
        data = {**form.fields, **(fields or {}), "__EVENTTARGET": control, "__EVENTARGUMENT": ""}
        response = self._send("POST", url, control=control, data=data, button_names=form.buttons)
        self._url, self._html = response.url, response.text

    def _async_postback(
        self, control: str, fields: Mapping[str, str], *, button: bool = False
    ) -> None:
        """UpdatePanel postback, as the browser does when a combo changes or a button is hit."""
        form = parsers.parse_form(self._html)
        url = urljoin(self._url, form.action)
        check_control(url, control)
        check_input_fields(fields)
        data = {**form.fields, **fields}
        if button:
            if control not in form.buttons:
                raise ConsorPlusError(f"El botón {control!r} no está en la página")
            data[control] = form.buttons[control]
            data["__EVENTTARGET"] = ""
        else:
            data["__EVENTTARGET"] = control
        data["__EVENTARGUMENT"] = ""
        data["__ASYNCPOST"] = "true"
        data[SCRIPT_MANAGER] = f"{UPDATE_PANEL}|{control}"

        response = self._send(
            "POST", url, control=control, data=data, button_names=form.buttons, headers=AJAX_HEADERS
        )
        if page_name(response.url) == LOGIN_PAGE:
            self._logged_in = False
            raise SessionExpiredError("Redirigido a login.aspx")
        nodes = parse_delta(response.text)
        self._check_delta(nodes)
        self._html = apply_delta(self._html, nodes)

    def _check_delta(self, nodes: list[DeltaNode]) -> None:
        for node in nodes:
            if node.type == "pageRedirect":
                if page_name(unquote(node.content)) == LOGIN_PAGE:
                    self._logged_in = False
                    raise SessionExpiredError("Redirigido a login.aspx")
                raise ConsorPlusError(f"Redirección inesperada a {page_name(node.content)!r}")
            if node.type == "error":
                raise ConsorPlusError(f"Error del servidor en el postback: {node.content[:200]}")
        if not any(n.type == "updatePanel" and n.id == UPDATE_PANEL_ID for n in nodes):
            raise ConsorPlusError("La respuesta no actualizó el panel de deuda")

    def _send(
        self,
        method: str,
        url: str,
        *,
        control: str | None = None,
        data: Mapping[str, str] | None = None,
        button_names: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> requests.Response:
        # Allowlist first: nothing is sent unless it is explicitly permitted.
        if urlsplit(url).netloc.lower() != self._netloc:
            raise ForbiddenActionError(f"Host no permitido: {urlsplit(url).netloc!r}")
        if method == "GET":
            check_get(url)
        elif method == "POST":
            if control is None:
                raise ForbiddenActionError("POST sin control de la allowlist")
            check_postback(url, control, data or {}, button_names or {})
        else:
            raise ForbiddenActionError(f"Método no permitido: {method}")

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt:
                self._sleep(self._backoff_seconds * 2 ** (attempt - 1))
            self._rate_limiter.wait()
            try:
                response = self._session.request(
                    method, url, data=data, headers=headers, timeout=self._timeout
                )
            except RETRY_EXCEPTIONS as exc:
                last_error = exc
                logger.warning("ConsorPlus %s %s failed (%s)", method, page_name(url), exc)
                continue
            if response.status_code in RETRY_STATUS_CODES:
                last_error = ConsorPlusError(f"HTTP {response.status_code}")
                logger.warning(
                    "ConsorPlus %s %s -> HTTP %s", method, page_name(url), response.status_code
                )
                continue
            if response.status_code >= 400:
                raise ConsorPlusError(f"HTTP {response.status_code} en {page_name(url)}")
            return response
        raise ConsorPlusUnavailableError(
            f"ConsorPlus no respondió tras {self._max_retries + 1} intentos"
        ) from last_error
