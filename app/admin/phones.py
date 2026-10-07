"""Panel page "Teléfonos": every phone, in two tabs, for operators and admins.

- GET  /admin/phones                    "A revisar" (area code assumed on import, needs_review;
                                        the default tab while there is any) and "Todos"
- POST /admin/phones/{id}/approve       one phone to review is verified
- POST /admin/phones/approve            the selected ones ("Aprobar seleccionados")
- POST /admin/phones/{id}/unlink        "Desvincular": the phone is deleted (the person stays)

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

from app.admin import formatting, labels
from app.admin.audit import log_admin_action
from app.admin.auth import admin_user
from app.admin.views import _in_thread
from app.config import Settings
from app.db.models import DataSource, Person, Phone, Unit, UnitPerson

PAGE_SIZE = 50
TABS = {"review": "A revisar", "all": "Todos"}
SOURCE_LABELS = labels.PHONE_SOURCE
STATUS_LABELS = labels.PHONE_STATUS  # by priority: see phone_status
UNLINK_CONFIRMATION = (
    "¿Desvincular este teléfono? La persona va a tener que volver a identificarse por "
    "WhatsApp. Si el número sigue cargado en ConsorPlus, la sincronización nocturna lo vuelve "
    "a crear: corregilo también allá."
)
# The query string the actions go back to: only these keys (never a URL).
LIST_PARAMS = ("tab", "q", "status", "source", "page")


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
    review_count = (
        session.scalar(select(func.count()).select_from(Phone).where(Phone.needs_review)) or 0
    )
    if tab not in TABS:
        tab = "review" if review_count else "all"
    status = status if status in STATUS_LABELS else ""
    source = source if source in {s.value for s in DataSource} else ""

    stmt: Select = select(Phone).join(Phone.person)
    stmt = stmt.where(Phone.needs_review.is_(True) if tab == "review" else true())
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
            .selectinload(Unit.building)
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
        )
        for p in phones
    ]
    return PhoneList(tab, query.strip(), status, source, page, pages, total, review_count, rows)


# --- Changes (sync, one transaction each) ---------------------------------------------------


def approve_phone(session: Session, phone_id: int, user: str) -> bool:
    """Only a phone still to review (False otherwise)."""
    phone = session.get(Phone, phone_id, with_for_update=True)
    if phone is None or not phone.needs_review:
        return False
    phone.verified = True
    phone.needs_review = False
    log_admin_action(session, user, "phone_approved", phone_e164=phone.e164, phone_id=phone.id)
    session.commit()
    return True


def unlink_phone(session: Session, phone_id: int, user: str) -> bool:
    phone = session.get(Phone, phone_id, with_for_update=True)
    if phone is None:
        return False
    log_admin_action(
        session,
        user,
        "phone_unlinked",
        phone_e164=phone.e164,
        phone_id=phone.id,
        person_id=phone.person_id,
        source=str(phone.source),
        verified=phone.verified,
    )
    session.delete(phone)
    session.commit()
    return True


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
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default

    def _back(self, request: Request, form: Any) -> RedirectResponse:
        url = str(request.url_for("admin:view-phones"))
        query = back_query(str(form.get("back", "")))
        return RedirectResponse(f"{url}?{query}" if query else url, status_code=302)

    @expose("/phones", methods=["GET"], identity="phones")
    async def phones_page(self, request: Request) -> Response:
        params = request.query_params
        with self.session_maker() as session:
            result = await _in_thread(
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
        base = {"tab": result.tab, "q": result.query, "status": result.status}
        base["source"] = result.source

        def link(**changes: Any) -> str:
            values = {**base, **changes}
            return "?" + urlencode({k: v for k, v in values.items() if v not in ("", None)})

        return await self.templates.TemplateResponse(
            request,
            "phones.html",
            {
                "title": "Teléfonos",
                "result": result,
                "tabs": TABS,
                "status_labels": STATUS_LABELS,
                "source_labels": {s.value: label for s, label in SOURCE_LABELS.items()},
                "link": link,
                "back": link(page=result.page)[1:],
                "unlink_confirmation": UNLINK_CONFIRMATION,
            },
        )

    @expose("/phones/{phone_id:int}/approve", methods=["POST"], identity="phone-approve")
    async def approve(self, request: Request) -> Response:
        form = await request.form()
        with self.session_maker() as session:
            done = await _in_thread(
                approve_phone, session, request.path_params["phone_id"], admin_user(request)
            )
        if done:
            Flash.success(request, "Teléfono aprobado: quedó verificado.")
        else:
            Flash.warning(request, "No se aprobó: ese teléfono ya no estaba a revisar.")
        return self._back(request, form)

    @expose("/phones/approve", methods=["POST"], identity="phones-approve")
    async def approve_selected(self, request: Request) -> Response:
        form = await request.form()
        user = admin_user(request)
        approved = 0
        for pk in {_int(v) for v in form.getlist("pk")} - {0}:
            with self.session_maker() as session:
                approved += await _in_thread(approve_phone, session, pk, user)
        if approved:
            Flash.success(request, f"Teléfonos aprobados: {approved}.")
        else:
            Flash.warning(request, "No se aprobó ningún teléfono: elegí alguno a revisar.")
        return self._back(request, form)

    @expose("/phones/{phone_id:int}/unlink", methods=["POST"], identity="phone-unlink")
    async def unlink(self, request: Request) -> Response:
        form = await request.form()
        with self.session_maker() as session:
            done = await _in_thread(
                unlink_phone, session, request.path_params["phone_id"], admin_user(request)
            )
        if done:
            Flash.success(request, "Teléfono desvinculado.")
        else:
            Flash.warning(request, "Ese teléfono ya no existía.")
        return self._back(request, form)
