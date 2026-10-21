"""Panel page "Teléfonos": every phone, in two tabs, for operators and admins.

- GET  /admin/phones                    "A revisar" (area code assumed on import, needs_review,
                                        or listed for two people, conflict: REVIEW; the default
                                        tab while there is any, each with «Por qué»: why and
                                        what to do) and "Todos"
- POST /admin/phones/{id}/approve       one phone to review is verified
- POST /admin/phones/approve            the selected ones ("Aprobar seleccionados")
- POST /admin/phones/{id}/unlink        "Desvincular": the phone is deleted (the person stays;
                                        from ConsorPlus, it is not created again at night:
                                        app.admin.neighbors_service.remove_phone)

The actions answer as always (a Flash and back to the list) or, asked by panel.js, without
reloading: the rows that changed and the counters (app.admin.fetch).

Phones are never created or edited here. Every change is audited in bot_events
(phone_approved, phone_unlinked). The forms carry no CSRF token, like the rest of the panel:
the session cookie is SameSite=Strict (app.admin.auth).
"""

import re
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import parse_qsl, urlencode

from sqladmin import BaseView, expose
from sqladmin.flash import Flash
from sqlalchemy import Select, func, or_, select, true
from sqlalchemy.orm import Session, selectinload
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.admin import formatting, labels, neighbors_service
from app.admin.audit import log_admin_action
from app.admin.auth import admin_user
from app.admin.fetch import fetch_reply, wants_fetch
from app.admin.views import _in_thread
from app.config import Settings
from app.db.models import DataSource, Person, Phone, Unit, UnitPerson
from app.sync.normalize import normalize_phone

PAGE_SIZE = 50
TABS = {"review": "A revisar", "all": "Todos"}
SOURCE_LABELS = labels.PHONE_SOURCE
STATUS_LABELS = labels.PHONE_STATUS  # by priority: see phone_status
# The query string the actions go back to: only these keys (never a URL).
LIST_PARAMS = ("tab", "q", "status", "source", "page")
# «A revisar»: the area code was assumed, or ConsorPlus lists the number for two people.
REVIEW = Phone.needs_review.is_(True) | Phone.conflict.is_(True)


def phone_status(phone: Phone) -> str:
    if phone.conflict:
        return "conflict"
    if phone.needs_review:
        return "review"
    return "verified" if phone.verified else "unverified"


def _status_condition(status: str) -> Any:
    if status == "conflict":
        return Phone.conflict.is_(True)
    rest = Phone.conflict.is_(False)
    if status == "review":
        return rest & Phone.needs_review.is_(True)
    rest = rest & Phone.needs_review.is_(False)
    return rest & Phone.verified.is_(status == "verified")


def person_units(phone: Phone) -> str:
    links = sorted(
        phone.person.units,
        key=lambda link: (link.unit.building.name, link.unit.label, link.role),
    )
    return "; ".join(
        f"{formatting.building(link.unit.building.name)} · {link.unit.label}"
        f" ({labels.label(labels.PERSON_ROLE, link.role)})"
        for link in links
    )


@dataclass(frozen=True)
class PersonLink:
    """A person as «Por qué» links them: to their building in Vecinos, marked."""

    name: str
    building_id: int | None
    person_id: int


@dataclass(frozen=True)
class ReviewReason:
    """Why a phone is «A revisar» and what to do, deduced from what is stored (the reason
    itself is not): conflict first (it does not identify anyone), then the assumed area code
    (the same rule that flagged it on import: app.sync.normalize), then who loaded it.

    kind: "conflict" | "area_code" | "bot" | "other". other: the conflict's other person
    (None until the roster sync records it). people: the links to Vecinos."""

    kind: str
    text: str
    other: PersonLink | None = None
    other_units: str = ""
    people: tuple[PersonLink, ...] = ()


def _person_link(person: Person) -> PersonLink:
    return PersonLink(person.full_name, _first_building(person), person.id)


def _first_unit(person: Person) -> str:
    """ "TORRE · 3B", "+N" more when there are."""
    links = sorted(person.units, key=lambda link: (link.unit.building.name, link.unit.label))
    if not links:
        return "sin unidad"
    first = f"{formatting.building(links[0].unit.building.name)} · {links[0].unit.label}"
    return first if len(links) == 1 else f"{first} y {len(links) - 1} más"


