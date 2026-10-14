"""Panel page "Vecinos": the roster of each building (who lives in each unit, their role, phones
and where the data came from), for operators and admins. Only to see and search here; adding,
editing and removing people is app.admin.neighbors_service (its page comes next).

- GET  /admin/neighbors                               one building (?building= its name, as the
                                                      list offers it, or ?building_id=) or a
                                                      search in all of them (?q=: name, unit or
                                                      phone in any format Teléfonos takes);
                                                      ?only=no_phone|manual, ?page=
- POST /admin/neighbors/refresh                       admins: read the building again from
                                                      ConsorPlus now (read only, like every
                                                      night; app.admin.neighbors_service)
- POST /admin/neighbors/suppressions/{id}/restore     admins: «Restaurar» one of «Quitados a
                                                      mano»

Units are in the order of ConsorPlus' listing (consorplus_unit_value, compared as numbers).
Admin-only actions are checked here (require_admin), not only hidden. The answers and the audit
carry counters and ids, never names.
"""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, ClassVar
from urllib.parse import parse_qsl, urlencode

from sqladmin import BaseView, expose
from sqladmin.flash import Flash
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.admin import formatting, labels, neighbors_service
from app.admin.auth import admin_user, display_names, is_admin, require_admin
from app.admin.fetch import fetch_reply, wants_fetch
from app.admin.phones import phone_status
from app.admin.views import _in_thread
from app.bot.identity import to_e164
from app.config import Settings
from app.db.models import (
    Building,
    DataSource,
    Person,
    PersonRole,
    Phone,
    RosterSuppression,
    SuppressionKind,
    Unit,
    UnitPerson,
)
from app.person_roles import role_title

PAGE_SIZE = 50  # units (a unit's people are never split between pages)
FILTERS = {"no_phone": "Sin teléfono", "manual": "Cargados a mano"}
# The query string the actions go back to: only these keys (never a URL).
LIST_PARAMS = ("building_id", "q", "only", "page")
ROLE_ORDER = {PersonRole.OWNER: 0, PersonRole.TENANT: 1, PersonRole.RESIDENT: 2}
# A search with fewer digits is not taken as part of a phone number (units: "10° C").
MIN_PHONE_DIGITS = 6
# Phones that do not identify as they are: a label and a link to Teléfonos.
FLAGGED = ("conflict", "review")


def natural_key(value: str) -> list[tuple[int, int, str]]:
    """ "2" before "10", as ConsorPlus lists its units."""
    return [
        (0, int(part), "") if part.isdigit() else (1, 0, part.lower())
        for part in re.split(r"(\d+)", value)
        if part
    ]


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text)
    return " ".join("".join(c for c in plain if not unicodedata.combining(c)).casefold().split())


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


# --- Buildings ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildingChoice:
    id: int
    label: str  # as the list offers it; with its code only when two share a name


def building_choices(session: Session) -> list[BuildingChoice]:
    rows = session.execute(
        select(Building.id, Building.name, Building.consorplus_code).where(
            Building.active.is_(True)
        )
    ).all()
    names = {bid: formatting.building(name) for bid, name, _ in rows}
    repeated = defaultdict(int)
    for name in names.values():
        repeated[_fold(name)] += 1
    choices = [
        BuildingChoice(
            bid, f"{names[bid]} (código {code})" if repeated[_fold(names[bid])] > 1 else names[bid]
        )
        for bid, _, code in rows
    ]
    return sorted(choices, key=lambda c: natural_key(_fold(c.label)))


@dataclass(frozen=True)
class Resolved:
    building_id: int | None
    warning: str | None = None


def resolve_building(choices: list[BuildingChoice], text: str) -> Resolved:
    """The building she chose in the list (its label, without caring for capitals or accents).
    A name two buildings share is never guessed: she is asked to choose again."""
    wanted = _fold(text)
    if not wanted:
        return Resolved(None)
    exact = [c for c in choices if _fold(c.label) == wanted]
    if len(exact) == 1:
        return Resolved(exact[0].id)
    shared = [c for c in choices if _fold(c.label).startswith(wanted + " (codigo ")]
    if len(shared) > 1:
        return Resolved(
            None,
            "Hay más de un edificio con ese nombre. Elegilo de la lista: cada uno tiene su código.",
        )
    return Resolved(None, "No encontramos ese edificio. Elegilo de la lista.")


# --- The list ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PhoneLine:
    number: str  # as the panel shows it: "351 555-0101"
    status: str  # "" or one of FLAGGED
    status_label: str
    search: str  # for Teléfonos' search: its digits


