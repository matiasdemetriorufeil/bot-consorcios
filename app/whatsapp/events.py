"""WhatsApp Cloud API webhook payloads -> what we store and answer. Pure functions, no I/O.

A delivery is {"object": "whatsapp_business_account", "entry": [{"changes": [...]}]}; each
change has a "field" and a "value":
- "messages": value.metadata.phone_number_id (our number), value.contacts[] (wa_id and
  profile.name of who writes), value.messages[] (incoming) and value.statuses[] (sent /
  delivered / read / failed of our messages; v25+ also "played" for voice notes).
- "smb_message_echoes" (coexistence: the studio writing from the WhatsApp Business app on the
  phone): value.message_echoes[], with from = our number and to = the contact.

Options tapped: an "interactive" message with button_reply / list_reply ({id, title}), or a
"button" message (template quick reply) with button.text. The bot takes the TITLE as what the
person wrote (app.bot.choices); the option's own id (ours: a signed payload of a claim's
button, app.claims.notify) or the template button's payload is kept apart (WaIncoming.payload).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MEDIA_TYPES = frozenset({"image", "document", "audio", "video", "sticker"})
# WhatsApp type -> the attachment kind the processor knows (app.channels.base).
ATTACHMENT_KINDS = {
    "image": "image",
    "sticker": "image",
    "document": "file",
    "audio": "audio",
    "video": "video",
    "location": "location",
    "contacts": "contact",
}
# Types the bot never answers (stored only): reactions, system notices, unsupported...
SILENT_TYPES = frozenset({"reaction", "system", "unsupported", "request_welcome", "ephemeral"})


def is_silent(message_type: str, text: str | None) -> bool:
    """Stored, but the bot does not answer it."""
    if message_type in SILENT_TYPES:
        return True
    return not (text or "").strip() and message_type not in ATTACHMENT_KINDS


@dataclass(frozen=True)
class WaMedia:
    media_id: str
    mime_type: str | None = None
    filename: str | None = None


@dataclass(frozen=True)
class WaIncoming:
    """A message of the contact (or, for an echo, of the studio to the contact)."""

    wamid: str
    wa_id: str  # the contact's WhatsApp id (digits)
    message_type: str
    text: str = ""
    timestamp: datetime | None = None
    profile_name: str | None = None
    media: WaMedia | None = None
    # The id of the option tapped or the template button's payload ("" if none).
    payload: str = ""

    @property
    def attachment_types(self) -> tuple[str, ...]:
        kind = ATTACHMENT_KINDS.get(self.message_type)
        return (kind,) if kind else ()

    @property
    def content_type(self) -> str:
        return "sticker" if self.message_type == "sticker" else "text"


@dataclass(frozen=True)
class WaStatus:
    wamid: str
    status: str
    timestamp: datetime | None = None
    error_code: int | None = None
    error_text: str | None = None


@dataclass
class WaDelivery:
    messages: list[WaIncoming] = field(default_factory=list)
    statuses: list[WaStatus] = field(default_factory=list)
    echoes: list[WaIncoming] = field(default_factory=list)


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def message_text(message: dict[str, Any]) -> str:
    """What the person wrote (the title of an option tapped, the caption of a media)."""
    kind = message.get("type")
    if kind == "text":
        return str(_dict(message.get("text")).get("body") or "").strip()
    if kind == "interactive":
        interactive = _dict(message.get("interactive"))
        reply = _dict(interactive.get("button_reply")) or _dict(interactive.get("list_reply"))
        return str(reply.get("title") or "").strip()
    if kind == "button":
        button = _dict(message.get("button"))
        return str(button.get("text") or button.get("payload") or "").strip()
    if kind in MEDIA_TYPES:
        return str(_dict(message.get(kind)).get("caption") or "").strip()
    return ""


def reply_payload(message: dict[str, Any]) -> str:
    """The payload of a template's quick reply, or the id of an interactive option."""
    kind = message.get("type")
    if kind == "button":
        return str(_dict(message.get("button")).get("payload") or "")[:200]
    if kind == "interactive":
        interactive = _dict(message.get("interactive"))
        reply = _dict(interactive.get("button_reply")) or _dict(interactive.get("list_reply"))
        return str(reply.get("id") or "")[:200]
    return ""


def _media(message: dict[str, Any]) -> WaMedia | None:
    kind = message.get("type")
    if kind not in MEDIA_TYPES:
        return None
    data = _dict(message.get(kind))
    if not data.get("id"):
        return None
    return WaMedia(
        media_id=str(data["id"]),
        mime_type=str(data["mime_type"]) if data.get("mime_type") else None,
        filename=str(data["filename"])[:255] if data.get("filename") else None,
    )


def _incoming(message: dict[str, Any], wa_id: str, profile_name: str | None) -> WaIncoming | None:
    wamid = message.get("id")
    if not wamid or not wa_id:
        return None
    return WaIncoming(
        wamid=str(wamid),
        wa_id=wa_id,
        message_type=str(message.get("type") or "unsupported"),
        text=message_text(message),
        timestamp=_timestamp(message.get("timestamp")),
        profile_name=profile_name,
        media=_media(message),
        payload=reply_payload(message),
    )


def _status(status: dict[str, Any]) -> WaStatus | None:
    if not status.get("id") or not status.get("status"):
        return None
    errors = _list(status.get("errors"))
    error = errors[0] if errors else {}
    details = _dict(error.get("error_data")).get("details")
    try:
        code = int(error["code"]) if error.get("code") is not None else None
    except (TypeError, ValueError):
        code = None
    text = " — ".join(str(t) for t in (error.get("title") or error.get("message"), details) if t)
    return WaStatus(
        wamid=str(status["id"]),
        status=str(status["status"]),
        timestamp=_timestamp(status.get("timestamp")),
        error_code=code,
        error_text=text or None,
    )


def parse_delivery(payload: dict[str, Any], phone_number_id: str) -> WaDelivery:
    """Everything in one webhook delivery for our number (other numbers are left out)."""
    delivery = WaDelivery()
    for entry in _list(payload.get("entry")):
        for change in _list(entry.get("changes")):
            value = _dict(change.get("value"))
            number = str(_dict(value.get("metadata")).get("phone_number_id") or "")
            if phone_number_id and number != phone_number_id:
                continue
            kind = change.get("field")
            if kind == "messages":
                names = {
                    str(c.get("wa_id")): _dict(c.get("profile")).get("name")
                    for c in _list(value.get("contacts"))
                }
                for message in _list(value.get("messages")):
                    wa_id = str(message.get("from") or "")
                    if parsed := _incoming(message, wa_id, names.get(wa_id)):
                        delivery.messages.append(parsed)
                for status in _list(value.get("statuses")):
                    if parsed_status := _status(status):
                        delivery.statuses.append(parsed_status)
            elif kind == "smb_message_echoes":
                for echo in _list(value.get("message_echoes")):
                    if parsed := _incoming(echo, str(echo.get("to") or ""), None):
                        delivery.echoes.append(parsed)
    return delivery
