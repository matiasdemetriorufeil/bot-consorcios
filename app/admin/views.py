"""Views of the admin panel. Texts in Spanish (voseo); every change is audited in bot_events
(app.admin.audit). Never registered here: verification codes, people's DNI, .env values.
Views with AdminOnly first are only for admins (operators get 403 and do not see them)."""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any, ClassVar
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import anyio
from sqladmin import BaseView, ModelView, action, expose
from sqladmin.filters import BooleanFilter, ForeignKeyFilter, StaticValuesFilter
from sqladmin.flash import Flash
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, object_session, selectinload
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from wtforms import ValidationError

from app.admin.audit import changed_fields, log_admin_action
from app.admin.auth import AdminOnly, admin_user
from app.admin.metrics import compute_metrics
from app.bot.bot_config import invalidate_bot_config, parse_hour, parse_weekdays
from app.bot.identity import (
    IdentityError,
    approve_verification_request,
    reject_verification_request,
)
from app.bot.unit_search import display_building_name
from app.config import Settings
from app.db.models import (
    BotSettings,
    Building,
    BuildingInfo,
    BuildingInfoCategory,
    DataSource,
    Person,
    PersonRole,
    Phone,
    QuickReply,
    SyncJob,
    SyncRun,
    Unit,
    UnitPerson,
    VerificationRequest,
    VerificationRequestStatus,
    WaTemplate,
)

_DATE_FORMAT = "%d/%m/%Y %H:%M"


def _local(value: datetime | None, timezone: str) -> str:
    return value.astimezone(ZoneInfo(timezone)).strftime(_DATE_FORMAT) if value else ""


def _pks(request: Request) -> list[int]:
    raw = request.query_params.get("pks", "")
    try:
        return [int(pk) for pk in raw.split(",") if pk]
    except ValueError:
        return []


def _back_to_list(request: Request, identity: str) -> RedirectResponse:
    return RedirectResponse(request.url_for("admin:list", identity=identity), status_code=302)


async def _in_thread(func: Callable[..., Any], *args: Any) -> Any:
    return await anyio.to_thread.run_sync(partial(func, *args))


class AuditedView(ModelView):
    """Logs creations, edits (names of the changed fields only) and deletions."""

    audit_name: ClassVar[str] = ""
    can_export = False

    def audit_ids(self, model: Any) -> dict[str, Any]:
        return {f"{self.audit_name}_id": model.id}

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        if is_created:
            return
        fields = changed_fields(model, data)
        session = object_session(model)
        if fields and session is not None:
            log_admin_action(
                session,
                admin_user(request),
                f"{self.audit_name}_updated",
                **self.audit_ids(model),
                fields=fields,
            )

    async def after_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> Response | None:
        session = object_session(model)
        if is_created and session is not None:
            log_admin_action(
                session, admin_user(request), f"{self.audit_name}_created", **self.audit_ids(model)
            )
            session.commit()
        return None

    async def on_model_delete(self, model: Any, request: Request) -> None:
        session = object_session(model)
        if session is not None:
            log_admin_action(
                session, admin_user(request), f"{self.audit_name}_deleted", **self.audit_ids(model)
            )


# --- Buildings --------------------------------------------------------------------------


class BuildingAdmin(AdminOnly, AuditedView, model=Building):
    name = "Edificio"
    name_plural = "Edificios"
    icon = "fa-solid fa-building"
    audit_name = "building"
    # Buildings come from ConsorPlus (roster sync): only these fields are edited here.
    can_create = False
    can_delete = False
    column_list = [
        Building.consorplus_code,
        Building.name,
        Building.address,
        Building.active,
        Building.pilot,
    ]
    column_searchable_list = [Building.name, Building.address]
    column_sortable_list = [Building.consorplus_code, Building.name, Building.active]
    column_default_sort = [(Building.name, False)]
    column_filters = [
        BooleanFilter(Building.active, title="Activo"),
        BooleanFilter(Building.pilot, title="Prueba piloto"),
    ]
    column_details_list = [*column_list, Building.created_at, Building.updated_at]
    form_columns = [Building.name, Building.address, Building.active, Building.pilot]
    column_labels = {
        Building.consorplus_code: "Código ConsorPlus",
        Building.name: "Nombre",
        Building.address: "Dirección",
        Building.active: "Activo",
        Building.pilot: "Prueba piloto",
        Building.created_at: "Creado",
        Building.updated_at: "Actualizado",
    }
    page_size = 25