@dataclass(frozen=True)
class PersonLine:
    person_id: int
    name: str
    role: str
    source: str
    phones: list[PhoneLine]


@dataclass(frozen=True)
class UnitGroup:
    unit_id: int
    label: str
    building: str
    people: list[PersonLine]


@dataclass(frozen=True)
class NeighborList:
    building_id: int | None
    building_label: str
    query: str
    only: str
    page: int
    pages: int
    total: int  # units shown (after the filter), all pages
    groups: list[UnitGroup]
    active_units: int = 0
    with_owner_phone: int = 0
    warning: str | None = None
    choices: list[BuildingChoice] = field(default_factory=list)

    @property
    def mode(self) -> str:
        if self.query:
            return "search"
        return "building" if self.building_id is not None else "choose"


def _phone_line(phone: Phone) -> PhoneLine:
    status = phone_status(phone)
    flagged = status if status in FLAGGED else ""
    return PhoneLine(
        number=formatting.phone(phone.e164),
        status=flagged,
        status_label=labels.PHONE_STATUS[flagged] if flagged else "",
        search=re.sub(r"\D", "", formatting.phone(phone.e164)),
    )


def _person_line(link: UnitPerson) -> PersonLine:
    phones = sorted(link.person.phones, key=lambda p: p.id)
    return PersonLine(
        person_id=link.person_id,
        name=link.person.full_name,
        role=role_title(link.role),
        source=labels.label(labels.PHONE_SOURCE, link.source),
        phones=[_phone_line(p) for p in phones],
    )


def _owner_has_phone(links: list[UnitPerson]) -> bool:
    return any(
        link.role == PersonRole.OWNER and any(not p.conflict for p in link.person.phones)
        for link in links
    )


def _by_hand(link: UnitPerson) -> bool:
    return link.source == DataSource.MANUAL or any(
        p.source == DataSource.MANUAL for p in link.person.phones
    )


def _links(session: Session, condition: Any) -> list[UnitPerson]:
    return list(
        session.scalars(
            select(UnitPerson)
            .join(Unit, Unit.id == UnitPerson.unit_id)
            .where(Unit.active.is_(True), condition)
            .options(selectinload(UnitPerson.person).selectinload(Person.phones))
        )
    )


def _group(unit: Unit, links: list[UnitPerson]) -> UnitGroup:
    ordered = sorted(links, key=lambda k: (ROLE_ORDER.get(k.role, 9), _fold(k.person.full_name)))
    return UnitGroup(
        unit_id=unit.id,
        label=unit.label,
        building=formatting.building(unit.building.name),
        people=[_person_line(link) for link in ordered],
    )


def _search_condition(term: str) -> Any:
    """People by name or phone: like Teléfonos (digits inside the number, from 6 of them so a
    unit's "10" is not a phone) plus the whole number in any format ("0351 15 555-0101")."""
    digits = re.sub(r"\D", "", term).lstrip("0")
    conditions = [Person.full_name.ilike(f"%{term}%")]
    phones = []
    if len(digits) >= MIN_PHONE_DIGITS:
        phones.append(Phone.e164.contains(digits, autoescape=True))
    if e164 := to_e164(term):
        phones.append(Phone.e164 == e164)
    if phones:
        conditions.append(Person.id.in_(select(Phone.person_id).where(or_(*phones))))
    return or_(*conditions)


