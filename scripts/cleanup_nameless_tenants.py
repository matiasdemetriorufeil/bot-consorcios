"""Delete the nameless tenants without phones that the roster sync created before (one time).

Since the roster sync stopped creating a tenant without name nor a valid phone (it identifies
nobody: tenants_without_contact in app.sync.roster), the ones created before stay. This deletes
the people that are exactly that:
- their ConsorPlus name is the nameless one (and nobody gave them a name in the panel);
- every unit link they have is TENANT with source "consorplus" (at least one);
- no phones.
Their links go with them; claims and verifications keep their own copy (SET NULL).

Only touches our database: never ConsorPlus. Without --apply it only shows how many there are.

Usage (from the repo root, with the db running):
    uv run python scripts/cleanup_nameless_tenants.py            # how many (changes nothing)
    uv run python scripts/cleanup_nameless_tenants.py --apply    # delete them
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, exists, func, not_, or_, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.audit import log_admin_action  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import DataSource, Person, PersonRole, Phone, UnitPerson  # noqa: E402
from app.sync.roster import NAMELESS_PERSON  # noqa: E402

AUDIT_USER = "script:cleanup_nameless_tenants"


def nameless_tenants(session: Session) -> list[int]:
    """Ids of the people to delete (see the module's docstring)."""
    links = select(UnitPerson.person_id).where(UnitPerson.person_id == Person.id)
    other_link = links.where(
        or_(UnitPerson.role != PersonRole.TENANT, UnitPerson.source != DataSource.CONSORPLUS)
    )
    stmt = (
        select(Person.id)
        .where(
            func.coalesce(Person.consorplus_name, Person.full_name) == NAMELESS_PERSON,
            Person.full_name == NAMELESS_PERSON,
            exists(links),
            not_(exists(other_link)),
            not_(exists(select(Phone.id).where(Phone.person_id == Person.id))),
        )
        .order_by(Person.id)
    )
    return list(session.scalars(stmt))


def delete_nameless_tenants(session: Session) -> int:
    """Delete them in one transaction (audited, counters only). How many."""
    ids = nameless_tenants(session)
    for person in session.scalars(select(Person).where(Person.id.in_(ids))):
        session.delete(person)
    if ids:
        log_admin_action(session, AUDIT_USER, "nameless_tenants_deleted", people=len(ids))
    session.commit()
    return len(ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="borrarlas (si no, solo cuenta)")
    parser.add_argument("--database-url", help="por defecto: DATABASE_URL")
    args = parser.parse_args(argv)

    url = args.database_url or get_settings().database_url
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            found = len(nameless_tenants(session))
            print(f"Base: {make_url(url).database}")
            print(f"Inquilinos sin nombre, sin teléfono y solo de ConsorPlus: {found}")
            if not args.apply:
                print("No se borró nada. Para borrarlos: --apply")
                return 0
            deleted = delete_nameless_tenants(session)
            print(f"Borrados: {deleted}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