def review_reason(phone: Phone) -> ReviewReason:
    this = _person_link(phone.person)
    if phone.conflict:
        other = phone.conflict_person
        if other is not None and other.id != phone.person_id:
            return ReviewReason(
                "conflict",
                "dejalo en la persona correcta.",
                other=_person_link(other),
                other_units=_first_unit(other),
                people=(this, _person_link(other)),
            )
        return ReviewReason(
            "conflict",
            "En ConsorPlus figura también en otra persona: dejalo en la persona correcta. "
            "Con quién, se sabe después de la próxima lectura de ConsorPlus.",
            people=(this,),
        )
    normalized = normalize_phone(phone.raw or "")
    if normalized is not None and normalized.needs_review:
        return ReviewReason("area_code", "Le agregamos el 351: confirmá que es un celular.")
    if phone.source == DataSource.BOT_VERIFIED:
        return ReviewReason("bot", "Lo asoció el bot: confirmá que es de esta persona.")
    return ReviewReason("other", "Confirmá que el número es de esta persona.")


def _first_building(person: Person) -> int | None:
    links = sorted(person.units, key=lambda link: (link.unit.building.name, link.unit.label))
    return links[0].unit.building_id if links else None


def search_condition(term: str) -> Any:
    """By person's name, the number as in ConsorPlus, or its digits ("351 555-0301",
    "0351 5550301", "+54 9 351…": compared without the leading 0 of the local format)."""
    term = term.strip()
    conditions = [Person.full_name.ilike(f"%{term}%"), Phone.raw.ilike(f"%{term}%")]
    digits = re.sub(r"\D", "", term).lstrip("0")
    if digits:
        conditions.append(Phone.e164.contains(digits, autoescape=True))
    return or_(*conditions)


@dataclass(frozen=True)
class PhoneRow:
    id: int
    number: str
    person: str
    units: str
    source: str
    status: str
    status_label: str
    date: str
    person_id: int = 0
    building_id: int | None = None  # of the person's first unit: the link to Vecinos
    reason: ReviewReason | None = None  # «Por qué», in «A revisar»


@dataclass(frozen=True)
class PhoneList:
    tab: str
    query: str
    status: str
    source: str
    page: int
    pages: int
    total: int
    review_count: int
    rows: list[PhoneRow]


