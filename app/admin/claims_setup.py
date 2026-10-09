"""Panel pages of the claims' set-up, admins only (AdminOnly: operators neither see them nor
open them, 403 on the server; each POST also checks require_admin):

- "Proveedores" (SQLAdmin): the companies that attend problems. Deactivated, never deleted.
  The WhatsApp is accepted in any format and stored normalized (app.claims.setup); one per
  active provider. Saving a number that is also of an owner, a tenant or a contact of
  Conversaciones only warns.
- "Tipos de problema" (SQLAdmin): the kinds of problem, with the WhatsApp list's limits
  (24 / 72 characters) checked on the server too. Deactivated, never deleted.
- "Reclamos por edificio" (own pages): the buildings, and in each one the table "Problemas y
  proveedores" saved whole, copied from another building, with its warnings and where the
  WhatsApp list splits in two.

Rules live in app.claims.setup; every change is audited in bot_events (admin_action).
"""

from typing import Any, ClassVar

from markupsafe import Markup
from sqladmin import BaseView, expose
from sqladmin.flash import Flash
from sqlalchemy import Select, func, select
from sqlalchemy.orm import object_session, selectinload
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from wtforms import SelectMultipleField, ValidationError

from app.admin import formatting, help, labels
from app.admin.audit import changed_fields, log_admin_action
from app.admin.auth import AdminOnly, admin_user, require_admin
from app.admin.filters import YesNoFilter
from app.admin.views import AuditedView
from app.bot.bot_config import parse_hour, parse_weekdays
from app.claims.claim_config import invalidate_claim_config
from app.claims.schedule import parse_dates
from app.claims.setup import (
    Choice,
    SetupProblem,
    TableRow,
    assigned_buildings,
    building_table,
    copy_building_table,
    normalize_provider_whatsapp,
    provider_with_whatsapp,
    save_building_table,
    split_for_whatsapp,
    table_warnings,
    whatsapp_in_use_warning,
)
from app.db.models import (
    Building,
    BuildingClaimCategory,
    ClaimCategory,
    ClaimScope,
    ClaimSettings,
    Provider,
)

MAX_BUILDINGS_LISTED = 3

# --- Providers ------------------------------------------------------------------------------


def _whatsapp_field(form: Any, field: Any) -> None:
    """Accepts any format; the field keeps the normalized number (or None)."""
    try:
        field.data = normalize_provider_whatsapp(field.data)
    except SetupProblem as exc:
        raise ValidationError(str(exc)) from exc


def _shown_phone(value: Any) -> Any:
    """The stored number as people write it ("351 555-0101") in the edit form."""
    return formatting.phone(value) if value else value


def _served(provider: Provider) -> list[BuildingClaimCategory]:
    return [
        a for a in provider.assignments if a.enabled and a.category.active and a.building.active
    ]


def served_buildings(provider: Provider, limit: int | None = MAX_BUILDINGS_LISTED) -> str:
    names = sorted({formatting.building(a.building.name) for a in _served(provider)})
    if not names:
        return "—"
    if limit is None or len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])} y {len(names) - limit} más"


def served_problems(provider: Provider) -> str:
    rows = sorted(_served(provider), key=lambda a: (a.category.sort_order, a.category.name))
    titles = list(dict.fromkeys(a.category.list_title for a in rows))
    return ", ".join(titles) or "—"


def _with_assignments(stmt: Select) -> Select:
    assignments = selectinload(Provider.assignments)
    return stmt.options(
        assignments.selectinload(BuildingClaimCategory.building),
        assignments.selectinload(BuildingClaimCategory.category),
    )


