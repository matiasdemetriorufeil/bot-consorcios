"""Load the initial kinds of problem (ClaimCategory) and, optionally, enable them in a building.

Idempotent: a kind of problem is found by its name; the missing ones are created (in this
order: sort_order 10, 20, ...) and the existing ones are left as they are (what an admin
changed in the panel stays). With --building <ConsorPlus code>, the building gets every
active kind it does not have yet: enabled, without provider ("Lo atiende el estudio") and in
the kind's order. Providers are never created: they are loaded in the panel. Every creation
is logged in bot_events as admin_action by the user "script", like the panel does. Only
touches our database: never ConsorPlus.

Usage (from the repo root, with the db running):
    uv run python scripts/seed_claim_categories.py --building 12 --dry-run
    uv run python scripts/seed_claim_categories.py --building 12
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.admin.audit import log_admin_action  # noqa: E402
from app.admin.formatting import building as building_name  # noqa: E402
from app.admin.labels import CLAIM_SCOPE, STUDIO  # noqa: E402
from app.claims.setup import building_table, split_for_whatsapp  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import (  # noqa: E402
    Building,
    BuildingClaimCategory,
    ClaimCategory,
    ClaimScope,
)

ADMIN_USER = "script"
ORDER_STEP = 10
B, U = ClaimScope.BUILDING, ClaimScope.UNIT
GAS_SAFETY_TEXT = (
    "Si sentís olor a gas: no prendas ni apagues luces ni aparatos eléctricos, no uses fuego, "
    "abrí puertas y ventanas, cerrá la llave de paso si podés hacerlo sin riesgo y alejate del "
    "lugar."
)


@dataclass(frozen=True)
class Seed:
    name: str
    list_title: str  # 24 characters at most (a WhatsApp list row)
    scope: ClaimScope
    list_description: str | None = None  # 72 at most
    urgent: bool = False
    follow_up_question: str | None = None
    safety_text: str | None = None


SEEDS = (
    Seed(
        "No funciona el o los ascensores",
        "Ascensor no funciona",
        B,
        "Uno o todos los ascensores",
        urgent=True,
        follow_up_question="¿Hay alguien encerrado?",
    ),
    Seed("No hay agua", "No hay agua", B),
    Seed(
        "No funciona el acceso magnético de la puerta",
        "Falla acceso magnético",
        B,
        "La llave o la tarjeta no abre la puerta",
    ),
    Seed(
        "No cierra la puerta principal o la puerta reja",
        "Puerta no cierra",
        B,
        "Puerta principal o puerta reja",
    ),
    Seed("Tengo o hay humedad o filtración de agua", "Humedad o filtración", U),
    Seed("Siento olor a gas", "Olor a gas", B, urgent=True, safety_text=GAS_SAFETY_TEXT),
    Seed("No funciona el portón de la cochera", "Portón de cochera", B, "No abre o no cierra"),
    Seed("No hay luz en todo el edificio", "Sin luz en el edificio", B),
    Seed("No tengo luz solo en mi departamento", "Sin luz en mi depto", U, "Solo en tu unidad"),
    Seed("Se escuchan ruidos molestos", "Ruidos molestos", U),
    Seed("Limpieza", "Limpieza", B),
    Seed("Otras quejas", "Otras quejas", U),
)


class SeedError(Exception):
    pass


@dataclass
class SeedResult:
    created: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    building: Building | None = None
    assigned: list[str] = field(default_factory=list)
    already_assigned: list[str] = field(default_factory=list)


def seed(
    session: Session, *, building_code: int | None = None, dry_run: bool = False
) -> SeedResult:
    """Create the missing kinds of problem and, with building_code, their rows in that
    building (all or nothing). Commits unless dry_run, which writes nothing."""
    result = SeedResult()
    building = None
    if building_code is not None:
        building = session.scalar(select(Building).where(Building.consorplus_code == building_code))
        if building is None:
            raise SeedError(f"no hay un edificio con código {building_code}: no cargué nada")
        result.building = building

    existing = {c.name.casefold(): c for c in session.scalars(select(ClaimCategory))}
    categories: list[ClaimCategory] = []
    for number, item in enumerate(SEEDS, start=1):
        if len(item.list_title) > 24 or len(item.list_description or "") > 72:
            raise SeedError(f"{item.name!r}: título o descripción demasiado largos")
        category = existing.get(item.name.casefold())
        if category is not None:
            result.unchanged.append(item.name)
            categories.append(category)
            continue
        result.created.append(item.name)
        if dry_run:
            continue
        category = ClaimCategory(
            name=item.name,
            list_title=item.list_title,
            list_description=item.list_description,
            scope=item.scope,
            urgent=item.urgent,
            follow_up_question=item.follow_up_question,
            safety_text=item.safety_text,
            sort_order=number * ORDER_STEP,
        )
        session.add(category)
        session.flush()
        log_admin_action(
            session, ADMIN_USER, "claim_category_created", claim_category_id=category.id
        )
        categories.append(category)

    if building is not None:
        assigned = set(
            session.scalars(
                select(BuildingClaimCategory.category_id).where(
                    BuildingClaimCategory.building_id == building.id
                )
            )
        )
        new_ids = []
        for category in categories:
            if not category.active:
                continue
            if category.id in assigned:
                result.already_assigned.append(category.name)
                continue
            result.assigned.append(category.name)
            if dry_run:
                continue
            session.add(
                BuildingClaimCategory(
                    building_id=building.id,
                    category_id=category.id,
                    enabled=True,
                    provider_id=None,
                    sort_order=category.sort_order,
                )
            )
            new_ids.append(category.id)
        if dry_run and result.created:
            result.assigned += result.created  # the new ones would be enabled too
        if new_ids:
            session.flush()
            log_admin_action(
                session,
                ADMIN_USER,
                "building_claims_seeded",
                building_id=building.id,
                category_ids=new_ids,
            )
    if not dry_run:
        session.commit()
    return result


def building_report(session: Session, building: Building) -> list[str]:
    """The building's table as the panel shows it (with WhatsApp's second list)."""
    rows = building_table(session, building.id)
    _, more = split_for_whatsapp([r for r in rows if r.enabled])
    second = {r.category.id for r in more}
    lines = [f"Tabla de {building_name(building.name)}:"]
    for row in rows:
        if second and row.category.id == more[0].category.id:
            lines.append("  -- Estos aparecen en una segunda lista («Más opciones») --")
        enabled = "sí" if row.enabled else "no"
        who = row.provider.name if row.provider else STUDIO
        scope = CLAIM_SCOPE[row.category.scope]
        lines.append(
            f"  {row.sort_order:>3}  {row.category.list_title:<24}  habilitado: {enabled:<2}  "
            f"{scope:<16}  {who}"
        )
    return lines


def report(session: Session, result: SeedResult, dry_run: bool) -> str:
    verb = "a crear" if dry_run else "creados"
    lines = [
        f"Tipos de problema {verb}: {len(result.created)}",
        *(f"  + {n}" for n in result.created),
        f"Tipos de problema que ya estaban (no se tocan): {len(result.unchanged)}",
        *(f"  = {n}" for n in result.unchanged),
    ]
    if result.building is not None:
        b = result.building
        verb = "a habilitar" if dry_run else "habilitados"
        lines += [
            f"Edificio: {b.name} (código {b.consorplus_code}, id {b.id})",
            f"Problemas {verb} (sin proveedor): {len(result.assigned)}",
            f"Problemas que el edificio ya tenía (no se tocan): {len(result.already_assigned)}",
        ]
        if not dry_run:
            lines += building_report(session, b)
    if dry_run:
        lines.append("Modo --dry-run: no escribí nada.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--building", type=int, help="código ConsorPlus del edificio (ej. 12)")
    parser.add_argument("--dry-run", action="store_true", help="mostrar sin escribir nada")
    parser.add_argument("--database-url", help="por defecto: DATABASE_URL")
    args = parser.parse_args()

    url = args.database_url or get_settings().database_url
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            result = seed(session, building_code=args.building, dry_run=args.dry_run)
            print(f"Base: {make_url(url).database}")
            print(report(session, result, args.dry_run))
    except SeedError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
