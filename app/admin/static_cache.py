"""The panel's static files (its own, SQLAdmin's and the guide's pictures): served by this server,
never from a CDN, with:

- the version in the URL (?v=), a short hash of each file's content computed at start-up: a
  change reaches every browser without clearing anything, and nobody has to remember to bump
  a number;
- a long cache when the URL carries ?v= (it never changes for that URL) and a revalidation
  (ETag) when it does not (a picture referenced from the style sheet, the guide's pictures);
- gzip only for text files (CSS, JS, SVG, the manifest). Never the panel's pages: compressing
  HTML that mixes a secret with what someone types opens the door to BREACH.
"""

import hashlib
from collections.abc import Iterable
from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LONG = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
COMPRESSED = (".css", ".js", ".svg", ".webmanifest")


def file_hash(path: Path) -> str:
    """The version of one file: the first 10 hex digits of its sha256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def versions(directory: Path, names: Iterable[str] | None = None) -> dict[str, str]:
    """{path relative to directory (with /): its hash}, for every file (or only `names`)."""
    if names is not None:
        return {name: file_hash(directory / name) for name in names}
    return {
        path.relative_to(directory).as_posix(): file_hash(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class StaticCache:
    """Wraps a StaticFiles app: the Cache-Control above and gzip for text files."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.gzip = GZipMiddleware(self._with_cache, minimum_size=500)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").endswith(COMPRESSED):
            await self.gzip(scope, receive, send)
        else:
            await self._with_cache(scope, receive, send)

    async def _with_cache(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        query = scope.get("query_string", b"")
        versioned = any(part.startswith(b"v=") for part in query.split(b"&"))

        async def send_with_cache(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] in (200, 304):
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = LONG if versioned else REVALIDATE
            await send(message)

        await self.app(scope, receive, send_with_cache)
