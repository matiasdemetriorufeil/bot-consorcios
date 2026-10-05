"""The agent's tools: declared once (TOOLS) and run by run_tool.

The phone of whoever is writing comes from ToolContext (set by the code from the incoming
message), NEVER from the model: no tool has a phone parameter and unknown arguments are
rejected. Every tool checks permissions itself; can_view_unit_finance is the only gate to
debt and payment codes. Building information is public (get_building_info needs no
verification) and never carries data of people or debts. Results are plain JSON with texts
already formatted for the chat.
"""

import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import identity
from app.bot.building_info import TOKEN_BUDGET, estimate_tokens, select_texts
from app.bot.identity import (
    ConfirmStatus,
    RequestStatus,
    StartStatus,
    can_view_unit_finance,
    identify_by_phone,
    to_e164,
)
from app.bot.unit_search import (
    SearchStatus,
    UnitCandidate,
    display_building_name,
    search_building,
    search_unit,
)
from app.config import Settings
from app.db.models import BotEvent, Building, BuildingInfo, PersonRole, Unit
from app.llm import ToolSpec
from app.notify.email import EmailSender
from app.sync.live import DebtResult

logger = logging.getLogger(__name__)

MAX_DEBT_LINES = 12
# The admin panel and .env can change it (app.bot.bot_config): this is only the default.
PAYMENT_CODE_HOW_TO: str = Settings.model_fields["payment_code_how_to"].default
NO_PAYMENT_CODE = "La unidad no tiene código de pago cargado: pedilo a la administración."
# Arguments never written to bot_events.
_SECRET_ARGS = frozenset({"code"})


# Why a conversation goes to a human (also its Chatwoot label, see app.chatwoot.handoff).
# The model picks one of HANDOFF_REASONS; the code also uses technical_error and non_pilot.
HANDOFF_REASONS = (
    "person_requested",
    "upset",
    "debt_claim",
    "payment_not_credited",
    "payment_plan",
    "emergency",
    "no_answer",
    "other",
)
DEFAULT_HANDOFF_NOTICE = "Ya le pasé tu consulta a una persona del estudio."


@dataclass(frozen=True)
class Handoff:
    """A request to pass the conversation to a human. The channel (Chatwoot) carries it out
    after sending the bot's last reply."""

    reason: str
    summary: str
    priority: str = "normal"  # "normal" | "urgent"


@dataclass
class ToolContext:
    session: Session
    phone: str  # raw WhatsApp number of whoever is writing, set by the code
    refresh_debt: Callable[[int], DebtResult]
    timezone: str = "America/Argentina/Cordoba"
    email_sender: EmailSender | None = None
    conversation_id: int | None = None
    # What to tell the person on handoff (depends on office hours, set by the agent).
    handoff_notice: str = DEFAULT_HANDOFF_NOTICE
    # The same for priority "urgent" (adds who to call outside office hours).
    urgent_handoff_notice: str = DEFAULT_HANDOFF_NOTICE
    # How to use the payment code and the self-service page (app.bot.bot_config).
    payment_how_to: str = PAYMENT_CODE_HOW_TO
    autogestion_url: str = ""
    # About the studio (not a building), for get_building_info: "de lunes a viernes de 9 a
    # 17" and who to call in an emergency outside office hours (admin panel / .env).
    office_hours_text: str = ""
    emergency_contact: str = ""
    # Set by handoff_to_human.
    handoff: Handoff | None = None
    # unit_ids find_unit returned in this turn. The history only keeps texts, so an id from
    # an earlier turn is gone: one the model "remembers" may be another unit (see run_tool).
    offered_unit_ids: set[int] = field(default_factory=set)

    @property
    def e164(self) -> str | None:
        return to_e164(self.phone)

    def log(self, event_type: str, **payload: Any) -> None:
        self.session.add(
            BotEvent(
                conversation_id=self.conversation_id,
                phone_e164=self.e164,
                event_type=event_type,
                payload=payload,
            )
        )
        self.session.commit()


