"""Views of the admin panel. Texts in Spanish (voseo); every change is audited in bot_events
(app.admin.audit). Never registered here: verification codes, people's DNI, .env values.
Views with AdminOnly first are only for admins (operators get 403 and do not see them)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from functools import partial
from typing import Any, ClassVar
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import anyio
from markupsafe import Markup
from sqladmin import BaseView, ModelView, expose
from sqladmin.flash import Flash
from sqladmin.formatters import BASE_FORMATTERS
from sqlalchemy import select
from sqlalchemy.orm import object_session
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from wtforms import ValidationError

from app.admin import formatting, labels
from app.admin.audit import changed_fields, log_admin_action
from app.admin.auth import AdminOnly, admin_user
from app.admin.filters import RelatedFilter, ValuesFilter, YesNoFilter
from app.admin.hours import HourField, check_listed
from app.admin.metrics import compute_claim_metrics, compute_metrics
from app.admin.numbers import QUICK_REPLY_ORDER, NumberField, RemembersRecord
from app.bot.bot_config import invalidate_bot_config, parse_weekdays
from app.business_hours import (
    WEEKDAYS,
    invalidate_calendar,
    national_holiday,
    national_holidays,
)
from app.config import Settings
from app.db.models import (
    BotSettings,
    Building,
    BuildingInfo,
    BuildingInfoCategory,
    ClosedDay,
    ClosedDayKind,
    QuickReply,
    SyncJob,
    SyncRun,
    WaTemplate,
)


def type_formatters(timezone: str) -> tuple[dict[Any, Any], dict[Any, Any]]:
    """SQLAdmin's formatters by value type (list, record page): dates in Argentina's time and
    short, any enum in Spanish (labels). setup_admin gives them to every ModelView."""
    common = {**BASE_FORMATTERS, StrEnum: labels.enum_label}
    listed = {**common, datetime: lambda v: formatting.when(v, timezone=timezone)}
    detail = {**common, datetime: lambda v: formatting.full(v, timezone)}
    return listed, detail


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
    menu_group = "buildings"  # its block of «Administración» (app.admin.MENU_GROUPS)
    name = "Edificio"
    name_plural = "Edificios"
    icon = "fa-solid fa-building"
    audit_name = "building"
    list_template = "building_list.html"  # the list's own look (5g); same links and filters
    # Buildings come from ConsorPlus (roster sync): only these fields are edited here.
    can_create = False
    can_delete = False
    column_list = [
        Building.consorplus_code,
        Building.name,
        Building.address,
        Building.active,
        Building.pilot,
        Building.claims_bot_enabled,
    ]
    # Hidden under 1280 px in the list (templates/sqladmin/list.html); the record has them.
    column_secondary = ["address"]
    column_searchable_list = [Building.name, Building.address]
    column_sortable_list = [Building.consorplus_code, Building.name, Building.active]
    column_default_sort = [(Building.name, False)]
    column_filters = [
        YesNoFilter(Building.active, title="Activo"),
        YesNoFilter(Building.pilot, title="Prueba piloto"),
        YesNoFilter(Building.claims_bot_enabled, title="Reclamos por el bot"),
    ]
    # The ConsorPlus code only here, in its own column: the name, as everywhere, without it.
    column_formatters = {Building.name: lambda m, a: formatting.building(m.name)}
    column_formatters_detail = column_formatters
    column_details_list = [*column_list, Building.created_at, Building.updated_at]
    # The name is only shown: the roster sync overwrites it (app/sync/roster.py).
    form_columns = [
        Building.name,
        Building.address,
        Building.active,
        Building.pilot,
        Building.claims_bot_enabled,
    ]
    form_widget_args = {"name": {"readonly": True}}
    form_args = {
        "name": {
            "description": "Viene de ConsorPlus: se actualiza con la sincronización y no "
            "se edita acá."
        },
        "claims_bot_enabled": {
            "description": "Todavía no hace nada: cuando esté listo, el bot va a tomar reclamos "
            "en este edificio. Qué problemas y quién los atiende se elige en «Reclamos por "
            "edificio»."
        },
    }
    column_labels = {
        Building.consorplus_code: "Código ConsorPlus",
        Building.name: "Nombre",
        Building.address: "Dirección",
        Building.active: "Activo",
        Building.pilot: "Prueba piloto",
        Building.claims_bot_enabled: "Reclamos por el bot",
        Building.created_at: "Creado",
        Building.updated_at: "Actualizado",
    }
    page_size = 25

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        data.pop("name", None)  # readonly is only the browser's: a sent name is ignored
        await super().on_model_change(data, model, is_created, request)


class BuildingInfoAdmin(AdminOnly, AuditedView, model=BuildingInfo):
    menu_group = "buildings"  # its block of «Administración» (app.admin.MENU_GROUPS)
    name = "Información de edificio"
    name_plural = "Información de edificios"
    icon = "fa-solid fa-book"
    audit_name = "building_info"
    list_template = "building_info_list.html"  # like «Edificios» (5g); same links and filters
    column_list = [
        BuildingInfo.building,
        BuildingInfo.category,
        BuildingInfo.title,
        BuildingInfo.updated_at,
    ]
    # Hidden under 1280 px in the list (templates/sqladmin/list.html); the record has them.
    column_secondary = ["updated_at"]
    column_searchable_list = [BuildingInfo.title, "building.name"]
    column_sortable_list = [BuildingInfo.category, BuildingInfo.title, BuildingInfo.updated_at]
    column_filters = [
        RelatedFilter(
            BuildingInfo.building_id, Building.name, title="Edificio", display=formatting.building
        ),
        ValuesFilter(
            BuildingInfo.category,
            [(c.value, labels.INFO_CATEGORY[c]) for c in BuildingInfoCategory],
            title="Categoría",
        ),
    ]
    column_formatters = {
        BuildingInfo.building: lambda m, a: formatting.building(m.building.name),
        BuildingInfo.category: lambda m, a: labels.label(labels.INFO_CATEGORY, m.category),
    }
    column_formatters_detail = column_formatters
    # SQLAdmin hands get_label the building's text (Building.__str__: its ConsorPlus name).
    form_args = {"building": {"get_label": lambda b: formatting.building(getattr(b, "name", b))}}
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

    def search_placeholder(self) -> str:
        return "título o edificio"  # SQLAdmin's default lists the columns: "building.name"

    async def scaffold_form(self, rules: Any = None) -> Any:
        form = await super().scaffold_form(rules)
        # SQLAdmin offers an enum's raw values ("reglamento"): the same values, in Spanish.
        form.category.kwargs["choices"] = [
            (c.value, labels.INFO_CATEGORY[c]) for c in BuildingInfoCategory
        ]
        # SQLAdmin compares the stored enum by its name ("HORARIOS"), never equal to the
        # values ("horarios"): the saved category was not selected and saving reset it.
        form.category.kwargs["coerce"] = lambda v: v.value if isinstance(v, StrEnum) else str(v)
        return form

    def audit_ids(self, model: BuildingInfo) -> dict[str, Any]:
        category = str(model.category) if model.category else None  # str until reloaded
        return {
            "building_info_id": model.id,
            "building_id": model.building_id,
            "category": category,
        }


# --- Sync runs (read only) --------------------------------------------------------------


class SyncRunAdmin(AdminOnly, ModelView, model=SyncRun):
    menu_group = "system"  # its block of «Administración» (app.admin.MENU_GROUPS)
    name = "Sincronización"
    name_plural = "Sincronizaciones"
    icon = "fa-solid fa-rotate"
    can_create = False
    can_edit = False
    can_delete = False
    can_export = False
    column_list = [
        SyncRun.kind,
        SyncRun.job,
        SyncRun.started_at,
        SyncRun.finished_at,
        SyncRun.status,
        SyncRun.units_ok,
        SyncRun.units_failed,
    ]
    # Hidden under 1280 px in the list (templates/sqladmin/list.html); the record has them.
    column_secondary = ["kind", "finished_at"]
    column_details_list = [*column_list, SyncRun.error_summary, SyncRun.stats]
    column_sortable_list = [SyncRun.started_at, SyncRun.job, SyncRun.status]
    column_default_sort = [(SyncRun.started_at, True)]
    column_filters = [
        ValuesFilter(SyncRun.job, [(j.value, labels.SYNC_JOB[j]) for j in SyncJob], title="Tarea"),
    ]
    column_formatters_detail = {
        SyncRun.error_summary: lambda m, a: error_lines(m.error_summary),
        SyncRun.stats: lambda m, a: stats_table(m.stats),
    }
    column_labels = {
        SyncRun.kind: "Tipo",
        SyncRun.job: "Tarea",
        SyncRun.started_at: "Inicio",
        SyncRun.finished_at: "Fin",
        SyncRun.status: "Estado",
        SyncRun.units_ok: "Bien",
        SyncRun.units_failed: "Con error",
        SyncRun.error_summary: "Errores",
        SyncRun.stats: "Estadísticas",
    }
    page_size = 50


def error_lines(text: str | None) -> Markup:
    """A sync's errors, one per line (escaped: they may quote ConsorPlus)."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return Markup('<span class="text-secondary">Sin errores.</span>')
    items = Markup("").join(Markup("<li>{}</li>").format(line) for line in lines)
    return Markup('<ul class="mb-0 ps-3 sync-errors">{}</ul>').format(items)


