"""A tiny Chrome DevTools client (headless Chrome, websockets): enough for screenshots and a few
measures of the panel, over the demo (demo_server.py, invented data only).

Chrome: the CHROME environment variable, else Windows' usual path. Each launch uses a new
temporary profile (no cache from a previous run).
"""

import asyncio
import base64
import itertools
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

import websockets

CHROME = os.environ.get("CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PORT = 9333
BASE = os.environ.get("PANEL_DEMO_URL", "http://127.0.0.1:8765")


class Browser:
    """Headless Chrome, closed (and its profile removed) on exit."""

    def __enter__(self) -> "Browser":
        self.profile = tempfile.mkdtemp(prefix="panel-survey-chrome-")
        self.proc = subprocess.Popen(  # noqa: S603 (Chrome, our own arguments)
            [
                CHROME,
                "--headless=new",
                f"--remote-debugging-port={PORT}",
                f"--user-data-dir={self.profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
                "--lang=es-AR",
                "--remote-allow-origins=*",
                "about:blank",
            ]
        )
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1)  # noqa: S310
                return self
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("Chrome no arrancó")

    def __exit__(self, *exc: object) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=10)
        shutil.rmtree(self.profile, ignore_errors=True)


class Page:
    def __init__(self, ws: Any, target_id: str) -> None:
        self.ws = ws
        self.target_id = target_id
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: list[dict[str, Any]] = []
        self.reader = asyncio.create_task(self._read())

    @classmethod
    async def open(cls) -> "Page":
        req = urllib.request.Request(  # noqa: S310 (local Chrome)
            f"http://127.0.0.1:{PORT}/json/new?about:blank", method="PUT"
        )
        target = json.load(urllib.request.urlopen(req))  # noqa: S310
        ws = await websockets.connect(target["webSocketDebuggerUrl"], max_size=50_000_000)
        page = cls(ws, target["id"])
        for domain in ("Page", "Network", "Runtime"):
            await page.send(f"{domain}.enable")
        return page

    async def _read(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.pending:
                self.pending.pop(msg["id"]).set_result(msg)
            else:
                self.events.append(msg)

    async def send(self, method: str, **params: Any) -> dict[str, Any]:
        i = next(self.ids)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self.pending[i] = fut
        await self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        msg = await asyncio.wait_for(fut, 60)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    async def size(self, width: int, height: int, mobile: bool = False) -> None:
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=1,
            mobile=mobile,
        )

    async def go(self, path: str, wait: float = 1.5) -> None:
        await self.send("Page.navigate", url=BASE + path)
        await asyncio.sleep(wait)

    async def js(self, expression: str) -> Any:
        result = await self.send(
            "Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True
        )
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result["result"].get("value")

    async def login(self, user: str, password: str) -> None:
        await self.go("/admin/login")
        await self.js(
            f"document.querySelector('[name=username]').value={json.dumps(user)};"
            f"document.querySelector('[name=password]').value={json.dumps(password)};"
            "document.querySelector('form').submit(); true"
        )
        await asyncio.sleep(2)

    async def shot(self, path: Path, full: bool = True) -> None:
        params: dict[str, Any] = {"format": "png"}
        if full:
            size = (await self.send("Page.getLayoutMetrics"))["cssContentSize"]
            params["clip"] = {
                "x": 0,
                "y": 0,
                "width": size["width"],
                "height": size["height"],
                "scale": 1,
            }
            params["captureBeyondViewport"] = True
        data = await self.send("Page.captureScreenshot", **params)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data["data"]))

    async def close(self) -> None:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/close/{self.target_id}")  # noqa: S310
        self.reader.cancel()
