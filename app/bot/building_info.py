"""Which building texts (BuildingInfo, loaded in the admin panel) go to the model.

All of them when they fit in TOKEN_BUDGET. Otherwise every text is split into sections
(at headings like "Artículo 5" or "Capítulo II", and at SECTION_TOKENS at most) and only the
sections sharing keywords with the question go, best first, up to the budget. Plain keyword
matching with crude stems and a few synonyms: no embeddings, no AI.

Tokens are estimated from the length (CHARS_PER_TOKEN), on the safe side for Spanish.
"""

import math
import re
import unicodedata
from dataclasses import dataclass

from app.db.models import BuildingInfo, BuildingInfoCategory

TOKEN_BUDGET = 8000
SECTION_TOKENS = 500
CHARS_PER_TOKEN = 3.5
STEM_LENGTH = 5

# How the bot names each kind of text when it cites it ("según el reglamento interno").
CATEGORY_SOURCES = {
    BuildingInfoCategory.REGLAMENTO: "reglamento interno",
    BuildingInfoCategory.HORARIOS: "horarios del edificio",
    BuildingInfoCategory.CONTACTOS: "contactos del edificio",
    BuildingInfoCategory.EMERGENCIAS: "información de emergencias del edificio",
    BuildingInfoCategory.OTROS: "información del edificio",
}

_HEADING = re.compile(
    r"^\s*(#+\s|(art[ií]culo|art\.|cap[ií]tulo|t[ií]tulo|secci[oó]n|anexo)\b|\d{1,3}[.)-]\s)",
    re.IGNORECASE,
)
_STOPWORD_TEXT = """
a al algo algun alguna algunas alguno algunos ante antes aqui asi cada como con contra
cual cuales cuando cuanto de del desde donde dos el ella ellas ellos en entre era es esa
ese eso esta estan este esto hay hasta la las le les lo los mas me mi mis muy ni no nos
o otra otro para pero poco por porque puede pueden puedo que quien se segun ser si sin
sobre son su sus tambien tan te tener tengo tiene tienen todo todos tu un una uno unos
usted y ya yo hola gracias quiero queria saber consulta pregunta decime podes podria
edificio consorcio dia dias
"""
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())
# Words that mean the same for these questions (compared by stem).
_SYNONYMS = [
    {"mascota", "perro", "gato", "animal"},
    {"mudanza", "mudar", "mudo", "flete"},
    {"pileta", "piscina", "natatorio"},
    {"ruido", "ruidos", "musica", "fiesta", "silencio", "descanso"},
    {"basura", "residuo", "residuos", "reciclable"},
    {"cochera", "garage", "garaje", "estacionamiento", "auto"},
    {"encargado", "portero", "conserje", "porteria"},
    {"sum", "salon", "quincho", "parrilla", "asador", "asado"},
    {"ropa", "tender", "tendedero", "lavarropas"},
    {"aire", "acondicionado", "split", "equipo"},
    {"obra", "obras", "refaccion", "reforma", "albanil"},
    {"horario", "hora", "horas"},
]


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _stem(word: str) -> str:
    return word[:STEM_LENGTH]


def _words(text: str) -> list[str]:
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    return re.findall(r"[a-z0-9]+", plain)


_SYNONYM_STEMS = [{_stem(w) for w in group} for group in _SYNONYMS]


def keywords(text: str) -> set[str]:
    """Stems of the meaningful words of a question, plus their synonyms."""
    stems = {_stem(w) for w in _words(text) if w not in _STOPWORDS and len(w) >= 3}
    for group in _SYNONYM_STEMS:
        if stems & group:
            stems |= group
    return stems


def split_sections(text: str, max_tokens: int = SECTION_TOKENS) -> list[str]:
    """Consecutive pieces of the text: a new one at each heading line or when it would grow
    past max_tokens. A single line longer than that is cut at spaces."""
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    pieces: list[str] = []
    for line in text.splitlines():
        while len(line) > max_chars:
            cut = line.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            pieces.append(line[:cut])
            line = line[cut:].lstrip()
        pieces.append(line)

    sections: list[str] = []
    current: list[str] = []
    size = 0
    for piece in pieces:
        starts = bool(_HEADING.match(piece)) and any(p.strip() for p in current)
        if current and (starts or size + len(piece) + 1 > max_chars):
            sections.append("\n".join(current).strip())
            current, size = [], 0
        current.append(piece)
        size += len(piece) + 1
    sections.append("\n".join(current).strip())
    return [s for s in sections if s]


@dataclass(frozen=True)
class _Section:
    info_index: int
    position: int
    text: str
    score: int
    tokens: int


@dataclass(frozen=True)
class Selection:
    texts: list[dict[str, str]]
    mode: str  # "full" (every text) | "sections" (only the relevant ones)
    sections_sent: int
    sections_total: int
    # Titles of the texts with nothing relevant (sections mode), so the bot knows they exist.
    other_titles: list[str]
    texts_tokens: int


def _text_entry(info: BuildingInfo, content: str) -> dict[str, str]:
    return {
        "title": info.title,
        "source": CATEGORY_SOURCES.get(info.category, "información del edificio"),
        "content": content,
    }


def select_texts(infos: list[BuildingInfo], question: str, budget: int = TOKEN_BUDGET) -> Selection:
    total = sum(estimate_tokens(i.title) + estimate_tokens(i.content) for i in infos)
    if total <= budget:
        texts = [_text_entry(i, i.content) for i in infos]
        return Selection(texts, "full", len(infos), len(infos), [], total)

    wanted = keywords(question)
    sections: list[_Section] = []
    for index, info in enumerate(infos):
        title_stems = {_stem(w) for w in _words(info.title)}
        for position, text in enumerate(split_sections(info.content)):
            stems = {_stem(w) for w in _words(text)} | title_stems
            score = len(wanted & stems)
            sections.append(_Section(index, position, text, score, estimate_tokens(text)))

    chosen: list[_Section] = []
    used = 0
    for section in sorted(sections, key=lambda s: (-s.score, s.info_index, s.position)):
        if section.score == 0:
            break
        if used + section.tokens > budget:
            continue
        chosen.append(section)
        used += section.tokens

    texts: list[dict[str, str]] = []
    for index, info in enumerate(infos):
        mine = sorted((s for s in chosen if s.info_index == index), key=lambda s: s.position)
        if not mine:
            continue
        parts = [mine[0].text]
        for before, after in zip(mine, mine[1:], strict=False):
            # Sections left out in between are marked so the model does not read them as one.
            gap = after.position != before.position + 1
            parts.append(f"[…]\n{after.text}" if gap else after.text)
        texts.append(_text_entry(info, "\n".join(parts)))
    sent_titles = {t["title"] for t in texts}
    others = [i.title for i in infos if i.title not in sent_titles]
    return Selection(texts, "sections", len(chosen), len(sections), others, used)