class ProviderAdmin(AdminOnly, AuditedView, model=Provider):
    name = "Proveedor"
    name_plural = "Proveedores"
    icon = "fa-solid fa-helmet-safety"
    audit_name = "provider"
    can_delete = False  # deactivated instead: buildings may still point to it
    column_list = [
        Provider.name,
        Provider.contact_name,
        Provider.whatsapp_e164,
        "buildings",
        "problems",
        Provider.active,
    ]
    column_details_list = [
        *column_list,
        Provider.other_phone,
        Provider.email,
        Provider.notes,
        Provider.created_at,
        Provider.updated_at,
    ]
    column_searchable_list = [
        Provider.name,
        Provider.contact_name,
        Provider.email,
        Provider.whatsapp_e164,
    ]
    column_sortable_list = [Provider.name, Provider.active]
    column_default_sort = [(Provider.name, False)]
    column_filters = [YesNoFilter(Provider.active, title="Activo")]
    column_formatters = {
        Provider.whatsapp_e164: lambda m, a: formatting.phone(m.whatsapp_e164),
        "buildings": lambda m, a: served_buildings(m),
        "problems": lambda m, a: served_problems(m),
    }
    column_formatters_detail = {
        **column_formatters,
        "buildings": lambda m, a: served_buildings(m, limit=None),
    }
    form_columns = [
        Provider.name,
        Provider.contact_name,
        Provider.whatsapp_e164,
        Provider.other_phone,
        Provider.email,
        Provider.notes,
        Provider.active,
    ]
    form_widget_args = {"notes": {"rows": 4}, "whatsapp_e164": {"inputmode": "tel"}}
    form_args = {
        "name": {"description": "El nombre de la empresa, por ejemplo «Ascensores del Centro»."},
        "whatsapp_e164": {
            "description": "Como lo tengas, con el código de área (351 555-0101). El bot le va "
            "a avisar de los reclamos por acá.",
            "validators": [_whatsapp_field],
            "filters": [_shown_phone],
        },
        "other_phone": {"description": "Otro teléfono para llamarlo (el bot no lo usa)."},
        "notes": {"description": "Lo ve solo el estudio."},
        "active": {
            "description": "Si lo desactivás, deja de aparecer para elegir. Los edificios que "
            "lo tenían asignado siguen mostrándolo hasta que elijas otro."
        },
    }
    column_labels = {
        Provider.name: "Empresa",
        Provider.contact_name: "Contacto",
        Provider.whatsapp_e164: "WhatsApp",
        "buildings": "Edificios",
        "problems": "Problemas que atiende",
        Provider.other_phone: "Otro teléfono",
        Provider.email: "Email",
        Provider.notes: "Notas",
        Provider.active: "Activo",
        Provider.created_at: "Creado",
        Provider.updated_at: "Actualizado",
    }
    page_size = 50

    def search_placeholder(self) -> str:
        return "empresa, contacto, email o WhatsApp"

    def list_query(self, request: Request) -> Select:
        return _with_assignments(super().list_query(request))

    def details_query(self, request: Request) -> Select:
        return _with_assignments(super().details_query(request))

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        e164 = data.get("whatsapp_e164")
        if e164 and data.get("active", True):
            with self.session_maker() as session:
                other = provider_with_whatsapp(session, e164, exclude_id=model.id)
            if other is not None:
                raise SetupProblem(
                    f"Ese WhatsApp ya lo tiene {other.name}. Desactivá ese proveedor o usá otro "
                    "número."
                )
        request.state.provider_before = None if is_created else (model.whatsapp_e164, model.active)
        await super().on_model_change(data, model, is_created, request)

    async def after_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> Response | None:
        await super().after_model_change(data, model, is_created, request)
        session = object_session(model)
        if session is None:
            return None
        before = getattr(request.state, "provider_before", None)
        new_number = before is None or before[0] != model.whatsapp_e164
        if new_number and (warning := whatsapp_in_use_warning(session, model.whatsapp_e164)):
            Flash.warning(request, warning)
        deactivated = before is not None and before[1] and not model.active
        if deactivated and (count := assigned_buildings(session, model.id)):
            Flash.warning(
                request,
                f"{model.name} está asignado en {count} edificio(s): hasta que elijas otro "
                "proveedor en «Reclamos por edificio», esos reclamos no le van a llegar.",
            )
        return None


# --- Kinds of problem -----------------------------------------------------------------------

SCOPE_CHOICES = [(s.value, labels.CLAIM_SCOPE[s]) for s in ClaimScope]


