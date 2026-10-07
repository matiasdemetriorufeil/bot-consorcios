"""What a handoff leaves for the operators: labels and the internal note. Invented data."""

from app.bot.identity import Identity, UnitAccess
from app.bot.tools import Handoff
from app.channels.handoff import handoff_labels, handoff_note, slug
from app.db.models import PersonRole

ANA = Identity(
    person_id=1,
    full_name="Ana Prueba",
    units=(
        UnitAccess(1, "031 RODAS II", "04-C", PersonRole.OWNER, 10),
        UnitAccess(2, "031 RODAS II", "Cochera 3", PersonRole.OWNER, 10),
        UnitAccess(3, "045 PEÑA ALTA", "01-A", PersonRole.TENANT, 11),
    ),
)


def test_slug() -> None:
    assert slug("Peña Alta") == "pena-alta"
    assert slug("Rodas II") == "rodas-ii"


def test_handoff_labels() -> None:
    urgent = Handoff("emergency", "x", "urgent")
    assert handoff_labels(urgent, ANA) == [
        "emergencia",
        "edificio-rodas-ii",
        "edificio-pena-alta",
        "urgente",
    ]
    assert handoff_labels(Handoff("cualquiera", "x"), Identity()) == ["otro-motivo"]


def test_handoff_note() -> None:
    note = handoff_note(Handoff("payment_plan", "Quiere pagar en cuotas."), ANA, phone_trusted=True)

    assert note.splitlines() == [
        "🤖 Derivado por el bot",
        "Motivo: Quiere un plan de pago · Prioridad: normal",
        "Quién escribe: Ana Prueba — RODAS II 04-C (propietario); RODAS II Cochera 3 "
        "(propietario); PEÑA ALTA 01-A (inquilino)",
        "Identificación: teléfono del canal (confiable)",
        "Resumen: Quiere pagar en cuotas.",
    ]
    unknown = handoff_note(Handoff("upset", "x", "urgent"), Identity(), phone_trusted=True)
    assert "Prioridad: URGENTE" in unknown
    assert "Número no verificado" in unknown
