"""Sync the roster (units, owners, tenants, phones) from ConsorPlus. Read-only on ConsorPlus.

Usage (from the repo root):
    uv run python scripts/sync_roster.py <building_code> [--dry-run]
    uv run python scripts/sync_roster.py --all [--dry-run]

`building_code` is the value of the building combo (e.g. "1"). Prints counters only: never
names, phones, emails or payment codes.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import Settings  # noqa: E402
from app.consorplus import ConsorPlusClient  # noqa: E402
from app.sync.roster import sync_roster  # noqa: E402

LABELS = {
    "buildings_ok": "edificios ok",
    "buildings_failed": "edificios con error",
    "units": "unidades",
    "units_deactivated": "unidades desactivadas (ya no están)",
    "people": "personas",
    "people_created": "personas nuevas",
    "phones_valid": "teléfonos válidos",
    "phones_invalid": "teléfonos inválidos",
    "phones_removed": "teléfonos quitados (ya no están)",
    "needs_review": "needs_review (característica asumida)",
    "units_without_owner_phone": "unidades sin teléfono de propietario",
    "conflicts": "conflictos (teléfono de otra persona)",
    "links_added": "vínculos agregados",
    "links_removed": "vínculos quitados",
    "nameless_contacts": "contactos sin nombre (creados/unidos)",
    "contacts_ignored": "contactos sin nombre ni contacto (ignorados)",
    "people_in_several_buildings": "personas en varios edificios (tel. no depurados)",
    "payment_codes_valid": "unidades con código electrónico válido",
    "payment_codes_invalid": "códigos electrónicos inválidos (guardados NULL)",
    "payment_codes_missing": "unidades sin código electrónico",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("building_code", nargs="?")
    target.add_argument("--all", action="store_true", help="recorrer todos los edificios")
    parser.add_argument("--dry-run", action="store_true", help="no escribir en la base")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings(_env_file=args.env_file)
    client = ConsorPlusClient.from_settings(settings)
    engine = create_engine(settings.database_url)

    started = time.monotonic()
    codes = None if args.all else [args.building_code]
    with Session(engine) as session:
        report = sync_roster(session, client, codes, dry_run=args.dry_run)
    engine.dispose()

    print("DRY RUN (no se escribió nada)" if args.dry_run else "Sincronización guardada")
    for key, value in report.counters().items():
        print(f"  {LABELS.get(key, key):40} {value}")
    print("  Por tipo de unidad:              unidades  c/tel.prop  c/email.prop")
    for unit_type, counts in report.stats()["by_unit_type"].items():
        print(
            f"    {unit_type[:30]:30} {counts['units']:9} {counts['with_owner_phone']:11} "
            f"{counts['with_owner_email']:13}"
        )
    for error in report.errors:
        print(f"  ERROR {error}")
    print(f"Estado: {report.status.value} ({time.monotonic() - started:.0f}s)")
    return 0 if report.buildings_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