def _stat_value(key: str, value: Any) -> Markup | str:
    if key == "elapsed_seconds" and isinstance(value, int | float):
        return formatting.duration(value)
    if key == "errors_by_type" and isinstance(value, dict):
        named: dict[str, int] = {}
        for name, count in value.items():
            text = labels.ERROR_TYPES.get(name, "Otro error")
            named[text] = named.get(text, 0) + count
        return stats_table(named, translate=False) if named else "Ninguno"
    if isinstance(value, dict):
        return stats_table(value)
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, int | float):
        return formatting.number(value)
    return str(value)


def stats_table(stats: dict[str, Any] | None, *, translate: bool = True) -> Markup:
    """sync_runs.stats as a table "what: how many" (nested ones, e.g. by unit type, inside)."""
    if not stats:
        return Markup('<span class="text-secondary">Sin datos.</span>')
    rows = Markup("").join(
        Markup('<tr><th class="fw-normal text-secondary">{}</th><td>{}</td></tr>').format(
            (labels.SYNC_STATS.get(key) or labels.humanize(key)) if translate else key,
            _stat_value(key, value),
        )
        for key, value in stats.items()
    )
    return Markup('<table class="table table-sm mb-0 sync-stats"><tbody>{}</tbody></table>').format(
        rows
    )


