"""Conversation turns -> agent history, the same for every channel."""

from collections.abc import Iterable

from app.bot.agent import HISTORY_MESSAGES, trim_history
from app.llm import AssistantMessage, Message, UserMessage


def to_history(turns: Iterable[tuple[bool, str]], limit: int = HISTORY_MESSAGES) -> list[Message]:
    """(is the person, text) in order -> the last `limit` messages. Consecutive texts of the
    same side are joined (one per line), so turns always alternate."""
    joined: list[tuple[bool, str]] = []
    for is_user, text in turns:
        if not text:
            continue
        if joined and joined[-1][0] == is_user:
            joined[-1] = (is_user, f"{joined[-1][1]}\n{text}")
        else:
            joined.append((is_user, text))
    history: list[Message] = [UserMessage(t) if u else AssistantMessage(t) for u, t in joined]
    return trim_history(history, limit)
