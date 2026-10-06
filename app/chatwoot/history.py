"""Chatwoot conversation messages (API JSON) -> agent history.

Incoming messages are the person's turns; outgoing and template messages (the bot's or an
operator's) are the assistant's. Private notes and activity messages are skipped. Consecutive
messages of the same side are joined, so turns always alternate.

A bot message with options ("input_select") keeps its titles (app.bot.choices.with_options).
An option chosen in the web widget is stored on that message (submitted_values), not as an
incoming message: it becomes the person's turn right after it.
"""

from typing import Any

from app.bot.agent import HISTORY_MESSAGES
from app.bot.choices import with_options
from app.channels.history import to_history
from app.llm import Message

# The API sends message_type as an integer; webhooks as a string.
_INCOMING = {0, "incoming"}
_ASSISTANT = {1, 3, "outgoing", "template"}


def _text(message: dict[str, Any]) -> str:
    text = (message.get("content") or "").strip()
    if text and message.get("content_type") == "input_select":
        titles = [str(i.get("title") or "") for i in _attributes(message).get("items") or []]
        return with_options(text, titles) if titles else text
    if text:
        return text
    if message.get("attachments"):
        types = sorted({str(a.get("file_type") or "file") for a in message["attachments"]})
        return f"[adjunto: {', '.join(types)}]"
    return ""


def _attributes(message: dict[str, Any]) -> dict[str, Any]:
    attributes = message.get("content_attributes")
    return attributes if isinstance(attributes, dict) else {}


def _chosen(message: dict[str, Any]) -> str:
    """The option chosen in the web widget on a bot message with options, if any."""
    if message.get("content_type") != "input_select":
        return ""
    submitted = _attributes(message).get("submitted_values") or []
    first = submitted[0] if isinstance(submitted, list) and submitted else {}
    if not isinstance(first, dict):
        return ""
    return str(first.get("title") or first.get("value") or "").strip()


def build_history(
    messages: list[dict[str, Any]],
    *,
    before_id: int,
    limit: int = HISTORY_MESSAGES,
    pending_selection: int | None = None,
) -> list[Message]:
    """The last `limit` messages strictly before message `before_id`, as agent history.
    pending_selection: the message whose widget choice is being answered now (its choice is
    the current message, not history)."""
    turns: list[tuple[bool, str]] = []  # (is_user, text)
    add = turns.append
    for message in sorted(messages, key=lambda m: int(m.get("id") or 0)):
        message_id = int(message.get("id") or 0)
        if message.get("private") or message_id >= before_id:
            continue
        kind = message.get("message_type")
        if kind in _INCOMING:
            is_user = True
        elif kind in _ASSISTANT:
            is_user = False
        else:
            continue
        add((is_user, _text(message)))
        if not is_user and message_id != pending_selection and (chosen := _chosen(message)):
            add((True, chosen))
    return to_history(turns, limit)
