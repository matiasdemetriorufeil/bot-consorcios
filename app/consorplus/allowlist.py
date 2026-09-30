"""Closed list of the only interactions allowed with ConsorPlus (READ-ONLY).

ConsorPlus is a production system. A WebForms postback runs server-side code, so every
postback must be listed here explicitly as (page, control). Never add an entry unless the
user explicitly asks for it (see CLAUDE.md).

All checks run BEFORE the request is sent.
"""

from collections.abc import Iterable, Mapping
from urllib.parse import unquote, urlsplit

from app.consorplus.errors import ForbiddenActionError, UnexpectedRedirectError

LOGIN_PAGE = "login.aspx"
LOADING_PAGE = "loadingcache.aspx"
HOME_PAGE = "home.aspx"
DEBT_PAGE = "detalledeudaunidad.aspx"
ROSTER_PAGE = "listado2036.aspx"  # "List. Todos Los Datos"

CONTENT_PREFIX = "ctl00$ContentPlaceHolder1$"
LOGIN_LINK = "lnkIniciarSesion"
USERNAME_FIELD = "txtUsername"
PASSWORD_FIELD = "txtPassword"  # noqa: S105 - form field name, not a secret
BUILDING_SELECT = CONTENT_PREFIX + "ddlEdificio"
UNIT_SELECT = CONTENT_PREFIX + "ddlDepto"
LOAD_BUTTON = CONTENT_PREFIX + "btnBuscar"
ROSTER_FROM_SELECT = CONTENT_PREFIX + "ddlEdificioDesde"
ROSTER_TO_SELECT = CONTENT_PREFIX + "ddlEdificioHasta"
# "Consultar". Its Excel sibling (Button4) and the debt page's btnListar ("Listar PDF") stay out.
ROSTER_QUERY_BUTTON = CONTENT_PREFIX + "btnListar"

# (page, control that triggers the postback). "" = postback with an empty __EVENTTARGET.
ALLOWED_POSTBACKS: frozenset[tuple[str, str]] = frozenset(
    {
        (LOGIN_PAGE, LOGIN_LINK),
        (LOADING_PAGE, ""),
        (DEBT_PAGE, BUILDING_SELECT),
        (DEBT_PAGE, UNIT_SELECT),
        (DEBT_PAGE, LOAD_BUTTON),
        (ROSTER_PAGE, ROSTER_QUERY_BUTTON),
    }
)

# Query pages that may be fetched with a plain GET (no query string).
ALLOWED_GET_PAGES: frozenset[str] = frozenset({LOGIN_PAGE, DEBT_PAGE, ROSTER_PAGE})

# The only form fields callers may set; everything else is copied from the page as-is.
ALLOWED_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        USERNAME_FIELD,
        PASSWORD_FIELD,
        BUILDING_SELECT,
        UNIT_SELECT,
        ROSTER_FROM_SELECT,
        ROSTER_TO_SELECT,
    }
)


# Redirects are followed by hand (never by requests), one hop at a time, and only these:
# - POST LoadingCache.aspx -> Home.aspx (end of the login);
# - anything -> login.aspx (expired session; the client then logs in again).
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5
ALLOWED_REDIRECTS: frozenset[tuple[str, str, str]] = frozenset(
    {("POST", LOADING_PAGE, HOME_PAGE)}  # (method, from page, to page)
)


def page_name(url: str) -> str:
    """Lower-cased file name of a URL path: 'https://x/Foo.aspx?a=1' -> 'foo.aspx'."""
    path = unquote(urlsplit(url).path)
    return path.rstrip("/").rsplit("/", 1)[-1].lower()


def check_get(url: str) -> None:
    page = page_name(url)
    if page not in ALLOWED_GET_PAGES or urlsplit(url).query:
        raise ForbiddenActionError(f"GET no permitido: {page!r}")


def check_control(url: str, control: str) -> None:
    page = page_name(url)
    if (page, control) not in ALLOWED_POSTBACKS:
        raise ForbiddenActionError(f"Postback no permitido: {page!r} / {control!r}")


def check_postback(
    url: str,
    control: str,
    data: Mapping[str, str],
    button_names: Iterable[str],
) -> None:
    """Validate a postback to `url` triggered by `control` with the POST body `data`.

    `button_names` are the submit/image buttons present in the page: ASP.NET treats a button
    as clicked when its name is in the body, whatever __EVENTTARGET says.
    """
    check_control(url, control)

    event_target = data.get("__EVENTTARGET", "")
    if event_target not in ("", control):
        raise ForbiddenActionError(f"__EVENTTARGET no permitido: {event_target!r}")

    for name in button_names:
        if name == control:
            continue
        if name in data or f"{name}.x" in data or f"{name}.y" in data:
            raise ForbiddenActionError(f"Botón no permitido en el POST: {name!r}")


def check_input_fields(fields: Mapping[str, str]) -> None:
    forbidden = set(fields) - ALLOWED_INPUT_FIELDS
    if forbidden:
        raise ForbiddenActionError(f"Campos no permitidos: {sorted(forbidden)}")


def check_redirect(method: str, from_url: str, status: int, to_url: str) -> None:
    """Validate ONE redirect hop before it is followed (always with a GET)."""
    source, target = page_name(from_url), page_name(to_url)
    if status in (307, 308) and method != "GET":
        # 307/308 ask to repeat the same method and body: never re-send a POST.
        raise UnexpectedRedirectError(
            f"Redirección {status} de {source!r} a {target!r}: repetiría el {method}"
        )
    if target == LOGIN_PAGE or (method, source, target) in ALLOWED_REDIRECTS:
        return
    raise UnexpectedRedirectError(
        f"ConsorPlus redirigió de {source!r} a {target!r}: redirección no permitida, no se siguió"
    )