def list_phones(
    session: Session,
    timezone: str,
    *,
    tab: str | None,
    query: str = "",
    status: str = "",
    source: str = "",
    page: int = 1,
) -> PhoneList:
    review_count = session.scalar(select(func.count()).select_from(Phone).where(REVIEW)) or 0
    if tab not in TABS:
        tab = "review" if review_count else "all"
    status = status if status in STATUS_LABELS else ""
    source = source if source in {s.value for s in DataSource} else ""

    stmt: Select = select(Phone).join(Phone.person)
    stmt = stmt.where(REVIEW if tab == "review" else true())
    if query.strip():
        stmt = stmt.where(search_condition(query))
    stmt = stmt.where(_status_condition(status) if status else true())
    stmt = stmt.where(Phone.source == source if source else true())

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(page, 1), pages)
    order = (Phone.id,) if tab == "review" else (Phone.created_at.desc(), Phone.id.desc())
    phones = session.scalars(
        stmt.options(
            selectinload(Phone.person)
            .selectinload(Person.units)
            .selectinload(UnitPerson.unit)
            .selectinload(Unit.building),
            selectinload(Phone.conflict_person)
            .selectinload(Person.units)
            .selectinload(UnitPerson.unit)
            .selectinload(Unit.building),
        )
        .order_by(*order)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    rows = [
        PhoneRow(
            id=p.id,
            number=formatting.phone(p.e164),
            person=p.person.full_name,
            units=person_units(p),
            source=labels.label(SOURCE_LABELS, p.source),
            status=phone_status(p),
            status_label=STATUS_LABELS[phone_status(p)],
            date=formatting.when(p.created_at, timezone=timezone),
            person_id=p.person_id,
            building_id=_first_building(p.person),
            reason=review_reason(p) if tab == "review" else None,
        )
        for p in phones
    ]
    return PhoneList(tab, query.strip(), status, source, page, pages, total, review_count, rows)


# --- Changes (sync, one transaction each) ---------------------------------------------------


def approve_phone(session: Session, phone_id: int, user: str) -> bool:
    """Only a phone still to review (False otherwise). Never one in conflict: it is left in the
    right person (Vecinos), not approved."""
    phone = session.get(Phone, phone_id, with_for_update=True)
    if phone is None or not phone.needs_review or phone.conflict:
        return False
    phone.verified = True
    phone.needs_review = False
    log_admin_action(session, user, "phone_approved", phone_e164=phone.e164, phone_id=phone.id)
    session.commit()
    return True


def unlink_phone(session: Session, phone_id: int, user: str) -> bool:
    """Like Vecinos: a number from ConsorPlus does not come back with the next sync."""
    return neighbors_service.remove_phone(session, user, phone_id)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def back_query(raw: str) -> str:
    """The list's query string, keeping only its own keys (a sent URL is never followed)."""
    pairs = [(k, v) for k, v in parse_qsl(raw.lstrip("?")) if k in LIST_PARAMS]
    return urlencode(pairs)


# --- Page -----------------------------------------------------------------------------------


class PhonesView(BaseView):
    name = "Teléfonos"
    icon = "fa-solid fa-address-book"
    # The menu's counter (templates/sqladmin/_macros.html, app.admin.pending).
    menu_counter = "phones"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default

    def _back(self, request: Request, form: Any) -> RedirectResponse:
        url = str(request.url_for("admin:view-phones"))
        query = back_query(str(form.get("back", "")))
        return RedirectResponse(f"{url}?{query}" if query else url, status_code=302)

    async def _list(self, session: Session, params: Any) -> PhoneList:
        return await _in_thread(
            lambda: list_phones(
                session,
                self.timezone,
                tab=params.get("tab"),
                query=params.get("q", ""),
                status=params.get("status", ""),
                source=params.get("source", ""),
                page=_int(params.get("page"), 1),
            )
        )

    def _context(self, result: PhoneList) -> dict[str, Any]:
        base = {"tab": result.tab, "q": result.query, "status": result.status}
        base["source"] = result.source

        def link(**changes: Any) -> str:
            values = {**base, **changes}
            return "?" + urlencode({k: v for k, v in values.items() if v not in ("", None)})

        return {
            "result": result,
            "tabs": TABS,
            "status_labels": STATUS_LABELS,
            "source_labels": {s.value: label for s, label in SOURCE_LABELS.items()},
            "link": link,
            "back": link(page=result.page)[1:],
        }

    @expose("/phones", methods=["GET"], identity="phones")
    async def phones_page(self, request: Request) -> Response:
        with self.session_maker() as session:
            result = await self._list(session, request.query_params)
        return await self.templates.TemplateResponse(
            request, "phones.html", {"title": "Teléfonos", **self._context(result)}
        )

    async def _answer(
        self, request: Request, form: Any, ok: bool, message: str, changed: set[int]
    ) -> Response:
        """After an action: the same Flash and redirect as always, or, asked by panel.js, what
        changed in the list it was done from (its tab, search and page): rows that are no
        longer in it go away, the others are redrawn, and the counters."""
        if not wants_fetch(request):
            (Flash.success if ok else Flash.warning)(request, message)
            return self._back(request, form)
        params = dict(parse_qsl(back_query(str(form.get("back", "")))))
        with self.session_maker() as session:
            result = await self._list(session, params)
        context = self._context(result)
        template = self.templates.env.get_template("_phone_row.html")
        shown = {row.id: row for row in result.rows}
        replace = {}
        for phone_id in changed:
            if phone_id in shown:
                html = await template.render_async(
                    {"request": request, **context, "row": shown[phone_id]}
                )
                replace[f'[data-phone="{phone_id}"]'] = html
        return fetch_reply(
            ok,
            message,
            remove=[f'[data-phone="{i}"]' for i in sorted(changed) if i not in shown],
            replace=replace,
            texts={
                "[data-review-count]": str(result.review_count),
                "[data-phones-total]": str(result.total),
            },
        )

    @expose("/phones/{phone_id:int}/approve", methods=["POST"], identity="phone-approve")
    async def approve(self, request: Request) -> Response:
        form = await request.form()
        phone_id = request.path_params["phone_id"]
        with self.session_maker() as session:
            done = await _in_thread(approve_phone, session, phone_id, admin_user(request))
        if done:
            return await self._answer(
                request, form, True, "Teléfono aprobado: quedó verificado.", {phone_id}
            )
        return await self._answer(
            request, form, False, "No se aprobó: ese teléfono ya no estaba a revisar.", set()
        )

    @expose("/phones/approve", methods=["POST"], identity="phones-approve")
    async def approve_selected(self, request: Request) -> Response:
        form = await request.form()
        user = admin_user(request)
        approved: set[int] = set()
        for pk in {_int(v) for v in form.getlist("pk")} - {0}:
            with self.session_maker() as session:
                if await _in_thread(approve_phone, session, pk, user):
                    approved.add(pk)
        if approved:
            message = f"Teléfonos aprobados: {len(approved)}."
            return await self._answer(request, form, True, message, approved)
        message = "No se aprobó ningún teléfono: elegí alguno a revisar."
        return await self._answer(request, form, False, message, set())

    @expose("/phones/{phone_id:int}/unlink", methods=["POST"], identity="phone-unlink")
    async def unlink(self, request: Request) -> Response:
        form = await request.form()
        phone_id = request.path_params["phone_id"]
        with self.session_maker() as session:
            done = await _in_thread(unlink_phone, session, phone_id, admin_user(request))
        if done:
            return await self._answer(request, form, True, "Teléfono desvinculado.", {phone_id})
        return await self._answer(request, form, False, "Ese teléfono ya no existía.", set())