class BuildingInfoAdmin(AdminOnly, AuditedView, model=BuildingInfo):
    name = "Información de edificio"
    name_plural = "Información de edificios"
    icon = "fa-solid fa-book"
    audit_name = "building_info"
    column_list = [
        BuildingInfo.id,
        BuildingInfo.building,
        BuildingInfo.category,
        BuildingInfo.title,
        BuildingInfo.updated_at,
    ]
    column_searchable_list = [BuildingInfo.title, "building.name"]
    column_sortable_list = [BuildingInfo.category, BuildingInfo.title, BuildingInfo.updated_at]
    column_filters = [
        ForeignKeyFilter(BuildingInfo.building_id, Building.name, title="Edificio"),
        StaticValuesFilter(
            BuildingInfo.category,
            [(c.value, c.value) for c in BuildingInfoCategory],
            title="Categoría",
        ),
    ]
    column_details_list = [*column_list, BuildingInfo.content, BuildingInfo.created_at]
    form_columns = [
        BuildingInfo.building,
        BuildingInfo.category,
        BuildingInfo.title,
        BuildingInfo.content,
    ]
    form_widget_args = {"content": {"rows": 15}}
    column_labels = {
        BuildingInfo.building: "Edificio",
        BuildingInfo.category: "Categoría",
        BuildingInfo.title: "Título",
        BuildingInfo.content: "Contenido",
        BuildingInfo.created_at: "Creado",
        BuildingInfo.updated_at: "Actualizado",
    }
    page_size = 25

    def audit_ids(self, model: BuildingInfo) -> dict[str, Any]:
        category = str(model.category) if model.category else None  # str until reloaded
        return {
            "building_info_id": model.id,
            "building_id": model.building_id,
            "category": category,
        }


# --- Phones to review -------------------------------------------------------------------


def _set_phone_reviewed(session: Session, phone_id: int, user: str) -> bool:
    phone = session.get(Phone, phone_id, with_for_update=True)
    if phone is None or not phone.needs_review:
        return False
    phone.verified = True
    phone.needs_review = False
    log_admin_action(session, user, "phone_approved", phone_e164=phone.e164, phone_id=phone.id)
    session.commit()
    return True


class PhoneReviewAdmin(ModelView, model=Phone):
    """Phones whose area code was assumed when imported from ConsorPlus (needs_review)."""

    name = "Teléfono a revisar"
    name_plural = "Teléfonos a revisar"
    icon = "fa-solid fa-phone"
    can_create = False
    can_edit = False
    can_export = False
    column_list = [
        Phone.id,
        Phone.e164,
        Phone.raw,
        "person.full_name",
        Phone.source,
        Phone.verified,
        Phone.created_at,
    ]
    column_details_list = column_list
    column_searchable_list = [Phone.e164, Phone.raw, "person.full_name"]
    column_labels = {
        Phone.e164: "Teléfono (normalizado)",
        Phone.raw: "Como figura en ConsorPlus",
        "person.full_name": "Persona",
        Phone.source: "Origen",
        Phone.verified: "Verificado",
        Phone.created_at: "Cargado",
    }
    page_size = 25

    def list_query(self, request: Request) -> Select:
        return (
            select(Phone)
            .where(Phone.needs_review.is_(True))
            .options(selectinload(Phone.person))
            .order_by(Phone.id)
        )

    def details_query(self, request: Request) -> Select:
        return super().details_query(request).options(selectinload(Phone.person))

    async def on_model_delete(self, model: Phone, request: Request) -> None:
        if not model.needs_review:
            raise ValueError("Solo se pueden eliminar teléfonos marcados para revisar.")
        session = object_session(model)
        if session is not None:
            log_admin_action(
                session,
                admin_user(request),
                "phone_deleted",
                phone_e164=model.e164,
                phone_id=model.id,
                person_id=model.person_id,
            )

    @action(
        name="approve",
        label="Aprobar",
        confirmation_message="¿Aprobar los teléfonos elegidos? Quedan verificados.",
    )
    async def approve(self, request: Request) -> Response:
        user = admin_user(request)
        approved = 0
        for pk in _pks(request):
            with self.session_maker() as session:
                approved += await _in_thread(_set_phone_reviewed, session, pk, user)
        if approved:
            Flash.success(request, f"Teléfonos aprobados: {approved}.")
        else:
            Flash.warning(request, "No se aprobó ningún teléfono (¿ya estaban revisados?).")
        return _back_to_list(request, self.identity)