class ClaimCategoryAdmin(AdminOnly, AuditedView, model=ClaimCategory):
    name = "Tipo de problema"
    name_plural = "Tipos de problema"
    icon = "fa-solid fa-list-check"
    audit_name = "claim_category"
    can_delete = False  # deactivated instead
    column_list = [
        ClaimCategory.sort_order,
        ClaimCategory.name,
        ClaimCategory.list_title,
        ClaimCategory.scope,
        ClaimCategory.urgent,
        ClaimCategory.active,
    ]
    column_details_list = [
        ClaimCategory.name,
        ClaimCategory.list_title,
        ClaimCategory.list_description,
        ClaimCategory.scope,
        ClaimCategory.urgent,
        ClaimCategory.follow_up_question,
        ClaimCategory.safety_text,
        ClaimCategory.emergency_phone,
        ClaimCategory.sort_order,
        ClaimCategory.active,
        ClaimCategory.updated_at,
    ]
    column_searchable_list = [ClaimCategory.name, ClaimCategory.list_title]
    column_sortable_list = [ClaimCategory.sort_order, ClaimCategory.name]
    column_default_sort = [(ClaimCategory.sort_order, False), (ClaimCategory.name, False)]
    column_filters = [YesNoFilter(ClaimCategory.active, title="Activo")]
    column_formatters = {
        ClaimCategory.scope: lambda m, a: labels.label(labels.CLAIM_SCOPE, m.scope),
    }
    column_formatters_detail = column_formatters
    form_columns = [
        ClaimCategory.name,
        ClaimCategory.list_title,
        ClaimCategory.list_description,
        ClaimCategory.scope,
        ClaimCategory.urgent,
        ClaimCategory.follow_up_question,
        ClaimCategory.safety_text,
        ClaimCategory.emergency_phone,
        ClaimCategory.sort_order,
        ClaimCategory.active,
    ]
    # data-count: panel.js shows "12 / 24" under the field.
    form_widget_args = {
        "list_title": {"maxlength": 24, "data-count": "24"},
        "list_description": {"maxlength": 72, "data-count": "72"},
        "safety_text": {"rows": 4},
    }
    form_args = {
        "name": {"description": "El problema completo, como lo va a leer el estudio."},
        "list_title": {
            "description": "Lo que la persona ve en la lista de WhatsApp. Hasta 24 caracteres."
        },
        "list_description": {
            "description": "Opcional: una aclaración que se ve debajo del título en la lista. "
            "Hasta 72 caracteres."
        },
        "scope": {"description": help.SCOPE_HELP},
        "urgent": {"description": "Los reclamos de este problema se marcan como urgentes."},
        "follow_up_question": {
            "description": "Opcional: el bot la pregunta apenas la persona elige este problema."
        },
        "safety_text": {
            "description": "Opcional: el bot lo dice antes que nada, apenas la persona elige "
            "este problema. Para lo que puede ser peligroso, como el gas."
        },
        "emergency_phone": {
            "description": "Opcional: un teléfono de emergencias que el bot da junto con eso."
        },
        "sort_order": {
            "description": "Los de número más chico aparecen primero. En cada edificio se puede "
            "cambiar."
        },
        "active": {"description": "Si lo desactivás, deja de aparecer en todos los edificios."},
    }
    column_labels = {
        ClaimCategory.name: "Problema",
        ClaimCategory.list_title: "Título en la lista",
        ClaimCategory.list_description: "Descripción en la lista",
        ClaimCategory.scope: "Alcance",
        ClaimCategory.urgent: "Urgente",
        ClaimCategory.follow_up_question: "Pregunta después de elegirlo",
        ClaimCategory.safety_text: "Aviso de seguridad",
        ClaimCategory.emergency_phone: "Teléfono de emergencias",
        ClaimCategory.sort_order: "Orden",
        ClaimCategory.active: "Activo",
        ClaimCategory.updated_at: "Actualizado",
    }
    page_size = 50

    def search_placeholder(self) -> str:
        return "problema o título"

    async def scaffold_form(self, rules: Any = None) -> Any:
        form = await super().scaffold_form(rules)
        # As in BuildingInfoAdmin: the values in Spanish, and the stored enum compared by value.
        form.scope.kwargs["choices"] = SCOPE_CHOICES
        form.scope.kwargs["coerce"] = lambda v: v.value if isinstance(v, ClaimScope) else str(v)
        return form

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        # The browser's maxlength is only a help: the limits hold on the server too.
        for field, limit in (("list_title", 24), ("list_description", 72)):
            value = (data.get(field) or "").strip()
            if len(value) > limit:
                label = self.column_labels[getattr(ClaimCategory, field)]
                raise SetupProblem(f"{label}: hasta {limit} caracteres (tiene {len(value)}).")
            data[field] = value or None
        if not data.get("list_title"):
            raise SetupProblem("El título en la lista no puede quedar vacío.")
        await super().on_model_change(data, model, is_created, request)


