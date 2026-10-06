"""What the bot needs from a messaging channel (Chatwoot, or the WhatsApp Cloud API directly).

app.channels.processor answers a message the same way whatever the channel: it identifies the
person, runs the agent and sends the turn. Everything that depends on the channel (whether
the bot still has the conversation, the history, how a message and its options are sent, what
a handoff leaves for the operators) goes through Channel.
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.bot.choices import Choice
from app.bot.identity import Identity
from app.bot.tools import Handoff
from app.llm import Message

# Attachments the studio can look at once the conversation is handed off.
VIEWABLE_ATTACHMENTS = frozenset({"image", "file"})


class ChannelError(Exception):
    """The channel failed or is not configured."""


@dataclass(frozen=True)
class InboundMessage:
    """One message of the person, as the processor sees it."""

    # The conversation in the channel: per-conversation lock, logs and BotEvent.
    conversation_id: int
    # The message id in the channel (for logs and bot_events).
    message_id: int
    # The phone to identify with ("" if none) and whether the channel itself set it.
    phone: str
    phone_trusted: bool
    content: str
    content_type: str = "text"
    attachment_types: tuple[str, ...] = ()
    # The channel's own message, read only by its Channel.
    source: object = field(default=None, compare=False)

    @property
    def is_sticker(self) -> bool:
        return self.content_type == "sticker"


class Channel(Protocol):
    # Prefix of the channel's own bot_events ("chatwoot" -> chatwoot_skipped, ...).
    name: str
    # Namespace of the conversation's Postgres advisory lock (app.channels.locks).
    lock_namespace: int

    def still_with_bot(self, message: InboundMessage) -> bool:
        """Whether the bot may still answer (nobody took the conversation)."""
        ...

    def history(self, message: InboundMessage) -> list[Message]:
        """The conversation before this message, as agent history (never raises)."""
        ...

    def supports_choices(self, message: InboundMessage) -> bool:
        """Whether options can go as buttons or a list (otherwise: numbered in the text)."""
        ...

    def send_text(self, message: InboundMessage, text: str, *, more: bool) -> None:
        """Sends one text message. more: another message of the same turn follows (the
        channel makes sure this one goes out first). ChannelError if it fails."""
        ...

    def send_choices(self, message: InboundMessage, text: str, choices: tuple[Choice, ...]) -> None:
        """Sends text with options to tap. ChannelError or ValueError if they cannot go."""
        ...

    def hand_off(
        self, message: InboundMessage, handoff: Handoff, who: Identity, trusted: bool
    ) -> None:
        """Leaves the conversation to a human. ChannelError if it fails."""
        ...

    def after_turn(self, message: InboundMessage, who: Identity) -> None:
        """Anything else to update at the end of a turn (never raises)."""
        ...