# --- All phones ---------------------------------------------------------------------------

_SOURCE_LABELS = {
    DataSource.CONSORPLUS: "ConsorPlus",
    DataSource.BOT_VERIFIED: "Bot (código por email)",
    DataSource.MANUAL: "Operador",
}
_ROLE_LABELS = {PersonRole.OWNER: "propietario", PersonRole.TENANT: "inquilino"}


def _person_units(phone: Phone) -> str:
    links = sorted(
        phone.person.units,
        key=lambda link: (link.unit.building.name, link.unit.label, link.role),
    )
    return "; ".join(
        f"{display_building_name(link.unit.building.name)} · {link.unit.label}"
        f" ({_ROLE_LABELS.get(link.role, link.role)})"
        for link in links
    )


def _unlink_phone(session: Session, phone_id: int, user: str) -> bool:
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


class PhoneAdmin(ModelView, model=Phone):
    """Every phone, to look up who a number belongs to. Neither created nor edited here: only
    unlinked (deleted) when the number changed hands or was linked to the wrong person."""

    name = "Teléfono"
    name_plural = "Teléfonos"
    icon = "fa-solid fa-address-book"
    can_create = False
    can_edit = False
    can_delete = False  # only through the audited "Desvincular" action
    can_export = False
    column_list = [
        Phone.e164,
        "person.full_name",
        "person.units",
        Phone.source,
        Phone.verified,
        Phone.created_at,
    ]
    column_details_list = [
        Phone.id,
        Phone.e164,
        Phone.raw,
        "person.full_name",
        "person.units",
        Phone.source,
        Phone.verified,
        Phone.needs_review,
        Phone.conflict,
        Phone.created_at,
    ]
    # Searched by search_query (number digits, raw text or person's name).
    column_searchable_list = [Phone.e164, "person.full_name"]
    column_sortable_list = [Phone.e164, Phone.source, Phone.verified, Phone.created_at]
    column_default_sort = [(Phone.created_at, True)]
    column_filters = [
        BooleanFilter(Phone.verified, title="Verificado"),
        BooleanFilter(Phone.needs_review, title="A revisar (característica supuesta)"),
        BooleanFilter(Phone.conflict, title="En conflicto (figura para otra persona)"),
        StaticValuesFilter(
            Phone.source, [(s.value, label) for s, label in _SOURCE_LABELS.items()], title="Fuente"
        ),
    ]
    column_labels = {
        Phone.id: "ID",
        Phone.e164: "Número",
        Phone.raw: "Como figura en ConsorPlus",
        "person.full_name": "Persona",
        "person.units": "Unidades de la persona",
        Phone.source: "Fuente",
        Phone.verified: "Verificado",
        Phone.needs_review: "A revisar",
        Phone.conflict: "En conflicto",
        Phone.created_at: "Fecha",
    }
    column_formatters = {
        "person.units": lambda m, a: _person_units(m),
        Phone.source: lambda m, a: _SOURCE_LABELS.get(m.source, m.source),
    }
    column_formatters_detail = column_formatters
    page_size = 50

    _timezone: ClassVar[str] = Settings.model_fields["timezone"].default

    def search_placeholder(self) -> str:
        return "número o nombre"

    def _with_person(self, stmt: Select) -> Select:
        return stmt.options(
            selectinload(Phone.person)
            .selectinload(Person.units)
            .selectinload(UnitPerson.unit)
            .selectinload(Unit.building)
        )

    def list_query(self, request: Request) -> Select:
        # Joined here once so search_query can filter by the person's name.
        return self._with_person(select(Phone).join(Phone.person))

    def details_query(self, request: Request) -> Select:
        return self._with_person(super().details_query(request))

    def search_query(self, stmt: Select, term: str) -> Select:
        term = term.strip()
        conditions = [Person.full_name.ilike(f"%{term}%"), Phone.raw.ilike(f"%{term}%")]
        # "351 555-0301", "0351 5550301", "+54 9 351…": compared by digits, without the
        # leading 0 of the local format.
        digits = re.sub(r"\D", "", term).lstrip("0")
        if digits:
            conditions.append(Phone.e164.contains(digits, autoescape=True))
        return stmt.where(or_(*conditions))

    async def get_list_value(self, obj: Any, prop: str, request: Request | None = None) -> Any:
        if prop == "created_at":
            return obj.created_at, _local(obj.created_at, self._timezone)
        return await super().get_list_value(obj, prop, request)

    async def get_detail_value(self, obj: Any, prop: str, request: Request | None = None) -> Any:
        if prop == "created_at":
            return obj.created_at, _local(obj.created_at, self._timezone)
        return await super().get_detail_value(obj, prop, request)

    @action(
        name="unlink",
        label="Desvincular",
        confirmation_message=(
            "¿Desvincular los teléfonos elegidos? Se borran y el bot deja de reconocer esos "
            "números. Si el número sigue cargado en ConsorPlus, la sincronización nocturna lo "
            "vuelve a crear: corregilo también allá."
        ),
    )
    async def unlink(self, request: Request) -> Response:
        user = admin_user(request)
        unlinked = 0
        for pk in _pks(request):
            with self.session_maker() as session:
                unlinked += await _in_thread(_unlink_phone, session, pk, user)
        if unlinked:
            Flash.success(request, f"Teléfonos desvinculados: {unlinked}.")
        else:
            Flash.warning(request, "No se desvinculó ningún teléfono (¿ya estaban borrados?).")
        return _back_to_list(request, self.identity)