# --- Problems and providers of each building ------------------------------------------------


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_table(form: FormData, rows: list[TableRow]) -> dict[int, Choice]:
    """The table's form: enabled_<id> (checkbox), provider_<id> ("" = the studio) and
    order_<id>, for each kind of problem shown."""
    choices = {}
    for row in rows:
        cid = row.category.id
        if f"order_{cid}" not in form:
            continue  # not in the page the admin saw (a kind added meanwhile)
        order = _int(form.get(f"order_{cid}"))
        if order is None:
            raise SetupProblem(f"{row.category.list_title}: el orden tiene que ser un número.")
        raw_provider = str(form.get(f"provider_{cid}", "")).strip()
        provider_id = _int(raw_provider) if raw_provider else None
        if raw_provider and provider_id is None:
            raise SetupProblem(f"{row.category.list_title}: elegí quién lo atiende.")
        choices[cid] = Choice(
            enabled=form.get(f"enabled_{cid}") == "on", provider_id=provider_id, sort_order=order
        )
    return choices


def _redirect(request: Request, identity: str, **params: Any) -> RedirectResponse:
    return RedirectResponse(request.url_for(f"admin:view-{identity}", **params), status_code=302)


class BuildingClaimsView(AdminOnly, BaseView):
    name = "Reclamos por edificio"
    icon = "fa-solid fa-table-list"
    session_maker: ClassVar[Any] = None

    # The menu links to this page: keep it the first one of the class.
    @expose("/building-claims", methods=["GET"], identity="building-claims")
    async def buildings_page(self, request: Request) -> Response:
        with self.session_maker() as session:
            buildings = list(session.scalars(select(Building).where(Building.active.is_(True))))
            counts = {
                (building_id, studio): count
                for building_id, studio, count in session.execute(
                    select(
                        BuildingClaimCategory.building_id,
                        BuildingClaimCategory.provider_id.is_(None),
                        func.count(),
                    )
                    .join(ClaimCategory)
                    .where(BuildingClaimCategory.enabled.is_(True), ClaimCategory.active.is_(True))
                    .group_by(
                        BuildingClaimCategory.building_id,
                        BuildingClaimCategory.provider_id.is_(None),
                    )
                )
            }
        rows = sorted(
            (
                {
                    "building": b,
                    "name": formatting.building(b.name),
                    "enabled": counts.get((b.id, True), 0) + counts.get((b.id, False), 0),
                    "studio": counts.get((b.id, True), 0),
                }
                for b in buildings
            ),
            key=lambda r: r["name"],
        )
        return await self.templates.TemplateResponse(
            request, "building_claims_list.html", {"title": "Reclamos por edificio", "rows": rows}
        )

    @expose(
        "/building-claims/{building_id:int}",
        methods=["GET", "POST"],
        identity="building-claims-table",
    )
    async def table_page(self, request: Request) -> Response:
        building_id = request.path_params["building_id"]
        error = None
        with self.session_maker() as session:
            building = session.get(Building, building_id)
            if building is None:
                return Response("Edificio inexistente", status_code=404)
            if request.method == "POST":
                require_admin(request)
                form = await request.form()
                try:
                    choices = parse_table(form, building_table(session, building_id))
                    change = save_building_table(session, building_id, choices)
                except SetupProblem as exc:
                    session.rollback()
                    error = str(exc)
                else:
                    if change.category_ids:
                        log_admin_action(
                            session,
                            admin_user(request),
                            "building_claims_saved",
                            building_id=building_id,
                            category_ids=change.category_ids,
                            fields=change.fields,
                        )
                    session.commit()
                    Flash.success(
                        request, "Tabla guardada." if change.category_ids else "No había cambios."
                    )
                    return _redirect(request, "building-claims-table", building_id=building_id)
            rows = building_table(session, building_id)
            enabled = [row for row in rows if row.enabled]
            _, more = split_for_whatsapp(enabled)
            providers = list(
                session.scalars(
                    select(Provider).where(Provider.active.is_(True)).order_by(Provider.name)
                )
            )
            others = [
                (b.id, formatting.building(b.name))
                for b in session.scalars(
                    select(Building).where(Building.active.is_(True), Building.id != building_id)
                )
            ]
            return await self.templates.TemplateResponse(
                request,
                "building_claims.html",
                {
                    "title": f"Problemas y proveedores · {formatting.building(building.name)}",
                    "building": building,
                    "rows": rows,
                    "enabled_count": len(enabled),
                    "second_list_from": more[0].category.id if more else None,
                    "second_list_ids": {row.category.id for row in more},
                    "providers": providers,
                    "warnings": table_warnings(rows),
                    "others": sorted(others, key=lambda o: o[1]),
                    "scope_labels": labels.CLAIM_SCOPE,
                    "studio_label": labels.STUDIO,
                    "error": error,
                },
                status_code=400 if error else 200,
            )

    @expose(
        "/building-claims/{building_id:int}/copy",
        methods=["POST"],
        identity="building-claims-copy",
    )
    async def copy_page(self, request: Request) -> Response:
        require_admin(request)
        building_id = request.path_params["building_id"]
        form = await request.form()
        source_id = _int(form.get("source_id"))
        with self.session_maker() as session:
            try:
                if source_id is None:
                    raise SetupProblem("Elegí de qué edificio copiar.")
                change = copy_building_table(session, building_id, source_id)
            except SetupProblem as exc:
                session.rollback()
                Flash.error(request, str(exc))
            else:
                log_admin_action(
                    session,
                    admin_user(request),
                    "building_claims_copied",
                    building_id=building_id,
                    from_building_id=source_id,
                    category_ids=change.category_ids,
                    fields=change.fields,
                )
                session.commit()
                changed = len(change.category_ids)
                source = session.get(Building, source_id)
                name = formatting.building(source.name) if source else ""
                Flash.success(
                    request,
                    f"Copiado de {name}: cambiaron {changed} problema(s)."
                    if changed
                    else f"Ya estaba igual que {name}.",
                )
        return _redirect(request, "building-claims-table", building_id=building_id)


