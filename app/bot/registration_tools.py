"""Registration by WhatsApp (request_registration): a tenant or a relative, or an owner whose
unit has no owner email, leaves their details for the studio to approve in Verificaciones.

The code builds the summary and its buttons; the request is only created when THIS message is
«Sí, enviar» to that same summary (the bot's previous message), as book_sum does. One pending
request per number. While it waits, what needs a registered number (debt, SUM, claims) says
so: pending_step.
"""

import re
import unicodedata
from typing import TYPE_CHECKING, Any

from app.bot import identity
from app.bot.choices import Choice, Offer
from app.bot.unit_search import display_building_name
from app.db.models import PersonRole, Unit, VerificationRequest
from app.person_roles import role_text
from app.sync.normalize import clean_name

if TYPE_CHECKING:
    from app.bot.tools import ToolContext

REGISTER_CHOICES = (Choice("Sí, enviar", "Sí, enviar"), Choice("Corregir", "Corregir"))
# What counts as "yes" to the summary (normalized: lower case, no accents nor punctuation).
YES_REGISTER = frozenset(
    {"si enviar", "si", "si dale", "dale", "confirmo", "si confirmo", "si esta bien", "esta bien"}
)
CREATED = "Listo, le pasé tus datos al estudio. Te aviso por acá cuando quedes registrado/a."
ALREADY_PENDING = (
    "Ya tenés una solicitud de registro esperando que la apruebe el estudio. Te aviso por acá "
    "cuando quedes registrado/a."
)
PENDING_SAY = (
    "Tu registro todavía está pendiente de aprobación del estudio, así que por ahora no puedo "
    "ayudarte con eso. Si querés, te paso con una persona del estudio."
)
PENDING_STEP = (
    "Decí el texto de say y ofrecé pasarlo con una persona con offer_choices ('Sí, pasame' / "
    "'No, gracias')."
)


# What the neighbor gets when the studio decides (app.claims.notify.Notifier.notify_registration).
REGISTERED = "Listo, ya quedaste registrado/a como {role} de {unit}. ¿En qué te ayudo?"
NOT_REGISTERED = (
    "Hola. Desde el estudio no pudimos confirmar tus datos, así que todavía no quedaste "
    "registrado/a. Si querés hablar con alguien del estudio, escribí «Hablar con alguien» y te "
    "atendemos."
)
# The template registro_aprobado (outside the 24-hour window), as stored in the conversation.
# Text to ask Meta for: docs/whatsapp.md.
REGISTERED_TEMPLATE_PREVIEW = (
    "Hola, ya quedaste registrado/a como {} de {} en el Estudio Diego Rufeil. Escribinos por "
    "acá cuando necesites."
)
SUMMARY_SENT = "El resumen con los botones ya le sale a la persona: no escribas nada más."


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z ]+", " ", plain).split())


def _confirmed(ctx: "ToolContext", summary: str) -> bool:
    """The person said yes now, to exactly this summary (the bot's previous message)."""
    return _normalize(ctx.user_text) in YES_REGISTER and summary in ctx.last_bot_text


def _ask(ctx: "ToolContext", text: str) -> dict[str, Any]:
    """Ends the turn with the summary and its buttons (built by the code)."""
    if ctx.handoff is not None or ctx.offer is not None:
        return {
            "status": "error",
            "reason": "cannot_ask_now",
            "next_step": "En este mensaje ya derivaste u ofreciste opciones: no registres ahora.",
        }
    if ctx.first_message:  # the summary is the whole reply: greet as with a debt message
        text = f"{ctx.greeting}\n\n{text}"
    ctx.offer = Offer(text, REGISTER_CHOICES)
    return {"status": "confirmation_requested", "next_step": SUMMARY_SENT}


def pending_step(ctx: "ToolContext") -> dict[str, Any] | None:
    """When this number's registration waits for the studio: the answer for what needs a
    registered number (None otherwise)."""
    if identity.pending_registration(ctx.session, ctx.phone) is None:
        return None
    return {
        "status": "denied",
        "reason": "pending_registration",
        "say": PENDING_SAY,
        "next_step": PENDING_STEP,
    }


def describe_pending(request: VerificationRequest) -> str:
    """For the message's context: «solicitud de registro pendiente … (inquilino/a de …)»."""
    unit = request.unit
    where = f"{display_building_name(unit.building.name)} {unit.label}"
    return (
        "número no registrado, con una solicitud de registro pendiente de aprobación del "
        f"estudio ({role_text(request.role)} de {where}): no puede ver deuda, reservar el SUM "
        "ni hacer reclamos hasta que la aprueben"
    )


def request_registration(
    ctx: "ToolContext", unit_id: int, role: str, full_name: str
) -> dict[str, Any]:
    if identity.identify_by_phone(ctx.session, ctx.phone).known:
        return {
            "status": "already_registered",
            "next_step": "El número ya está registrado: no hace falta registrarlo.",
        }
    pending = identity.pending_registration(ctx.session, ctx.phone)
    if pending is not None:
        return {"status": "already_pending", "say": ALREADY_PENDING}
    try:
        person_role = PersonRole(role)
    except ValueError:
        return {"status": "error", "reason": "invalid_role"}
    name = clean_name(full_name)
    if len(name.split()) < 2:
        return {
            "status": "missing_name",
            "next_step": "Pedile su nombre y apellido (los dos) y volvé a llamar.",
        }
    unit = ctx.session.get(Unit, unit_id)
    if unit is None or not unit.active:
        return {"status": "unit_not_found", "next_step": "Volvé a buscarla con find_unit."}
    if person_role == PersonRole.OWNER and identity.owner_email_available(ctx.session, unit_id):
        return {
            "status": "use_email",
            "next_step": "Es propietario/a y la unidad tiene email: ofrecé mandarle el código "
            "con start_email_verification (no registres).",
        }
    where = f"{display_building_name(unit.building.name)} {unit.label}"
    summary = f"Te registro como {role_text(person_role)} de {where}, a nombre de {name}."
    if not _confirmed(ctx, summary):
        return _ask(ctx, f"{summary}\n¿Está bien?")
    requested = identity.request_operator_verification(
        ctx.session, ctx.phone, unit_id, name, person_role
    )
    match requested.status:
        case identity.RequestStatus.CREATED:
            return {"status": "created", "say": CREATED}
        case identity.RequestStatus.ALREADY_PENDING:
            return {"status": "already_pending", "say": ALREADY_PENDING}
        case identity.RequestStatus.RATE_LIMITED:
            return {
                "status": "rate_limited",
                "next_step": "Decí que por hoy no se pueden mandar más solicitudes y ofrecé "
                "pasarlo con una persona.",
            }
        case _:
            return {"status": requested.status.value}