# PhoneReviewAdmin already uses "phone" (the default for the model) in its URLs.
PhoneAdmin.identity = "phones"


# --- Operator verifications -------------------------------------------------------------


def _unit_owners(session: Session, unit_id: int) -> list[Person]:
    return list(
        session.scalars(
            select(Person)
            .join(UnitPerson, UnitPerson.person_id == Person.id)
            .where(UnitPerson.unit_id == unit_id, UnitPerson.role == PersonRole.OWNER)
            .order_by(Person.full_name, Person.id)
        )
    )


class VerificationRequestAdmin(ModelView, model=VerificationRequest):
    """Pending requests left by request_operator_verification (units without owner email)."""

    name = "Verificación pendiente"
    name_plural = "Verificaciones pendientes"
    icon = "fa-solid fa-user-check"
    can_create = False
    can_edit = False
    can_delete = False
    can_export = False
    column_list = [
        VerificationRequest.id,
        "unit.building.name",
        "unit.label",
        VerificationRequest.claimed_name,
        VerificationRequest.phone_e164,
        VerificationRequest.created_at,
    ]
    column_details_list = [*column_list, VerificationRequest.status]
    column_labels = {
        "unit.building.name": "Edificio",
        "unit.label": "Unidad",
        VerificationRequest.claimed_name: "Nombre declarado",
        VerificationRequest.phone_e164: "Teléfono",
        VerificationRequest.created_at: "Fecha",
        VerificationRequest.status: "Estado",
    }
    column_formatters = {
        "unit.building.name": lambda m, a: display_building_name(m.unit.building.name),
    }
    column_formatters_detail = column_formatters
    page_size = 25

    def _with_unit(self, stmt: Select) -> Select:
        return stmt.options(selectinload(VerificationRequest.unit).selectinload(Unit.building))

    def list_query(self, request: Request) -> Select:
        return self._with_unit(
            select(VerificationRequest)
            .where(VerificationRequest.status == VerificationRequestStatus.PENDING)
            .order_by(VerificationRequest.created_at)
        )

    def details_query(self, request: Request) -> Select:
        return self._with_unit(super().details_query(request))

    @action(name="approve", label="Aprobar (elegir propietario)")
    async def approve(self, request: Request) -> Response:
        pks = _pks(request)
        if len(pks) != 1:
            Flash.warning(request, "Aprobá las verificaciones de a una.")
            return _back_to_list(request, self.identity)
        return RedirectResponse(
            request.url_for(f"admin:view-{self.identity}-approve_page", pk=pks[0]),
            status_code=302,
        )

    @action(
        name="reject",
        label="Rechazar",
        confirmation_message="¿Rechazar las verificaciones elegidas? El número no se asocia.",
    )
    async def reject(self, request: Request) -> Response:
        user = admin_user(request)
        rejected, errors = 0, []
        for pk in _pks(request):
            with self.session_maker() as session:
                try:
                    await _in_thread(self._reject, session, pk, user)
                    rejected += 1
                except IdentityError as exc:
                    session.rollback()
                    errors.append(str(exc))
        if rejected:
            Flash.success(request, f"Verificaciones rechazadas: {rejected}.")
        for error in errors:
            Flash.error(request, error)
        return _back_to_list(request, self.identity)

    @staticmethod
    def _reject(session: Session, request_id: int, user: str) -> None:
        request = reject_verification_request(session, request_id, resolved_by=user)
        log_admin_action(
            session,
            user,
            "verification_rejected",
            phone_e164=request.phone_e164,
            request_id=request.id,
            unit_id=request.unit_id,
        )
        session.commit()

    @staticmethod
    def _approve(session: Session, request_id: int, person_id: int, user: str) -> None:
        request = approve_verification_request(session, request_id, person_id, resolved_by=user)
        log_admin_action(
            session,
            user,
            "verification_approved",
            phone_e164=request.phone_e164,
            request_id=request.id,
            unit_id=request.unit_id,
            person_id=person_id,
        )
        session.commit()

    @expose("/approve/{pk:int}", methods=["GET", "POST"])
    async def approve_page(self, request: Request) -> Response:
        pk = request.path_params["pk"]
        error = None
        with self.session_maker() as session:
            if request.method == "POST":
                form = await request.form()
                try:
                    person_id = int(str(form.get("person_id", "")))
                except ValueError:
                    error = "Elegí el propietario al que se asocia el teléfono."
                else:
                    try:
                        await _in_thread(self._approve, session, pk, person_id, admin_user(request))
                    except IdentityError as exc:
                        session.rollback()
                        error = str(exc)
                    else:
                        Flash.success(request, "Verificación aprobada: el teléfono quedó asociado.")
                        return _back_to_list(request, self.identity)

            verification = session.scalar(
                self._with_unit(select(VerificationRequest).where(VerificationRequest.id == pk))
            )
            if verification is None:
                return Response("Solicitud inexistente", status_code=404)
            owners = _unit_owners(session, verification.unit_id)
            context = {
                "title": "Aprobar verificación",
                "verification": verification,
                "building": display_building_name(verification.unit.building.name),
                "created_at": _local(verification.created_at, self._timezone),
                "owners": owners,
                "pending": verification.status == VerificationRequestStatus.PENDING,
                "error": error,
                "list_url": request.url_for("admin:list", identity=self.identity),
            }
            return await self.templates.TemplateResponse(
                request,
                "verification_approve.html",
                context,
                status_code=400 if error else 200,
            )

    _timezone: ClassVar[str] = Settings.model_fields["timezone"].default


