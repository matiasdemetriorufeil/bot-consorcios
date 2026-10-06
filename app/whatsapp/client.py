"""WhatsApp Cloud API client (Graph API, WHATSAPP_GRAPH_VERSION).

POST /{phone_number_id}/messages sends text, reply buttons (up to 3), a list (up to 10 rows)
or a template; Meta answers with the message's WhatsApp id (wamid) once it accepted it, and
its fate (sent / delivered / read / failed) arrives later as a status webhook. Options are
checked against app.bot.choices first (the limits are Meta's), so a rejection here is rare.

Media: GET /{media_id} gives a short-lived URL (minutes), downloaded with the same token.

⚠️ Development only: WHATSAPP_DEV_RECIPIENT_REWRITE rewrites the recipient (Meta's test number
only sends to its allowed list, which takes Argentine mobiles as 54 + area + 15 + number while
WhatsApp gives them as 549 + area + number). Used only with APP_ENV=development.
"""

import logging
from collections.abc import Sequence
from typing import Any

import requests

from app.bot.choices import MAX_BUTTONS, Choice, problems
from app.channels.base import ChannelError
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com"
LIST_BUTTON = "Ver opciones"
DOWNLOAD_CHUNK = 64 * 1024


class WhatsAppError(ChannelError):
    """The Cloud API failed (code: Meta's error code, when it gave one) or is not configured."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class WindowClosedError(WhatsAppError):
    """More than 24 h since the contact's last message: only templates can be sent."""


class MediaTooLargeError(WhatsAppError):
    pass


def parse_rewrites(raw: str) -> dict[str, str]:
    """ "from:to,from:to" (digits, no "+") -> {from: to}. Malformed pairs are skipped."""
    rewrites: dict[str, str] = {}
    for pair in raw.split(","):
        source, _, target = pair.partition(":")
        source, target = source.strip().lstrip("+"), target.strip().lstrip("+")
        if pair.strip() and not (source.isdigit() and target.isdigit()):
            logger.warning("WHATSAPP_DEV_RECIPIENT_REWRITE: malformed pair skipped")
            continue
        if source:
            rewrites[source] = target
    return rewrites


class WhatsAppClient:
    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        *,
        graph_version: str = "v26.0",
        timeout: float = 15,
        http: requests.Session | None = None,
        recipient_rewrites: dict[str, str] | None = None,
    ) -> None:
        self._token = access_token
        self.phone_number_id = phone_number_id
        self.base_url = f"{GRAPH_URL}/{graph_version}"
        self.timeout = timeout
        self._http = http or requests.Session()
        self._rewrites = recipient_rewrites or {}

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "WhatsAppClient":
        s = settings or get_settings()
        if not s.whatsapp_access_token or not s.whatsapp_phone_number_id:
            raise WhatsAppError("faltan WHATSAPP_ACCESS_TOKEN o WHATSAPP_PHONE_NUMBER_ID")
        rewrites = parse_rewrites(s.whatsapp_dev_recipient_rewrite)
        if rewrites and s.app_env != "development":
            logger.warning("WHATSAPP_DEV_RECIPIENT_REWRITE ignored: APP_ENV is not development")
            rewrites = {}
        elif rewrites:
            logger.info("WHATSAPP_DEV_RECIPIENT_REWRITE enabled for %d recipient(s)", len(rewrites))
        return cls(
            s.whatsapp_access_token.get_secret_value(),
            s.whatsapp_phone_number_id,
            graph_version=s.whatsapp_graph_version,
            timeout=s.whatsapp_timeout_seconds,
            recipient_rewrites=rewrites,
        )

    # --- HTTP -----------------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        what = f"{method} {url.removeprefix(self.base_url)}"
        try:
            response = self._http.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise WhatsAppError(f"{what}: {type(exc).__name__}") from exc
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}
        if response.status_code >= 400:
            error = data.get("error") if isinstance(data, dict) else None
            error = error if isinstance(error, dict) else {}
            code = error.get("code")
            raise WhatsAppError(
                f"{what}: HTTP {response.status_code} {error.get('message') or ''}".strip(),
                code=int(code) if isinstance(code, int) else None,
            )
        return data if isinstance(data, dict) else {}

    def _recipient(self, wa_id: str) -> str:
        digits = wa_id.lstrip("+")
        target = self._rewrites.get(digits)
        if target is None:
            return digits
        logger.info("[DEV_RECIPIENT_REWRITE] recipient %s -> %s", digits, target)
        return target

    def _send(self, to: str, kind: str, content: dict[str, Any]) -> str:
        """POST a message; returns its wamid."""
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._recipient(to),
            "type": kind,
            kind: content,
        }
        data = self._request("POST", f"{self.base_url}/{self.phone_number_id}/messages", json=body)
        messages = data.get("messages")
        wamid = messages[0].get("id") if isinstance(messages, list) and messages else None
        if not wamid:
            raise WhatsAppError("POST /messages: sin id de mensaje")
        return str(wamid)

    # --- Messages -------------------------------------------------------------------------

    def send_text(self, to: str, text: str) -> str:
        return self._send(to, "text", {"preview_url": False, "body": text})

    def send_choices(self, to: str, text: str, choices: Sequence[Choice]) -> str:
        """Reply buttons (up to 3) or a list (more). ValueError if Meta would reject them."""
        titles = [c.title for c in choices]
        if found := problems(text, titles):
            raise ValueError("; ".join(found))
        if len(choices) <= MAX_BUTTONS:
            action: dict[str, Any] = {
                "buttons": [
                    {"type": "reply", "reply": {"id": f"opt-{n}", "title": title}}
                    for n, title in enumerate(titles, 1)
                ]
            }
            kind = "button"
        else:
            rows = [{"id": f"opt-{n}", "title": title} for n, title in enumerate(titles, 1)]
            action = {"button": LIST_BUTTON, "sections": [{"rows": rows}]}
            kind = "list"
        return self._send(
            to, "interactive", {"type": kind, "body": {"text": text}, "action": action}
        )

    def send_template(
        self,
        to: str,
        name: str,
        language: str = "es_AR",
        components: list[dict[str, Any]] | None = None,
    ) -> str:
        """An approved template: the only thing that can go outside the 24-hour window."""
        template: dict[str, Any] = {"name": name, "language": {"code": language}}
        if components:
            template["components"] = components
        return self._send(to, "template", template)

    # --- Media ----------------------------------------------------------------------------

    def get_media(self, media_id: str) -> dict[str, Any]:
        """{url, mime_type, file_size, sha256, id}; the url lasts a few minutes."""
        return self._request("GET", f"{self.base_url}/{media_id}")

    def download(self, url: str, max_bytes: int) -> bytes:
        """The file at a media url. MediaTooLargeError past max_bytes (stops reading)."""
        if not url.startswith("https://"):  # the token never goes over plain http
            raise WhatsAppError("descarga de media: URL sin https")
        try:
            response = self._http.get(
                url, headers=self._headers(), timeout=self.timeout, stream=True
            )
        except requests.RequestException as exc:
            raise WhatsAppError(f"descarga de media: {type(exc).__name__}") from exc
        with response:
            if response.status_code >= 400:
                raise WhatsAppError(f"descarga de media: HTTP {response.status_code}")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(DOWNLOAD_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise MediaTooLargeError(f"más de {max_bytes} bytes")
                chunks.append(chunk)
        return b"".join(chunks)
