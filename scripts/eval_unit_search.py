"""Measure unit_search against the units in the database (read only).

For every active unit it builds 3 texts the way an owner could type them and prints ONLY
percentages: never names, labels or ids (the database may hold real data).

    docker compose run --rm api python -m scripts.eval_unit_search
"""

import re
import unicodedata
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.unit_search import SearchStatus, search_unit
from app.db.models import Building, Unit
from app.db.session import SessionLocal

_GENERIC = {"edificio", "consorcio", "de", "del", "la", "las", "los", "el", "y"}
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}
_FLOOR_UNIT = re.compile(r"^0*(\d{1,2})\s*[-\s°º.]?\s*([A-Za-z])$")
_PB_UNIT = re.compile(r"^PB\s*[-\s.]?\s*([A-Za-z0-9]+)$", re.IGNORECASE)
_PARKING = re.compile(r"^(?:COC|CO|CH)\s*[-\s.]?\s*0*(\d+)$", re.IGNORECASE)


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _words(building_name: str) -> list[str]:
    name = re.sub(r"^\s*\d{3}\s+", "", building_name)
    name = re.sub(r"\(.*?\)", " ", name)
    return re.findall(r"[^\W_]+", _plain(name))


def building_full(name: str) -> str:
    """Whole name, roman numerals as numbers: "Rodas 2"."""
    return " ".join(_ROMAN.get(w, w) for w in _words(name)).title()


def building_short(name: str) -> str:
    """Abbreviated: the longest meaningful word plus any number ("rodas ii")."""
    words = _words(name)
    meaningful = [w for w in words if w not in _GENERIC and not w.isdigit() and w not in _ROMAN]
    numbers = [w for w in words if w.isdigit() or w in _ROMAN]
    if not meaningful:
        return " ".join(words)
    return " ".join([max(meaningful, key=len), *numbers])


def unit_ordinal(label: str) -> str:
    if m := _FLOOR_UNIT.match(label.strip()):
        return f"{int(m.group(1))}º {m.group(2).upper()}"
    if m := _PB_UNIT.match(label.strip()):
        return f"PB {m.group(1)}"
    return re.sub(r"[-.]+", " ", label).strip()


def unit_compact(label: str) -> str:
    """Without º nor the separator between floor and letter: "04-C" -> "4C", "B2-10" stays."""
    compact = re.sub(r"[°º]", "", label)
    compact = re.sub(r"(?<=\d)[\s\-.]+(?=[A-Za-z])|(?<=[A-Za-z])[\s\-.]+(?=\d)", "", compact)
    return re.sub(r"^0+(?=\d)", "", compact.strip())


def unit_spoken(label: str) -> str:
    if m := _FLOOR_UNIT.match(label.strip()):
        return f"piso {int(m.group(1))} depto {m.group(2).lower()}"
    if m := _PB_UNIT.match(label.strip()):
        return f"planta baja {m.group(1).lower()}"
    if m := _PARKING.match(label.strip()):
        return f"cochera {int(m.group(1))}"
    return label


def main() -> None:
    results: dict[str, Counter[str]] = {v: Counter() for v in ("A", "B", "C")}
    with SessionLocal() as session:
        units = session.execute(
            select(Unit.id, Unit.label, Building.name)
            .join(Building, Building.id == Unit.building_id)
            .where(Unit.active.is_(True), Building.active.is_(True))
        ).all()
        for unit_id, label, name in units:
            queries = {
                "A": (building_full(name), unit_ordinal(label)),
                "B": (building_short(name), unit_compact(label)),
                "C": (f"{unit_spoken(label)} de {_plain(re.sub(r'^\d{3} ', '', name))}", ""),
            }
            for variant, (building_text, unit_text) in queries.items():
                results[variant][_classify(session, unit_id, building_text, unit_text)] += 1

    print(f"Unidades activas evaluadas: {len(units)} (3 variantes c/u)")
    print("A = nombre completo + '4º C' | B = abreviado + '4C' | C = 'piso 4 depto c de ...'")
    total: Counter[str] = sum(results.values(), Counter())
    for variant, counts in [*results.items(), ("TOTAL", total)]:
        n = sum(counts.values()) or 1
        pct = {k: 100 * counts[k] / n for k in _OUTCOMES}
        print(
            f"{variant:>5}: acierto único {pct['found']:5.1f}% | "
            f"candidatas {pct['ambiguous_ok'] + pct['ambiguous_miss']:5.1f}% "
            f"(con la correcta {pct['ambiguous_ok']:5.1f}%) | "
            f"no encontrada {pct['not_found']:5.1f}% | "
            f"ÚNICO INCORRECTO {pct['found_wrong']:4.1f}%"
        )


_OUTCOMES = ("found", "found_wrong", "ambiguous_ok", "ambiguous_miss", "not_found")


def _classify(session: Session, unit_id: int, building_text: str, unit_text: str) -> str:
    result = search_unit(session, building_text, unit_text)
    ids = {c.unit_id for c in result.candidates}
    if result.status == SearchStatus.FOUND:
        return "found" if unit_id in ids else "found_wrong"
    if result.status == SearchStatus.AMBIGUOUS:
        return "ambiguous_ok" if unit_id in ids else "ambiguous_miss"
    return "not_found"


if __name__ == "__main__":
    main()
