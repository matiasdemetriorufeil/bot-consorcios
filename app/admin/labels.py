"""Every stored value the panel shows, in Spanish, in one place: views, SQLAdmin formatters
and filters all read these (never the raw value: "pending", "consorplus", "nightly"...)."""

from typing import Any

from app.channels.handoff import REASONS
from app.db.models import (
    BuildingInfoCategory,
    DataSource,
    PanelRole,
    PersonRole,
    ReservationSource,
    SyncJob,
    SyncKind,
    SyncStatus,
    VerificationRequestStatus,
    WaConversationStatus,
)

VERIFICATION_STATUS = {
    VerificationRequestStatus.PENDING: "Pendiente",
    VerificationRequestStatus.APPROVED: "Aprobada",
    VerificationRequestStatus.REJECTED: "Rechazada",
}
PHONE_SOURCE = {
    DataSource.CONSORPLUS: "ConsorPlus",
    DataSource.MANUAL: "Cargado a mano",
    DataSource.BOT_VERIFIED: "Por WhatsApp",
}
# By priority (app.admin.phones.phone_status): a phone in conflict may also be to review...
PHONE_STATUS = {
    "conflict": "En conflicto",
    "review": "A revisar",
    "verified": "Verificado",
    "unverified": "Sin verificar",
}
CONVERSATION_STATUS = {
    WaConversationStatus.BOT: "Con el bot",
    WaConversationStatus.WAITING_HUMAN: "Esperando persona",
    WaConversationStatus.HUMAN: "Con una persona",
    WaConversationStatus.RESOLVED: "Resuelta",
}
INFO_CATEGORY = {
    BuildingInfoCategory.REGLAMENTO: "Reglamento",
    BuildingInfoCategory.HORARIOS: "Horarios",
    BuildingInfoCategory.CONTACTOS: "Contactos",
    BuildingInfoCategory.EMERGENCIAS: "Emergencias",
    BuildingInfoCategory.OTROS: "Otros",
}
SYNC_KIND = {SyncKind.NIGHTLY: "Nocturna", SyncKind.LIVE: "En el momento"}
SYNC_JOB = {
    SyncJob.ROSTER: "Propietarios y unidades",
    SyncJob.DEBT: "Deudas",
    SyncJob.CANARY: "Prueba de conexión",
}
SYNC_STATUS = {
    SyncStatus.OK: "Completa",
    SyncStatus.PARTIAL: "Con errores",
    SyncStatus.FAILED: "Falló",
}
RESERVATION_SOURCE = {
    ReservationSource.BOT: "Por WhatsApp",
    ReservationSource.PANEL: "Desde el panel",
}
PERSON_ROLE = {PersonRole.OWNER: "propietario", PersonRole.TENANT: "inquilino"}
PANEL_ROLE = {PanelRole.ADMIN: "Admin", PanelRole.OPERATOR: "Operadora"}

# Handoff reasons: a short label (the inbox's tags) and the long text of
# app.channels.handoff.REASONS (the handoff summary).
REASON_SHORT = {
    "person_requested": "Pide una persona",
    "upset": "Persona molesta",
    "debt_claim": "Reclamo de deuda",
    "payment_not_credited": "Pago no acreditado",
    "payment_plan": "Plan de pago",
    "emergency": "Urgencia",
    "no_answer": "Sin respuesta",
    "other": "Otro motivo",
    "technical_error": "Error técnico",
    "non_pilot": "Fuera del piloto",
    "window_closed": "Ventana cerrada",
}

# The bot's tools (app.bot), for the metrics.
TOOLS = {
    "get_debt": "Consultar deuda",
    "get_payment_info": "Código de pago",
    "find_unit": "Buscar unidad",
    "get_building_info": "Información del edificio",
    "handoff_to_human": "Derivar a una persona",
    "start_email_verification": "Verificar por email",
    "confirm_email_code": "Confirmar código",
    "request_operator_verification": "Pedir verificación",
    "sum_availability": "Disponibilidad del SUM",
    "book_sum": "Reservar SUM",
    "cancel_sum_reservation": "Cancelar reserva",
    "my_sum_reservations": "Mis reservas",
    "offer_choices": "Ofrecer opciones",
}
UNKNOWN_TOOL = "Otra herramienta"

