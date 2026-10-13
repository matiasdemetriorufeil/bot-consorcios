"""The panel's error pages (404, 403, 500 and the rest), in Spanish and in its style
(templates/sqladmin/error.html), for every view inside /admin: SQLAdmin's and ours.

- An action sent without reloading (panel.js, header X-Panel-Fetch) gets JSON as always, with the
  same status code. panel.js tells the session from a permission by it: a 403 with this JSON
  is a permission (its message as an error notice, nothing reloaded); a 401, the login or a 403
  without it, the session is gone; anything else not 2xx: «No se pudo confirmar…». A request
  without a session never gets here: SQLAdmin sends it to the login first, as before.
- A 500 never shows what failed (its class, its message) unless APP_ENV is exactly
  "development". It is caught by ServerErrors (inside the session, so the page can have its
  menu), logged here once with its traceback and not raised again: no second "response already
  started" error, no second traceback.
- Without a session, the page has no menu (error.html extends _error_bare.html).
"""

import html
import logging
from http import HTTPStatus
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.admin.fetch import fetch_reply, wants_fetch

logger = logging.getLogger(__name__)

TEXTS: dict[int, tuple[str, str]] = {
    # status: (the tab's title and the heading, what to do)
    403: ("Sin permiso", "Esto no se puede hacer desde el panel."),
    404: ("No encontramos esa página", "Puede que el enlace esté mal o que ya no exista."),
    500: (
        "Algo salió mal",
        "Algo salió mal. Probá de nuevo; si sigue, avisale a un administrador.",
    ),
}
GENERIC = ("No se pudo hacer", "Probá de nuevo; si sigue, avisale a un administrador.")
# The last resort if even the error page cannot be drawn: no template, nothing of the request.
BARE_500 = (
    '<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">'
    "<title>Algo salió mal · Panel · Estudio Diego Rufeil</title></head><body><main>"
    "<h1>Algo salió mal</h1>"
    "<p>Algo salió mal. Probá de nuevo; si sigue, avisale a un administrador.</p>"
    '<p><a href="/admin/">Volver a Pendientes</a></p></main></body></html>'
)


def texts(status: int) -> tuple[str, str]:
    return TEXTS.get(status, GENERIC)


async def _page(templates: Any, request: Request, status: int, detail: str | None) -> Response:
    return await templates.TemplateResponse(
        request,
        "sqladmin/error.html",
        {"status_code": status, "message": detail},
        status_code=status,
    )


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


# An operator's action that only admins can do (by fetch: a sentence for a small notice).
ADMIN_ONLY = "Esta acción es solo para administradores."


def _json(status: int, request: Request | None = None) -> Response:
    title, text = texts(status)
    role = request.session.get("role") if request and "session" in request.scope else None
    if status == 403 and role != "admin":
        text = ADMIN_ONLY
    reply = fetch_reply(False, text if status != 404 else f"{title}.")
    reply.status_code = status
    return reply


def http_error_handler(templates: Any, show_details: bool) -> Any:
    """For admin.admin.exception_handlers[HTTPException] (SQLAdmin's own, replaced)."""

    async def handle(request: Request, exc: Exception) -> Response:
        status = exc.status_code if isinstance(exc, HTTPException) else 500
        if wants_fetch(request):
            return _json(status, request)
        detail = getattr(exc, "detail", None)
        if detail == _phrase(status):  # Starlette's English default says nothing new
            detail = None
        return await _page(
            templates, request, status, str(detail) if show_details and detail else None
        )

    return handle


class ServerErrors:
    """Any error that is not an HTTPException: logged once, answered with the 500 page (or JSON
    for panel.js). Installed as the innermost of the admin's middlewares, inside the session."""

    def __init__(self, app: ASGIApp, templates: Any, show_details: bool) -> None:
        self.app = app
        self.templates = templates
        self.show_details = show_details

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def watched(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, watched)
        except Exception as exc:
            logger.exception("Admin panel: error in %s %s", scope.get("method"), scope.get("path"))
            if started:  # nothing else can be sent; it is in the log
                return
            response = await self._answer(Request(scope, receive), exc)
            await response(scope, receive, send)

    async def _answer(self, request: Request, exc: Exception) -> Response:
        if wants_fetch(request):
            return _json(500)
        detail = f"{type(exc).__name__}: {exc}" if self.show_details else None
        try:
            return await _page(self.templates, request, 500, detail)
        except Exception:
            logger.exception("Admin panel: the error page could not be drawn")
            extra = f"<pre>{html.escape(detail)}</pre>" if detail else ""
            return HTMLResponse(BARE_500.replace("</main>", extra + "</main>"), status_code=500)