# --- General settings -------------------------------------------------------------------


def _valid_weekdays(form: Any, field: Any) -> None:
    if field.data and field.data.strip() and parse_weekdays(field.data) is None:
        raise ValidationError("Números del 0 (lunes) al 6 (domingo) separados por coma.")


def _valid_url(form: Any, field: Any) -> None:
    value = (field.data or "").strip()
    if value:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValidationError("Tiene que ser una dirección web completa (https://...).")


# Never the .env value itself: only that there is a default.
_DEFAULT_TEXT = "Si lo dejás vacío, el bot usa el texto por defecto."
_DEFAULT_VALUE = "Si lo dejás vacío, el bot usa el valor por defecto."
# The office hours: a list of hours (app.admin.hours); «Por defecto» = empty.
DEFAULT_OPTION = "Por defecto"
_DEFAULT_HOUR = "Con «Por defecto», el bot usa el horario de siempre."


class SettingsProblem(ValueError):
    """A setting that cannot be saved; SQLAdmin shows the text over the form (400)."""


class BotSettingsAdmin(AdminOnly, AuditedView, model=BotSettings):
    """The single row of bot settings; values never shown here: the .env ones."""

    menu_group = "bot"  # its block of «Administración» (app.admin.MENU_GROUPS)
    name = "Configuración del bot"
    name_plural = "Configuración del bot"
    icon = "fa-solid fa-gear"
    audit_name = "bot_settings"
    can_create = False
    can_delete = False
    column_list = [BotSettings.id, BotSettings.updated_at]
    column_details_exclude_list = [BotSettings.id]
    form_overrides = {"office_hours_start": HourField, "office_hours_end": HourField}
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
            "description": "El bot saluda con este texto en el primer mensaje. " + _DEFAULT_TEXT
        },
        "office_hours_start": {"description": _DEFAULT_HOUR, "empty": DEFAULT_OPTION},
        "office_hours_end": {
            "description": "Tiene que ser después de «desde». " + _DEFAULT_HOUR,
            "empty": DEFAULT_OPTION,
        },
        "office_weekdays": {
            "description": "0 = lunes … 6 = domingo, separados por coma (ej. 0,1,2,3,4). "
            + _DEFAULT_VALUE,
            "validators": [_valid_weekdays],
        },
        "out_of_hours_text": {
            "description": "Al derivar fuera de horario, reemplaza el aviso de cuándo le van a "
            "responder. " + _DEFAULT_TEXT
        },
        "emergency_contact_text": {
            "description": "Se suma al aviso de una urgencia fuera de horario. " + _DEFAULT_TEXT
        },
        "autogestion_url": {
            "description": "El bot la ofrece junto con la deuda. " + _DEFAULT_VALUE,
            "validators": [_valid_url],
        },
        "payment_code_how_to": {
            "description": "El bot lo copia tal cual al dar el código de pago. " + _DEFAULT_TEXT
        },
    }

    def audit_ids(self, model: BotSettings) -> dict[str, Any]:
        return {}

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        try:
            check_listed(data, model, ("office_hours_start", "office_hours_end"))
        except ValueError as exc:
            raise SettingsProblem(str(exc)) from exc
        start, end = data.get("office_hours_start") or "", data.get("office_hours_end") or ""
        if start and end and end <= start:
            raise SettingsProblem(
                "El horario de atención «hasta» tiene que ser después del «desde»."
            )
        fields = changed_fields(model, data)
        session = object_session(model)
        if fields and session is not None:
            log_admin_action(session, admin_user(request), "bot_settings_changed", fields=fields)

    async def after_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> Response | None:
        invalidate_bot_config()  # this process sees the change right away
        return None