def _page(groups: list[UnitGroup], page: int) -> tuple[list[UnitGroup], int, int]:
    pages = max(1, -(-len(groups) // PAGE_SIZE))
    page = min(max(page, 1), pages)
    return groups[(page - 1) * PAGE_SIZE : page * PAGE_SIZE], page, pages


def list_neighbors(
    session: Session,
    *,
    building_id: int | None = None,
    building_text: str = "",
    query: str = "",
    only: str = "",
    page: int = 1,
) -> NeighborList:
    choices = building_choices(session)
    warning = None
    if building_id is None and building_text.strip():
        resolved = resolve_building(choices, building_text)
        building_id, warning = resolved.building_id, resolved.warning
    known = {c.id: c.label for c in choices}
    if building_id not in known:
        building_id = None
    label = known.get(building_id, building_text.strip()) if building_id else building_text.strip()
    only = only if only in FILTERS else ""
    query = query.strip()
    base = {
        "building_id": building_id,
        "building_label": label,
        "query": query,
        "warning": warning,
        "choices": choices,
    }

    if query:
        matched_units = select(Unit.id).where(Unit.label.ilike(f"%{query}%"))
        links = _links(
            session,
            or_(UnitPerson.unit_id.in_(matched_units), UnitPerson.person_id.in_(
                select(Person.id).where(_search_condition(query))
            )),
        )  # fmt: skip
        by_unit: dict[int, list[UnitPerson]] = defaultdict(list)
        for link in links:
            by_unit[link.unit_id].append(link)
        units = session.scalars(
            select(Unit)
            .where(
                Unit.active.is_(True),
                or_(Unit.id.in_(by_unit), Unit.id.in_(matched_units)),
            )
            .options(selectinload(Unit.building))
        ).all()
        units = sorted(
            units,
            key=lambda u: (
                _fold(formatting.building(u.building.name)),
                natural_key(u.consorplus_unit_value),
            ),
        )
        groups = [_group(u, by_unit.get(u.id, [])) for u in units]
        shown, page, pages = _page(groups, page)
        total = len(groups)
        return NeighborList(**base, only="", page=page, pages=pages, total=total, groups=shown)

    if building_id is None:
        return NeighborList(**base, only=only, page=1, pages=1, total=0, groups=[])

    units = session.scalars(
        select(Unit)
        .where(Unit.building_id == building_id, Unit.active.is_(True))
        .options(selectinload(Unit.building))
    ).all()
    units = sorted(units, key=lambda u: natural_key(u.consorplus_unit_value))
    by_unit = defaultdict(list)
    for link in _links(session, Unit.building_id == building_id):
        by_unit[link.unit_id].append(link)
    with_phone = {u.id for u in units if _owner_has_phone(by_unit.get(u.id, []))}
    active_units = len(units)
    if only == "no_phone":
        units = [u for u in units if u.id not in with_phone]
    groups = []
    for unit in units:
        links = by_unit.get(unit.id, [])
        if only == "manual":
            links = [link for link in links if _by_hand(link)]
            if not links:
                continue
        groups.append(_group(unit, links))
    shown, page, pages = _page(groups, page)
    return NeighborList(
        **base,
        only=only,
        page=page,
        pages=pages,
        total=len(groups),
        groups=shown,
        active_units=active_units,
        with_owner_phone=len(with_phone),
    )


# --- «Quitados a mano» -------------------------------------------------------------------------


@dataclass(frozen=True)
class SuppressionRow:
    id: int
    what: str
    who: str
    when: str


def list_suppressions(session: Session, building_id: int, timezone: str) -> list[SuppressionRow]:
    rows = session.execute(
        select(RosterSuppression, Unit.label)
        .outerjoin(Unit, Unit.id == RosterSuppression.unit_id)
        .where(RosterSuppression.building_id == building_id)
        .order_by(RosterSuppression.created_at.desc(), RosterSuppression.id.desc())
    ).all()
    names = display_names(session)
    result = []
    for suppression, unit_label in rows:
        person = suppression.person_name or "—"
        if suppression.kind == SuppressionKind.LINK:
            what = f"{unit_label} · {role_title(suppression.role)} · {person}"
        else:
            what = f"Teléfono {formatting.phone(suppression.e164)} · {person}"
        result.append(
            SuppressionRow(
                id=suppression.id,
                what=what,
                who=names.get(suppression.created_by, suppression.created_by),
                when=formatting.when(suppression.created_at, timezone=timezone),
            )
        )
    return result


def refresh_message(counters: dict[str, int]) -> str:
    """What «Actualizar desde ConsorPlus» says: counters only."""
    return (
        f"Listo: se leyeron {counters.get('units', 0)} unidades. Vínculos nuevos: "
        f"{counters.get('links_added', 0)}; quitados: {counters.get('links_removed', 0)}. "
        f"Personas nuevas: {counters.get('people_created', 0)}. Teléfonos quitados: "
        f"{counters.get('phones_removed', 0)}."
    )


REFRESH_FAILED = "No se pudo leer el edificio en ConsorPlus. Probá más tarde."


def back_query(raw: str) -> str:
    """The list's query string, keeping only its own keys (a sent URL is never followed)."""
    pairs = [(k, v) for k, v in parse_qsl(raw.lstrip("?")) if k in LIST_PARAMS]
    return urlencode(pairs)


# --- Page --------------------------------------------------------------------------------------


class NeighborsView(BaseView):
    name = "Vecinos"
    icon = "fa-solid fa-people-roof"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default
    # «Actualizar desde ConsorPlus»: None in production (ConsorPlusClient and the app's
    # engine); tests and the demo panel set them.
    roster_source: ClassVar[Any] = None
    lock_engine: ClassVar[Any] = None

    def _url(self, request: Request, query: str = "") -> str:
        url = str(request.url_for("admin:view-neighbors"))
        return f"{url}?{query}" if query else url

    def _context(self, request: Request, result: NeighborList) -> dict[str, Any]:
        base = {
            "building_id": result.building_id,
            "q": result.query,
            "only": result.only,
            "page": result.page,
        }

        def link(**changes: Any) -> str:
            values = {**base, **changes}
            return "?" + urlencode({k: v for k, v in values.items() if v not in ("", None)})

        return {
            "result": result,
            "filters": FILTERS,
            "link": link,
            "back": link()[1:],
            "is_admin": is_admin(request),
        }

    def _load(self, session: Session, params: Any) -> tuple[NeighborList, list[SuppressionRow]]:
        result = list_neighbors(
            session,
            building_id=_int(params.get("building_id"), 0) or None,
            building_text=str(params.get("building", "")),
            query=str(params.get("q", "")),
            only=str(params.get("only", "")),
            page=_int(params.get("page"), 1),
        )
        removed = (
            list_suppressions(session, result.building_id, self.timezone)
            if result.building_id is not None and result.mode == "building"
            else []
        )
        return result, removed

    @expose("/neighbors", methods=["GET"], identity="neighbors")
    async def neighbors_page(self, request: Request) -> Response:
        with self.session_maker() as session:
            result, removed = await _in_thread(self._load, session, request.query_params)
        context = self._context(request, result)
        context["removed"] = removed if context["is_admin"] else []
        return await self.templates.TemplateResponse(
            request, "neighbors.html", {"title": "Vecinos", **context}
        )

    async def _results_html(self, request: Request, params: dict[str, str]) -> tuple[str, int]:
        with self.session_maker() as session:
            result, removed = await _in_thread(self._load, session, params)
        template = self.templates.env.get_template("_neighbors_results.html")
        html = await template.render_async({"request": request, **self._context(request, result)})
        return html, len(removed)

    @expose("/neighbors/refresh", methods=["POST"], identity="neighbors-refresh")
    async def refresh(self, request: Request) -> Response:
        require_admin(request)
        form = await request.form()
        back = back_query(str(form.get("back", "")))
        building_id = _int(form.get("building_id"))
        ok, message = False, "Elegí un edificio."
        with self.session_maker() as session:
            building = session.get(Building, building_id) if building_id else None
            if building is not None:
                code = str(building.consorplus_code)
                try:
                    counters = await _in_thread(
                        lambda: neighbors_service.refresh_building(
                            session,
                            admin_user(request),
                            code,
                            source=self.roster_source,
                            engine=self.lock_engine,
                        )
                    )
                except neighbors_service.NeighborError as exc:
                    message = str(exc)
                else:
                    ok = not counters.get("buildings_failed")
                    message = refresh_message(counters) if ok else REFRESH_FAILED
        if not wants_fetch(request):
            (Flash.success if ok else Flash.warning)(request, message)
            return RedirectResponse(self._url(request, back), status_code=302)
        html, removed = await self._results_html(request, dict(parse_qsl(back)))
        return fetch_reply(
            ok,
            message,
            replace={"#neighbors-results": html} if ok else {},
            texts={"[data-removed-count]": str(removed)} if ok else {},
        )

    @expose(
        "/neighbors/suppressions/{suppression_id:int}/restore",
        methods=["POST"],
        identity="neighbor-restore",
    )
    async def restore(self, request: Request) -> Response:
        require_admin(request)
        form = await request.form()
        back = back_query(str(form.get("back", "")))
        suppression_id = request.path_params["suppression_id"]
        with self.session_maker() as session:
            done = await _in_thread(
                neighbors_service.restore_suppression, session, admin_user(request), suppression_id
            )
        message = (
            "Restaurado: vuelve la próxima vez que se lean los datos de ConsorPlus del edificio."
            if done
            else "Eso ya estaba restaurado."
        )
        if not wants_fetch(request):
            (Flash.success if done else Flash.warning)(request, message)
            return RedirectResponse(self._url(request, back), status_code=302)
        params = dict(parse_qsl(back))
        with self.session_maker() as session:
            _, removed = await _in_thread(self._load, session, params)
        return fetch_reply(
            done,
            message,
            remove=[f'[data-suppression="{suppression_id}"]'],
            texts={"[data-removed-count]": str(len(removed))},
        )
