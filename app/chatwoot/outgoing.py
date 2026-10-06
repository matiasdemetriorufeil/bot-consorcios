"""What the bot sends in one turn -> the Chatwoot messages to create, in order.

Chatwoot sends each message to WhatsApp in its own job, and the jobs do not keep the order:
a turn with debt blocks went out with the agent's text BEFORE the block. So a turn is one
message: the blocks (app.bot.debt_message), a blank line between them, and the agent's text
at the end (join_blocks). Only when it does not fit in MAX_MESSAGE (WhatsApp cuts at 4096) it
is split, at block boundaries; a block is cut (at line ends) only if it alone does not fit.

With options (offer_choices) the whole turn goes in the text of the message with buttons if
it fits app.bot.choices.MAX_TEXT (WhatsApp's limit for interactive messages); otherwise the
blocks go first as text and the buttons after, with the agent's text only.
"""

from collections.abc import Sequence

from app.bot.choices import MAX_TEXT
from app.bot.debt_message import BLOCK_SEPARATOR, join_blocks

MAX_MESSAGE = 4000


def pack(parts: Sequence[str], limit: int = MAX_MESSAGE) -> list[str]:
    """The parts joined with a blank line into as few messages of at most `limit` characters
    as the order allows, never splitting a part that fits in one message."""
    messages: list[str] = []
    current = ""
    for part in parts:
        for piece in _fit(part, limit):
            joined = f"{current}{BLOCK_SEPARATOR}{piece}" if current else piece
            if len(joined) <= limit:
                current = joined
            else:
                messages.append(current)
                current = piece
    if current:
        messages.append(current)
    return messages


def _fit(part: str, limit: int) -> list[str]:
    """A part longer than `limit` cut at line ends (a line longer than that, by characters)."""
    if len(part) <= limit:
        return [part]
    pieces: list[str] = []
    current = ""
    for line in part.split("\n"):
        while len(line) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:limit])
            line = line[limit:]
        joined = f"{current}\n{line}" if current else line
        if len(joined) <= limit:
            current = joined
        else:
            pieces.append(current)
            current = line
    if current:
        pieces.append(current)
    return pieces


def with_buttons(blocks: Sequence[str], text: str) -> tuple[list[str], str]:
    """(text messages to send first, text of the message with the options)."""
    whole = join_blocks(blocks, text)
    if len(whole) <= MAX_TEXT:
        return [], whole
    return pack(blocks), text
