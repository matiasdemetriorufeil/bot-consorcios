"""The agent's claim tools: start_claim (hands the conversation to the step-by-step flow of
app.claims.flow), my_claims and claim_status. The model never registers a claim nor writes
what the neighbor reads about one: the flow and these functions build those texts
(app.claims.texts) and the channel sends them as is (ToolContext.blocks, ToolContext.offer).

A claim is only shown to who reported it or joined it; any other number is answered the same
as one that does not exist.
"""

from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.bot.choices import Offer
from app.bot.identity import identify_by_phone, to_e164
from app.bot.unit_search import display_building_name
from app.claims import flow, texts
from app.claims.service import claim_of_person, claims_of_person
from app.db.models import Claim, ClaimStatus

if TYPE_CHECKING:
    from app.bot.tools import ToolContext

STEP_SENT = "El paso del reclamo ya sale así, armado por el sistema: no escribas nada más."
LIST_SENT = (
    "El mensaje con los reclamos ya se le manda tal cual, antes de tu respuesta: NO lo repitas. "
    'Como mucho una línea corta ("¿te ayudo con algo más?").'
)


# In the first message the code puts the greeting before its blocks (app.bot.agent).
GREETED = (
    " Antes de ese mensaje el sistema ya saludó y se presentó: no te presentes ni saludes de nuevo."
)


def _greeted(ctx: "ToolContext") -> str:
    return GREETED if ctx.first_message and ctx.blocks else ""


def _status(claim: Claim) -> str:
    return texts.STATUS[ClaimStatus(claim.status)]


def my_claims_text(session: Session, phone: str, now: Any = None) -> str | None:
    """The person's claims as the neighbor reads them (None: unknown number)."""
    who = identify_by_phone(session, phone)
    if not who.known:
        return None
    claims = claims_of_person(session, who.person_id, to_e164(phone), now)
    if not claims:
        return texts.MY_CLAIMS_NONE
    lines = [
        texts.MY_CLAIMS_LINE.format(
            number=c.number,
            problem=c.category.list_title,
            building=display_building_name(c.building.name),
            status=_status(c),
        )
        for c in claims
    ]
    return "\n".join([texts.MY_CLAIMS, *lines])


def claim_status_text(session: Session, phone: str, number: int, timezone: str) -> str:
    who = identify_by_phone(session, phone)
    claim = claim_of_person(session, number, who.person_id, to_e164(phone))
    if claim is None:
        return texts.CLAIM_NOT_FOUND
    return texts.CLAIM_STATUS.format(
        number=claim.number,
        problem=claim.category.list_title,
        building=display_building_name(claim.building.name),
        date=claim.created_at.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y"),
        status=_status(claim),
    )


def apply_flow_reply(ctx: "ToolContext", reply: flow.FlowReply) -> None:
    """A step of the flow as this turn's reply (it ends the turn, like offer_choices)."""
    ctx.blocks.extend(reply.blocks)
    if reply.text:
        ctx.offer = Offer(reply.text, reply.choices)
        ctx.code_reply = True


# --- Tools ----------------------------------------------------------------------------------


def start_claim(ctx: "ToolContext", category_hint: str = "") -> dict[str, Any]:
    if ctx.handoff is not None:
        return {"status": "error", "reason": "handed_off"}
    reply = flow.start(
        ctx.session, ctx.phone, category_hint, conversation_id=ctx.conversation_id, now=ctx.now
    )
    ctx.blocks.extend(reply.blocks)
    safety = (
        " La indicación de seguridad ya le llegó a la persona (armada por el sistema): no la "
        "repitas."
        if reply.blocks
        else ""
    )
    match reply.status:
        case "ok":
            apply_flow_reply(ctx, flow.FlowReply(text=reply.text, choices=reply.choices))
            return {"status": "ok", "next_step": STEP_SENT}
        case "not_verified":
            if reply.urgent:
                step = (
                    "Es una urgencia y el número no está verificado: derivá ya con "
                    "handoff_to_human (priority urgent) y decí su tell_person." + safety
                )
            else:
                step = (
                    "Si lo que describe es una urgencia (agua que avanza, gente encerrada, "
                    "riesgo para personas), derivá ya con handoff_to_human (priority urgent) y "
                    "su tell_person. Si no: para registrar el reclamo hay que verificar el "
                    "número; pedile edificio y unidad (find_unit) y ofrecé mandarle el código "
                    "al email (como siempre). Cuando confirm_email_code dé verified, el reclamo "
                    "sigue solo." + safety
                )
            return {
                "status": "not_verified",
                "urgent": reply.urgent,
                "next_step": step + _greeted(ctx),
            }
        case "not_available":
            if reply.urgent:
                step = (
                    "Decí el texto de say y, como es una urgencia, derivá ya con "
                    "handoff_to_human (priority urgent) y su tell_person." + safety
                )
            else:
                step = (
                    "Si lo que describe es una urgencia (agua que avanza, gente encerrada, "
                    "riesgo para personas), decí say y derivá ya con handoff_to_human (priority "
                    "urgent). Si no, decí el texto de say y ofrecé pasarlo con una persona con "
                    'offer_choices ("Sí, pasame" / "No, gracias").'
                )
            return {
                "status": "not_available",
                "urgent": reply.urgent,
                "say": reply.text,
                "next_step": step + _greeted(ctx),
            }
        case _:
            return {"status": "error", "reason": reply.status}


def my_claims(ctx: "ToolContext") -> dict[str, Any]:
    text = my_claims_text(ctx.session, ctx.phone, ctx.now)
    if text is None:
        return {
            "status": "not_verified",
            "next_step": "Para ver sus reclamos hay que verificar el número (find_unit y el "
            "código por email, como siempre).",
        }
    ctx.blocks.append(text)
    return {"status": "ok", "next_step": LIST_SENT + _greeted(ctx)}


def claim_status(ctx: "ToolContext", number: int) -> dict[str, Any]:
    ctx.blocks.append(claim_status_text(ctx.session, ctx.phone, number, ctx.timezone))
    return {"status": "ok", "next_step": LIST_SENT + _greeted(ctx)}