# --- "Configuración de reclamos" ------------------------------------------------------------

WEEKDAY_CHOICES = list(enumerate(["Lunes", "Martes", "Miércoles", "Jueves", "Viernes",
                                  "Sábado", "Domingo"]))  # fmt: skip


def _checkboxes(field: Any, **kwargs: Any) -> Markup:
    """Inline check boxes (SQLAdmin's "form-control" class would draw them as text boxes)."""
    boxes = [
        Markup(
            '<label class="form-check form-check-inline">'
            '<input class="form-check-input" type="checkbox" name="{name}" id="{id}" '
            'value="{value}"{checked}><span class="form-check-label">{label}</span></label>'
        ).format(
            name=field.name,
            id=f"{field.id}-{value}",
            value=value,
            label=label,
            checked=Markup(" checked") if checked else "",
        )  # fmt: skip
        for value, label, checked, _attrs in field.iter_choices()
    ]
    return Markup('<div id="{id}">{boxes}</div>').format(id=field.id, boxes=Markup("").join(boxes))


class WeekdaysField(SelectMultipleField):
    """The days as check boxes Lunes ... Domingo; stored as "0,1,2" (0 = Monday)."""

    widget = staticmethod(_checkboxes)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["choices"] = WEEKDAY_CHOICES
        kwargs["coerce"] = int
        super().__init__(*args, **kwargs)

    def process_data(self, value: Any) -> None:
        if isinstance(value, str):
            value = parse_weekdays(value) or []
        super().process_data(value)

    def pre_validate(self, form: Any) -> None:
        if not self.data:
            raise ValidationError("Elegí al menos un día.")
        super().pre_validate(form)


def _valid_hour(form: Any, field: Any) -> None:
    if parse_hour(str(field.data or "")) is None:
        raise ValidationError("Usá el formato HH:MM, por ejemplo 08:00.")


def _valid_holidays(form: Any, field: Any) -> None:
    _dates, wrong = parse_dates(field.data or "")
    if wrong:
        raise ValidationError(
            f"Estas fechas no se entienden: {', '.join(wrong)}. Una por renglón, como 24/12/2026."
        )


def _positive(form: Any, field: Any) -> None:
    if field.data is None or field.data < 1:
        raise ValidationError("Tiene que ser un número entero desde 1.")


