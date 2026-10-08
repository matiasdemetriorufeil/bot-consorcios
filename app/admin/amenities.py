"""Panel pages of the SUM ("Reservas de SUM").

SQLAdmin has no weekly grid, so these are BaseView pages with their own templates, inside the
panel (same login, menu and style). Operators see the list and the weekly agenda, book and
cancel; adding a SUM and its set-up (rules, limits, switches, slots) are for admins only
(require_admin, checked by each of those pages). Rules live in app.amenities.booking, never
here: the panel only collects the operator's choice and shows the result. Every change is
audited in bot_events (admin_action) with ids and field names, never texts.

SQLAdmin names each page "admin:view-<identity>" and links the menu to the first page of
the class (amenities_list must stay first).
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from sqladmin import BaseView, expose
from sqladmin.flash import Flash
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.admin import formatting, labels
from app.admin.agenda import build_agenda
from app.admin.audit import log_admin_action
from app.admin.auth import admin_user, is_admin, require_admin
from app.amenities.booking import (
    POLICY_RULES,
    BookingError,
    availability,
    book,
    cancel,
    check_booking,
    describe_slot,
    problem_message,
)
from app.config import Settings
from app.db.models import (
    Amenity,
    AmenitySlot,
    Building,
    Reservation,
    ReservationSource,
    ReservationStatus,
    Unit,
)

WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
WEEKDAYS_SHORT = ["Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"]
# Config form: (field, label, minimum). max_per_unit_per_month may be blank (no limit).
NUMBER_FIELDS = (
    ("min_advance_hours", "Anticipación mínima (horas)", 0),
    ("max_advance_days", "Anticipación máxima (días)", 1),
    ("max_per_unit_per_month", "Reservas por unidad por mes", 1),
    ("cancel_until_hours", "Cancelación hasta (horas antes)", 0),
)
FLAG_FIELDS = ("active", "bot_booking_enabled", "blocks_debtors")


class FormProblem(ValueError):
    """What is wrong in a form, in Spanish (shown as is)."""


def _redirect(request: Request, identity: str, **params: Any) -> RedirectResponse:
    query = params.pop("query", None)
    url = request.url_for(f"admin:view-{identity}", **params)
    return RedirectResponse(f"{url}?{query}" if query else str(url), status_code=302)


def _int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _time(value: Any) -> time | None:
    try:
        return time.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _weekdays(form: FormData, name: str) -> list[int]:
    days = {d for v in form.getlist(name) if (d := _int(v)) is not None and 0 <= d <= 6}
    return sorted(days)


def building_name(amenity: Amenity) -> str:
    return formatting.building(amenity.building.name)


# --- Changes (sync, one transaction each) ---------------------------------------------------


def create_amenity(session: Session, building_id: int, user: str) -> Amenity:
    building = session.get(Building, building_id)
    if building is None:
        raise FormProblem("Elegí un edificio.")
    if session.scalar(select(Amenity.id).where(Amenity.building_id == building_id)):
        raise FormProblem(f"{formatting.building(building.name)} ya tiene SUM.")
    amenity = Amenity(building_id=building_id)
    session.add(amenity)
    session.flush()
    log_admin_action(
        session, user, "amenity_created", amenity_id=amenity.id, building_id=building_id
    )
    session.commit()
    return amenity


def update_amenity(session: Session, amenity: Amenity, form: FormData, user: str) -> list[str]:
    """Apply the config form; returns the names of the changed fields."""
    values: dict[str, Any] = {}
    name = str(form.get("name", "")).strip()
    if not name:
        raise FormProblem("El nombre no puede quedar vacío.")
    values["name"] = name[:100]
    values["rules_text"] = str(form.get("rules_text", "")).strip() or None
    for field, label, minimum in NUMBER_FIELDS:
        raw = str(form.get(field, "")).strip()
        if field == "max_per_unit_per_month" and not raw:
            values[field] = None  # no limit
            continue
        number = _int(raw)
        if number is None or number < minimum:
            raise FormProblem(f"{label}: tiene que ser un número entero desde {minimum}.")
        values[field] = number
    for field in FLAG_FIELDS:
        values[field] = form.get(field) == "on"
    changed = sorted(f for f, v in values.items() if getattr(amenity, f) != v)
    for field in changed:
        setattr(amenity, field, values[field])
    if changed:
        log_admin_action(session, user, "amenity_updated", amenity_id=amenity.id, fields=changed)
    session.commit()
    return changed


def _same_slot(slot: AmenitySlot, weekday: int, start: time, end: time) -> bool:
    return slot.active and (slot.weekday, slot.start_time, slot.end_time) == (weekday, start, end)


def add_slots(
    session: Session, amenity: Amenity, weekdays: list[int], start: time, end: time,
    label: str, user: str,
) -> list[AmenitySlot]:  # fmt: skip
    """The same slot on each weekday (skips days that already have it)."""
    if not weekdays:
        raise FormProblem("Elegí al menos un día.")
    if start == end:
        raise FormProblem("El turno tiene que empezar y terminar a distinta hora.")
    added = []
    for weekday in weekdays:
        if any(_same_slot(s, weekday, start, end) for s in amenity.slots):
            continue
        slot = AmenitySlot(
            weekday=weekday, start_time=start, end_time=end, label=label.strip()[:50] or None
        )
        amenity.slots.append(slot)
        added.append(slot)
    session.flush()
    if added:
        log_admin_action(
            session,
            user,
            "amenity_slots_added",
            amenity_id=amenity.id,
            slot_ids=[s.id for s in added],
        )
    session.commit()
    return added


def copy_slots(
    session: Session, amenity: Amenity, source: int, targets: list[int], user: str
) -> list[AmenitySlot]:
    """Every active slot of the source weekday onto the target weekdays (missing ones only)."""
    targets = [t for t in targets if t != source]
    if not targets:
        raise FormProblem("Elegí al menos un día de destino distinto del de origen.")
    originals = [s for s in amenity.slots if s.active and s.weekday == source]
    if not originals:
        raise FormProblem(f"El {WEEKDAYS[source].lower()} no tiene turnos para copiar.")
    added = []
    for weekday in targets:
        for original in originals:
            if any(
                _same_slot(s, weekday, original.start_time, original.end_time)
                for s in amenity.slots
            ):
                continue
            slot = AmenitySlot(
                weekday=weekday,
                start_time=original.start_time,
                end_time=original.end_time,
                label=original.label,
            )
            amenity.slots.append(slot)
            added.append(slot)
    session.flush()
    if added:
        log_admin_action(
            session,
            user,
            "amenity_slots_copied",
            amenity_id=amenity.id,
            from_weekday=source,
            to_weekdays=targets,
            slot_ids=[s.id for s in added],
        )
    session.commit()
    return added


def remove_slot(session: Session, amenity: Amenity, slot_id: int, today: date, user: str) -> bool:
    """Delete a slot; one with reservations stays, inactive (history keeps its times).
    Returns whether it was kept inactive. Refused while it has future confirmed ones."""
    slot = session.get(AmenitySlot, slot_id)
    if slot is None or slot.amenity_id != amenity.id or not slot.active:
        raise FormProblem("Ese turno no existe.")
    future = session.scalar(
        select(func.count(Reservation.id)).where(
            Reservation.slot_id == slot.id,
            Reservation.status == ReservationStatus.CONFIRMED,
            Reservation.date >= today,
        )
    )
    if future:
        raise FormProblem(
            f"El turno tiene {future} reserva(s) confirmada(s) a futuro: cancelalas antes."
        )
    used = session.scalar(select(Reservation.id).where(Reservation.slot_id == slot.id).limit(1))
    if used:
        slot.active = False
    else:
        amenity.slots.remove(slot)
    log_admin_action(
        session,
        user,
        "amenity_slot_removed",
        amenity_id=amenity.id,
        slot_id=slot_id,
        kept_inactive=bool(used),
    )
    session.commit()
    return bool(used)


# --- Pages ----------------------------------------------------------------------------------


class AmenitiesView(BaseView):
    name = "Reservas de SUM"
    icon = "fa-solid fa-calendar-days"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default

    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return self.now().astimezone(ZoneInfo(self.timezone)).date()

    def _amenity(self, session: Session, amenity_id: int) -> Amenity | None:
        return session.scalar(
            select(Amenity)
            .where(Amenity.id == amenity_id)
            .options(selectinload(Amenity.building), selectinload(Amenity.slots))
        )

    async def _page(
        self, request: Request, template: str, title: str, status_code: int = 200, **context: Any
    ) -> Response:
        return await self.templates.TemplateResponse(
            request,
            template,
            {
                "title": title,
                "weekdays": WEEKDAYS,
                "weekdays_short": WEEKDAYS_SHORT,
                "is_admin": is_admin(request),
                **context,
            },
            status_code=status_code,
        )

    # The menu links to this page: keep it the first one of the class.
    @expose("/amenities", methods=["GET"], identity="amenities")
    async def amenities_list(self, request: Request) -> Response:
        today = self.today()
        with self.session_maker() as session:
            amenities = list(
                session.scalars(
                    select(Amenity).options(
                        selectinload(Amenity.building), selectinload(Amenity.slots)
                    )
                )
            )
            upcoming = dict(
                session.execute(
                    select(Reservation.amenity_id, func.count(Reservation.id))
                    .where(
                        Reservation.status == ReservationStatus.CONFIRMED,
                        Reservation.date.between(today, today + timedelta(days=6)),
                    )
                    .group_by(Reservation.amenity_id)
                ).all()
            )
            rows = sorted(
                (
                    {
                        "amenity": a,
                        "building": building_name(a),
                        "slots": sum(1 for s in a.slots if s.active),
                        "upcoming": upcoming.get(a.id, 0),
                    }
                    for a in amenities
                ),
                key=lambda r: r["building"],
            )
            with_amenity = {a.building_id for a in amenities}
            buildings = [
                (b.id, formatting.building(b.name))
                for b in session.scalars(
                    select(Building).where(Building.active.is_(True)).order_by(Building.name)
                )
                if b.id not in with_amenity
            ]
            return await self._page(
                request, "amenities_list.html", "Reservas de SUM", rows=rows, buildings=buildings
            )

    @expose("/amenities/new", methods=["POST"], identity="amenity-new")
    async def amenity_new(self, request: Request) -> Response:
        require_admin(request)  # the SUM's set-up: admins only
        form = await request.form()
        with self.session_maker() as session:
            try:
                amenity = create_amenity(
                    session, _int(form.get("building_id")) or 0, admin_user(request)
                )
            except FormProblem as exc:
                Flash.error(request, str(exc))
                return _redirect(request, "amenities")
            Flash.success(request, "SUM agregado: cargá las reglas y los turnos.")
            return _redirect(request, "amenity-config", amenity_id=amenity.id)

    @expose(
        "/amenities/{amenity_id:int}/config", methods=["GET", "POST"], identity="amenity-config"
    )
    async def amenity_config(self, request: Request) -> Response:
        require_admin(request)  # the SUM's set-up: admins only
        amenity_id = request.path_params["amenity_id"]
        error = None
        with self.session_maker() as session:
            amenity = self._amenity(session, amenity_id)
            if amenity is None:
                return Response("SUM inexistente", status_code=404)
            if request.method == "POST":
                try:
                    changed = update_amenity(
                        session, amenity, await request.form(), admin_user(request)
                    )
                except FormProblem as exc:
                    session.rollback()
                    error = str(exc)
                else:
                    Flash.success(
                        request, "Configuración guardada." if changed else "No había cambios."
                    )
                    return _redirect(request, "amenity-config", amenity_id=amenity_id)
            by_day = [[s for s in amenity.slots if s.active and s.weekday == d] for d in range(7)]
            return await self._page(
                request,
                "amenity_config.html",
                f"{amenity.name} · {building_name(amenity)}",
                status_code=400 if error else 200,
                amenity=amenity,
                by_day=by_day,
                describe_slot=describe_slot,
                number_fields=NUMBER_FIELDS,
                error=error,
            )

    @expose("/amenities/{amenity_id:int}/slots", methods=["POST"], identity="amenity-slots")
    async def amenity_slots(self, request: Request) -> Response:
        require_admin(request)  # the SUM's set-up: admins only
        amenity_id = request.path_params["amenity_id"]
        form = await request.form()
        with self.session_maker() as session:
            amenity = self._amenity(session, amenity_id)
            if amenity is None:
                return Response("SUM inexistente", status_code=404)
            user = admin_user(request)
            try:
                if form.get("copy_from") is not None:
                    source = _int(form.get("copy_from"))
                    if source is None or not 0 <= source <= 6:
                        raise FormProblem("Elegí el día de origen.")
                    added = copy_slots(session, amenity, source, _weekdays(form, "to"), user)
                else:
                    start, end = _time(form.get("start")), _time(form.get("end"))
                    if start is None or end is None:
                        raise FormProblem("Indicá la hora de inicio y de fin del turno.")
                    added = add_slots(
                        session,
                        amenity,
                        _weekdays(form, "weekday"),
                        start,
                        end,
                        str(form.get("label", "")),
                        user,
                    )
            except FormProblem as exc:
                session.rollback()
                Flash.error(request, str(exc))
            else:
                Flash.success(
                    request,
                    f"Turnos agregados: {len(added)}." if added else "Esos turnos ya existían.",
                )
            return _redirect(request, "amenity-config", amenity_id=amenity_id)

    @expose(
        "/amenities/{amenity_id:int}/slots/{slot_id:int}/remove",
        methods=["POST"],
        identity="amenity-slot-remove",
    )
    async def amenity_slot_remove(self, request: Request) -> Response:
        require_admin(request)  # the SUM's set-up: admins only
        amenity_id = request.path_params["amenity_id"]
        with self.session_maker() as session:
            amenity = self._amenity(session, amenity_id)
            if amenity is None:
                return Response("SUM inexistente", status_code=404)
            try:
                remove_slot(
                    session,
                    amenity,
                    request.path_params["slot_id"],
                    self.today(),
                    admin_user(request),
                )
            except FormProblem as exc:
                session.rollback()
                Flash.error(request, str(exc))
            else:
                Flash.success(request, "Turno eliminado.")
            return _redirect(request, "amenity-config", amenity_id=amenity_id)

    @expose("/amenities/{amenity_id:int}/week", methods=["GET"], identity="amenity-week")
    async def amenity_week(self, request: Request) -> Response:
        """The weekly agenda (app.admin.agenda). ?start: any date of the week; ?day: the day
        shown on phones (0 = Monday), by default today if it is in that week."""
        amenity_id = request.path_params["amenity_id"]
        today = self.today()
        asked = _date(request.query_params.get("start")) or today
        monday = asked - timedelta(days=asked.weekday())
        days = [monday + timedelta(days=i) for i in range(7)]
        phone_day = _int(request.query_params.get("day"))
        if phone_day is None or not 0 <= phone_day <= 6:
            phone_day = today.weekday() if today in days else 0
        with self.session_maker() as session:
            amenity = self._amenity(session, amenity_id)
            if amenity is None:
                return Response("SUM inexistente", status_code=404)
            cells = availability(
                session, amenity, days[0], days[-1], now=self.now(), timezone=self.timezone
            )
            units = {
                u.id: u.label
                for u in session.scalars(
                    select(Unit).where(
                        Unit.id.in_({c.reservation.unit_id for c in cells if c.reservation})
                    )
                )
            }
            agenda = build_agenda(
                days,
                cells,
                amenity.slots,
                today=today,
                units=units,
                message=lambda problem: problem_message(problem, amenity),
            )

            def week_link(first_day: date, day: int) -> str:
                return f"?start={first_day.isoformat()}&day={day}"

            previous_day = (
                week_link(monday, phone_day - 1)
                if phone_day > 0
                else week_link(monday - timedelta(days=7), 6)
            )
            next_day = (
                week_link(monday, phone_day + 1)
                if phone_day < 6
                else week_link(monday + timedelta(days=7), 0)
            )
            this_monday = today - timedelta(days=today.weekday())
            return await self._page(
                request,
                "amenity_week.html",
                f"{amenity.name} · {building_name(amenity)}",
                amenity=amenity,
                agenda=agenda,
                phone_day=phone_day,
                previous_week=week_link(monday - timedelta(days=7), phone_day),
                next_week=week_link(monday + timedelta(days=7), phone_day),
                this_week=week_link(this_monday, today.weekday()),
                previous_day=previous_day,
                next_day=next_day,
            )

    @expose("/amenities/{amenity_id:int}/book", methods=["GET", "POST"], identity="amenity-book")
    async def amenity_book(self, request: Request) -> Response:
        amenity_id = request.path_params["amenity_id"]
        source = await request.form() if request.method == "POST" else request.query_params
        slot_id, day = _int(source.get("slot")), _date(source.get("date"))
        with self.session_maker() as session:
            amenity = self._amenity(session, amenity_id)
            if amenity is None:
                return Response("SUM inexistente", status_code=404)
            slot = session.get(AmenitySlot, slot_id) if slot_id else None
            if slot is None or slot.amenity_id != amenity.id or day is None:
                return Response("Turno inexistente", status_code=404)
            error: BookingError | None = None
            unit_id = _int(source.get("unit_id"))
            notes = str(source.get("notes", ""))
            if request.method == "POST":
                if unit_id is None:
                    error = BookingError([], ["Elegí la unidad."])
                else:
                    override = POLICY_RULES if source.get("override") == "yes" else frozenset()
                    # Which policy rules this reservation skips (for the audit).
                    skipped = sorted(
                        p.value
                        for p in check_booking(
                            session,
                            amenity,
                            slot,
                            day,
                            session.get(Unit, unit_id),
                            now=self.now(),
                            timezone=self.timezone,
                        )
                        if p in override
                    )
                    try:
                        reservation = book(
                            session,
                            amenity,
                            slot.id,
                            day,
                            unit_id,
                            now=self.now(),
                            timezone=self.timezone,
                            source=ReservationSource.PANEL,
                            notes=notes,
                            override=override,
                        )
                    except BookingError as exc:
                        session.rollback()
                        error = exc
                    else:
                        log_admin_action(
                            session,
                            admin_user(request),
                            "reservation_created",
                            amenity_id=amenity.id,
                            reservation_id=reservation.id,
                            slot_id=slot.id,
                            date=day.isoformat(),
                            unit_id=unit_id,
                            overridden=skipped,
                        )
                        session.commit()
                        Flash.success(request, "Reserva confirmada.")
                        return _redirect(
                            request,
                            "amenity-week",
                            amenity_id=amenity.id,
                            query=f"start={day.isoformat()}",
                        )
            units = list(
                session.scalars(
                    select(Unit)
                    .where(Unit.building_id == amenity.building_id, Unit.active.is_(True))
                    .order_by(Unit.label)
                )
            )
            return await self._page(
                request,
                "amenity_book.html",
                f"Reservar · {amenity.name} · {building_name(amenity)}",
                status_code=400 if error else 200,
                amenity=amenity,
                slot=slot,
                day=day,
                slot_text=describe_slot(slot),
                units=units,
                unit_id=unit_id,
                notes=notes,
                error=error,
            )

    @expose(
        "/amenities/{amenity_id:int}/reservations/{reservation_id:int}",
        methods=["GET", "POST"],
        identity="amenity-reservation",
    )
    async def amenity_reservation(self, request: Request) -> Response:
        amenity_id = request.path_params["amenity_id"]
        with self.session_maker() as session:
            reservation = session.scalar(
                select(Reservation)
                .where(
                    Reservation.id == request.path_params["reservation_id"],
                    Reservation.amenity_id == amenity_id,
                )
                .options(
                    selectinload(Reservation.unit),
                    selectinload(Reservation.slot),
                    selectinload(Reservation.amenity).selectinload(Amenity.building),
                )
            )
            if reservation is None:
                return Response("Reserva inexistente", status_code=404)
            error = None
            if request.method == "POST":
                form = await request.form()
                if form.get("confirm") != "yes":
                    error = "Confirmá la cancelación."
                else:
                    try:
                        cancel(
                            session,
                            reservation,
                            now=self.now(),
                            timezone=self.timezone,
                            by_panel=True,
                        )
                    except BookingError as exc:
                        session.rollback()
                        error = str(exc)
                    else:
                        log_admin_action(
                            session,
                            admin_user(request),
                            "reservation_cancelled",
                            amenity_id=amenity_id,
                            reservation_id=reservation.id,
                        )
                        session.commit()
                        Flash.success(request, "Reserva cancelada: el turno quedó libre.")
                        return _redirect(
                            request,
                            "amenity-week",
                            amenity_id=amenity_id,
                            query=f"start={reservation.date.isoformat()}",
                        )
            return await self._page(
                request,
                "amenity_reservation.html",
                f"Reserva · {reservation.amenity.name} · {building_name(reservation.amenity)}",
                status_code=400 if error else 200,
                reservation=reservation,
                slot_text=describe_slot(reservation.slot),
                weekday=WEEKDAYS[reservation.date.weekday()],
                confirmed=reservation.status == ReservationStatus.CONFIRMED,
                created_at=formatting.full(reservation.created_at, self.timezone),
                cancelled_at=(
                    formatting.full(reservation.cancelled_at, self.timezone)
                    if reservation.cancelled_at
                    else None
                ),
                source_label=labels.label(labels.RESERVATION_SOURCE, reservation.source),
                error=error,
            )