# --- Sync runs (read only) --------------------------------------------------------------


class SyncRunAdmin(AdminOnly, ModelView, model=SyncRun):
    name = "Sincronización"
    name_plural = "Sincronizaciones"
    icon = "fa-solid fa-rotate"
    can_create = False
    can_edit = False
    can_delete = False
    can_export = False
    column_list = [
        SyncRun.id,
        SyncRun.kind,
        SyncRun.job,
        SyncRun.started_at,
        SyncRun.finished_at,
        SyncRun.status,
        SyncRun.units_ok,
        SyncRun.units_failed,
    ]
    column_details_list = [*column_list, SyncRun.error_summary, SyncRun.stats]
    column_sortable_list = [SyncRun.started_at, SyncRun.job, SyncRun.status]
    column_default_sort = [(SyncRun.started_at, True)]
    column_filters = [
        StaticValuesFilter(SyncRun.job, [(j.value, j.value) for j in SyncJob], title="Tarea"),
    ]
    column_labels = {
        SyncRun.kind: "Tipo",
        SyncRun.job: "Tarea",
        SyncRun.started_at: "Inicio",
        SyncRun.finished_at: "Fin",
        SyncRun.status: "Estado",
        SyncRun.units_ok: "OK",
        SyncRun.units_failed: "Con error",
        SyncRun.error_summary: "Errores",
        SyncRun.stats: "Estadísticas",
    }
    page_size = 50


