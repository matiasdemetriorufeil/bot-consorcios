"""Invented WhatsApp Cloud API webhook payloads and a fake Cloud API client (no network)."""

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from typing import Any

from app.bot.choices import MIN_SENT_OPTIONS, Choice, problems
from app.whatsapp.client import MediaTooLargeError, WhatsAppError

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"
PHONE_NUMBER_ID = "100000000000001"
OTHER_PHONE_NUMBER_ID = "100000000000999"
BUSINESS_NUMBER = "5493510000000"
CONTACT_WA_ID = "5493515550101"  # invented, same as the owner of tests/bot factories

_wamids = count(1)


def wamid() -> str:
    return f"wamid.TEST{next(_wamids):06d}"


def _envelope(field_name: str, value: dict[str, Any], phone_number_id: str) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "200000000000001",
                "changes": [
                    {
                        "field": field_name,
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": BUSINESS_NUMBER,
                                "phone_number_id": phone_number_id,
                            },
                            **value,
                        },
                    }
                ],
            }
        ],
    }


def incoming(
    *messages: dict[str, Any],
    wa_id: str = CONTACT_WA_ID,
    name: str = "Contacto Prueba",
    phone_number_id: str = PHONE_NUMBER_ID,
) -> dict[str, Any]:
    """A "messages" delivery with these messages (built by text_message, media_message...)."""
    return _envelope(
        "messages",
        {
            "contacts": [{"profile": {"name": name}, "wa_id": wa_id}],
            "messages": [{"from": wa_id, **m} for m in messages],
        },
        phone_number_id,
    )


def _base(kind: str, at: datetime, message_id: str | None) -> dict[str, Any]:
    return {"id": message_id or wamid(), "timestamp": str(int(at.timestamp())), "type": kind}


def text_message(body: str, at: datetime, message_id: str | None = None) -> dict[str, Any]:
    return {**_base("text", at, message_id), "text": {"body": body}}


def button_reply(
    title: str, at: datetime, message_id: str | None = None, reply_id: str = "opt-1"
) -> dict[str, Any]:
    return {
        **_base("interactive", at, message_id),
        "interactive": {"type": "button_reply", "button_reply": {"id": reply_id, "title": title}},
    }


def template_button(
    title: str, payload: str, at: datetime, message_id: str | None = None
) -> dict[str, Any]:
    """A quick-reply button of a template, as Meta delivers it."""
    return {**_base("button", at, message_id), "button": {"text": title, "payload": payload}}


def list_reply(title: str, at: datetime, message_id: str | None = None) -> dict[str, Any]:
    return {
        **_base("interactive", at, message_id),
        "interactive": {"type": "list_reply", "list_reply": {"id": "opt-4", "title": title}},
    }


def media_message(
    kind: str,
    at: datetime,
    *,
    mime: str = "image/jpeg",
    caption: str | None = None,
    filename: str | None = None,
    media_id: str = "900000000000001",
    message_id: str | None = None,
) -> dict[str, Any]:
    media: dict[str, Any] = {"id": media_id, "mime_type": mime, "sha256": "abc"}
    if caption:
        media["caption"] = caption
    if filename:
        media["filename"] = filename
    return {**_base(kind, at, message_id), kind: media}


def reaction(at: datetime, to: str) -> dict[str, Any]:
    return {**_base("reaction", at, None), "reaction": {"message_id": to, "emoji": "👍"}}


def statuses(*items: dict[str, Any], phone_number_id: str = PHONE_NUMBER_ID) -> dict[str, Any]:
    return _envelope("messages", {"statuses": list(items)}, phone_number_id)


def status(
    message_id: str, value: str, at: datetime, *, error: tuple[int, str] | None = None
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": message_id,
        "status": value,
        "timestamp": str(int(at.timestamp())),
        "recipient_id": CONTACT_WA_ID,
    }
    if error:
        item["errors"] = [
            {
                "code": error[0],
                "title": error[1],
                "message": error[1],
                "error_data": {"details": "detalle inventado"},
            }
        ]
    return item


def echoes(*items: dict[str, Any], to: str = CONTACT_WA_ID) -> dict[str, Any]:
    return _envelope(
        "smb_message_echoes",
        {"message_echoes": [{"from": BUSINESS_NUMBER, "to": to, **i} for i in items]},
        PHONE_NUMBER_ID,
    )


def body_of(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


def signed_headers(body: bytes, secret: str = APP_SECRET) -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={digest}"}


@dataclass
class FakeWhatsApp:
    """Stands in for WhatsAppClient: records what is sent and returns invented wamids."""

    fail_on: set[str] = field(default_factory=set)
    error_code: int | None = 131000
    media: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    sent: list[tuple[str, str, Any]] = field(default_factory=list)  # (kind, to, content)
    wamids: list[str] = field(default_factory=list)
    # Per message with options or a template: the payloads of its buttons, in order.
    payloads: list[list[str]] = field(default_factory=list)
    # The components of each template sent (its variables and buttons).
    components: list[Any] = field(default_factory=list)

    def _send(self, kind: str, to: str, content: Any) -> str:
        if kind in self.fail_on:
            raise WhatsAppError(f"{kind} rechazado", code=self.error_code)
        self.sent.append((kind, to, content))
        self.wamids.append(wamid())
        return self.wamids[-1]

    def send_text(self, to: str, text: str) -> str:
        return self._send("text", to, text)

    def send_choices(self, to: str, text: str, choices: tuple[Choice, ...]) -> str:
        if found := problems(text, [c.title for c in choices], MIN_SENT_OPTIONS):
            raise ValueError("; ".join(found))
        self.payloads.append([c.payload for c in choices])
        return self._send("choices", to, (text, [c.title for c in choices]))

    def send_template(self, to: str, name: str, language: str = "es_AR", components=None) -> str:  # type: ignore[no-untyped-def]
        self.components.append(components)
        buttons = [c for c in components or [] if c.get("type") == "button"]
        self.payloads.append([b["parameters"][0]["payload"] for b in buttons])
        return self._send("template", to, (name, language))

    def send_image(self, to: str, media_id: str, caption: str = "") -> str:
        return self._send("image", to, (media_id, caption))

    def upload_media(self, content: bytes, mime_type: str, filename: str = "foto") -> str:
        if "upload_media" in self.fail_on:
            raise WhatsAppError("POST /media falló")
        return f"subido-{len(content)}"

    def get_media(self, media_id: str) -> dict[str, Any]:
        if "get_media" in self.fail_on:
            raise WhatsAppError("GET media falló")
        return self.media[media_id]

    def download(self, url: str, max_bytes: int) -> bytes:
        content = self.files[url]
        if len(content) > max_bytes:
            raise MediaTooLargeError("grande")
        return content

    def texts(self) -> list[str]:
        return [content for kind, _, content in self.sent if kind == "text"]

    def kinds(self) -> list[str]:
        return [kind for kind, _, _ in self.sent]