class ClosedDayProblem(ValueError):
    """A closed day that cannot be saved; SQLAdmin shows the text over the form (400)."""


BAD_DAY = "No se entendió la fecha. Probá de nuevo desde la tabla."


def _holiday_formatter(model: ClosedDay, attribute: Any) -> str:
    return national_holiday(model.day) or "—"


@dataclass(frozen=True)
class HolidayRow:
    """A national holiday as «Días cerrados» lists it: «lunes 24/11», its name and whether the
    studio opens anyway (the id of that row, to remove it)."""

    day: date
    label: str
    name: str
    open_anyway_id: int | None


@dataclass(frozen=True)
class HolidayTable:
    year: int
    years: list[int]
    upcoming: list[HolidayRow]
    past: list[HolidayRow]


class ClosedDayAdmin(AdminOnly, AuditedView, model=ClosedDay):
    """The days that change the business days of the bot and the claims (app.business_hours).
    Its page (closed_day_list.html): the national holidays of this year or the next one, each
    with «Abre igual» / «Quitar abre igual», and under them the days closed by hand. Only added
    and removed; the buttons go through the same insert_model / delete_model as SQLAdmin's
    forms, so the checks (on_model_change) and the audit are the same."""

    menu_group = "bot"  # its block of «Administración» (app.admin.MENU_GROUPS)
    name = "Día cerrado"
    name_plural = "Días cerrados"
    icon = "fa-solid fa-calendar-xmark"
    audit_name = "closed_day"
    list_template = "closed_day_list.html"
    timezone = "America/Argentina/Cordoba"
    clock: Callable[[], datetime] = staticmethod(lambda: datetime.now(UTC))
    can_edit = False
    column_list = [ClosedDay.day, ClosedDay.reason]
    column_details_list = [
        ClosedDay.day,
        ClosedDay.kind,
        ClosedDay.reason,
        "national_holiday",
        ClosedDay.created_at,
    ]
    column_sortable_list = [ClosedDay.day]
    column_default_sort = [(ClosedDay.day, False)]
    form_columns = [ClosedDay.day, ClosedDay.reason]
    column_labels = {
        ClosedDay.day: "Fecha",
        ClosedDay.kind: "Qué pasa ese día",
        ClosedDay.reason: "Motivo",
        "national_holiday": "Feriado nacional",
        ClosedDay.created_at: "Cargado",
    }
    column_formatters = {
        ClosedDay.day: lambda m, a: f"{m.day:%d/%m/%Y}",
        "national_holiday": _holiday_formatter,
    }
    column_formatters_detail = column_formatters
    form_args = {
        "day": {
            "description": "Un día que el estudio no atiende. Los feriados nacionales ya cuentan "
            "solos: no hace falta cargarlos."
        },
        "reason": {"description": "Por ejemplo «cierre del estudio». Solo lo ve el estudio."},
    }
    page_size = 50

    def search_placeholder(self) -> str:
        return "motivo"

    def list_query(self, request: Request) -> Any:
        # The holidays the studio opens anyway are in the table of holidays, not here.
        return super().list_query(request).where(ClosedDay.kind == ClosedDayKind.CLOSED)

    async def list(self, request: Request) -> Any:
        pagination = await super().list(request)
        request.state.holidays = await _in_thread(self.holiday_table, request)
        return pagination

    def holiday_table(self, request: Request) -> HolidayTable:
        today = self.clock().astimezone(ZoneInfo(self.timezone)).date()
        years = [today.year, today.year + 1]
        asked = request.query_params.get("year", "")
        year = int(asked) if asked.isdigit() and int(asked) in years else today.year
        with self.session_maker() as session:
            found = session.execute(
                select(ClosedDay.day, ClosedDay.id).where(
                    ClosedDay.kind == ClosedDayKind.OPEN_ANYWAY,
                    ClosedDay.day.between(date(year, 1, 1), date(year, 12, 31)),
                )
            )
            open_anyway = {day: pk for day, pk in found}
        rows = [
            HolidayRow(day, f"{WEEKDAYS[day.weekday()]} {day:%d/%m}", name, open_anyway.get(day))
            for day, name in national_holidays(year)
        ]
        return HolidayTable(
            year=year,
            years=years,
            upcoming=[r for r in rows if r.day >= today],
            past=[r for r in rows if r.day < today],
        )

    def _back(self, request: Request, year: str) -> RedirectResponse:
        url = str(request.url_for("admin:list", identity=self.identity))
        return RedirectResponse(f"{url}?year={year}" if year.isdigit() else url, 303)

    @expose("/open-anyway", methods=["POST"])
    async def open_anyway(self, request: Request) -> Response:
        """«Abre igual» on a national holiday: the same insert as the form (checked, audited)."""
        form = await request.form()
        year = str(form.get("year") or "")
        try:
            day = date.fromisoformat(str(form.get("day") or ""))
            data = {"day": day, "kind": ClosedDayKind.OPEN_ANYWAY.value, "reason": ""}
            await self.insert_model(request, data)
        except ClosedDayProblem as exc:
            Flash.error(request, str(exc))
        except ValueError:  # not a date
            Flash.error(request, BAD_DAY)
        else:
            Flash.success(request, f"Listo: el {day:%d/%m/%Y} el estudio abre igual.")
        return self._back(request, year)

    @expose("/remove", methods=["POST"])
    async def remove(self, request: Request) -> Response:
        """«Quitar» / «Quitar abre igual»: the same delete as SQLAdmin's (audited)."""
        form = await request.form()
        year, pk = str(form.get("year") or ""), str(form.get("id") or "")
        if not (pk.isdigit() and await _in_thread(self._exists, int(pk))):
            Flash.error(request, "Ese día ya no estaba cargado.")
            return self._back(request, year)
        await self.delete_model(request, pk)
        Flash.success(request, "Listo: se quitó.")
        return self._back(request, year)

    def day_label(self, day: date) -> str:
        """ "jueves 24/12/2026"."""
        return f"{WEEKDAYS[day.weekday()]} {day:%d/%m/%Y}"

    def _exists(self, pk: int) -> bool:
        with self.session_maker() as session:
            return session.get(ClosedDay, pk) is not None

    def audit_ids(self, model: ClosedDay) -> dict[str, Any]:
        return {"closed_day_id": model.id, "day": model.day.isoformat(), "kind": str(model.kind)}

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        day, kind = data.get("day"), ClosedDayKind(data.get("kind") or ClosedDayKind.CLOSED)
        data["kind"] = kind.value
        data["reason"] = reason = (data.get("reason") or "").strip()
        holiday = national_holiday(day) if isinstance(day, date) else None
        if kind == ClosedDayKind.OPEN_ANYWAY and holiday is None:
            raise ClosedDayProblem(
                "Ese día no es feriado nacional: «Abre igual» es solo para feriados."
            )
        if kind == ClosedDayKind.CLOSED and holiday is not None:
            raise ClosedDayProblem(
                f"Ese día ya es feriado nacional ({holiday}): no hace falta cargarlo."
            )
        if not reason:
            if holiday is None:
                raise ClosedDayProblem("Escribí el motivo, por ejemplo «cierre del estudio».")
            data["reason"] = holiday
        if await _in_thread(self._taken, day):
            raise ClosedDayProblem("Ese día ya está cargado: quitalo primero para cambiarlo.")

    def _taken(self, day: Any) -> bool:
        # Before SQLAdmin adds the new row to a session: its own one.
        with self.session_maker() as session:
            return session.scalar(select(ClosedDay.id).where(ClosedDay.day == day)) is not None

    async def after_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> Response | None:
        await super().after_model_change(data, model, is_created, request)
        invalidate_calendar()  # this process sees the change right away
        return None

    async def after_model_delete(self, model: Any, request: Request) -> None:
        invalidate_calendar()