# --- Declarations -----------------------------------------------------------------------


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_UNIT_ID = {
    "type": "integer",
    "description": "unit_id que devolvió find_unit EN ESTE MENSAJE, o de una unidad propia "
    "del contexto. Nunca uno recordado de un mensaje anterior.",
}

TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="find_unit",
        description=(
            "Busca una unidad (departamento, cochera o local) por lo que escribió la persona. "
            "Devuelve la unidad encontrada o candidatas con unit_id y etiquetas. Si hay "
            "candidatas de varios edificios, preguntá primero el edificio (lista en buildings)."
        ),
        parameters=_object(
            {
                "building_text": {
                    "type": "string",
                    "description": "Edificio tal como lo escribió la persona (ej. 'Rodas 2').",
                },
                "unit_text": {
                    "type": "string",
                    "description": "Unidad tal como la escribió (ej. '4 C', 'cochera 12'). "
                    "Vacío si viene junto con el edificio.",
                },
            },
            ["building_text"],
        ),
    ),
    ToolSpec(
        name="start_email_verification",
        description=(
            "Para un número NO verificado: manda un código de 6 dígitos al email de los "
            "propietarios de la unidad. Devuelve status, reason (si no se mandó) y say: el "
            "texto que tenés que decirle a la persona."
        ),
        parameters=_object({"unit_id": _UNIT_ID}, ["unit_id"]),
    ),
    ToolSpec(
        name="confirm_email_code",
        description="Verifica el código de 6 dígitos que la persona recibió por email.",
        parameters=_object(
            {"code": {"type": "string", "description": "El código tal como lo escribió."}},
            ["code"],
        ),
    ),
    ToolSpec(
        name="request_operator_verification",
        description=(
            "Cuando la unidad no tiene email de propietario: deja pedido que una persona del "
            "estudio verifique el número. Pedile antes su nombre completo."
        ),
        parameters=_object(
            {
                "unit_id": _UNIT_ID,
                "claimed_name": {
                    "type": "string",
                    "description": "Nombre y apellido que dice tener la persona.",
                },
            },
            ["unit_id", "claimed_name"],
        ),
    ),
    ToolSpec(
        name="get_debt",
        description=(
            "Deuda de expensas y código de pago Siro de una unidad. Solo funciona si quien "
            "escribe es propietario verificado de esa unidad. Devuelve textos ya formateados."
        ),
        parameters=_object({"unit_id": _UNIT_ID}, ["unit_id"]),
    ),
    ToolSpec(
        name="get_building_info",
        description=(
            "Información pública de un edificio cargada por el estudio (reglamento interno, "
            "horarios, contactos, emergencias) y datos del estudio (horario de atención, "
            "contacto de emergencias). No requiere verificar el número. Respondé solo con lo "
            "que devuelve, citando de dónde sale."
        ),
        parameters=_object(
            {
                "question": {
                    "type": "string",
                    "description": "Qué quiere saber, con sus palabras (ej. 'se pueden tener "
                    "perros', 'horario de mudanzas').",
                },
                "building": {
                    "type": "string",
                    "description": "Edificio tal como lo nombró la persona en la conversación "
                    "(ej. 'Rodas 2'). Vacío si no lo nombró: nunca lo elijas vos.",
                },
            },
            ["question"],
        ),
    ),
    ToolSpec(
        name="handoff_to_human",
        description=(
            "Deriva la conversación a una persona del estudio: si la pide, está enojada, "
            "reclama por la deuda, quiere un plan de pago, es urgente o no sabés la respuesta. "
            "Pago que no figura: solo si aceptó tu oferta de derivar o insiste (ver reglas de "
            "'Ya pagué'). Un mensaje que cambia de tema no es un sí a una oferta anterior."
        ),
        parameters=_object(
            {
                "reason": {
                    "type": "string",
                    "enum": list(HANDOFF_REASONS),
                    "description": "person_requested: pide una persona; upset: enojado o "
                    "molesto; debt_claim: reclama por la deuda; payment_not_credited: pago "
                    "que no figura acreditado (aceptó que lo revise una persona); "
                    "payment_plan: plan de pago o cuotas; emergency: urgencia; "
                    "no_answer: no tenés la respuesta; other: otro motivo.",
                },
                "summary": {
                    "type": "string",
                    "description": "Resumen para el operador: qué pidió y qué se le respondió.",
                },
                "priority": {"type": "string", "enum": ["normal", "urgent"]},
            },
            ["reason", "summary", "priority"],
        ),
    ),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


