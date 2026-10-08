"""The set-up of claims, as rules (the admin panel only collects the admin's choices and shows
the result, like app.amenities.booking for the SUM):

- a provider's WhatsApp, normalized like the roster's phones and unique among active providers;
- the table "Problemas y proveedores" of a building: one row per active kind of problem with
  whether it is enabled, who attends it (no provider: the studio) and its order; saved whole,
  or copied from another building;
- what deserves a warning (problems the studio attends, providers the bot cannot notify);
- how the enabled problems fit in a WhatsApp list (split_for_whatsapp).

Changes are flushed, never committed: the caller (the panel, the seed) audits them in
bot_events (admin_action) with what these functions return, and commits. Only our database:
nothing is sent to anyone.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.bot.identity import to_e164
from app.db.models import (
    Building,
    BuildingClaimCategory,
    ClaimCategory,
    Phone,
    Provider,
    WaContact,
)

# A WhatsApp list has at most 10 rows. The number of problems of a building is not limited:
# in step 8.3, when a building has more than 10 enabled, the bot shows the first 9 and a row
# "Más opciones" that opens a second list with the rest (split_for_whatsapp).
WHATSAPP_LIST_MAX_ROWS = 10


class SetupProblem(ValueError):
    """What is wrong in what the admin chose, in Spanish (shown as is)."""


# --- Providers ------------------------------------------------------------------------------


def normalize_provider_whatsapp(raw: str | None) -> str | None:
    """Any format ("0351 15 555-0101", "+54 9 351 555 0101", "3515550101") as WhatsApp
    E.164; None when blank. Raises SetupProblem when it is not a number WhatsApp accepts or
    its area code would have to be guessed."""
    text = (raw or "").strip()
    if not text:
        return None
    e164 = to_e164(text)
    if e164 is None:
        raise SetupProblem(
            "Ese WhatsApp no es un número válido. Escribilo con el código de área, por "
            "ejemplo 351 555-0101."
        )
    return e164


def provider_with_whatsapp(
    session: Session, e164: str, *, exclude_id: int | None = None
) -> Provider | None:
    """The active provider that already has this WhatsApp (other than exclude_id)."""
    stmt = select(Provider).where(Provider.whatsapp_e164 == e164, Provider.active.is_(True))
    if exclude_id is not None:
        stmt = stmt.where(Provider.id != exclude_id)
    return session.scalars(stmt.limit(1)).first()


def whatsapp_in_use_warning(session: Session, e164: str | None) -> str | None:
    """A warning (it does not block) when the number is of an owner or tenant of the roster
    or wrote to the studio (a contact of Conversaciones): from step 8.5 the messages of a
    provider's number go to the providers' flow."""
    if not e164:
        return None
    whose = []
    if session.scalar(select(Phone.id).where(Phone.e164 == e164).limit(1)):
        whose.append("de un propietario o inquilino")
    if session.scalar(
        select(WaContact.id)
        .where(WaContact.phone_e164 == e164, WaContact.simulated.is_(False))
        .limit(1)
    ):
        whose.append("de alguien que escribió por WhatsApp (está en Conversaciones)")
    if not whose:
        return None
    return (
        f"Ojo: ese WhatsApp también es {' y '.join(whose)}. Cuando el bot de reclamos esté "
        "andando, los mensajes de ese número se van a tomar como de un proveedor."
    )


def assigned_buildings(session: Session, provider_id: int) -> int:
    """In how many buildings the provider attends some enabled problem."""
    rows = session.scalars(
        select(BuildingClaimCategory.building_id)
        .where(
            BuildingClaimCategory.provider_id == provider_id,
            BuildingClaimCategory.enabled.is_(True),
        )
        .distinct()
    )
    return len(list(rows))


# --- The table of a building ----------------------------------------------------------------


@dataclass(frozen=True)
class TableChange:
    """What a save or a copy changed: the kinds of problem and the fields."""

    category_ids: list[int]
    fields: list[str]


@dataclass(frozen=True)
class Choice:
    """What the admin chose for one problem in one building."""

    enabled: bool
    provider_id: int | None
    sort_order: int


@dataclass
class TableRow:
    category: ClaimCategory
    enabled: bool
    provider: Provider | None
    sort_order: int

    @property
    def provider_id(self) -> int | None:
        return self.provider.id if self.provider else None


def _current(category: ClaimCategory, row: BuildingClaimCategory | None) -> Choice:
    """A problem's choice in a building; with no row yet: disabled, the studio, its own
    order."""
    if row is None:
        return Choice(enabled=False, provider_id=None, sort_order=category.sort_order)
    return Choice(row.enabled, row.provider_id, row.sort_order)


def _active_categories(session: Session) -> list[ClaimCategory]:
    return list(
        session.scalars(
            select(ClaimCategory)
            .where(ClaimCategory.active.is_(True))
            .order_by(ClaimCategory.sort_order, ClaimCategory.name)
        )
    )


def _assignments(session: Session, building_id: int) -> dict[int, BuildingClaimCategory]:
    rows = session.scalars(
        select(BuildingClaimCategory)
        .where(BuildingClaimCategory.building_id == building_id)
        .options(selectinload(BuildingClaimCategory.provider))
    )
    return {row.category_id: row for row in rows}


def enabled_categories(session: Session, building_id: int) -> list[ClaimCategory]:
    """The kinds of problem that can be reported in the building, in its table's order."""
    return list(
        session.scalars(
            select(ClaimCategory)
            .join(BuildingClaimCategory, BuildingClaimCategory.category_id == ClaimCategory.id)
            .where(
                BuildingClaimCategory.building_id == building_id,
                BuildingClaimCategory.enabled.is_(True),
                ClaimCategory.active.is_(True),
            )
            .order_by(
                BuildingClaimCategory.sort_order, ClaimCategory.sort_order, ClaimCategory.name
            )
        )
    )


