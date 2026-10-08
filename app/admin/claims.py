"""Panel section "Reclamos", for operators and admins:

- GET  /admin/claims                      the list: tabs Abiertos / Cerrados / Todos, filters,
                                          search (urgent open ones first, then the newest)
- GET  /admin/claims/poll                 the urgent open claims (JSON): the menu's counter and
                                          the alert of a new one (panel.js; "Conversaciones"
                                          gets them in its own poll)
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

from sqladmin import BaseView, expose
from sqladmin.flash import Flash
from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.admin import formatting, labels
from app.admin.auth import admin_user, display_names
from app.bot.identity import to_e164
from app.claims.notify import Notifier
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
    Claim,
    ClaimActor,
    ClaimAttachment,
    ClaimCategory,
    ClaimEvent,
    ClaimReporter,
    ClaimSource,
    ClaimStatus,
    Phone,
    Provider,
    Unit,
    UnitPerson,
    WaContact,
    WaConversation,
)
from app.whatsapp.media import INLINE_TYPES, base_mime

PAGE_SIZE = 50
# How often the claims' pages ask for new urgent claims (seconds).
POLL_SECONDS = 10
TABS = {"open": "Abiertos", "closed": "Cerrados", "all": "Todos"}
TAB_STATUSES = {"open": OPEN_CLAIM_STATUSES, "closed": CLOSED_CLAIM_STATUSES}
STUDIO_FILTER = "studio"
URGENT_FILTER = {"yes": "Urgentes", "no": "No urgentes"}
WHOLE_BUILDING = "Todo el edificio"


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
    page: int = 1

    @property
    def any_filter(self) -> bool:
        return bool(self.building or self.category or self.attends or self.status or self.urgent)

    def params(self, **changes: Any) -> dict[str, Any]:
        values = {
            "tab": self.tab, "q": self.query, "building": self.building,
            "category": self.category, "attends": self.attends, "status": self.status,
            "urgent": self.urgent, "page": self.page if self.page > 1 else None,
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
    status_label: str
    group: str
    ago: str
    urgent: bool


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
            status_label=labels.label(labels.CLAIM_STATUS, c.status),
            group=labels.CLAIM_STATUS_GROUP[ClaimStatus(c.status)],
            ago=formatting.ago(c.status_at, now),
            urgent=c.urgent,
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
    }


# Open claims someone of the studio should look at now: urgent, or the provider cannot
# attend it, or it could not be sent to the provider.
NEEDS_SOMEONE = Claim.status.in_(OPEN_CLAIM_STATUSES) & (
    Claim.urgent.is_(True) | Claim.attention.is_not(None)
)


def _alert_title(claim: Claim) -> str:
    if claim.attention is not None:
        return f"{labels.label(labels.CLAIM_ATTENTION, claim.attention)} · #{claim.number}"
    return f"Reclamo urgente #{claim.number}"


def urgent_open_claims(session: Session) -> list[dict[str, Any]]:
    """The open claims that need someone (urgent, or with an alert), newest first: the
    panel's sound and notification, and the menu's counter. The key includes the alert, so a
    claim that gets one later alerts again."""
    claims = session.scalars(
        select(Claim)
        .where(NEEDS_SOMEONE)
        .options(selectinload(Claim.building), selectinload(Claim.category))
        .order_by(Claim.created_at.desc(), Claim.id.desc())
    ).all()
    return [
        {
            "id": f"{c.id}:{c.attention or 'urgent'}",
            "number": c.number,
            "title": _alert_title(c),
            "problem": c.category.list_title,
            "building": formatting.building(c.building.name),
        }
        for c in claims
    ]


def urgent_open_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Claim).where(NEEDS_SOMEONE)) or 0


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
    status_label: str
    group: str
    attends: str
    reporter: PersonLine
    joined: list[PersonLine] = field(default_factory=list)
    photos: list[Photo] = field(default_factory=list)
    events: list[EventLine] = field(default_factory=list)


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
        status_label=labels.label(labels.CLAIM_STATUS, claim.status),
        group=labels.CLAIM_STATUS_GROUP[ClaimStatus(claim.status)],
        attends=attends_text(claim),
        reporter=person(reporter_text(claim), claim.reporter_phone_e164, claim.reporter_unit),
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


# --- "Nuevo reclamo" ------------------------------------------------------------------------


@dataclass(frozen=True)
class RosterPerson:
    value: str  # "<unit id>-<person id>"
    name: str
    role: str


def roster(session: Session, building_id: int) -> list[tuple[Unit, list[RosterPerson]]]:
    """The active units of the building with their people (for "Del padrón")."""
    units = session.scalars(
        select(Unit)
        .where(Unit.building_id == building_id, Unit.active.is_(True))
        .options(selectinload(Unit.people).selectinload(UnitPerson.person))
    ).all()
    result = []
    for unit in sorted(units, key=lambda u: u.label):
        people = [
            RosterPerson(
                value=f"{unit.id}-{link.person_id}",
                name=link.person.full_name,
                role=labels.label(labels.PERSON_ROLE, link.role),
            )
            for link in sorted(unit.people, key=lambda link: (link.role, link.person.full_name))
        ]
        result.append((unit, people))
    return result


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


def _redirect(request: Request, identity: str, query: str = "", **params: Any) -> RedirectResponse:
    url = str(request.url_for(f"admin:view-{identity}", **params))
    return RedirectResponse(f"{url}?{query}" if query else url, status_code=302)


NO_WHATSAPP = "{provider} no tiene WhatsApp cargado: avisale por teléfono y marcalo como avisado."
NOT_CONFIGURED = "WhatsApp no está configurado en este servidor: no se mandó nada."


class ClaimsView(BaseView):
    name = "Reclamos"
    icon = "fa-solid fa-screwdriver-wrench"
    # The menu shows the urgent open claims next to its name (templates/sqladmin/_macros.html).
    menu_badge = "urgent_claims"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default
    # Builds the claims' WhatsApp messages (setup_admin; None in a server without WhatsApp).
    notifier_factory: ClassVar[Any] = None

    def now(self) -> datetime:
        return datetime.now(UTC)

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
        current = result.filters

        def link(**changes: Any) -> str:
            return "?" + urlencode(current.params(**changes))

        return await self._page(
            request,
            "claims.html",
            "Reclamos",
            result=result,
            tabs=TABS,
            options=options,
            link=link,
            poll_seconds=POLL_SECONDS,
        )

    @expose("/claims/poll", methods=["GET"], identity="claims-poll")
    async def claims_poll(self, request: Request) -> Response:
        with self.session_maker() as session:
            urgent = urgent_open_claims(session)
        return JSONResponse({"urgent_claims": urgent}, headers={"Cache-Control": "no-store"})

    @expose("/claims/new", methods=["GET", "POST"], identity="claim-new")
    async def claim_new(self, request: Request) -> Response:
        form: FormData | None = await request.form() if request.method == "POST" else None
        source = form if form is not None else request.query_params
        building_id = _int(source.get("building_id"))
        error = None
        with self.session_maker() as session:
            building = session.get(Building, building_id) if building_id else None
            if form is not None and building is not None:
                try:
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
                        user=admin_user(request),
                        now=self.now(),
                    )
                except ClaimProblem as exc:
                    session.rollback()
                    error = str(exc)
                else:
                    sent = None
                    if not result.repeated:
                        sent = self._send_to_provider(session, result.claim, admin_user(request))
                    session.commit()
                    if isinstance(sent, tuple):
                        (Flash.success if sent[0] else Flash.warning)(request, sent[1])
                    number = result.claim.number
                    if result.already_reporter:
                        Flash.info(
                            request,
                            f"Ya había un reclamo abierto (#{number}) y esa persona ya estaba en "
                            "ese: no se cargó otro.",
                        )
                    elif result.repeated:
                        Flash.info(
                            request, f"Ya había un reclamo abierto (#{number}): se sumó a ese."
                        )
                    else:
                        Flash.success(request, f"Reclamo #{number} cargado.")
                    return _redirect(request, "claim", claim_id=result.claim.id)
            buildings = sorted(
                (
                    (b.id, formatting.building(b.name))
                    for b in session.scalars(select(Building).where(Building.active.is_(True)))
                ),
                key=lambda o: o[1],
            )
            context: dict[str, Any] = {
                "buildings": buildings,
                "building": building,
                "building_name": formatting.building(building.name) if building else "",
                "error": error,
                "form": form,
                "scope_labels": labels.CLAIM_SCOPE,
            }
            if building is not None:
                context["categories"] = enabled_categories(session, building.id)
                context["roster"] = roster(session, building.id)
            return await self._page(
                request,
                "claim_new.html",
                "Nuevo reclamo",
                status_code=400 if error else 200,
                **context,
            )

    @expose("/claims/{claim_id:int}", methods=["GET"], identity="claim")
    async def claim_detail(self, request: Request) -> Response:
        with self.session_maker() as session:
            claim = load_claim(session, request.path_params["claim_id"])
            if claim is None:
                return Response("Reclamo inexistente", status_code=404)
            page = claim_page(session, claim, self.now(), self.timezone)
            providers = session.scalars(
                select(Provider).where(Provider.active.is_(True)).order_by(Provider.name)
            ).all()
            return await self._page(
                request,
                "claim.html",
                f"Reclamo #{claim.number}",
                page=page,
                providers=providers,
                studio_label=labels.STUDIO,
                scope_labels=labels.CLAIM_SCOPE,
                source_label=labels.label(labels.CLAIM_SOURCE, claim.source),
                attention_label=labels.label(labels.CLAIM_ATTENTION, claim.attention, ""),
                previous_status=labels.label(labels.CLAIM_STATUS, claim.previous.status)
                if claim.previous
                else "",
            )

    def notifier(self) -> Notifier | None:
        """The claims' WhatsApp messages (None: WhatsApp is not set up in this server)."""
        return self.notifier_factory() if self.notifier_factory else None

    async def _act(self, request: Request, action: Any, done: str) -> Response:
        """Apply one change to the claim through the service and go back to it. The action
        may return a second message (what happened with a WhatsApp notice)."""
        claim_id = request.path_params["claim_id"]
        form = await request.form()
        with self.session_maker() as session:
            claim = session.get(Claim, claim_id, with_for_update=True)
            if claim is None:
                return Response("Reclamo inexistente", status_code=404)
            try:
                extra = action(session, claim, form)
            except ClaimProblem as exc:
                session.rollback()
                Flash.error(request, str(exc))
            else:
                session.commit()
                Flash.success(request, done)
                if isinstance(extra, tuple):
                    ok, text = extra
                    (Flash.success if ok else Flash.warning)(request, text)
        return _redirect(request, "claim", claim_id=claim_id)

    def _send_to_provider(self, session: Session, claim: Claim, user: str) -> Any:
        """After a change: a claim waiting for its provider (with WhatsApp) goes to it."""
        provider = claim.provider
        if claim.status != ClaimStatus.PENDING_SEND or provider is None:
            return None
        if not provider.whatsapp_e164:
            return False, NO_WHATSAPP.format(provider=provider.name)
        notifier = self.notifier()
        if notifier is None:
            return False, NOT_CONFIGURED
        sent = notifier.notify_provider(session, claim, user)
        if sent.ok:
            return True, f"Se le mandó el reclamo por WhatsApp a {provider.name}."
        return False, f"No se le pudo mandar a {provider.name}: {sent.error}."

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
            return self._send_to_provider(session, claim, user)

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
                self._send_to_provider(s, c, who["user"]),
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
