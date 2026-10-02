"""Mark (or unmark) buildings as part of the bot pilot (buildings.pilot).

Known people whose buildings are all outside the pilot are handed straight to a human by the
Chatwoot bot. Only touches our database: never ConsorPlus.

Usage (from the repo root, with the db running):
    uv run python scripts/set_pilot.py --buildings 31,40          # mark
    uv run python scripts/set_pilot.py --buildings 31 --off       # unmark
    uv run python scripts/set_pilot.py --buildings 101 --eval     # on the evals database

--buildings are the visible building codes (ConsorPlus code, the number before the name,
"031 RODAS II" -> 31). If any code does not exist nothing is changed. Prints how the given
buildings ended up and the full list of pilot buildings.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import Building  # noqa: E402


class PilotError(Exception):
    pass


def parse_codes(text: str) -> list[int]:
    """ "1, 2,31" -> [1, 2, 31]."""
    try:
        codes = [int(item) for item in text.split(",") if item.strip()]
    except ValueError as exc:
        raise PilotError(f"códigos inválidos: {text!r} (tienen que ser números: 1,2,31)") from exc
    if not codes:
        raise PilotError("no se indicó ningún edificio")
    return list(dict.fromkeys(codes))


def set_pilot(session: Session, codes: list[int], pilot: bool) -> list[Building]:
    """Set pilot on the buildings with these codes (all or nothing). Commits."""
    found = session.scalars(select(Building).where(Building.consorplus_code.in_(codes))).all()
    missing = sorted(set(codes) - {b.consorplus_code for b in found})
    if missing:
        raise PilotError(
            "no existen edificios con código " + ", ".join(map(str, missing)) + ": no cambié nada"
        )
    for building in found:
        building.pilot = pilot
    session.commit()
    return sorted(found, key=lambda b: b.consorplus_code)


def pilot_buildings(session: Session) -> list[Building]:
    return list(
        session.scalars(
            select(Building).where(Building.pilot.is_(True)).order_by(Building.consorplus_code)
        )
    )


def _line(building: Building) -> str:
    state = "PILOTO" if building.pilot else "fuera del piloto"
    inactive = " (inactivo)" if not building.active else ""
    return f"  {building.consorplus_code:>4}  {building.name}{inactive}: {state}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--buildings", required=True, help="códigos separados por coma: 1,2")
    parser.add_argument("--off", action="store_true", help="desmarcar (sacar del piloto)")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--eval", action="store_true", help="base de evals (<POSTGRES_DB>_eval)")
    target.add_argument("--database-url", help="por defecto: DATABASE_URL")
    args = parser.parse_args()

    url = args.database_url or get_settings().database_url
    if args.eval:
        base = make_url(url)
        url = base.set(database=f"{base.database}_eval").render_as_string(hide_password=False)
    engine = create_engine(url)
    try:
        codes = parse_codes(args.buildings)
        with Session(engine) as session:
            changed = set_pilot(session, codes, pilot=not args.off)
            print(f"Base: {make_url(url).database}")
            print("Edificios indicados:")
            for building in changed:
                print(_line(building))
            pilots = pilot_buildings(session)
            print(f"Edificios en el piloto ({len(pilots)}):")
            for building in pilots:
                print(_line(building))
            if not pilots:
                print("  (ninguno: todos los propietarios identificados van a una persona)")
    except PilotError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
