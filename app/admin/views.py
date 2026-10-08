"""Views of the admin panel. Texts in Spanish (voseo); every change is audited in bot_events
(app.admin.audit). Never registered here: verification codes, people's DNI, .env values.
Views with AdminOnly first are only for admins (operators get 403 and do not see them)."""

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from typing import Any, ClassVar
from urllib.parse import urlparse

import anyio
from markupsafe import Markup
from sqladmin import BaseView, ModelView, expose
from sqladmin.formatters import BASE_FORMATTERS
from sqlalchemy.orm import object_session
from starlette.requests import Request
from starlette.responses import Response
from wtforms import ValidationError

from app.admin import formatting, labels
from app.admin.audit import changed_fields, log_admin_action
from app.admin.auth import AdminOnly, admin_user
from app.admin.filters import RelatedFilter, ValuesFilter, YesNoFilter
from app.admin.metrics import compute_metrics
from app.bot.bot_config import invalidate_bot_config, parse_hour, parse_weekdays
from app.config import Settings
from app.db.models import (
    BotSettings,
    Building,
    BuildingInfo,
    BuildingInfoCategory,
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
        Building.claims_bot_enabled,
    ]
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
    name = "Información de edificio"
    name_plural = "Información de edificios"
    icon = "fa-solid fa-book"
    audit_name = "building_info"
    column_list = [
        BuildingInfo.building,
        BuildingInfo.category,
        BuildingInfo.title,
        BuildingInfo.updated_at,
    ]
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


# Never the .env value itself: only that there is a default.
_DEFAULT_TEXT = "Si lo dejás vacío, el bot usa el texto por defecto."
_DEFAULT_VALUE = "Si lo dejás vacío, el bot usa el valor por defecto."


class BotSettingsAdmin(AdminOnly, AuditedView, model=BotSettings):
    """The single row of bot settings; values never shown here: the .env ones."""

    name = "Configuración del bot"
    name_plural = "Configuración del bot"
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
            "description": "El bot saluda con este texto en el primer mensaje. " + _DEFAULT_TEXT
        },
        "office_hours_start": {
            "description": "HH:MM. " + _DEFAULT_VALUE,
            "validators": [_valid_hour],
        },
        "office_hours_end": {
            "description": "HH:MM. " + _DEFAULT_VALUE,
            "validators": [_valid_hour],
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
            {
                "title": "Métricas",
                "m": metrics,
                "peak": peak or 1,
                "tools": [(labels.tool(name), count) for name, count in metrics.top_tools],
            },
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
