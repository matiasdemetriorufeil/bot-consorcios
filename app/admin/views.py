"""Views of the admin panel. Texts in Spanish (voseo); every change is audited in bot_events
(app.admin.audit). Never registered here: verification codes, people's DNI, .env values.
Views with AdminOnly first are only for admins (operators get 403 and do not see them)."""

from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any, ClassVar
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import anyio
from sqladmin import BaseView, ModelView, expose
from sqladmin.filters import BooleanFilter, ForeignKeyFilter, StaticValuesFilter
from sqlalchemy.orm import object_session
from starlette.requests import Request
from starlette.responses import Response
from wtforms import ValidationError

from app.admin.audit import changed_fields, log_admin_action
from app.admin.auth import AdminOnly, admin_user
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

_DATE_FORMAT = "%d/%m/%Y %H:%M"


def _local(value: datetime | None, timezone: str) -> str:
    return value.astimezone(ZoneInfo(timezone)).strftime(_DATE_FORMAT) if value else ""


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
    # The name is only shown: the roster sync overwrites it (app/sync/roster.py).
    form_columns = [Building.name, Building.address, Building.active, Building.pilot]
    form_widget_args = {"name": {"readonly": True}}
    form_args = {
        "name": {
            "description": "Viene de ConsorPlus: se actualiza con la sincronización y no "
            "se edita acá."
        }
    }
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
