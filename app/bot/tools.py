"""The agent's tools: declared once (TOOLS) and run by run_tool.

The phone of whoever is writing comes from ToolContext (set by the code from the incoming
message), NEVER from the model: no tool has a phone parameter and unknown arguments are
rejected. Every tool checks permissions itself; can_view_unit_finance is the only gate to
debt and payment codes. Results are plain JSON with texts already formatted for the chat.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import identity
from app.bot.identity import (
    ConfirmStatus,
    RequestStatus,
    StartStatus,
    can_view_unit_finance,
    identify_by_phone,
    to_e164,
)
from app.bot.unit_search import SearchStatus, display_building_name, search_unit
from app.db.models import BotEvent, Building, PersonRole, Unit
from app.llm import ToolSpec
from app.notify.email import EmailSender
from app.sync.live import DebtResult

logger = logging.getLogger(__name__)

MAX_DEBT_LINES = 12
PAYMENT_CODE_HOW_TO = (
    "Con este código podés pagar por Pago Mis Cuentas o Red Link (home banking o cajero)."
)
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
    # Set by handoff_to_human.
    handoff: Handoff | None = None

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


_UNIT_ID = {"type": "integer", "description": "unit_id devuelto por find_unit."}

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
            "propietarios de la unidad. Devuelve los emails enmascarados o que no hay email."
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
        description="Información y reglamento de un edificio (horarios, normas, contactos).",
        parameters=_object(
            {
                "building_id": {"type": "integer", "description": "building_id del edificio."},
                "question": {"type": "string", "description": "Qué quiere saber."},
            },
            ["building_id", "question"],
        ),
    ),
    ToolSpec(
        name="handoff_to_human",
        description=(
            "Deriva la conversación a una persona del estudio: si la pide, está enojada, "
            "reclama por deuda o pagos no acreditados, quiere un plan de pago, es urgente o "
            "no sabés la respuesta."
        ),
        parameters=_object(
            {
                "reason": {
                    "type": "string",
                    "enum": list(HANDOFF_REASONS),
                    "description": "person_requested: pide una persona; upset: enojado o "
                    "molesto; debt_claim: reclama por la deuda; payment_not_credited: pago "
                    "que no figura; payment_plan: plan de pago o cuotas; emergency: urgencia; "
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
    ctx.log("tool_call", tool=name, args=_loggable(clean), status=result.get("status"))
    # Name and status only: arguments may carry codes or what the person wrote.
    logger.info("Conversation %s: tool %s -> %s", ctx.conversation_id, name, result.get("status"))
    return result


# --- Handlers ---------------------------------------------------------------------------


def _building_ids(session: Session, names: tuple[str, ...]) -> list[dict[str, Any]]:
    if not names:
        return []
    rows = session.execute(
        select(Building.id, Building.name).where(
            Building.name.in_(names), Building.active.is_(True)
        )
    ).all()
    order = {n: i for i, n in enumerate(names)}
    rows = sorted(rows, key=lambda r: order.get(r[1], 0))
    return [{"building_id": bid, "name": display_building_name(n)} for bid, n in rows]


def find_unit(ctx: ToolContext, building_text: str, unit_text: str = "") -> dict[str, Any]:
    found = search_unit(ctx.session, building_text, unit_text)
    buildings = _building_ids(ctx.session, found.buildings)
    ids = [c.unit_id for c in found.candidates]
    unit_building = dict(
        ctx.session.execute(select(Unit.id, Unit.building_id).where(Unit.id.in_(ids))).all()
    )
    candidates = [
        {
            "unit_id": c.unit_id,
            "building_id": unit_building.get(c.unit_id),
            "building": display_building_name(c.building_name),
            "unit": c.unit_label,
        }
        for c in found.candidates
    ]
    if found.status == SearchStatus.FOUND:
        return {"status": "found", "unit": candidates[0]}
    if found.status == SearchStatus.AMBIGUOUS:
        several = len({c["building_id"] for c in candidates}) > 1
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


def start_email_verification(ctx: ToolContext, unit_id: int) -> dict[str, Any]:
    if can_view_unit_finance(ctx.session, ctx.phone, unit_id):
        return {"status": "already_verified", "message": "Este número ya está verificado."}
    started = identity.start_email_verification(
        ctx.session, ctx.phone, unit_id=unit_id, sender=ctx.email_sender
    )
    match started.status:
        case StartStatus.CODES_SENT:
            return {
                "status": "codes_sent",
                "masked_emails": list(started.masked_emails),
                "next_step": f"Pedile el código de {identity.CODE_DIGITS} dígitos que le "
                f"llegó por email. Vence en {identity.CODE_VALID_MINUTES} minutos.",
            }
        case StartStatus.NO_EMAIL:
            return {
                "status": "sin_email",
                "next_step": "La unidad no tiene email de propietario. Pedile su nombre "
                "completo y usá request_operator_verification.",
            }
        case StartStatus.RATE_LIMITED:
            return {"status": "rate_limited", "next_step": "Demasiados intentos hoy: derivá."}
        case StartStatus.SEND_FAILED:
            return {"status": "send_failed", "next_step": "No se pudo mandar el email: derivá."}
        case _:
            return {"status": started.status.value}


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
        result["payment_how_to"] = PAYMENT_CODE_HOW_TO
    else:
        result["payment_code"] = None
        result["payment_how_to"] = NO_PAYMENT_CODE
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


def get_building_info(ctx: ToolContext, building_id: int, question: str) -> dict[str, Any]:
    # Stage 5 loads the rules of each building.
    return {"status": "no_info", "message": "Sin información cargada para este edificio."}


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
    return {"status": "ok", "tell_person": ctx.handoff_notice}


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