def _sort_key(row: TableRow) -> tuple[int, int, str]:
    return (row.sort_order, row.category.sort_order, row.category.name)


def building_table(session: Session, building_id: int) -> list[TableRow]:
    """One row per active kind of problem, in the building's order. Inactive kinds are not
    shown (their rows stay as they are)."""
    assignments = _assignments(session, building_id)
    rows = []
    for category in _active_categories(session):
        row = assignments.get(category.id)
        if row is None:
            rows.append(TableRow(category, False, None, category.sort_order))
        else:
            rows.append(TableRow(category, row.enabled, row.provider, row.sort_order))
    return sorted(rows, key=_sort_key)


def _apply(
    session: Session,
    building_id: int,
    choices: dict[int, Choice],
    categories: Iterable[ClaimCategory],
) -> TableChange:
    """Write the choices (one row per kind of problem)."""
    assignments = _assignments(session, building_id)
    changed_ids: list[int] = []
    fields: set[str] = set()
    for category in categories:
        choice = choices.get(category.id)
        if choice is None:
            continue
        row = assignments.get(category.id)
        before = _current(category, row)
        differs = {
            name
            for name in ("enabled", "provider_id", "sort_order")
            if getattr(before, name) != getattr(choice, name)
        }
        if row is None:
            row = BuildingClaimCategory(building_id=building_id, category_id=category.id)
            session.add(row)
        row.enabled = choice.enabled
        row.provider_id = choice.provider_id
        row.sort_order = choice.sort_order
        if differs:
            changed_ids.append(category.id)
            fields |= differs
    session.flush()
    return TableChange(changed_ids, sorted(fields))


def save_building_table(
    session: Session, building_id: int, choices: dict[int, Choice]
) -> TableChange:
    """Save the whole table of a building. A provider must exist and be active, unless it
    already attended that problem (kept as is until the admin picks another)."""
    if session.get(Building, building_id) is None:
        raise SetupProblem("Ese edificio no existe.")
    assignments = _assignments(session, building_id)
    providers = {p.id: p for p in session.scalars(select(Provider))}
    categories = _active_categories(session)
    for category in categories:
        choice = choices.get(category.id)
        if choice is None:
            continue
        if choice.sort_order < 0:
            raise SetupProblem(f"{category.list_title}: el orden tiene que ser 0 o más.")
        if choice.provider_id is None:
            continue
        provider = providers.get(choice.provider_id)
        current = assignments.get(category.id)
        kept = current is not None and current.provider_id == choice.provider_id
        if provider is None or not (provider.active or kept):
            raise SetupProblem(f"{category.list_title}: elegí un proveedor activo.")
    return _apply(session, building_id, choices, categories)


def copy_building_table(session: Session, building_id: int, source_id: int) -> TableChange:
    """Leave the building's table like the source's (enabled, provider and order, for every
    active kind of problem; those the source never had end up disabled)."""
    if source_id == building_id:
        raise SetupProblem("Elegí otro edificio para copiar.")
    if session.get(Building, building_id) is None or session.get(Building, source_id) is None:
        raise SetupProblem("Ese edificio no existe.")
    categories = _active_categories(session)
    source = _assignments(session, source_id)
    choices = {c.id: _current(c, source.get(c.id)) for c in categories}
    return _apply(session, building_id, choices, categories)


# --- Warnings and the WhatsApp list ---------------------------------------------------------


def _titles(rows: Iterable[TableRow]) -> str:
    return ", ".join(row.category.list_title for row in rows)


def table_warnings(rows: Sequence[TableRow]) -> list[str]:
    """What the admin should know about the enabled problems of a building."""
    enabled = [row for row in rows if row.enabled]
    warnings = []
    studio = [row for row in enabled if row.provider is None]
    if studio:
        warnings.append(
            f"Estos los va a atender el estudio (no tienen proveedor): {_titles(studio)}."
        )
    by_provider: dict[int, tuple[Provider, list[TableRow]]] = {}
    for row in enabled:
        if row.provider is not None:
            by_provider.setdefault(row.provider.id, (row.provider, []))[1].append(row)
    for provider, provider_rows in by_provider.values():
        if not provider.active:
            warnings.append(
                f"{provider.name} está desactivado: elegí otro proveedor para "
                f"{_titles(provider_rows)}."
            )
        elif not provider.whatsapp_e164:
            warnings.append(
                f"{provider.name} no tiene WhatsApp cargado: el bot no le va a poder avisar "
                f"({_titles(provider_rows)})."
            )
    return warnings


def split_for_whatsapp[T](enabled: Sequence[T]) -> tuple[list[T], list[T]]:
    """The enabled problems (in order) as WhatsApp lists: all in one when they fit (10 rows at
    most); otherwise the first 9 plus a row "Más opciones" with the rest (the second list).
    Step 8.3's bot shows them this way; the panel shows where the split falls."""
    if len(enabled) <= WHATSAPP_LIST_MAX_ROWS:
        return list(enabled), []
    first = WHATSAPP_LIST_MAX_ROWS - 1
    return list(enabled[:first]), list(enabled[first:])