# --- General settings -------------------------------------------------------------------


def _valid_hour(form: Any, field: Any) -> None:
    if field.data and field.data.strip() and parse_hour(field.data) is None:
        raise ValidationError("Usá el formato HH:MM, por ejemplo 09:00.")


def _valid_weekdays(form: Any, field: Any) -> None:
    if field.data and field.data.strip() and parse_weekdays(field.data) is None:
        raise ValidationError("Números del 0 (lunes) al 6 (domingo) separados por coma.")


def _valid_url(form: Any, field: Any) -> None:
    value = (field.data or "").strip()
    if value:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValidationError("Tiene que ser una dirección web completa (https://...).")


_FALLBACK = "Vacío: se usa el valor de .env (o el predeterminado)."
_BUILT_IN = "Vacío: el bot usa su texto automático."


class BotSettingsAdmin(AdminOnly, AuditedView, model=BotSettings):
    """The single row of bot settings; values never shown here: the .env ones."""

    name = "Configuración general"
    name_plural = "Configuración general"
    icon = "fa-solid fa-gear"
    audit_name = "bot_settings"
    can_create = False
    can_delete = False
    column_list = [BotSettings.id, BotSettings.updated_at]
    column_details_exclude_list = [BotSettings.id]
    form_columns = [
        BotSettings.welcome_message,
        BotSettings.office_hours_start,
        BotSettings.office_hours_end,
        BotSettings.office_weekdays,
        BotSettings.out_of_hours_text,
        BotSettings.emergency_contact_text,
        BotSettings.autogestion_url,
        BotSettings.payment_code_how_to,
    ]
    column_labels = {
        BotSettings.id: "Configuración",
        BotSettings.welcome_message: "Mensaje de bienvenida",
        BotSettings.office_hours_start: "Horario de atención: desde",
        BotSettings.office_hours_end: "Horario de atención: hasta",
        BotSettings.office_weekdays: "Días de atención",
        BotSettings.out_of_hours_text: "Texto fuera de horario",
        BotSettings.emergency_contact_text: "Contacto para urgencias fuera de horario",
        BotSettings.autogestion_url: "URL de autogestión",
        BotSettings.payment_code_how_to: "Cómo pagar con el código",
        BotSettings.updated_at: "Actualizado",
    }
    column_formatters = {BotSettings.id: lambda m, a: "Configuración del bot"}
    form_args = {
        "welcome_message": {
            "description": "El bot saluda con este texto en el primer mensaje. " + _BUILT_IN
        },
        "office_hours_start": {"description": "HH:MM. " + _FALLBACK, "validators": [_valid_hour]},
        "office_hours_end": {"description": "HH:MM. " + _FALLBACK, "validators": [_valid_hour]},
        "office_weekdays": {
            "description": "0 = lunes … 6 = domingo, separados por coma (ej. 0,1,2,3,4). "
            + _FALLBACK,
            "validators": [_valid_weekdays],
        },
        "out_of_hours_text": {
            "description": "Al derivar fuera de horario, reemplaza el aviso de cuándo le van a "
            "responder. " + _BUILT_IN
        },
        "emergency_contact_text": {
            "description": "Se suma al aviso de una urgencia fuera de horario. " + _FALLBACK
        },
        "autogestion_url": {
            "description": "El bot la ofrece junto con la deuda. " + _FALLBACK,
            "validators": [_valid_url],
        },
        "payment_code_how_to": {
            "description": "El bot lo copia tal cual al dar el código de pago. " + _FALLBACK
        },
    }

    def audit_ids(self, model: BotSettings) -> dict[str, Any]:
        return {}

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        fields = changed_fields(model, data)
        session = object_session(model)
        if fields and session is not None:
            log_admin_action(session, admin_user(request), "bot_settings_changed", fields=fields)

    async def after_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> Response | None:
        invalidate_bot_config()  # this process sees the change right away
        return None