# Counters of sync_runs.stats (app.sync.roster.RosterReport, app.sync.nightly.DebtReport,
# app.sync.canary).
SYNC_STATS = {
    "buildings_ok": "Edificios leídos",
    "buildings_failed": "Edificios con error",
    "units": "Unidades",
    "units_ok": "Unidades consultadas",
    "units_failed": "Unidades con error",
    "units_skipped": "Unidades sin consultar (ConsorPlus caído)",
    "units_with_debt": "Unidades con deuda",
    "units_deactivated": "Unidades dadas de baja",
    "debt_lines": "Renglones de deuda",
    "snapshots_replaced": "Deudas reemplazadas",
    "snapshots_purged": "Deudas viejas borradas",
    "people": "Personas",
    "people_created": "Personas nuevas",
    "phones_valid": "Teléfonos válidos",
    "phones_invalid": "Teléfonos inválidos",
    "phones_removed": "Teléfonos quitados",
    "needs_review": "Teléfonos a revisar",
    "units_without_owner_phone": "Unidades sin teléfono del propietario",
    "conflicts": "Teléfonos en conflicto",
    "verified_phones_kept": "Teléfonos verificados conservados",
    "links_added": "Vínculos nuevos",
    "links_removed": "Vínculos quitados",
    "nameless_contacts": "Contactos sin nombre",
    "contacts_ignored": "Contactos ignorados",
    "people_in_several_buildings": "Personas en varios edificios",
    "payment_codes_valid": "Códigos de pago válidos",
    "payment_codes_invalid": "Códigos de pago inválidos",
    "payment_codes_missing": "Unidades sin código de pago",
    "lines": "Renglones leídos",
    "with_owner_phone": "Con teléfono del propietario",
    "with_owner_email": "Con email del propietario",
    "elapsed_seconds": "Duración",
    "by_unit_type": "Por tipo de unidad",
    "errors_by_type": "Errores por tipo",
}
# app.consorplus.errors (and the most common others), by class name.
ERROR_TYPES = {
    "ConsorPlusError": "Error de ConsorPlus",
    "ForbiddenActionError": "Acción no permitida",
    "LoginError": "No se pudo ingresar a ConsorPlus",
    "SessionExpiredError": "Sesión vencida",
    "ConsorPlusUnavailableError": "ConsorPlus no disponible",
    "NotFoundError": "No encontrado en ConsorPlus",
    "ParseError": "Página distinta a la esperada",
    "DeltaParseError": "Página distinta a la esperada",
    "UnexpectedRedirectError": "Redirección inesperada",
    "TimeoutError": "Tiempo de espera agotado",
}


def label(table: dict[Any, str], value: Any, default: str = "—") -> str:
    """The Spanish text of a stored value (a StrEnum or its plain string)."""
    if value is None:
        return default
    for key, text in table.items():
        if key == value or str(key) == str(value):
            return text
    return humanize(str(value))


def humanize(key: str) -> str:
    """An unknown key, readable: "units_skipped" -> "Units skipped"."""
    text = key.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "—"


def reason(code: str | None) -> tuple[str, str] | None:
    """(short label, long text) of a handoff reason; an unknown one is "Otro motivo"."""
    if not code:
        return None
    long = REASONS.get(code, REASONS["other"])[1]
    return REASON_SHORT.get(code, REASON_SHORT["other"]), long


def tool(name: str) -> str:
    return TOOLS.get(name, UNKNOWN_TOOL)


# Every enum the panel may show, to its table (SQLAdmin's type formatter for any StrEnum).
ENUM_TABLES: dict[type, dict[Any, str]] = {
    VerificationRequestStatus: VERIFICATION_STATUS,
    DataSource: PHONE_SOURCE,
    WaConversationStatus: CONVERSATION_STATUS,
    BuildingInfoCategory: INFO_CATEGORY,
    SyncKind: SYNC_KIND,
    SyncJob: SYNC_JOB,
    SyncStatus: SYNC_STATUS,
    ReservationSource: RESERVATION_SOURCE,
    PersonRole: PERSON_ROLE,
    PanelRole: PANEL_ROLE,
}
LANGUAGES = {"es_AR": "Español (Argentina)", "es": "Español", "en": "Inglés"}


def enum_label(value: Any) -> str:
    table = ENUM_TABLES.get(type(value))
    return label(table, value) if table is not None else humanize(str(value))