# --- Argument validation ----------------------------------------------------------------


class InvalidArguments(ValueError):
    pass


def validate_arguments(spec: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any]:
    """Check arguments against the tool's (flat) schema. Unknown arguments are rejected: the
    model cannot slip in a phone or anything the code did not declare."""
    schema = spec.parameters
    properties: dict[str, Any] = schema.get("properties", {})
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        raise InvalidArguments(f"argumentos no permitidos: {', '.join(unknown)}")
    missing = [name for name in schema.get("required", []) if arguments.get(name) is None]
    if missing:
        raise InvalidArguments(f"faltan argumentos: {', '.join(missing)}")
    clean: dict[str, Any] = {}
    for name, value in arguments.items():
        prop = properties[name]
        if prop["type"] == "integer":
            # JSON numbers may arrive as 12.0.
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            if isinstance(value, bool) or not isinstance(value, int):
                raise InvalidArguments(f"{name} tiene que ser un número entero")
        elif prop["type"] == "string":
            if not isinstance(value, str):
                raise InvalidArguments(f"{name} tiene que ser texto")
            if "enum" in prop and value not in prop["enum"]:
                raise InvalidArguments(f"{name} tiene que ser uno de {prop['enum']}")
        clean[name] = value
    return clean


def _loggable(arguments: dict[str, Any]) -> dict[str, Any]:
    return {k: ("[omitido]" if k in _SECRET_ARGS else v) for k, v in arguments.items()}


def _unconfirmed_id(ctx: ToolContext, arguments: dict[str, Any]) -> str | None:
    """The reason when a unit_id did not come from the code: find_unit in this turn, or the
    phone's own units (in the per-message context, and after confirm_email_code). None when
    it is fine."""
    unit_id = arguments.get("unit_id")
    if unit_id is None or unit_id in ctx.offered_unit_ids:
        return None
    own = identify_by_phone(ctx.session, ctx.phone).units
    if all(u.unit_id != unit_id for u in own):
        return "unit_not_confirmed"
    return None


_NOT_CONFIRMED_STEPS = {
    "unit_not_confirmed": "Ese unit_id no lo devolvió find_unit en este mensaje. Llamá "
    "find_unit con el edificio y la unidad que dijo la persona y usá el unit_id que devuelva.",
}