# --- Metrics ----------------------------------------------------------------------------


class MetricsView(AdminOnly, BaseView):
    name = "Métricas"
    icon = "fa-solid fa-chart-line"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default

    @expose("/metrics", methods=["GET"], identity="metrics")
    async def metrics_page(self, request: Request) -> Response:
        with self.session_maker() as session:
            metrics = await _in_thread(compute_metrics, session, datetime.now(UTC), self.timezone)
        peak = max((n for _, n in metrics.conversations_per_day), default=0)
        return await self.templates.TemplateResponse(
            request,
            "metrics.html",
            {"title": "Métricas", "m": metrics, "peak": peak or 1},
        )


# --- Inbox settings: WhatsApp templates and quick replies -------------------------------


class WaTemplateAdmin(AdminOnly, AuditedView, model=WaTemplate):
    """Approved templates the inbox offers when the 24-hour window is closed."""

    name = "Plantilla de WhatsApp"
    name_plural = "Plantillas de WhatsApp"
    icon = "fa-solid fa-envelope-open-text"
    audit_name = "wa_template"
    can_delete = False  # deactivated instead: sent messages keep their text anyway
    column_list = [WaTemplate.label, WaTemplate.name, WaTemplate.language, WaTemplate.active]
    column_details_list = [*column_list, WaTemplate.body, WaTemplate.updated_at]
    column_sortable_list = [WaTemplate.label, WaTemplate.active]
    column_default_sort = [(WaTemplate.label, False)]
    form_columns = [
        WaTemplate.label,
        WaTemplate.name,
        WaTemplate.language,
        WaTemplate.body,
        WaTemplate.active,
    ]
    form_widget_args = {"body": {"rows": 5}}
    column_labels = {
        WaTemplate.label: "Nombre para las operadoras",
        WaTemplate.name: "Nombre en Meta",
        WaTemplate.language: "Idioma",
        WaTemplate.body: "Texto",
        WaTemplate.active: "Activa",
        WaTemplate.updated_at: "Actualizada",
    }
    form_args = {
        "name": {
            "description": "Exactamente como figura aprobada en Meta (WhatsApp Manager > "
            "Plantillas). Solo plantillas sin variables."
        },
        "language": {"description": "El código del idioma aprobado, por ejemplo es_AR o es."},
        "body": {
            "description": "El texto de la plantilla, tal cual: queda en el historial de la "
            "conversación (lo que recibe la persona es lo aprobado en Meta)."
        },
    }
    page_size = 50


class QuickReplyAdmin(AdminOnly, AuditedView, model=QuickReply):
    """Saved texts the operators insert in a reply with one click."""

    name = "Respuesta rápida"
    name_plural = "Respuestas rápidas"
    icon = "fa-solid fa-bolt"
    audit_name = "quick_reply"
    column_list = [QuickReply.title, QuickReply.sort_order, QuickReply.active]
    column_details_list = [*column_list, QuickReply.content, QuickReply.updated_at]
    column_sortable_list = [QuickReply.title, QuickReply.sort_order]
    column_default_sort = [(QuickReply.sort_order, False), (QuickReply.title, False)]
    form_columns = [QuickReply.title, QuickReply.content, QuickReply.sort_order, QuickReply.active]
    form_widget_args = {"content": {"rows": 5}}
    column_labels = {
        QuickReply.title: "Título",
        QuickReply.content: "Texto",
        QuickReply.sort_order: "Orden",
        QuickReply.active: "Activa",
        QuickReply.updated_at: "Actualizada",
    }
    form_args = {
        "sort_order": {"description": "Las de número más chico aparecen primero."},
        "content": {"description": "Se inserta tal cual en la caja de respuesta (editable)."},
    }
    page_size = 50
