"""Chatwoot conversation messages (API JSON) -> agent history.

Incoming messages are the person's turns; outgoing and template messages (the bot's or an
operator's) are the assistant's. Private notes and activity messages are skipped. Consecutive
messages of the same side are joined, so turns always alternate.
"""

from typing import Any

from app.bot.agent import HISTORY_MESSAGES, trim_history
from app.llm import AssistantMessage, Message, UserMessage

# The API sends message_type as an integer; webhooks as a string.
_INCOMING = {0, "incoming"}
_ASSISTANT = {1, 3, "outgoing", "template"}


def _text(message: dict[str, Any]) -> str:
    text = (message.get("content") or "").strip()
    if text:
        return text
    if message.get("attachments"):
        types = sorted({str(a.get("file_type") or "file") for a in message["attachments"]})
        return f"[adjunto: {', '.join(types)}]"
    return ""


def build_history(
    messages: list[dict[str, Any]], *, before_id: int, limit: int = HISTORY_MESSAGES
) -> list[Message]:
    """The last `limit` messages strictly before message `before_id`, as agent history."""
    turns: list[tuple[bool, str]] = []  # (is_user, text)
    for message in sorted(messages, key=lambda m: int(m.get("id") or 0)):
        if message.get("private") or int(message.get("id") or 0) >= before_id:
            continue
        kind = message.get("message_type")
        if kind in _INCOMING:
            is_user = True
        elif kind in _ASSISTANT:
            is_user = False
        else:
            continue
        text = _text(message)
        if not text:
            continue
        if turns and turns[-1][0] == is_user:
            turns[-1] = (is_user, f"{turns[-1][1]}\n{text}")
        else:
            turns.append((is_user, text))
    history: list[Message] = [UserMessage(t) if u else AssistantMessage(t) for u, t in turns]
    return trim_history(history, limit)
