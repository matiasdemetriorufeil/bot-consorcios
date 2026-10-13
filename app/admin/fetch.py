"""The panel's actions without reloading: the same endpoint, the same checks and audit; only the
answer changes when panel.js asks for it (header X-Panel-Fetch: 1). Then it is JSON with what to
say (a small notice) and what to change in the page: rows to remove, blocks to replace (their
new HTML, rendered by the server), texts to set (counters). Without the header (no JavaScript)
each endpoint answers as always: a Flash and a redirect.
"""

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

HEADER = "x-panel-fetch"


def wants_fetch(request: Request) -> bool:
    return request.headers.get(HEADER) == "1"


def fetch_reply(
    ok: bool,
    message: str,
    *,
    remove: list[str] | None = None,
    replace: dict[str, str] | None = None,
    texts: dict[str, str] | None = None,
) -> JSONResponse:
    """remove: CSS selectors of what goes away; replace: selector -> its new HTML; texts:
    selector -> its new text (a counter). An error (ok False) stays on screen until closed."""
    data: dict[str, Any] = {
        "ok": ok,
        "message": message,
        "level": "success" if ok else "error",
        "remove": remove or [],
        "replace": replace or {},
        "texts": texts or {},
    }
    return JSONResponse(data, headers={"Cache-Control": "no-store"})
