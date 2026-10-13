"""Panel section "Reclamos", for operators and admins:

- GET  /admin/claims                      the list: tabs Abiertos / Cerrados / Todos, filters,
                                          search (urgent open ones first, then the newest)
- GET  /admin/claims/new                  "Nuevo reclamo" (by phone or in person): first the
- POST /admin/claims/new                  building, then the form
- GET  /admin/claims/{id}                 the claim: data, who reported, photos, history
- POST /admin/claims/{id}/provider        "Cambiar quién lo atiende"
- POST /admin/claims/{id}/close           "Cerrar como solucionado" (with a reason)
- POST /admin/claims/{id}/cancel          "Cancelar reclamo" (with a reason)
- POST /admin/claims/{id}/note            "Nota interna"
- POST /admin/claims/{id}/resend          "Reenviar al proveedor" (by WhatsApp)
- POST /admin/claims/{id}/mark-sent       "Marcar como avisado" (told by phone)

A claim with a provider with WhatsApp is sent to it when it is created here and when the
provider changes; closing it as solved tells the neighbors (app.claims.notify). Cancelling tells
nobody.

Every change goes through app.claims.service (its rules, history and audit); this module only
collects the choice and shows the result. Nothing is sent to anybody from here.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlencode

from markupsafe import Markup, escape
from sqladmin import BaseView, expose
from sqladmin.flash import Flash
from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin import formatting, labels
from app.admin.auth import admin_user, display_names
from app.admin.fetch import fetch_reply, wants_fetch
from app.admin.inbox import add_note as add_conversation_note
from app.admin.phones import search_condition as phone_search
from app.bot.identity import to_e164
from app.claims import texts
from app.claims.notify import Notifier
from app.claims.schedule import when_text
from app.claims.service import (
    ClaimProblem,
    Reporter,
    add_note,
    change_provider,
    close_claim,
    create_claim,
    mark_sent,
)
from app.claims.setup import enabled_categories
from app.config import Settings
from app.db.models import (
    CLOSED_CLAIM_STATUSES,
    OPEN_CLAIM_STATUSES,
    Building,
    BuildingClaimCategory,
    Claim,
    ClaimActor,
    ClaimAttachment,
    ClaimAttention,
    ClaimCategory,
    ClaimEvent,
    ClaimEventKind,
    ClaimReporter,
    ClaimSource,
    ClaimStatus,
    Person,
    Phone,
    Provider,
    Unit,
    UnitPerson,
    WaContact,
    WaConversation,
    WaConversationStatus,
)
from app.whatsapp.media import INLINE_TYPES, base_mime

PAGE_SIZE = 50
TABS = {"open": "Abiertos", "closed": "Cerrados", "all": "Todos"}
TAB_STATUSES = {"open": OPEN_CLAIM_STATUSES, "closed": CLOSED_CLAIM_STATUSES}
STUDIO_FILTER = "studio"
URGENT_FILTER = {"yes": "Urgentes", "no": "No urgentes"}
ATTENTION_FILTER = {"yes": "Necesitan atención (incluye urgentes)"}
WHOLE_BUILDING = "Todo el edificio"
# Open claims someone of the studio should look at now (the filter «Necesitan atención», the
# menu's counter and «Pendientes»): urgent, or with an alert (the
# provider cannot attend it, it could not be sent, the provider did not confirm, it has been
# open too long: app.claims.jobs).
NEEDS_SOMEONE = Claim.status.in_(OPEN_CLAIM_STATUSES) & (
    Claim.urgent.is_(True) | Claim.attention.is_not(None)
)


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# --- The list -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Filters:
    tab: str = "open"
    query: str = ""
    building: int | None = None
    category: int | None = None
    attends: str = ""  # "" | "studio" | a provider's id
    status: str = ""
    urgent: str = ""
    attention: str = ""  # "yes": only the open ones that need someone (NEEDS_SOMEONE)
    page: int = 1

    @property
    def any_filter(self) -> bool:
        return bool(
            self.building or self.category or self.attends or self.status or self.urgent
            or self.attention
        )  # fmt: skip

    def params(self, **changes: Any) -> dict[str, Any]:
        values = {
            "tab": self.tab, "q": self.query, "building": self.building,
            "category": self.category, "attends": self.attends, "status": self.status,
            "urgent": self.urgent, "attention": self.attention,
            "page": self.page if self.page > 1 else None,
        }  # fmt: skip
        values.update(changes)
        return {k: v for k, v in values.items() if v not in ("", None)}


def read_filters(params: Any) -> Filters:
    tab = params.get("tab", "open")
    status = params.get("status", "")
    attends = params.get("attends", "")
    return Filters(
        tab=tab if tab in TABS else "open",
        query=params.get("q", "").strip(),
        building=_int(params.get("building")),
        category=_int(params.get("category")),
        attends=attends if attends == STUDIO_FILTER or _int(attends) else "",
        status=status if status in {s.value for s in ClaimStatus} else "",
        urgent=params.get("urgent", "") if params.get("urgent") in URGENT_FILTER else "",
        attention="yes" if params.get("attention") in ATTENTION_FILTER else "",
        page=max(1, _int(params.get("page")) or 1),
    )


def search_condition(term: str) -> Any:
    """A claim by its number ("1003", "#1003"), a unit, a name or part of a phone (also of the
    neighbors who joined)."""
    term = term.strip()
    like = f"%{term}%"
    units = select(Unit.id).where(Unit.label.ilike(like))
    joined = select(ClaimReporter.claim_id).where(ClaimReporter.name.ilike(like))
    conditions = [
        Claim.reporter_name.ilike(like),
        Claim.unit_id.in_(units),
        Claim.reporter_unit_id.in_(units),
        Claim.id.in_(joined),
    ]
    number = term.lstrip("#").strip()
    if number.isdigit() and len(number) <= 9:
        conditions.append(Claim.number == int(number))
    digits = re.sub(r"\D", "", term).lstrip("0")
    if len(digits) >= 4:
        conditions.append(Claim.reporter_phone_e164.contains(digits, autoescape=True))
        conditions.append(
            Claim.id.in_(
                select(ClaimReporter.claim_id).where(
                    ClaimReporter.phone_e164.contains(digits, autoescape=True)
                )
            )
        )
    return or_(*conditions)


def _filtered(filters: Filters) -> Select:
    stmt = select(Claim)
    if filters.query:
        stmt = stmt.where(search_condition(filters.query))
    if filters.building:
        stmt = stmt.where(Claim.building_id == filters.building)
    if filters.category:
        stmt = stmt.where(Claim.category_id == filters.category)
    if filters.attends == STUDIO_FILTER:
        stmt = stmt.where(Claim.provider_id.is_(None))
    elif filters.attends:
        stmt = stmt.where(Claim.provider_id == int(filters.attends))
    if filters.status:
        stmt = stmt.where(Claim.status == filters.status)
    if filters.urgent:
        stmt = stmt.where(Claim.urgent.is_(filters.urgent == "yes"))
    if filters.attention:
        stmt = stmt.where(NEEDS_SOMEONE)
    return stmt


@dataclass(frozen=True)
class ClaimRow:
    id: int
    number: int
    date: str
    building: str
    unit: str
    problem: str
    reporter: str
    others: int
    attends: str
    status_label: str  # short (the badge)
    status_long: str  # on hover
    group: str
    ago: str
    urgent: bool
    attention: str = ""  # why someone of the studio should look at it ("" if nothing)

    @property
    def place(self) -> str:
        """ "TORRE EJEMPLO · 02-B" / "TORRE EJEMPLO · todo el edificio"."""
        unit = self.unit if self.unit != WHOLE_BUILDING else WHOLE_BUILDING.lower()
        return f"{self.building} · {unit}"


@dataclass(frozen=True)
class ClaimList:
    filters: Filters
    counts: dict[str, int]
    total: int
    pages: int
    rows: list[ClaimRow]


def reporter_text(claim: Claim) -> str:
    return claim.reporter_name or formatting.phone(claim.reporter_phone_e164)


def attends_text(claim: Claim) -> str:
    return claim.provider.name if claim.provider else labels.STUDIO_SHORT


def unit_text(claim: Claim) -> str:
    return claim.unit.label if claim.unit else WHOLE_BUILDING


def list_claims(session: Session, filters: Filters, now: datetime, timezone: str) -> ClaimList:
    base = _filtered(filters)
    filtered = base.subquery()
    counted = select(filtered.c.status, func.count()).group_by(filtered.c.status)
    by_status = {ClaimStatus(status): n for status, n in session.execute(counted)}
    counts = {
        "open": sum(by_status.get(s, 0) for s in OPEN_CLAIM_STATUSES),
        "closed": sum(by_status.get(s, 0) for s in CLOSED_CLAIM_STATUSES),
    }
    counts["all"] = counts["open"] + counts["closed"]
    total = counts[filters.tab]
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(filters.page, pages)
    stmt = base
    if filters.tab in TAB_STATUSES:
        stmt = stmt.where(Claim.status.in_(TAB_STATUSES[filters.tab]))
    urgent_open = case((Claim.urgent.is_(True) & Claim.status.in_(OPEN_CLAIM_STATUSES), 0), else_=1)
    claims = session.scalars(
        stmt.options(
            selectinload(Claim.building),
            selectinload(Claim.unit),
            selectinload(Claim.category),
            selectinload(Claim.provider),
            selectinload(Claim.reporters),
        )
        .order_by(urgent_open, Claim.created_at.desc(), Claim.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    rows = [
        ClaimRow(
            id=c.id,
            number=c.number,
            date=formatting.when(c.created_at, now, timezone),
            building=formatting.building(c.building.name),
            unit=unit_text(c),
            problem=c.category.list_title,
            reporter=reporter_text(c),
            others=len(c.reporters),
            attends=attends_text(c),
            status_label=labels.claim_status(c.status)[0],
            status_long=labels.claim_status(c.status)[1],
            group=labels.state(c.status, "claim"),
            ago=formatting.ago(c.status_at, now),
            urgent=c.urgent,
            attention=texts.attention_text(c, now) if c.is_open else "",
        )
        for c in claims
    ]
    return ClaimList(Filters(**{**filters.__dict__, "page": page}), counts, total, pages, rows)


def filter_options(session: Session) -> dict[str, list[tuple[str, str]]]:
    buildings = session.scalars(
        select(Building).where(Building.id.in_(select(Claim.building_id)))
    ).all()
    categories = session.scalars(
        select(ClaimCategory).order_by(ClaimCategory.sort_order, ClaimCategory.name)
    ).all()
    providers = session.scalars(select(Provider).order_by(Provider.name)).all()
    return {
        "buildings": sorted(
            ((str(b.id), formatting.building(b.name)) for b in buildings), key=lambda o: o[1]
        ),
        "categories": [(str(c.id), c.list_title) for c in categories],
        "attends": [(STUDIO_FILTER, labels.STUDIO_SHORT)]
        + [(str(p.id), p.name) for p in providers],
        "statuses": [(s.value, labels.CLAIM_STATUS[s]) for s in ClaimStatus],
        "urgent": list(URGENT_FILTER.items()),
        "attention": list(ATTENTION_FILTER.items()),
    }


def claims_stamp(session: Session) -> str:
    """Something that changes with every change of a claim: its last update and its last
    event (a neighbor who joins leaves only an event). No data of anybody: the panel's numbers
    carry it and the list redraws itself when it changes (app.admin.pending, panel.js)."""
    updated = session.scalar(select(func.max(Claim.updated_at)))
    event = session.scalar(select(func.max(ClaimEvent.id)))
    return f"{int(updated.timestamp() * 1000) if updated else 0}:{event or 0}"


def _alert_title(claim: Claim, now: datetime) -> str:
    if claim.attention is not None:
        return f"{texts.attention_text(claim, now)} · #{claim.number}"
    return f"Reclamo urgente #{claim.number}"


def urgent_open_claims(session: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    """The open claims that need someone (urgent, or with an alert), newest first: the
    panel's sound and notification, and the menu's counter. The key includes the alert, so a
    claim that gets one later alerts again."""
    claims = session.scalars(
        select(Claim)
        .where(NEEDS_SOMEONE)
        .options(selectinload(Claim.building), selectinload(Claim.category))
        .order_by(Claim.created_at.desc(), Claim.id.desc())
    ).all()
    now = now or datetime.now(UTC)
    return [
        {
            "id": f"{c.id}:{c.attention or 'urgent'}",
            "number": c.number,
            "title": _alert_title(c, now),
            "problem": c.category.list_title,
            "building": formatting.building(c.building.name),
        }
        for c in claims
    ]


# --- The claim ------------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonLine:
    name: str
    phone: str
    unit: str
    conversation_id: int | None
    joined_at: str = ""


@dataclass(frozen=True)
class EventLine:
    when: str
    who: str
    text: str
    kind: str
    # A WhatsApp notice: how its delivery went ("entregado", "leído"...), "" otherwise.
    delivery: str = ""


@dataclass(frozen=True)
class Photo:
    message_id: int
    image: bool
    name: str


@dataclass
class ClaimPage:
    claim: Claim
    building: str
    unit: str
    status_label: str  # short (the badge)
    status_long: str
    group: str
    attends: str
    reporter: PersonLine
    joined: list[PersonLine] = field(default_factory=list)
    photos: list[Photo] = field(default_factory=list)
    events: list[EventLine] = field(default_factory=list)
    attention: str = ""  # why someone of the studio should look at it
    scheduled: str = ""  # when it goes to the provider (out of the providers' hours)
    reminded: str = ""  # when the provider got the reminder
    sent_ago: str = ""  # since when the provider was told ("hace 2 h"), while not confirmed
    had_provider: bool = False  # see had_provider()


def load_claim(session: Session, claim_id: int) -> Claim | None:
    return session.scalars(
        select(Claim)
        .where(Claim.id == claim_id)
        .options(
            selectinload(Claim.building),
            selectinload(Claim.unit),
            selectinload(Claim.reporter_unit),
            selectinload(Claim.category),
            selectinload(Claim.provider),
            selectinload(Claim.previous),
            selectinload(Claim.reporters).selectinload(ClaimReporter.unit),
            selectinload(Claim.attachments).selectinload(ClaimAttachment.message),
            selectinload(Claim.events).selectinload(ClaimEvent.message),
        )
    ).first()


def _conversations(session: Session, phones: list[str]) -> dict[str, int]:
    """phone -> its conversation in Conversaciones (test chat contacts excluded)."""
    if not phones:
        return {}
    rows = session.execute(
        select(WaContact.phone_e164, WaConversation.id)
        .join(WaConversation, WaConversation.contact_id == WaContact.id)
        .where(WaContact.phone_e164.in_(phones), WaContact.simulated.is_(False))
    )
    return {phone: cid for phone, cid in rows}


def claim_page(session: Session, claim: Claim, now: datetime, timezone: str) -> ClaimPage:
    phones = [p for p in [claim.reporter_phone_e164, *(r.phone_e164 for r in claim.reporters)] if p]
    conversations = _conversations(session, phones)
    names = display_names(session)

    def person(name: str, phone: str | None, unit: Unit | None) -> PersonLine:
        return PersonLine(
            name=name,
            phone=formatting.phone(phone) if phone else "",
            unit=unit.label if unit else "",
            conversation_id=conversations.get(phone) if phone else None,
        )

    page = ClaimPage(
        claim=claim,
        building=formatting.building(claim.building.name),
        unit=unit_text(claim),
        status_label=labels.claim_status(claim.status)[0],
        status_long=labels.claim_status(claim.status)[1],
        group=labels.state(claim.status, "claim"),
        attends=attends_text(claim),
        reporter=person(reporter_text(claim), claim.reporter_phone_e164, claim.reporter_unit),
        attention=texts.attention_text(claim, now) if claim.is_open else "",
        scheduled=when_text(claim.send_after, now, timezone)
        if claim.send_after and claim.status == ClaimStatus.PENDING_SEND
        else "",
        reminded=formatting.when(claim.reminded_at, now, timezone)
        if claim.reminded_at and claim.status == ClaimStatus.SENT
        else "",
        sent_ago=formatting.ago(claim.sent_at, now)
        if claim.sent_at and claim.status == ClaimStatus.SENT
        else "",
        had_provider=had_provider(session, claim),
    )
    for r in claim.reporters:
        line = person(r.name, r.phone_e164, r.unit)
        page.joined.append(
            PersonLine(
                **{**line.__dict__, "joined_at": formatting.when(r.created_at, now, timezone)}
            )
        )
    for a in claim.attachments:
        mime = base_mime(a.message.media_mime)
        page.photos.append(
            Photo(
                message_id=a.wa_message_id,
                image=mime in INLINE_TYPES and mime.startswith("image/"),
                name=a.message.media_filename or "Adjunto",
            )
        )
    for e in claim.events:
        if e.actor == ClaimActor.PANEL and e.panel_user:
            who = names.get(e.panel_user, e.panel_user)
        else:
            who = labels.label(labels.CLAIM_ACTOR, e.actor)
        page.events.append(
            EventLine(
                when=formatting.full(e.created_at, timezone),
                who=who,
                text=labels.claim_event(e.kind, e.text),
                kind=str(e.kind),
                delivery=labels.label(labels.MESSAGE_STATUS, e.message.status, "")
                if e.message is not None and e.message.status
                else "",
            )
        )
    return page


# --- «Qué hacer»: one main button for the status, the rest in «Otras acciones» ----------------


@dataclass(frozen=True)
class Action:
    """A button of «Qué hacer». key: resend (now, or the page's choice out of hours), send_now
    (a scheduled one), mark_sent, provider, close, cancel."""

    key: str
    label: str


RESEND = Action("resend", "Reenviar al proveedor")
SEND_CHOOSE = Action("resend", "Mandar al proveedor")
SEND_NOW = Action("send_now", "Mandar ahora")
MARK_SENT = Action("mark_sent", "Marcar como avisado")
CHANGE_PROVIDER = Action("provider", "Cambiar quién lo atiende")
CHOOSE_PROVIDER = Action("provider", "Elegir proveedor")
CLOSE = Action("close", "Cerrar como solucionado")
CANCEL = Action("cancel", "Cancelar reclamo")


def had_provider(session: Session, claim: Claim) -> bool:
    """Whether a claim the studio has was (or should have been) a provider's: the provider said
    it cannot attend it, it was ever told or changed, or in its building the problem has a
    provider. Otherwise the studio attends it by design (no provider for it there)."""
    if claim.attention == ClaimAttention.DECLINED:
        return True
    if any(e.provider_id or e.kind == ClaimEventKind.PROVIDER_CHANGED for e in claim.events):
        return True
    assigned = session.scalar(
        select(BuildingClaimCategory.provider_id).where(
            BuildingClaimCategory.building_id == claim.building_id,
            BuildingClaimCategory.category_id == claim.category_id,
        )
    )
    return assigned is not None


def claim_actions(
    claim: Claim, *, scheduled: bool, wait_when: str, had_provider: bool
) -> tuple[Action | None, list[Action]]:
    """(the main button, «Otras acciones») for the claim's status, only what app.claims.service
    and this view allow. «Avisado» has no main one: the provider still has to confirm."""
    status = ClaimStatus(claim.status)
    if status in CLOSED_CLAIM_STATUSES:
        return None, []
    if status == ClaimStatus.PENDING_SEND and claim.provider is not None:
        if not claim.provider.whatsapp_e164:
            return MARK_SENT, [CHANGE_PROVIDER, CLOSE, CANCEL]
        main = SEND_NOW if scheduled else SEND_CHOOSE if wait_when else RESEND
        return main, [MARK_SENT, CHANGE_PROVIDER, CLOSE, CANCEL]
    if status == ClaimStatus.SENT:
        return None, [CLOSE, CHANGE_PROVIDER, CANCEL]
    if status == ClaimStatus.ACKNOWLEDGED:
        return CLOSE, [CHANGE_PROVIDER, CANCEL]
    # The studio has it.
    if had_provider:
        return CHOOSE_PROVIDER, [CLOSE, CANCEL]
    return CLOSE, [CHOOSE_PROVIDER, CANCEL]


# --- "Nuevo reclamo" ------------------------------------------------------------------------
# It starts with the person: «¿Quién reclama?» searches the roster (GET /claims/people) and,
# once chosen, the rest of the form comes for that building (GET /claims/new/form), without
# reloading. «No está en el padrón»: the building, then the name and the phone. From a
# conversation (?conversation=<id>) the person, or the phone, comes already chosen.

PEOPLE_LIMIT = 20
PEOPLE_MIN_CHARS = 2


@dataclass(frozen=True)
class PersonChoice:
    """A person of the roster in one of their active units."""

    person_id: int
    name: str
    unit_id: int
    unit: str
    building_id: int
    building: str
    role: str

    @property
    def value(self) -> str:
        """The form's "person" ("<unit id>-<person id>", as reporter_from_form reads it)."""
        return f"{self.unit_id}-{self.person_id}"

    @property
    def place(self) -> str:
        return f"{self.building} · {self.unit}"


def _choices(session: Session, condition: Any) -> list[PersonChoice]:
    rows = session.execute(
        select(
            UnitPerson.person_id,
            Person.full_name,
            Unit.id,
            Unit.label,
            Building.id,
            Building.name,
            UnitPerson.role,
        )  # fmt: skip
        .join(Person, Person.id == UnitPerson.person_id)
        .join(Unit, Unit.id == UnitPerson.unit_id)
        .join(Building, Building.id == Unit.building_id)
        .where(Unit.active.is_(True), Building.active.is_(True), condition)
        .order_by(Person.full_name, Building.name, Unit.label)
        .limit(PEOPLE_LIMIT)
    ).all()
    return [
        PersonChoice(
            person_id=person_id,
            name=name,
            unit_id=unit_id,
            unit=label,
            building_id=building_id,
            building=formatting.building(building),
            role=labels.label(labels.PERSON_ROLE, role),
        )
        for person_id, name, unit_id, label, building_id, building, role in rows
    ]


def search_people(session: Session, term: str) -> list[PersonChoice]:
    """The roster by name, unit or phone (the formats «Teléfonos» understands: "351 555-0301",
    "0351 5550301", "+54 9 351…"), in active units of active buildings. From 2 characters; a
    phone from 4 digits (fewer would match almost every number)."""
    term = term.strip()
    if len(term) < PEOPLE_MIN_CHARS:
        return []
    like = f"%{term}%"
    digits = re.sub(r"\D", "", term).lstrip("0")
    by_person = Person.full_name.ilike(like)
    if len(digits) >= 4:
        by_person = Person.id.in_(
            select(Person.id)
            .outerjoin(Phone, Phone.person_id == Person.id)
            .where(phone_search(term))
        )
    return _choices(session, or_(by_person, Unit.label.ilike(like)))


def person_choice(
    session: Session, person_id: int | None, unit_id: int | None
) -> PersonChoice | None:
    if person_id is None or unit_id is None:
        return None
    found = _choices(session, (UnitPerson.person_id == person_id) & (Unit.id == unit_id))
    return found[0] if found else None


@dataclass
class FromConversation:
    """What «Cargar reclamo» from a conversation brings: the person in each of their units
    (several: she picks one), or, when the number is nobody's, its phone and WhatsApp name."""

    conversation_id: int
    choices: list[PersonChoice] = field(default_factory=list)
    phone: str = ""
    name: str = ""


def from_conversation(session: Session, conversation_id: int | None) -> FromConversation | None:
    """None: no such conversation, or a provider's (their claims are not theirs to report)."""
    if conversation_id is None:
        return None
    row = session.execute(
        select(WaConversation, WaContact)
        .join(WaContact, WaContact.id == WaConversation.contact_id)
        .where(WaConversation.id == conversation_id)
    ).first()
    if row is None:
        return None
    conversation, contact = row
    provider = session.scalar(
        select(Provider.id).where(
            Provider.whatsapp_e164 == contact.phone_e164, Provider.active.is_(True)
        )
    )
    if conversation.status == WaConversationStatus.PROVIDER or provider is not None:
        return None
    result = FromConversation(conversation.id)
    person_id = session.scalar(
        select(Phone.person_id).where(Phone.e164 == contact.phone_e164, Phone.conflict.is_(False))
    )
    if person_id is not None:
        result.choices = _choices(session, UnitPerson.person_id == person_id)
    if not result.choices:
        result.phone = formatting.phone(contact.phone_e164)
        result.name = contact.profile_name or ""
    return result


def building_units(session: Session, building_id: int) -> list[Unit]:
    """The building's active units (the unit of «No está en el padrón», optional)."""
    units = session.scalars(
        select(Unit).where(Unit.building_id == building_id, Unit.active.is_(True))
    ).all()
    return sorted(units, key=lambda u: u.label)


# «… reclamo #1003» in an internal note: a link to that claim (by its number).
_CLAIM_REFERENCE = re.compile(r"reclamo #(\d{1,9})")
CLAIM_NUMBER_PATH = "/admin/claims/number/{}"


def claim_links(text: str | None) -> Markup:
    """An internal note's text, escaped, with «reclamo #N» as a link to the claim (Jinja filter
    of the conversation's internal notes: only the studio writes those)."""
    text = text or ""
    parts, last = [], 0
    for match in _CLAIM_REFERENCE.finditer(text):
        number = match.group(1)
        parts.append(escape(text[last : match.start()]))
        link = Markup('reclamo <a href="{}">#{}</a>')
        parts.append(link.format(CLAIM_NUMBER_PATH.format(number), number))
        last = match.end()
    parts.append(escape(text[last:]))
    return Markup("").join(parts)


def claim_number_note(number: int, repeated: bool) -> str:
    """The internal note left in the conversation the claim was loaded from."""
    return f"Se sumó al reclamo #{number}." if repeated else f"Se cargó el reclamo #{number}."


def _person_phone(session: Session, person_id: int) -> str | None:
    """The person's phone for the claim: a verified one first, never one in conflict."""
    phone = session.scalars(
        select(Phone)
        .where(Phone.person_id == person_id, Phone.conflict.is_(False))
        .order_by(Phone.verified.desc(), Phone.id)
        .limit(1)
    ).first()
    return phone.e164 if phone else None


def reporter_from_form(
    session: Session, form: FormData, building_id: int, unit_id: int | None
) -> Reporter:
    if form.get("reporter") == "roster":
        raw = str(form.get("person", ""))
        unit_part, _, person_part = raw.partition("-")
        person_unit, person_id = _int(unit_part), _int(person_part)
        if person_unit is None or person_id is None:
            raise ClaimProblem("Elegí la persona del padrón.")
        found = session.scalars(
            select(UnitPerson)
            .join(Unit)
            .where(
                UnitPerson.unit_id == person_unit,
                UnitPerson.person_id == person_id,
                Unit.building_id == building_id,
            )
            .limit(1)
        ).first()
        if found is None:
            raise ClaimProblem("Esa persona no es de este edificio.")
        if unit_id is not None and person_unit != unit_id:
            raise ClaimProblem("Esa persona no es de la unidad elegida.")
        person = found.person
        return Reporter(
            name=person.full_name,
            phone_e164=_person_phone(session, person_id),
            person_id=person_id,
            unit_id=person_unit,
        )
    name = str(form.get("name", "")).strip()
    if not name:
        raise ClaimProblem("Escribí el nombre de quien reclamó.")
    raw_phone = str(form.get("phone", "")).strip()
    phone = to_e164(raw_phone) if raw_phone else None
    if raw_phone and phone is None:
        raise ClaimProblem(
            "Ese teléfono no es válido. Escribilo con el código de área, por ejemplo 351 555-0101."
        )
    return Reporter(name=name[:200], phone_e164=phone, unit_id=unit_id)


# --- Pages ----------------------------------------------------------------------------------


def _mode(form: Any) -> str:
    """The page's choice out of the providers' hours: "now" or "later" ("auto" otherwise)."""
    mode = str(form.get("when", ""))
    return mode if mode in ("now", "later") else "auto"


def _redirect(request: Request, identity: str, query: str = "", **params: Any) -> RedirectResponse:
    url = str(request.url_for(f"admin:view-{identity}", **params))
    return RedirectResponse(f"{url}?{query}" if query else url, status_code=302)


NO_WHATSAPP = "{provider} no tiene WhatsApp cargado: avisale por teléfono y marcalo como avisado."
NOT_CONFIGURED = "WhatsApp no está configurado en este servidor: no se mandó nada."


class ClaimsView(BaseView):
    name = "Reclamos"
    icon = "fa-solid fa-screwdriver-wrench"
    # In the bar at the bottom on phones (templates/sqladmin/layout.html), not in «Más».
    bottom_nav = True
    # The menu's counter (templates/sqladmin/_macros.html, app.admin.pending).
    menu_counter = "claims"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default
    # Builds the claims' WhatsApp messages (setup_admin; None in a server without WhatsApp).
    notifier_factory: ClassVar[Any] = None
    # The panel's clock (setup_admin; tests fix it): the providers' hours depend on it.
    clock: ClassVar[Any] = None

    def now(self) -> datetime:
        return self.clock() if self.clock else datetime.now(UTC)

    async def _page(
        self, request: Request, template: str, title: str, status_code: int = 200, **context: Any
    ) -> Response:
        return await self.templates.TemplateResponse(
            request, template, {"title": title, **context}, status_code=status_code
        )

    # The menu links to this page: keep it the first one of the class.
    @expose("/claims", methods=["GET"], identity="claims")
    async def claims_list(self, request: Request) -> Response:
        filters = read_filters(request.query_params)
        with self.session_maker() as session:
            result = list_claims(session, filters, self.now(), self.timezone)
            options = filter_options(session)
            stamp = claims_stamp(session)
        current = result.filters

        def link(**changes: Any) -> str:
            return "?" + urlencode(current.params(**changes))

        context = {"result": result, "tabs": TABS, "link": link}
        if request.query_params.get("partial") == "1":
            # Only the list, redrawn by panel.js when a claim changed.
            template = self.templates.env.get_template("_claims_list.html")
            html = await template.render_async({"request": request, **context})
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})
        fields = [
            ("building", "Edificio", options["buildings"], current.building),
            ("category", "Problema", options["categories"], current.category),
            ("attends", "Lo atiende", options["attends"], current.attends),
            ("status", "Estado", options["statuses"], current.status),
            ("urgent", "Urgente", options["urgent"], current.urgent),
            ("attention", "Atención", options["attention"], current.attention),
        ]
        return await self._page(
            request,
            "claims.html",
            "Reclamos",
            **context,
            filter_fields=fields,
            applied_filters=[label for _, label, _, value in fields if value],
            claims_stamp=stamp,
        )

    def _new_context(
        self, session: Session, source: Any, error: str | None = None, form: Any = None
    ) -> dict[str, Any]:
        """What «Nuevo reclamo» shows for the choices in source (the query, or the form sent
        back with an error): who reports, then the building's form."""
        conversation = from_conversation(session, _int(source.get("conversation")))
        mode = "other" if source.get("reporter") == "other" else "roster"
        chosen = None
        if mode == "roster":
            person = str(source.get("person", ""))
            unit_part, _, person_part = person.partition("-")
            unit_id = _int(source.get("unit_id")) if not person else _int(unit_part)
            person_id = _int(source.get("person_id")) if not person else _int(person_part)
            chosen = person_choice(session, person_id, unit_id)
            if chosen is None and conversation and len(conversation.choices) == 1:
                chosen = conversation.choices[0]
            if chosen is None and conversation and not conversation.choices:
                mode = "other"
            if chosen is None and source.get("building_id"):
                mode = "other"  # a building without a person: «No está en el padrón»
        building_id = chosen.building_id if chosen else _int(source.get("building_id"))
        building = session.get(Building, building_id) if building_id else None
        if building is not None and not building.active:
            building = None
        query = str(source.get("q", "")).strip()
        people = search_people(session, query) if query and not chosen else []
        if not chosen and not query and conversation and len(conversation.choices) > 1:
            people = conversation.choices
        context: dict[str, Any] = {
            "buildings": sorted(
                (
                    (b.id, formatting.building(b.name))
                    for b in session.scalars(select(Building).where(Building.active.is_(True)))
                ),
                key=lambda o: o[1],
            ),
            "mode": mode,
            "chosen": chosen,
            "building": building,
            "building_name": formatting.building(building.name) if building else "",
            "query": query,
            "people": people,
            "searched": bool(query) and not chosen,
            "conversation": conversation,
            "error": error,
            "form": form,
            "scope_labels": labels.CLAIM_SCOPE,
        }
        if building is not None:
            context["categories"] = enabled_categories(session, building.id)
            context["units"] = building_units(session, building.id)
        return context

    @expose("/claims/new", methods=["GET", "POST"], identity="claim-new")
    async def claim_new(self, request: Request) -> Response:
        form: FormData | None = await request.form() if request.method == "POST" else None
        error = None
        with self.session_maker() as session:
            if form is not None:
                created = self._create(session, request, form)
                if isinstance(created, Response):
                    return created
                error = created
            context = self._new_context(
                session, form if form is not None else request.query_params, error, form
            )
            return await self._page(
                request,
                "claim_new.html",
                "Nuevo reclamo",
                status_code=400 if error else 200,
                **context,
            )

    def _create(self, session: Session, request: Request, form: FormData) -> Response | str:
        """Load the claim (the redirect to it) or say why not (the page again, with it)."""
        user = admin_user(request)
        building = session.get(Building, _int(form.get("building_id")) or 0)
        conversation = from_conversation(session, _int(form.get("conversation")))
        try:
            if building is None:
                raise ClaimProblem("Elegí quién reclama o el edificio.")
            unit_id = _int(form.get("unit_id"))
            category_id = _int(form.get("category_id"))
            if category_id is None:
                raise ClaimProblem("Elegí el problema.")
            result = create_claim(
                session,
                building_id=building.id,
                category_id=category_id,
                unit_id=unit_id,
                description=str(form.get("description", "")),
                reporter=reporter_from_form(session, form, building.id, unit_id),
                source=ClaimSource.PANEL,
                user=user,
                wa_conversation_id=conversation.conversation_id if conversation else None,
                now=self.now(),
            )
        except ClaimProblem as exc:
            session.rollback()
            return str(exc)
        sent = None
        if not result.repeated:
            sent = self._send_to_provider(session, result.claim, user)
        number = result.claim.number
        if conversation is not None:
            # Commits it all: the claim and the note in the conversation it came from.
            add_conversation_note(
                session, conversation.conversation_id, user,
                claim_number_note(number, result.repeated),
            )  # fmt: skip
        session.commit()
        if isinstance(sent, tuple):
            (Flash.success if sent[0] else Flash.warning)(request, sent[1])
        if result.already_reporter:
            Flash.info(
                request,
                f"Ya había un reclamo abierto (#{number}) y esa persona ya estaba en ese: no se "
                "cargó otro.",
            )
        elif result.repeated:
            Flash.info(request, f"Ya había un reclamo abierto (#{number}): se sumó a ese.")
        else:
            Flash.success(request, f"Reclamo #{number} cargado.")
        return _redirect(request, "claim", claim_id=result.claim.id)

    @expose("/claims/people", methods=["GET"], identity="claims-people")
    async def claims_people(self, request: Request) -> Response:
        """«¿Quién reclama?»: the people found (a list of links to choose one)."""
        with self.session_maker() as session:
            query = request.query_params.get("q", "").strip()
            context = {
                "people": search_people(session, query),
                "searched": len(query) >= PEOPLE_MIN_CHARS,
                "conversation": from_conversation(
                    session, _int(request.query_params.get("conversation"))
                ),
            }
            return await self._fragment(request, "_claim_people.html", context)

    @expose("/claims/new/form", methods=["GET"], identity="claim-new-form")
    async def claim_new_form(self, request: Request) -> Response:
        """The rest of the form, for the chosen person or building (no reload)."""
        with self.session_maker() as session:
            context = self._new_context(session, request.query_params)
            return await self._fragment(request, "_claim_new_form.html", context)

    @expose("/claims/number/{number:int}", methods=["GET"], identity="claim-number")
    async def claim_by_number(self, request: Request) -> Response:
        """A claim by its number (the link of the notes «Se cargó el reclamo #N»)."""
        with self.session_maker() as session:
            claim_id = session.scalar(
                select(Claim.id).where(Claim.number == request.path_params["number"])
            )
        if claim_id is None:
            raise HTTPException(status_code=404)
        return _redirect(request, "claim", claim_id=claim_id)

    async def _fragment(self, request: Request, name: str, context: dict[str, Any]) -> Response:
        template = self.templates.env.get_template(name)
        html = await template.render_async({"request": request, **context})
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    def _detail_context(self, session: Session, claim: Claim) -> dict[str, Any]:
        """Everything the claim's page shows (the page, and its block redrawn after an action
        without reloading)."""
        page = claim_page(session, claim, self.now(), self.timezone)
        providers = session.scalars(
            select(Provider).where(Provider.active.is_(True)).order_by(Provider.name)
        ).all()
        wait_when = self._wait_when(session, claim)
        main, others = claim_actions(
            claim, scheduled=bool(page.scheduled), wait_when=wait_when,
            had_provider=page.had_provider,
        )  # fmt: skip
        return {
            "page": page,
            "main_action": main,
            "other_actions": others,
            "providers": providers,
            "studio_label": labels.STUDIO,
            "scope_labels": labels.CLAIM_SCOPE,
            "source_label": labels.label(labels.CLAIM_SOURCE, claim.source),
            "wait_when": wait_when,
            "previous_status": labels.label(labels.CLAIM_STATUS, claim.previous.status)
            if claim.previous
            else "",
        }

    @expose("/claims/{claim_id:int}", methods=["GET"], identity="claim")
    async def claim_detail(self, request: Request) -> Response:
        with self.session_maker() as session:
            claim = load_claim(session, request.path_params["claim_id"])
            if claim is None:
                raise HTTPException(status_code=404)
            context = self._detail_context(session, claim)
            return await self._page(request, "claim.html", f"Reclamo #{claim.number}", **context)

    def notifier(self) -> Notifier | None:
        """The claims' WhatsApp messages (None: WhatsApp is not set up in this server)."""
        return self.notifier_factory(now=self.now) if self.notifier_factory else None

    def _wait_when(self, session: Session, claim: Claim) -> str:
        """Out of the providers' hours, when they open next ("mañana a las 8:00"): the page
        then asks whether to send it now or then. "" inside them."""
        notifier = self.notifier() if claim.is_open else None
        opening = notifier.must_wait(session, claim) if notifier else None
        return when_text(opening, self.now(), self.timezone) if opening else ""

    async def _act(self, request: Request, action: Any, done: str) -> Response:
        """Apply one change to the claim through the service and go back to it. The action
        may return a second message (what happened with a WhatsApp notice). Asked by panel.js
        (app.admin.fetch), the same change answers with the page's block redrawn instead."""
        claim_id = request.path_params["claim_id"]
        form = await request.form()
        messages: list[tuple[str, str]] = []  # (level, text), as the Flashes of the page
        with self.session_maker() as session:
            claim = session.get(Claim, claim_id, with_for_update=True)
            if claim is None:
                raise HTTPException(status_code=404)
            try:
                extra = action(session, claim, form)
            except ClaimProblem as exc:
                session.rollback()
                messages.append(("error", str(exc)))
            else:
                session.commit()
                messages.append(("success", done))
                if isinstance(extra, tuple):
                    ok, text = extra
                    messages.append(("success" if ok else "warning", text))
            if wants_fetch(request):
                session.expire_all()
                claim = load_claim(session, claim_id)
                template = self.templates.env.get_template("_claim_detail.html")
                html = await template.render_async(
                    {"request": request, **self._detail_context(session, claim)}
                )
                ok = all(level == "success" for level, _ in messages)
                return fetch_reply(
                    ok, " ".join(text for _, text in messages), replace={"#claim-page": html}
                )
        flash = {"success": Flash.success, "warning": Flash.warning, "error": Flash.error}
        for level, text in messages:
            flash[level](request, text)
        return _redirect(request, "claim", claim_id=claim_id)

    def _send_to_provider(
        self, session: Session, claim: Claim, user: str, mode: str = "auto"
    ) -> Any:
        """After a change: a claim waiting for its provider (with WhatsApp) goes to it, or,
        out of the providers' hours, is scheduled for their next opening (mode: "auto", or
        the page's choice "now" / "later")."""
        provider = claim.provider
        if claim.status != ClaimStatus.PENDING_SEND or provider is None:
            return None
        if not provider.whatsapp_e164:
            return False, NO_WHATSAPP.format(provider=provider.name)
        notifier = self.notifier()
        if notifier is None:
            return False, NOT_CONFIGURED
        done = notifier.dispatch(session, claim, user, mode=mode)
        if done.sent is None:
            return True, f"Se le va a mandar a {provider.name} {done.when}."
        if done.sent.ok:
            return True, f"Se le mandó el reclamo por WhatsApp a {provider.name}."
        return False, f"No se le pudo mandar a {provider.name}: {done.sent.error}."

    def _tell_neighbors(self, session: Session, claim: Claim) -> Any:
        notifier = self.notifier()
        if notifier is None:
            return False, NOT_CONFIGURED
        sent = notifier.notify_neighbors(session, claim, "solved")
        if not sent:
            return None
        went = sum(s.ok for s in sent)
        if went == len(sent):
            return True, "Se les avisó a los vecinos que está solucionado."
        return False, f"Se les avisó a {went} de {len(sent)} vecinos: mirá la historia."

    @expose("/claims/{claim_id:int}/resend", methods=["POST"], identity="claim-resend")
    async def claim_resend(self, request: Request) -> Response:
        user = admin_user(request)

        def resend(session: Session, claim: Claim, form: Any) -> Any:
            if claim.status != ClaimStatus.PENDING_SEND or claim.provider is None:
                raise ClaimProblem("Este reclamo no está esperando que se le avise al proveedor.")
            return self._send_to_provider(session, claim, user, _mode(form))

        return await self._act(request, resend, "Listo.")

    @expose("/claims/{claim_id:int}/mark-sent", methods=["POST"], identity="claim-mark-sent")
    async def claim_mark_sent(self, request: Request) -> Response:
        who = self._who(request)

        def mark(session: Session, claim: Claim, form: Any) -> None:
            if claim.provider is None:
                raise ClaimProblem("Lo atiende el estudio: no hay proveedor a quien avisarle.")
            mark_sent(session, claim, text=f"Avisado a {claim.provider.name} por teléfono", **who)

        return await self._act(request, mark, "Marcado como avisado.")

    def _who(self, request: Request) -> dict[str, Any]:
        return {"actor": ClaimActor.PANEL, "user": admin_user(request), "now": self.now()}

    @expose("/claims/{claim_id:int}/provider", methods=["POST"], identity="claim-provider")
    async def claim_provider(self, request: Request) -> Response:
        who = self._who(request)
        return await self._act(
            request,
            lambda s, c, f: (
                change_provider(s, c, _int(f.get("provider_id")), **who),
                self._send_to_provider(s, c, who["user"], _mode(f)),
            )[1],
            "Listo: cambió quién lo atiende.",
        )

    @expose("/claims/{claim_id:int}/close", methods=["POST"], identity="claim-close")
    async def claim_close(self, request: Request) -> Response:
        who = self._who(request)
        return await self._act(
            request,
            lambda s, c, f: (
                close_claim(s, c, ClaimStatus.SOLVED, str(f.get("reason", "")), **who),
                self._tell_neighbors(s, c),
            )[1],
            "Reclamo cerrado como solucionado.",
        )

    @expose("/claims/{claim_id:int}/cancel", methods=["POST"], identity="claim-cancel")
    async def claim_cancel(self, request: Request) -> Response:
        who = self._who(request)
        return await self._act(
            request,
            lambda s, c, f: close_claim(
                s, c, ClaimStatus.CANCELLED, str(f.get("reason", "")), **who
            ),
            "Reclamo cancelado.",
        )

    @expose("/claims/{claim_id:int}/note", methods=["POST"], identity="claim-note")
    async def claim_note(self, request: Request) -> Response:
        who = self._who(request)
        return await self._act(
            request,
            lambda s, c, f: add_note(s, c, str(f.get("text", "")), **who),
            "Nota guardada.",
        )
