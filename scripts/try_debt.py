"""Manual check: show the debt of ONE ConsorPlus unit (read-only). Prints amounts only.

Usage (from the repo root):
    uv run python scripts/try_debt.py <building_code> <unit_value> [--env-file PATH]
    uv run python scripts/try_debt.py <building_code> --list-units

`unit_value` is the value of the unit combo (not the label); --list-units prints the values
without owner names.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.consorplus import ConsorPlusClient  # noqa: E402


def _money(value) -> str:
    return f"$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("building_code")
    parser.add_argument("unit_value", nargs="?")
    parser.add_argument("--list-units", action="store_true", help="listar unidades (sin nombres)")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    if not args.list_units and not args.unit_value:
        parser.error("falta unit_value (o usá --list-units)")

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings(_env_file=args.env_file)
    client = ConsorPlusClient.from_settings(settings)

    started = time.monotonic()
    if args.list_units:
        for unit in client.list_units(args.building_code):
            print(f"{unit.value:>8}  {unit.label}")
        return 0

    debt = client.get_debt(args.building_code, args.unit_value)
    elapsed = time.monotonic() - started

    print(f"Edificio {debt.building_code} / unidad {debt.unit_value}")
    for line in debt.lines:
        print(
            f"  {line.period:8} {line.concept:28} "
            f"importe {_money(line.concept_amount or 0):>15}  "
            f"saldo {_money(line.balance):>15}  "
            f"acumulado {_money(line.accumulated or 0):>15}"
        )
    print(f"Total adeudado: {_money(debt.total)}")
    print("Estado:", "al día" if debt.is_up_to_date else "con deuda")
    print(f"({len(debt.lines)} líneas, {elapsed:.1f}s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