# --- Metrics ----------------------------------------------------------------------------


def per_day_summary(per_day: list[tuple[date, int]]) -> str:
    """What the compact chart of Métricas says without looking at it (its aria-label): the
    total of the period, the day with most conversations and the one with fewest (the first
    of them on a tie)."""
    if not per_day:
        return "Conversaciones por día: sin datos."
    total = sum(n for _, n in per_day)
    most = max(per_day, key=lambda item: item[1])
    fewest = min(per_day, key=lambda item: item[1])
    return (
        f"Conversaciones por día, últimos {len(per_day)} días: {total} en total. "
        f"El día con más: {most[0]:%d/%m}, con {most[1]}. "
        f"El día con menos: {fewest[0]:%d/%m}, con {fewest[1]}."
    )


class MetricsView(AdminOnly, BaseView):
    menu_group = "system"  # its block of «Administración» (app.admin.MENU_GROUPS)
    name = "Métricas"
    icon = "fa-solid fa-chart-line"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default

    @expose("/metrics", methods=["GET"], identity="metrics")
    async def metrics_page(self, request: Request) -> Response:
        with self.session_maker() as session:
            now = datetime.now(UTC)
            metrics = await _in_thread(compute_metrics, session, now, self.timezone)
            claims = await _in_thread(compute_claim_metrics, session, now, self.timezone)
        per_day = metrics.conversations_per_day
        peak = max((n for _, n in per_day), default=0)
        return await self.templates.TemplateResponse(
            request,
            "metrics.html",
            {
                "title": "Métricas",
                "m": metrics,
                "c": claims,
                "peak": peak or 1,
                "per_day_summary": per_day_summary(per_day),
                "tools": [(labels.tool(name), count) for name, count in metrics.top_tools],
            },
        )