class ClaimSettingsAdmin(AdminOnly, AuditedView, model=ClaimSettings):
    """The single row of "Configuración de reclamos" (app.claims.claim_config): the menu opens
    its form directly, like "Configuración del bot"."""

    name = "Configuración de reclamos"
    name_plural = "Configuración de reclamos"
    icon = "fa-solid fa-clock"
    audit_name = "claim_settings"
    can_create = False
    can_delete = False
    column_list = [ClaimSettings.id, ClaimSettings.updated_at]
    column_details_exclude_list = [ClaimSettings.id]
    form_columns = [
        ClaimSettings.provider_weekdays,
        ClaimSettings.provider_hours_start,
        ClaimSettings.provider_hours_end,
        ClaimSettings.holidays,
        ClaimSettings.urgent_any_time,
        ClaimSettings.reminder_hours,
        ClaimSettings.reminder_urgent_minutes,
        ClaimSettings.alert_hours,
        ClaimSettings.alert_urgent_minutes,
        ClaimSettings.stale_days,
    ]
    form_overrides = {"provider_weekdays": WeekdaysField}
    form_widget_args = {"holidays": {"rows": 5, "placeholder": "24/12/2026\n25/12/2026"}}
    column_labels = {
        ClaimSettings.id: "Configuración",
        ClaimSettings.provider_weekdays: "Días para escribirles a los proveedores",
        ClaimSettings.provider_hours_start: "Horario de proveedores: desde",
        ClaimSettings.provider_hours_end: "Horario de proveedores: hasta",
        ClaimSettings.holidays: "Feriados",
        ClaimSettings.urgent_any_time: "Urgentes a cualquier hora",
        ClaimSettings.reminder_hours: "Recordatorio al proveedor (horas de horario)",
        ClaimSettings.reminder_urgent_minutes: "Recordatorio si es urgente (minutos)",
        ClaimSettings.alert_hours: "Aviso al estudio si no confirma (horas de horario)",
        ClaimSettings.alert_urgent_minutes: "Aviso al estudio si es urgente (minutos)",
        ClaimSettings.stale_days: "Aviso si sigue sin solucionar (días)",
        ClaimSettings.updated_at: "Actualizado",
    }
    column_formatters = {ClaimSettings.id: lambda m, a: "Configuración de reclamos"}
    form_args = {
        "provider_weekdays": {
            "description": "Los días en que el bot les escribe a los proveedores (hora de Córdoba)."
        },
        "provider_hours_start": {
            "description": "HH:MM. Fuera de este horario, un reclamo que no es urgente se le "
            "manda al proveedor cuando abre.",
            "validators": [_valid_hour],
        },
        "provider_hours_end": {"description": "HH:MM.", "validators": [_valid_hour]},
        "holidays": {
            "description": "Una fecha por renglón, como 24/12/2026. Esos días no cuentan como "
            "horario.",
            "validators": [_valid_holidays],
        },
        "urgent_any_time": {
            "description": "Los reclamos urgentes (gas, ascensor...) se le mandan al proveedor "
            "aunque sea de noche o feriado."
        },
        "reminder_hours": {
            "description": "Si no tocó «Recibido», se le recuerda una vez, contando solo horas "
            "del horario.",
            "validators": [_positive],
        },
        "reminder_urgent_minutes": {
            "description": "Lo mismo para los urgentes, en minutos a cualquier hora.",
            "validators": [_positive],
        },
        "alert_hours": {
            "description": "Si sigue sin confirmar, el panel avisa (con sonido). Tiene que ser "
            "más que el recordatorio.",
            "validators": [_positive],
        },
        "alert_urgent_minutes": {
            "description": "Lo mismo para los urgentes, en minutos. Más que su recordatorio.",
            "validators": [_positive],
        },
        "stale_days": {
            "description": "Un reclamo confirmado, o que tiene el estudio, y que sigue sin "
            "solucionar: el panel avisa a los tantos días.",
            "validators": [_positive],
        },
    }

    def audit_ids(self, model: ClaimSettings) -> dict[str, Any]:
        return {}

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        days = data.get("provider_weekdays") or []
        data["provider_weekdays"] = ",".join(str(d) for d in sorted(set(days)))
        start = parse_hour(str(data.get("provider_hours_start") or "")) or ""
        end = parse_hour(str(data.get("provider_hours_end") or "")) or ""
        data["provider_hours_start"], data["provider_hours_end"] = start, end
        if end <= start:
            raise SetupProblem("El horario «hasta» tiene que ser después del «desde».")
        if data.get("alert_hours", 0) <= data.get("reminder_hours", 0):
            raise SetupProblem(
                "El aviso al estudio tiene que ser después del recordatorio (más horas)."
            )
        if data.get("alert_urgent_minutes", 0) <= data.get("reminder_urgent_minutes", 0):
            raise SetupProblem(
                "El aviso al estudio de los urgentes tiene que ser después de su recordatorio "
                "(más minutos)."
            )
        fields = changed_fields(model, data)
        session = object_session(model)
        if fields and session is not None:
            log_admin_action(session, admin_user(request), "claim_settings_changed", fields=fields)

    async def after_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> Response | None:
        invalidate_claim_config()  # this process sees the change right away
        return None
