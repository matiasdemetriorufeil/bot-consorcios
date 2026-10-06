"""Chatwoot Agent Bot webhook payloads -> what the bot needs. Pure functions, no I/O.

message_created (v4.18, Message#webhook_data): id, content, content_type, message_type
("incoming" / "outgoing" / "activity" / "template"), private, attachments[].file_type,
content_attributes, account.id, inbox.id, sender (the contact, for incoming messages) and
conversation (id = display id, status, channel = "Channel::Whatsapp" / "Channel::WebWidget" /
..., meta.sender = the contact).

Options tapped (see app.bot.choices): in WhatsApp the tap arrives as an ordinary incoming
message whose content is the option's title. In the web widget no message is created:
Chatwoot stores content_attributes.submitted_values ([{title, value}]) on the bot's own
"input_select" message and sends message_updated. That event is taken as the person writing
the title (IncomingMessage.selection_of = the id of the message with the options).
"""

from dataclasses import dataclass, field
from typing import Any

# Attachments the studio can look at once the conversation is handed off.
VIEWABLE_ATTACHMENTS = frozenset({"image", "file"})


@dataclass(frozen=True)
class IncomingMessage:
    message_id: int
    conversation_id: int  # display id
    account_id: int | None
    inbox_id: int | None
    conversation_status: str | None
    content: str
    content_type: str = "text"
    attachment_types: tuple[str, ...] = ()
    contact_id: int | None = None
    contact_phone: str | None = None
    contact_attributes: dict[str, Any] = field(default_factory=dict)
    channel: str | None = None  # the inbox's channel type, e.g. "Channel::Whatsapp"
    # A web widget option tapped: the id of the bot's message with the options (also
    # message_id, which is what makes the tap processed only once).
    selection_of: int | None = None

    @property
    def is_sticker(self) -> bool:
        return self.content_type == "sticker"


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def submitted_title(payload: dict[str, Any]) -> str | None:
    """The option chosen in the web widget, when the event is that (see the module doc)."""
    if (
        payload.get("event") != "message_updated"
        or payload.get("message_type") != "outgoing"
        or payload.get("content_type") != "input_select"
    ):
        return None
    submitted = (payload.get("content_attributes") or {}).get("submitted_values") or []
    first = submitted[0] if isinstance(submitted, list) and submitted else {}
    if not isinstance(first, dict):
        return None
    chosen = str(first.get("title") or first.get("value") or "").strip()
    return chosen or None


def ignore_reason(payload: dict[str, Any]) -> str | None:
    """Why the bot must not answer this event, or None when it is a message to answer."""
    if submitted_title(payload) is None:
        if payload.get("event") != "message_created":
            return "not_message_created"
        if payload.get("message_type") != "incoming":
            return "not_incoming"
    if payload.get("private"):
        return "private"
    conversation = payload.get("conversation") or {}
    # Pending = still with the bot. Open/resolved/snoozed: a human has it (or had it).
    if conversation.get("status") != "pending":
        return "not_pending"
    if _int(payload.get("id")) is None or _int(conversation.get("id")) is None:
        return "malformed"
    return None


def parse_incoming(payload: dict[str, Any]) -> IncomingMessage:
    conversation = payload.get("conversation") or {}
    sender = payload.get("sender") or {}
    if sender.get("type") not in (None, "contact"):
        sender = {}
    meta_sender = (conversation.get("meta") or {}).get("sender") or {}
    contact = sender or meta_sender
    inbox_id = _int((payload.get("inbox") or {}).get("id")) or _int(conversation.get("inbox_id"))
    chosen = submitted_title(payload)
    return IncomingMessage(
        message_id=int(payload["id"]),
        conversation_id=int(conversation["id"]),
        account_id=_int((payload.get("account") or {}).get("id")),
        inbox_id=inbox_id,
        conversation_status=conversation.get("status"),
        content=chosen or (payload.get("content") or "").strip(),
        content_type="text" if chosen else payload.get("content_type") or "text",
        attachment_types=tuple(
            str(a.get("file_type") or "file") for a in payload.get("attachments") or []
        ),
        contact_id=_int(contact.get("id")),
        contact_phone=contact.get("phone_number") or None,
        contact_attributes=dict(contact.get("custom_attributes") or {}),
        channel=conversation.get("channel") or None,
        selection_of=int(payload["id"]) if chosen else None,
    )
