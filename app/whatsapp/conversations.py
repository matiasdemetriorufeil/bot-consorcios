"""States of a WhatsApp conversation and how they change.

    bot ──(bot hands off)──> waiting_human ──(operator takes it)──> human
     ↑                              │                                 │
     └──(returned to the bot)───────┴─────────────────────────────────┤
     └──(contact writes again)── resolved <──(resolved)───────────────┘

The bot only answers in "bot". A message from the studio's phone app (coexistence echo)
moves the conversation to "human". From "resolved", the contact's next message gives it back
to the bot, which starts a new stretch: its history starts at that message (bot_since).
"""

from datetime import datetime

from app.db.models import WaConversation, WaConversationStatus

BOT = WaConversationStatus.BOT
WAITING_HUMAN = WaConversationStatus.WAITING_HUMAN
HUMAN = WaConversationStatus.HUMAN
RESOLVED = WaConversationStatus.RESOLVED


def on_inbound(conversation: WaConversation, message_id: int, at: datetime) -> None:
    """A new message of the contact."""
    if conversation.last_inbound_at is None or at > conversation.last_inbound_at:
        conversation.last_inbound_at = at
    conversation.unread_count = (conversation.unread_count or 0) + 1
    if conversation.status == RESOLVED:
        conversation.status = BOT
        conversation.bot_since_message_id = message_id
        conversation.assigned_to = None
        clear_handoff(conversation)


def clear_handoff(conversation: WaConversation) -> None:
    conversation.handoff_reason = None
    conversation.handoff_priority = None
    conversation.handoff_summary = None
    conversation.handoff_labels = []
    conversation.handed_off_at = None


def hand_off(
    conversation: WaConversation,
    *,
    reason: str,
    priority: str,
    summary: str,
    labels: list[str],
    at: datetime,
) -> None:
    """The bot passes the conversation to a human (it stops answering)."""
    conversation.status = WAITING_HUMAN
    conversation.handoff_reason = reason
    conversation.handoff_priority = priority
    conversation.handoff_summary = summary
    conversation.handoff_labels = labels
    conversation.handed_off_at = at


def taken_by_operator(conversation: WaConversation, operator: str | None = None) -> None:
    """An operator writes (from the panel or the phone app) or takes it."""
    conversation.status = HUMAN
    if operator:
        conversation.assigned_to = operator


def assign(conversation: WaConversation, operator: str) -> None:
    taken_by_operator(conversation, operator)


def return_to_bot(conversation: WaConversation) -> None:
    """An operator gives it back: the bot answers the next message (same stretch)."""
    conversation.status = BOT
    conversation.assigned_to = None


def resolve(conversation: WaConversation, at: datetime) -> None:
    conversation.status = RESOLVED
    conversation.resolved_at = at
    conversation.unread_count = 0