# --- Inbox settings: WhatsApp templates and quick replies -------------------------------


class WaTemplateAdmin(AdminOnly, AuditedView, model=WaTemplate):
    """Approved templates the inbox offers when the 24-hour window is closed."""

    menu_group = "bot"  # its block of «Administración» (app.admin.MENU_GROUPS)
    name = "Plantilla de WhatsApp"
    name_plural = "Plantillas de WhatsApp"
    icon = "fa-solid fa-envelope-open-text"
    audit_name = "wa_template"
    can_delete = False  # deactivated instead: sent messages keep their text anyway
    column_list = [WaTemplate.label, WaTemplate.name, WaTemplate.language, WaTemplate.active]
    # Hidden under 1280 px in the list (templates/sqladmin/list.html); the record has them.
    column_secondary = ["language"]
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
    column_formatters = {
        WaTemplate.language: lambda m, a: labels.LANGUAGES.get(m.language, m.language)
    }
    column_formatters_detail = column_formatters
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


class QuickReplyAdmin(AdminOnly, RemembersRecord, AuditedView, model=QuickReply):
    """Saved texts the operators insert in a reply with one click."""

    menu_group = "bot"  # its block of «Administración» (app.admin.MENU_GROUPS)
    name = "Respuesta rápida"
    name_plural = "Respuestas rápidas"
    icon = "fa-solid fa-bolt"
    audit_name = "quick_reply"
    column_list = [QuickReply.title, QuickReply.sort_order, QuickReply.active]
    column_details_list = [*column_list, QuickReply.content, QuickReply.updated_at]
    column_sortable_list = [QuickReply.title, QuickReply.sort_order]
    column_default_sort = [(QuickReply.sort_order, False), (QuickReply.title, False)]
    form_columns = [QuickReply.title, QuickReply.content, QuickReply.sort_order, QuickReply.active]
    form_overrides = {"sort_order": NumberField}  # a whole number, 0 to 999 (app.admin.numbers)
    form_widget_args = {"content": {"rows": 5}}
    column_labels = {
        QuickReply.title: "Título",
        QuickReply.content: "Texto",
        QuickReply.sort_order: "Orden",
        QuickReply.active: "Activa",
        QuickReply.updated_at: "Actualizada",
    }
    form_args = {
        "sort_order": {
            "description": "Las de número más chico aparecen primero. De 0 a 999.",
            "bounds": QUICK_REPLY_ORDER,
        },
        "content": {"description": "Se inserta tal cual en la caja de respuesta (editable)."},
    }
    page_size = 50
