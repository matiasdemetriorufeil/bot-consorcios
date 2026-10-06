"""Tolerant, deterministic search of a unit from what a person types ("Rodas 2 4C",
"rodas II 4º C", "4 C de Rodas II"). Text normalization + rapidfuzz, no AI.

It never picks a unit on its own unless the match is unambiguous: one building clearly best
and exactly one unit matching exactly. Anything else comes back as candidates to ask about.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Building, Unit

BUILDING_MIN_SCORE = 80
# Buildings this close to the best one are also candidates.
BUILDING_SCORE_MARGIN = 5
UNIT_FUZZY_MIN_SCORE = 75
# Unit types whose label tokens are compared without order (see _order_free).
_ORDER_FREE_TYPES = frozenset({"loc", "coc"})
MAX_CANDIDATES = 5
# A query token "consumes" a building token (combined text) above this similarity.
TOKEN_MATCH_SCORE = 80

_STOPWORDS = frozenset(
    {
        "de", "del", "la", "el", "los", "las", "y", "en",
        "edificio", "edif", "ed", "consorcio", "cons",
        "piso", "departamento", "depto", "dpto", "dto", "dep", "unidad", "uf", "nro", "numero",
        "num",
    }
)  # fmt: skip
_ROMAN = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8",
    "ix": "9", "x": "10", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15",
}  # fmt: skip
# The same thing written in different ways (ConsorPlus codes and how people say them).
_SYNONYMS = {
    "cochera": "coc", "cocheras": "coc", "coch": "coc", "co": "coc", "ch": "coc",
    "subsuelo": "ss", "sub": "ss",
    "local": "loc", "oficina": "of", "ofic": "of", "ofc": "of", "entrepiso": "ep",
    "torre": "t",
}  # fmt: skip
# Glued ConsorPlus codes: "TII" (torre II), "TA" (torre A), "PBA" (PB A), "TIVPBA".
_GLUED_CODE = re.compile(r"^(?:t(iv|i{1,3}|a|b))?(pb)?([a-z])?$")
# ConsorPlus prefixes building names with a 3-digit code ("007 LOS PINOS").
_CODE_PREFIX = re.compile(r"^\s*\d{3}\s+(?=\S)")
_ORDINAL = re.compile(r"\b(\d+)(?:ro|do|to|er|vo|no|mo)\b")


class SearchStatus(StrEnum):
    FOUND = "found"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class UnitCandidate:
    unit_id: int
    building_name: str
    unit_label: str


@dataclass(frozen=True)
class UnitSearchResult:
    status: SearchStatus
    # FOUND: exactly one. AMBIGUOUS: the options to ask the person about.
    candidates: tuple[UnitCandidate, ...] = ()
    # Buildings that matched (useful to say "no encontré esa unidad en ...").
    buildings: tuple[str, ...] = field(default=())

    @property
    def unit(self) -> UnitCandidate | None:
        return self.candidates[0] if self.status == SearchStatus.FOUND else None


def _words(text: str) -> list[str]:
    # Ordinal marks go before NFKD, which would turn "º" into "o".
    decomposed = unicodedata.normalize("NFKD", re.sub(r"[º°ª]", " ", text or ""))
    clean = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    clean = clean.replace("planta baja", "pb")
    clean = _ORDINAL.sub(r"\1", clean)
    clean = re.sub(r"(\d)([a-z])", r"\1 \2", clean)
    clean = re.sub(r"([a-z])(\d)", r"\1 \2", clean)
    return [w for w in re.sub(r"[^a-z0-9]+", " ", clean).split() if w not in _STOPWORDS]


def _word_tokens(word: str, roman: bool) -> list[str]:
    if roman and word in _ROMAN:
        return [_ROMAN[word]]
    if word.isdigit():
        return [word.lstrip("0") or "0"]
    return _split_code(_SYNONYMS.get(word, word))


def tokens(text: str, *, roman: bool = False) -> list[str]:
    """Normalized words: no accents/case/punctuation/ordinals/stopwords, digits split from
    letters ("4C" -> "4", "c"), no leading zeros, synonyms unified ("cochera" -> "coc").
    With roman=True "II" -> "2" (for building names)."""
    return [t for w in _words(text) for t in _word_tokens(w, roman)]


def _aligned_tokens(text: str) -> tuple[list[str], list[str]]:
    """The same token list twice, position by position: with and without roman numerals."""
    with_roman: list[str] = []
    plain: list[str] = []
    for word in _words(text):
        r, p = _word_tokens(word, True), _word_tokens(word, False)
        with_roman += r if len(r) == len(p) else p
        plain += p
    return with_roman, plain


def _split_code(word: str) -> list[str]:
    """ "tii" -> ["t", "2"], "pba" -> ["pb", "a"], "tivpba" -> ["t", "4", "pb", "a"]."""
    glued = _GLUED_CODE.match(word) if len(word) > 2 or word in ("ti", "ta", "tb") else None
    if glued is None:
        return [word]
    tower, pb, letter = glued.groups()
    parts = ["t", _ROMAN.get(tower, tower)] if tower else []
    return parts + ([pb] if pb else []) + ([letter] if letter else [])


# Words of a building name too common to show the person named it.
_NOT_DISTINCTIVE = frozenset({"torre", "sum", "salon"})


def named_in(building_text: str, texts: Sequence[str]) -> bool:
    """Whether the person wrote the building the model passes: some distinctive word of it
    (3+ letters) appears, by its first 4 letters, in what the person wrote ("Rodas" in "el
    rodas 2", "Algarrobos" in "algarrobo"). Guards the rule that the model never chooses a
    building the person did not name. Nothing distinctive to compare: accepted."""
    words = [
        w
        for w in _words(building_text)
        if len(w) >= 3 and not w.isdigit() and w not in _NOT_DISTINCTIVE
    ]
    if not words:
        return True
    said = " ".join(_words(" ".join(texts)))
    return any(w[:4] in said for w in words)


def display_building_name(name: str) -> str:
    """ "007 LOS PINOS" -> "LOS PINOS": the name without the ConsorPlus code, for the chat."""
    return _CODE_PREFIX.sub("", name).strip() or name


def _building_tokens(name: str) -> list[str]:
    return tokens(_CODE_PREFIX.sub("", name), roman=True)


def _unit_key(label: str) -> tuple[str, ...]:
    """ "04-C", "4º C", "piso 4 dpto C" -> ("4", "c"). Order matters: "TI-4A" != "TIV-1A"."""
    return tuple(tokens(label))


def _order_free(wanted: tuple[str, ...], key: tuple[str, ...]) -> bool:
    """Locales and cocheras: "local de planta baja" is "PB-LOC", "cochera 12" is "12 COC".
    Departments keep the order ("TI-4A" != "TIV-1A")."""
    return bool(_ORDER_FREE_TYPES & set(wanted) & set(key))


def _same_unit(wanted: tuple[str, ...], key: tuple[str, ...]) -> bool:
    if _order_free(wanted, key):
        return Counter(wanted) == Counter(key)
    return wanted == key


def _starts_like(wanted: tuple[str, ...], key: tuple[str, ...]) -> bool:
    if _order_free(wanted, key):
        return Counter(wanted) <= Counter(key)
    return key[: len(wanted)] == wanted


def _same(query_token: str, name_token: str) -> bool:
    """Equal, or a small typo in a long word ("rodaz" / "rodas"). Numbers must be equal."""
    if query_token == name_token:
        return True
    return (
        min(len(query_token), len(name_token)) >= 4
        and query_token.isalpha()
        and name_token.isalpha()
        and fuzz.ratio(query_token, name_token) >= TOKEN_MATCH_SCORE
    )


def _contiguous_run(query: list[str], name: list[str]) -> int | None:
    """Where the whole name appears, in order and together, inside the query."""
    for i in range(len(query) - len(name) + 1):
        if all(_same(query[i + k], name[k]) for k in range(len(name))):
            return i
    return None


@dataclass(frozen=True)
class _BuildingMatch:
    building: Building
    score: float
    # Query positions explained by the name, and the rest (the unit, in combined text).
    explained: int
    leftover: list[str]
    # All of the name is in the query but split apart: "piso 1 depto a de X II" for "X I".
    broken: bool


def _match_building(
    building: Building, name: list[str], query: str, q_roman: list[str], q_plain: list[str]
) -> _BuildingMatch:
    score = fuzz.token_set_ratio(query, " ".join(name))
    run = _contiguous_run(q_roman, name)
    if run is not None:
        leftover = q_plain[:run] + q_plain[run + len(name) :]
        return _BuildingMatch(building, score, len(name), leftover, broken=False)
    available = list(name)
    leftover = []
    for token_roman, token_plain in zip(q_roman, q_plain, strict=True):
        hit = next((n for n in available if _same(token_roman, n)), None)
        if hit is None:
            leftover.append(token_plain)
        else:
            available.remove(hit)
    return _BuildingMatch(
        building, score, len(q_roman) - len(leftover), leftover, broken=not available
    )


def _best_buildings(
    session: Session, q_roman: list[str], q_plain: list[str]
) -> list[_BuildingMatch]:
    """The active buildings that best match the query tokens (empty when none does)."""
    query = " ".join(q_roman)
    matches: list[_BuildingMatch] = []
    for building in session.scalars(select(Building).where(Building.active.is_(True))):
        name = _building_tokens(building.name)
        if name:
            match = _match_building(building, name, query, q_roman, q_plain)
            if match.score >= BUILDING_MIN_SCORE:
                matches.append(match)
    if not matches:
        return []
    best = max(m.score for m in matches)
    close = [m for m in matches if m.score >= best - BUILDING_SCORE_MARGIN]
    # Among similar scores, the names that explain more of the text without being split
    # apart: "Rodas 2" prefers "Rodas II" over "Rodas"; "Rodas" alone stays ambiguous.
    best_fit = max((m.explained, not m.broken) for m in close)
    return sorted(
        (m for m in close if (m.explained, not m.broken) == best_fit),
        key=lambda m: (-m.score, m.building.name),
    )


def search_building(session: Session, building_text: str) -> list[Building]:
    """Active buildings matching what a person typed ("rodas 2", "la torre del sol"): one
    when it is clear, several when it is ambiguous, none when nothing is close enough."""
    q_roman, q_plain = _aligned_tokens(building_text)
    if not q_roman:
        return []
    return [m.building for m in _best_buildings(session, q_roman, q_plain)][:MAX_CANDIDATES]


def search_unit(session: Session, building_text: str, unit_text: str = "") -> UnitSearchResult:
    """Find an active unit. If unit_text is empty, building_text may hold both parts."""
    q_roman, q_plain = _aligned_tokens(building_text)
    if not q_roman:
        return UnitSearchResult(SearchStatus.NOT_FOUND)
    unit_query = tokens(unit_text)
    combined = not unit_query

    top = _best_buildings(session, q_roman, q_plain)
    if not top:
        return UnitSearchResult(SearchStatus.NOT_FOUND)

    exact: list[UnitCandidate] = []
    fuzzy: list[tuple[float, UnitCandidate]] = []
    for match in top:
        wanted = tuple(match.leftover if combined else unit_query)
        if not wanted:
            continue
        units = session.scalars(
            select(Unit).where(Unit.building_id == match.building.id, Unit.active.is_(True))
        )
        for unit in units:
            candidate = UnitCandidate(unit.id, match.building.name, unit.label)
            key = _unit_key(unit.label)
            if _same_unit(wanted, key):
                exact.append(candidate)
                continue
            # Incomplete ("PB" for "PB A") is offered as a candidate, never chosen alone.
            prefix = len(wanted) < len(key) and _starts_like(wanted, key)
            score = 100 if prefix else fuzz.ratio(" ".join(wanted), " ".join(key))
            if score >= UNIT_FUZZY_MIN_SCORE:
                fuzzy.append((score, candidate))

    buildings = tuple(m.building.name for m in top)
    if len(exact) == 1 and len(top) == 1:
        return UnitSearchResult(SearchStatus.FOUND, (exact[0],), buildings)
    if exact:
        return UnitSearchResult(SearchStatus.AMBIGUOUS, tuple(exact[:MAX_CANDIDATES]), buildings)
    if fuzzy:
        fuzzy.sort(key=lambda f: -f[0])
        options = tuple(c for _, c in fuzzy[:MAX_CANDIDATES])
        return UnitSearchResult(SearchStatus.AMBIGUOUS, options, buildings)
    return UnitSearchResult(SearchStatus.NOT_FOUND, (), buildings)