def run_tool(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one tool call from the model and log it in bot_events (never the code)."""
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        result: dict[str, Any] = {"status": "error", "error": f"herramienta inexistente: {name}"}
        ctx.log("tool_call", tool=name, args=_loggable(arguments), status="unknown_tool")
        return result
    try:
        clean = validate_arguments(spec, arguments)
    except InvalidArguments as exc:
        ctx.log("tool_call", tool=name, args=_loggable(arguments), status="invalid_arguments")
        return {"status": "error", "error": str(exc)}
    if reason := _unconfirmed_id(ctx, clean):
        ctx.log("tool_call", tool=name, args=_loggable(clean), status="error", reason=reason)
        logger.info("Conversation %s: tool %s -> error (%s)", ctx.conversation_id, name, reason)
        return {
            "status": "error",
            "reason": reason,
            "next_step": f"{_NOT_CONFIRMED_STEPS[reason]} No se lo cuentes a la persona.",
        }
    try:
        result = _HANDLERS[name](ctx, **clean)
    except Exception as exc:
        ctx.session.rollback()
        logger.exception("Tool %s failed", name)
        result = {"status": "error", "error": "falla interna de la herramienta"}
        ctx.log(
            "tool_call", tool=name, args=_loggable(clean), status="error", error=type(exc).__name__
        )
        return result
    status = result.get("status")
    reason = result.get("reason")
    extra = {"reason": reason} if reason else {}
    ctx.log("tool_call", tool=name, args=_loggable(clean), status=status, **extra)
    # Name, status and reason only: arguments may carry codes or what the person wrote.
    shown = f"{status} ({reason})" if reason else status
    logger.info("Conversation %s: tool %s -> %s", ctx.conversation_id, name, shown)
    return result


# --- Handlers ---------------------------------------------------------------------------


def find_unit(ctx: ToolContext, building_text: str, unit_text: str = "") -> dict[str, Any]:
    found = search_unit(ctx.session, building_text, unit_text)
    buildings = [display_building_name(n) for n in found.buildings]
    candidates = [
        {
            "unit_id": c.unit_id,
            "building": display_building_name(c.building_name),
            "unit": c.unit_label,
        }
        for c in found.candidates
    ]
    ctx.offered_unit_ids.update(c.unit_id for c in found.candidates)
    if found.status == SearchStatus.FOUND:
        return {"status": "found", "unit": candidates[0]}
    if found.status == SearchStatus.AMBIGUOUS:
        several = len({c.building_name for c in found.candidates}) > 1
        result: dict[str, Any] = {"status": "ambiguous", "candidates": candidates}
        if several:
            result["buildings"] = buildings
            result["next_step"] = "Preguntá primero de qué edificio es."
        else:
            result["next_step"] = "Preguntá cuál de estas unidades es."
        return result
    result = {"status": "not_found"}
    if buildings:
        result["buildings"] = buildings
        result["next_step"] = "El edificio coincide pero no la unidad: pedí que la confirme."
    return result


# Why start_email_verification sent no code: a closed list decided by the code. The model
# says the `say` text of the result, never its own reading of the status.
NOT_SENT_REASONS = {
    StartStatus.NO_EMAIL: "no_owner_email",
    StartStatus.RATE_LIMITED: "rate_limited",
    StartStatus.SEND_FAILED: "send_failed",
    StartStatus.NOT_FOUND: "unit_not_found",
    StartStatus.AMBIGUOUS: "unit_not_found",  # not reachable with a unit_id
    StartStatus.INVALID_PHONE: "invalid_phone",
}


def _unit_name(unit: UnitCandidate | None) -> str | None:
    return f"{display_building_name(unit.building_name)} {unit.unit_label}" if unit else None


def wait_text(retry_at: datetime, now: datetime) -> str:
    """How long until retry_at, rounded up: "40 minutos", "1 hora", "5 horas"."""
    minutes = max(1, math.ceil((retry_at - now).total_seconds() / 60))
    if minutes < 60:
        return "1 minuto" if minutes == 1 else f"{minutes} minutos"
    hours = math.ceil(minutes / 60)
    return "1 hora" if hours == 1 else f"{hours} horas"


def start_email_verification(ctx: ToolContext, unit_id: int) -> dict[str, Any]:
    if can_view_unit_finance(ctx.session, ctx.phone, unit_id):
        return {
            "status": "already_verified",
            "say": "Tu número ya está verificado para esa unidad.",
        }
    started = identity.start_email_verification(
        ctx.session, ctx.phone, unit_id=unit_id, sender=ctx.email_sender
    )
    unit = _unit_name(started.unit)
    if started.status == StartStatus.CODES_SENT:
        emails = ", ".join(started.masked_emails)
        return {
            "status": "codes_sent",
            "unit": unit,
            "masked_emails": list(started.masked_emails),
            "say": f"Listo, te mandé un código de {identity.CODE_DIGITS} dígitos a {emails}, "
            f"el email del propietario de {unit}. Vence en {identity.CODE_VALID_MINUTES} "
            "minutos: escribímelo acá.",
            "next_step": "Cuando lo escriba, usá confirm_email_code.",
        }
    reason = NOT_SENT_REASONS[started.status]
    result: dict[str, Any] = {"status": "not_sent", "reason": reason, "unit": unit}
    match reason:
        case "no_owner_email":
            result["say"] = (
                f"La unidad {unit} no tiene un email de propietario cargado, así que no puedo "
                "mandarte el código. Si querés, decime tu nombre y apellido y le pido a una "
                "persona del estudio que verifique tu número."
            )
            result["next_step"] = (
                "Si da su nombre, usá request_operator_verification con este unit_id."
            )
        case "rate_limited":
            wait = wait_text(started.retry_at, datetime.now(UTC)) if started.retry_at else None
            result["say"] = (
                "Ya enviamos varios códigos desde este número hoy; probá de nuevo "
                f"{f'en {wait}' if wait else 'más tarde'} o te paso con una persona."
            )
            result["next_step"] = "Si quiere una persona, usá handoff_to_human."
        case "send_failed":
            result["say"] = "No pude mandar el email con el código en este momento."
            result["next_step"] = "Derivá con handoff_to_human y sumá su tell_person."
        case "unit_not_found":
            result["say"] = "No encontré esa unidad. ¿Me confirmás el edificio y la unidad?"
            result["next_step"] = "Volvé a buscarla con find_unit."
        case _:  # invalid_phone
            result["say"] = "No puedo verificar este número automáticamente."
            result["next_step"] = "Derivá con handoff_to_human y sumá su tell_person."
    return result


def confirm_email_code(ctx: ToolContext, code: str) -> dict[str, Any]:
    confirmed = identity.confirm_email_code(ctx.session, ctx.phone, code)
    match confirmed.status:
        case ConfirmStatus.VERIFIED:
            return {"status": "verified", "units": _units_of(confirmed.identity)}
        case ConfirmStatus.WRONG_CODE | ConfirmStatus.INVALID_CODE:
            return {"status": confirmed.status.value, "attempts_left": confirmed.attempts_left}
        case ConfirmStatus.EXPIRED | ConfirmStatus.LOCKED:
            return {
                "status": confirmed.status.value,
                "next_step": "Hay que empezar de nuevo con start_email_verification.",
            }
        case _:
            return {"status": confirmed.status.value}


def request_operator_verification(
    ctx: ToolContext, unit_id: int, claimed_name: str
) -> dict[str, Any]:
    requested = identity.request_operator_verification(
        ctx.session, ctx.phone, unit_id, claimed_name
    )
    if requested.status in (RequestStatus.CREATED, RequestStatus.ALREADY_PENDING):
        return {
            "status": requested.status.value,
            "next_step": "Una persona del estudio va a verificar el número y le avisa.",
        }
    return {"status": requested.status.value}


def format_money(amount: Decimal) -> str:
    """Decimal("165060") -> "$165.060,00" (Argentine format)."""
    text = f"{abs(amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-${text}" if amount < 0 else f"${text}"


def _local(ctx: ToolContext, moment: datetime) -> str:
    return moment.astimezone(ZoneInfo(ctx.timezone)).strftime("%d/%m/%Y %H:%M")


def get_debt(ctx: ToolContext, unit_id: int) -> dict[str, Any]:
    if not can_view_unit_finance(ctx.session, ctx.phone, unit_id):
        return _debt_denied(ctx, unit_id)
    unit = ctx.session.get(Unit, unit_id)
    if unit is None:
        return {"status": "not_found"}
    try:
        debt = ctx.refresh_debt(unit_id)
    except LookupError:
        return {"status": "not_found"}
    label = f"{display_building_name(unit.building.name)} {unit.label}"
    if debt.snapshot is None:
        return {
            "status": "no_data",
            "unit": label,
            "next_step": "No hay dato de deuda: decilo y ofrecé derivar.",
        }
    snap = debt.snapshot
    result: dict[str, Any] = {
        "status": "ok",
        "unit": label,
        "total_debt": format_money(snap.total_amount),
        "up_to_date": snap.is_up_to_date,
        "data_date": _local(ctx, snap.fetched_at),
        "detail": [
            f"{line.period} {line.concept}: {format_money(line.balance_due)}"
            for line in snap.lines[:MAX_DEBT_LINES]
        ],
    }
    if debt.stale:
        result["note"] = "No se pudo actualizar ahora: es el último dato guardado (ver data_date)."
    if unit.payment_code:
        result["payment_code"] = unit.payment_code
        result["payment_how_to"] = ctx.payment_how_to
    else:
        result["payment_code"] = None
        result["payment_how_to"] = NO_PAYMENT_CODE
    if ctx.autogestion_url:
        result["autogestion_url"] = ctx.autogestion_url
    return result


def _debt_denied(ctx: ToolContext, unit_id: int) -> dict[str, Any]:
    who = identify_by_phone(ctx.session, ctx.phone)
    if not who.known:
        return {
            "status": "denied",
            "reason": "not_verified",
            "next_step": "El número no está verificado: ofrecé verificarlo por email "
            "(start_email_verification).",
        }
    if any(u.unit_id == unit_id and u.role == PersonRole.TENANT for u in who.units):
        return {
            "status": "denied",
            "reason": "tenant",
            "next_step": "Solo los propietarios pueden ver deuda y código de pago. Ofrecé derivar.",
        }
    return {
        "status": "denied",
        "reason": "not_owner",
        "next_step": "Este número no es de un propietario de esa unidad. No des datos de ella.",
    }


HOW_TO_ANSWER = (
    "Respondé SOLO con lo que dicen texts (o studio si la pregunta es sobre el estudio) y "
    "decí de dónde sale (ej. 'según el reglamento interno', con el source o el title). Si el "
    "dato no está escrito ahí, decí que no tenés esa información cargada y derivá: no lo "
    "deduzcas ni lo completes con lo que suele pasar en otros edificios."
)


def _studio(ctx: ToolContext) -> dict[str, str]:
    studio = {"name": "Estudio Diego Rufeil (administración del consorcio)"}
    if ctx.office_hours_text:
        studio["office_hours"] = ctx.office_hours_text
    if ctx.emergency_contact:
        studio["emergency_contact"] = ctx.emergency_contact
    return studio


def _resolve_building(
    ctx: ToolContext, building: str
) -> tuple[Building | None, dict[str, Any] | None]:
    """The building asked about, or the result to return when it is not clear which one.
    Named: tolerant search over every active building (the information is public).
    Not named: the phone's own building, when all its units are in one."""
    if building.strip():
        found = search_building(ctx.session, building)
        if len(found) == 1:
            return found[0], None
        if found:
            return None, {
                "status": "ambiguous_building",
                "buildings": [display_building_name(b.name) for b in found],
                "next_step": "Preguntá cuál de estos edificios es.",
            }
        return None, {
            "status": "building_not_found",
            "next_step": "No encontré ese edificio entre los que administra el estudio: "
            "pedí que lo confirme (nombre o dirección). No respondas sobre él.",
        }
    own: dict[int, str] = {}
    for u in identify_by_phone(ctx.session, ctx.phone).units:
        own.setdefault(u.building_id, u.building_name)
    if len(own) == 1:
        return ctx.session.get(Building, next(iter(own))), None
    if own:
        return None, {
            "status": "which_building",
            "buildings": [display_building_name(n) for n in own.values()],
            "next_step": "La persona tiene unidades en varios edificios: si la pregunta es "
            "sobre un edificio, preguntá de cuál antes de responder. Si es sobre el estudio, "
            "respondé con studio.",
        }
    return None, {
        "status": "need_building",
        "next_step": "Si la pregunta es sobre un edificio, preguntá cuál es. Si es sobre el "
        "estudio, respondé con studio.",
    }


def get_building_info(ctx: ToolContext, question: str, building: str = "") -> dict[str, Any]:
    """Public: no verification. Only BuildingInfo texts, the building's name and address and
    the studio settings: never people, units or debts."""
    found, problem = _resolve_building(ctx, building)
    result: dict[str, Any] = problem or {}
    stats: dict[str, Any] = {}
    if found is not None:
        infos = list(
            ctx.session.scalars(
                select(BuildingInfo)
                .where(BuildingInfo.building_id == found.id)
                .order_by(BuildingInfo.id)
            )
        )
        result = {"status": "ok", "building": display_building_name(found.name)}
        if found.address:
            result["address"] = found.address
        stats = {"building_id": found.id, "texts_total": len(infos)}
        if not infos:
            result["status"] = "no_info"
            result["next_step"] = (
                "El estudio no cargó información de este edificio: decí que no tenés ese dato "
                "y derivá. Si la pregunta es sobre el estudio, respondé con studio."
            )
        else:
            chosen = select_texts(infos, question, TOKEN_BUDGET)
            stats |= {
                "mode": chosen.mode,
                "texts_tokens": chosen.texts_tokens,
                "sections_sent": chosen.sections_sent,
                "sections_total": chosen.sections_total,
            }
            if chosen.texts:
                result["texts"] = chosen.texts
                result["how_to_answer"] = HOW_TO_ANSWER
            else:
                result["status"] = "no_match"
                result["next_step"] = (
                    "Ningún texto cargado del edificio habla de eso: decí que no tenés esa "
                    "información y derivá. Si la pregunta es sobre el estudio, respondé con "
                    "studio."
                )
            if chosen.other_titles:
                result["other_texts"] = chosen.other_titles
    result["studio"] = _studio(ctx)
    tokens = estimate_tokens(json.dumps(result, ensure_ascii=False))
    ctx.log("building_info", status=result["status"], tokens=tokens, **stats)
    logger.info(
        "Conversation %s: get_building_info -> %s, ~%d tokens",
        ctx.conversation_id, result["status"], tokens,
    )  # fmt: skip
    return result


def handoff_to_human(
    ctx: ToolContext, reason: str, summary: str, priority: str = "normal"
) -> dict[str, Any]:
    """Records the handoff; the channel opens the conversation for a human after the reply.
    Several calls in one turn: the first one wins, unless a later one is urgent."""
    ctx.log("handoff", reason=reason, summary=summary, priority=priority)
    logger.info(
        "Conversation %s: handoff requested (%s, %s)", ctx.conversation_id, reason, priority
    )
    if ctx.handoff is None or (priority == "urgent" and ctx.handoff.priority != "urgent"):
        ctx.handoff = Handoff(reason=reason, summary=summary, priority=priority)
    notice = ctx.urgent_handoff_notice if priority == "urgent" else ctx.handoff_notice
    return {"status": "ok", "tell_person": notice}


def _units_of(who: identity.Identity) -> list[dict[str, Any]]:
    return [
        {
            "unit_id": u.unit_id,
            "building": display_building_name(u.building_name),
            "unit": u.unit_label,
            "role": "propietario" if u.role == PersonRole.OWNER else "inquilino",
        }
        for u in who.units
    ]


_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "find_unit": find_unit,
    "start_email_verification": start_email_verification,
    "confirm_email_code": confirm_email_code,
    "request_operator_verification": request_operator_verification,
    "get_debt": get_debt,
    "get_building_info": get_building_info,
    "handoff_to_human": handoff_to_human,
}
