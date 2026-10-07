"""What a handoff leaves for the operators: labels (WaConversation.handoff_labels) and an
internal note in the conversation."""

import re
import unicodedata

from app.bot.identity import Identity
from app.bot.tools import Handoff
from app.bot.unit_search import display_building_name
from app.db.models import PersonRole

# Handoff reason -> (label, text for the note). The panel's inbox shows the text.
REASONS: dict[str, tuple[str, str]] = {
    "person_requested": ("pide-persona", "Pidió hablar con una persona"),
    "upset": ("molesto", "Está molesto o enojado"),
    "debt_claim": ("reclamo-deuda", "Reclamo por la deuda"),
    "payment_not_credited": ("pago-no-acreditado", "Pago que no figura acreditado"),
    "payment_plan": ("plan-de-pago", "Quiere un plan de pago"),
    "emergency": ("emergencia", "Urgencia en el edificio"),
    "no_answer": ("sin-respuesta", "El bot no tenía la respuesta"),
    "other": ("otro-motivo", "Otro motivo"),
    "technical_error": ("error-tecnico", "Error técnico del bot"),
    "non_pilot": ("fuera-de-piloto", "Edificio fuera de la prueba piloto del bot"),
    "window_closed": ("ventana-cerrada", "Ventana de 24 h cerrada: el bot no pudo responder"),
}
URGENT_LABEL = "urgente"
BUILDING_LABEL_PREFIX = "edificio-"


def slug(text: str) -> str:
    """ "Rodas II" -> "rodas-ii" (labels: lowercase letters, digits, - and _)."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def building_names(who: Identity) -> list[str]:
    return list(dict.fromkeys(display_building_name(u.building_name) for u in who.units))


def handoff_labels(handoff: Handoff, who: Identity) -> list[str]:
    labels = [REASONS.get(handoff.reason, REASONS["other"])[0]]
    labels += [BUILDING_LABEL_PREFIX + slug(name) for name in building_names(who)]
    if handoff.priority == "urgent":
        labels.append(URGENT_LABEL)
    return [label for label in dict.fromkeys(labels) if label]


def describe_person(who: Identity) -> str:
    if not who.known:
        return "Número no verificado (no se sabe quién es)."
    units = "; ".join(
        f"{display_building_name(u.building_name)} {u.unit_label} "
        f"({'propietario' if u.role == PersonRole.OWNER else 'inquilino'})"
        for u in who.units
    )
    return f"{who.full_name}" + (f" — {units}" if units else " — sin unidades activas")


def handoff_note(handoff: Handoff, who: Identity, *, phone_trusted: bool) -> str:
    reason = REASONS.get(handoff.reason, (None, handoff.reason))[1]
    priority = "URGENTE" if handoff.priority == "urgent" else "normal"
    phone = (
        "teléfono del canal (confiable)"
        if phone_trusted
        else "bandeja sin teléfono confiable: el número del contacto no se usó para identificar"
    )
    return "\n".join(
        [
            "🤖 Derivado por el bot",
            f"Motivo: {reason} · Prioridad: {priority}",
            f"Quién escribe: {describe_person(who)}",
            f"Identificación: {phone}",
            f"Resumen: {handoff.summary}",
        ]
    )
