"""Options the person can tap instead of typing (WhatsApp reply buttons and lists).

WhatsApp Cloud limits (Meta rejects what exceeds them, it does not cut anything): up to 3
reply buttons with titles of 20 characters at most; a list of up to 10 rows with titles of 24
at most; the text of an interactive message up to 1024 characters. 3 options or fewer go as
buttons and more as a list (app.whatsapp.client).

When the person taps, WhatsApp gives back the option's TITLE, which the bot takes as what the
person wrote, so titles must be distinct and make sense alone. Where
buttons cannot be shown, the options go numbered in the text (numbered_text).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

MAX_BUTTONS = 3
MAX_OPTIONS = 10
MIN_OPTIONS = 2
MAX_BUTTON_TITLE = 20
MAX_LIST_TITLE = 24
MAX_TEXT = 1024


class Choice(NamedTuple):
    title: str
    value: str


@dataclass(frozen=True)
class Offer:
    """A reply with options to tap (offer_choices, or a confirmation built by the code)."""

    text: str
    choices: tuple[Choice, ...]

    @property
    def titles(self) -> list[str]:
        return [c.title for c in self.choices]


def title_limit(count: int) -> int:
    """Longest title allowed for `count` options (buttons up to 3, a list beyond)."""
    return MAX_BUTTON_TITLE if count <= MAX_BUTTONS else MAX_LIST_TITLE


def problems(text: str, titles: Sequence[str]) -> list[str]:
    """What WhatsApp would reject (in Spanish: the model reads them). Empty when it fits."""
    found: list[str] = []
    if not text.strip():
        found.append("falta el texto del mensaje")
    elif len(text) > MAX_TEXT:
        found.append(f"el texto tiene {len(text)} caracteres (máximo {MAX_TEXT})")
    if not MIN_OPTIONS <= len(titles) <= MAX_OPTIONS:
        found.append(f"tiene que haber entre {MIN_OPTIONS} y {MAX_OPTIONS} opciones")
    limit = title_limit(len(titles))
    if long := [t for t in titles if len(t) > limit]:
        found.append(f"títulos de más de {limit} caracteres: {', '.join(long)}")
    if any(not t.strip() for t in titles):
        found.append("hay opciones vacías")
    if len({t.strip().casefold() for t in titles}) != len(titles):
        found.append("hay opciones repetidas")
    return found


def numbered_text(text: str, titles: Sequence[str]) -> str:
    """The fallback where buttons cannot be shown: the options numbered after the text."""
    options = "\n".join(f"{n}. {title}" for n, title in enumerate(titles, 1))
    return f"{text}\n\n{options}\n\nRespondé con el número o escribí la opción."


def with_options(text: str, titles: Sequence[str]) -> str:
    """How an offer is kept in the agent's history: the text plus the titles offered, so the
    next turn knows what "Sí, pasame" (or "2") answers."""
    return f"{text}\n[Opciones: {' / '.join(titles)}]"
