"""Load building information (BuildingInfo) from a JSON file into our database.

The file looks like:
    {"building": "Rodas II", "source": "...",
     "entries": [{"title": "...", "category": "reglamento", "content": "..."}]}

The building is found with the same tolerant search as get_building_info (search_building):
if it does not match exactly one active building nothing is loaded and the candidates are
shown. Idempotent: an entry whose title already exists in that building updates it instead of
adding a duplicate (and is left alone when nothing changed). Every creation or update is logged
in bot_events as admin_action by the user "script", like the admin panel does. Only touches
our database: never ConsorPlus.

Usage (from the repo root, with the db running):
    uv run python scripts/load_building_info.py private/building_info/rodas-ii.json --dry-run
    uv run python scripts/load_building_info.py private/building_info/rodas-ii.json
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.admin.audit import log_admin_action  # noqa: E402
from app.bot.unit_search import search_building  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import Building, BuildingInfo, BuildingInfoCategory  # noqa: E402

ADMIN_USER = "script"


class LoadError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    title: str
    category: BuildingInfoCategory
    content: str


@dataclass(frozen=True)
class InfoFile:
    building: str
    source: str
    entries: list[Entry]


@dataclass
class LoadResult:
    building: Building
    created: list[str] = field(default_factory=list)
    updated: list[tuple[str, list[str]]] = field(default_factory=list)  # (title, fields)
    unchanged: list[str] = field(default_factory=list)


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LoadError(f"{where}: tiene que ser un texto no vacío")
    return value.strip()


def parse_info(data: Any) -> InfoFile:
    """Validate the JSON structure and the categories. Raises LoadError."""
    if not isinstance(data, dict):
        raise LoadError("el JSON tiene que ser un objeto con building, source y entries")
    building = _text(data.get("building"), "building")
    source = data.get("source") or ""
    if not isinstance(source, str):
        raise LoadError("source: tiene que ser un texto")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise LoadError("entries: tiene que ser una lista no vacía")

    valid = ", ".join(c.value for c in BuildingInfoCategory)
    entries: list[Entry] = []
    seen: set[str] = set()
    for number, raw in enumerate(raw_entries, start=1):
        where = f"entries[{number}]"
        if not isinstance(raw, dict):
            raise LoadError(f"{where}: tiene que ser un objeto con title, category y content")
        title = _text(raw.get("title"), f"{where}.title")
        if len(title) > 200:
            raise LoadError(f"{where}.title: más de 200 caracteres")
        if title.casefold() in seen:
            raise LoadError(f"{where}.title: {title!r} está repetido en el archivo")
        seen.add(title.casefold())
        category_text = _text(raw.get("category"), f"{where}.category").lower()
        try:
            category = BuildingInfoCategory(category_text)
        except ValueError as exc:
            raise LoadError(
                f"{where}.category: {category_text!r} no es válida (válidas: {valid})"
            ) from exc
        content = _text(raw.get("content"), f"{where}.content")
        entries.append(Entry(title, category, content))
    return InfoFile(building, source.strip(), entries)


def read_info_file(path: Path) -> InfoFile:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LoadError(f"no pude leer {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LoadError(f"{path} no es un JSON válido: {exc}") from exc
    return parse_info(data)


def resolve_building(session: Session, name: str) -> Building:
    found = search_building(session, name)
    if len(found) == 1:
        return found[0]
    if not found:
        raise LoadError(f"ningún edificio activo coincide con {name!r}: no cargué nada")
    names = "\n".join(f"  {b.consorplus_code:>4}  {b.name}" for b in found)
    raise LoadError(f"{name!r} coincide con varios edificios, no cargué nada. Candidatos:\n{names}")


def load_info(session: Session, info: InfoFile, *, dry_run: bool = False) -> LoadResult:
    """Create or update the entries in the building (all or nothing). Commits unless dry_run,
    which writes nothing."""
    building = resolve_building(session, info.building)
    existing: dict[str, list[BuildingInfo]] = {}
    for row in session.scalars(select(BuildingInfo).where(BuildingInfo.building_id == building.id)):
        existing.setdefault(row.title.strip().casefold(), []).append(row)

    result = LoadResult(building)
    for entry in info.entries:
        rows = existing.get(entry.title.casefold(), [])
        if len(rows) > 1:
            ids = ", ".join(str(r.id) for r in rows)
            raise LoadError(
                f"{entry.title!r} ya está {len(rows)} veces en el edificio (ids {ids}): "
                "dejá una sola desde el panel. No cargué nada"
            )
        if not rows:
            result.created.append(entry.title)
            if not dry_run:
                row = BuildingInfo(
                    building_id=building.id,
                    title=entry.title,
                    category=entry.category,
                    content=entry.content,
                )
                session.add(row)
                session.flush()
                log_admin_action(
                    session, ADMIN_USER, "building_info_created", building_info_id=row.id
                )
            continue
        row = rows[0]
        new = {"title": entry.title, "category": entry.category, "content": entry.content}
        fields = sorted(name for name, value in new.items() if getattr(row, name) != value)
        if not fields:
            result.unchanged.append(entry.title)
            continue
        result.updated.append((entry.title, fields))
        if not dry_run:
            for name in fields:
                setattr(row, name, new[name])
            log_admin_action(
                session,
                ADMIN_USER,
                "building_info_updated",
                building_info_id=row.id,
                fields=fields,
            )
    if not dry_run:
        session.commit()
    return result


def report(result: LoadResult, dry_run: bool) -> str:
    b = result.building
    verb = ("crearía", "actualizaría") if dry_run else ("creadas", "actualizadas")
    lines = [
        f"Edificio: {b.name} (código {b.consorplus_code}, id {b.id})",
        f"Entradas {verb[0]}: {len(result.created)}",
        *(f"  + {t}" for t in result.created),
        f"Entradas {verb[1]}: {len(result.updated)}",
        *(f"  ~ {t} (cambia: {', '.join(fs)})" for t, fs in result.updated),
        f"Entradas sin cambios: {len(result.unchanged)}",
        *(f"  = {t}" for t in result.unchanged),
    ]
    if dry_run:
        lines.append("Modo --dry-run: no escribí nada.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path, help="JSON con building, source y entries")
    parser.add_argument("--dry-run", action="store_true", help="mostrar sin escribir nada")
    parser.add_argument("--database-url", help="por defecto: DATABASE_URL")
    args = parser.parse_args()

    url = args.database_url or get_settings().database_url
    engine = create_engine(url)
    try:
        info = read_info_file(args.file)
        with Session(engine) as session:
            result = load_info(session, info, dry_run=args.dry_run)
            print(f"Base: {make_url(url).database}")
            print(report(result, args.dry_run))
    except LoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
