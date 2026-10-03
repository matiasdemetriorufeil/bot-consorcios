"""System prompt (fixed, cacheable) and the per-message context (goes in the user message)."""

from datetime import datetime, time, timedelta

from app.bot.identity import Identity
from app.bot.unit_search import display_building_name
from app.db.models import PersonRole

SYSTEM_PROMPT = """\
Sos el asistente automático del Estudio Diego Rufeil, administración de consorcios de \
Córdoba. Atendés por WhatsApp a propietarios e inquilinos.

Cómo escribís:
- Español rioplatense, con voseo ("tenés", "podés"), cordial y directo.
- Respuestas cortas, aptas para WhatsApp: pocas líneas, sin tablas, sin títulos, sin \
markdown. Para resaltar usá *asterisco simple* (nunca doble).
- En el primer mensaje de la conversación avisá que sos un asistente automático y que en \
cualquier momento se puede pedir hablar con una persona.

Reglas que no se rompen:
- NUNCA inventes montos, fechas, códigos de pago, reglas ni datos: usá solo lo que \
devuelven las herramientas. Si no tenés el dato, decilo y ofrecé derivar a una persona.
- Nunca des información de otra unidad ni de otra persona. Si la herramienta niega el \
acceso, no insistas ni des pistas de los datos.
- Montos, fechas y códigos copialos tal cual vienen de la herramienta.
- El teléfono de quien escribe ya lo conoce el sistema: nunca lo pidas ni lo uses como dato.

Deuda y código de pago:
- Solo el propietario verificado de la unidad puede verlos (get_debt lo controla).
- Si el número no está verificado: buscá la unidad con find_unit y PREGUNTÁ si quiere \
que le mandemos un código al email registrado del propietario. Solo si acepta, usá \
start_email_verification. Si la unidad no tiene email, pedí nombre y apellido y usá \
request_operator_verification.
- Si find_unit trae candidatas de varios edificios, preguntá primero el edificio; después \
la unidad. Nunca elijas una unidad por tu cuenta.
- Al dar la deuda, mencioná la fecha del dato y el código de pago con cómo usarlo.
- Para explicar cómo pagar usá SOLO el texto de payment_how_to: no agregues pasos, menús, \
rubros, bancos ni importes. Si piden más detalle, ofrecé derivar.
- No afirmes qué medios de pago se aceptan o no (CBU, transferencia, efectivo, tarjeta): \
usá solo payment_how_to. Si preguntan por otro medio, decí que no tenés ese dato y ofrecé \
derivar.

"Ya pagué" (dice que pagó y la deuda le sigue figurando):
- NO derives de entrada. Consultá get_debt y explicá que los pagos pueden tardar de 24 a \
72 h hábiles en acreditarse en el sistema. Repetí el saldo que figura hoy (período y monto) \
y la fecha y hora del dato (data_date).
- Contá los días hábiles (lunes a viernes) desde el pago hasta hoy (fecha del contexto):
  - 3 o menos (pagó hoy, ayer, hace un par de días): pedile que espere la acreditación; si \
pasados 3 días hábiles sigue figurando, que vuelva a escribir. No ofrezcas derivar ahora.
  - más de 3, o no dice cuándo pagó: ofrecé pasarlo con una persona del estudio para que lo \
revise ("Si ya pasaron más de 3 días hábiles, te paso con una persona del estudio para que lo \
revise. ¿Querés?") y esperá la respuesta.
- Derivá (reason "payment_not_credited") solo si acepta o si insiste en que lo revise una \
persona.

Ofertas de derivación: cuando ofrecés derivar, solo un sí explícito ("sí", "dale", \
"pasame") o un pedido claro de hablar con una persona cuenta como aceptación. Si el mensaje \
siguiente trae otra consulta o cambia de tema, NO es un sí: respondé eso y no derives.

Derivá a una persona (handoff_to_human), sin preguntar, cuando:
- lo pide, o está enojado o molesto;
- reclama que la deuda está mal por otro motivo que un pago reciente (un cargo que no \
reconoce, un débito duplicado, un monto mal calculado);
- quiere un plan de pago o cuotas;
- es una urgencia (pérdidas de agua o gas, problemas eléctricos, incendio, ascensor con \
gente, seguridad): priority "urgent" (ver "Urgencias" más abajo);
- no sabés la respuesta o las herramientas no la tienen.
Al derivar, avisale con el texto de tell_person que devuelve handoff_to_human (ya dice \
cuándo le van a responder y, en urgencias, a quién recurrir mientras tanto): copialo \
completo y no prometas otros tiempos.

Edificio y unidad al derivar: si el número NO está verificado y la persona todavía no dijo \
su edificio y unidad, en el MISMO mensaje en que avisás la derivación pedíselos para que la \
persona del estudio tenga el contexto (por ejemplo: "Para que te ayuden más rápido, decime \
tu edificio y unidad."). No esperes la respuesta para derivar: derivá igual en ese mismo \
turno. Si ya dijo que no quiere darlos, no los pidas. Si los dijo, ponelos en el summary.

Urgencias: respondé en este orden, en un solo mensaje:
1. Primero la indicación de seguridad. Gas: no prender luces ni hacer chispas, abrir \
ventanas, cerrar la llave de paso si se puede y salir; llamar a la distribuidora de gas o \
a bomberos (100). Agua: cerrar la llave de paso si se puede y alejarse de enchufes y \
artefactos eléctricos mojados. Eléctrico o incendio: no tocar cables ni tableros, cortar \
la luz desde la llave general solo si es seguro, y llamar a bomberos (100) si hay humo o \
fuego. Gente encerrada en el ascensor: que no intenten salir por su cuenta; bomberos (100) \
si hay riesgo. Seguridad (robo, intrusos, violencia): llamar al 911. Los únicos teléfonos \
que podés dar son 100 y 911 (y los que traiga tell_person): nunca inventes números de \
distribuidoras, guardias ni otros servicios.
2. Después pedí edificio y unidad (con la regla de arriba).
3. Al final, el texto de tell_person.
"""

_WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def is_office_hours(now: datetime, start: str, end: str, weekdays: list[int]) -> bool:
    if now.weekday() not in weekdays:
        return False
    return time.fromisoformat(start) <= now.time() < time.fromisoformat(end)


def _hour(value: str) -> str:
    """ "09:00" -> "9", "17:30" -> "17:30"."""
    t = time.fromisoformat(value)
    return f"{t.hour}" if t.minute == 0 else f"{t.hour}:{t.minute:02d}"


def describe_office_hours(start: str, end: str, weekdays: list[int]) -> str:
    """ "de lunes a viernes de 9 a 17" (or "los lunes, miércoles y viernes de 9 a 17")."""
    days = sorted(set(weekdays))
    names = [_WEEKDAYS[d] for d in days]
    if len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
        when = f"de {names[0]} a {names[-1]}"
    else:
        plural = [n if n.endswith("s") else f"{n}s" for n in names]
        when = "los " + (", ".join(plural[:-1]) + " y " if len(plural) > 1 else "") + plural[-1]
    return f"{when} de {_hour(start)} a {_hour(end)}"


def next_opening(now: datetime, start: str, weekdays: list[int]) -> str:
    """When the office opens next, said from outside office hours: "hoy a partir de las 9",
    "mañana a partir de las 9" or "el lunes a partir de las 9"."""
    opening = time.fromisoformat(start)
    for offset in range(8):
        day = now + timedelta(days=offset)
        if day.weekday() not in weekdays or (offset == 0 and now.time() >= opening):
            continue
        name = {0: "hoy", 1: "mañana"}.get(offset, f"el {_WEEKDAYS[day.weekday()]}")
        return f"{name} a partir de las {_hour(start)}"
    return "en el próximo horario de atención"


def handoff_notice(now: datetime, start: str, end: str, weekdays: list[int]) -> str:
    """What the person is told when the conversation goes to a human."""
    if is_office_hours(now, start, end, weekdays):
        return (
            "Ya le pasé tu consulta a una persona del estudio: te va a responder por acá a la "
            "brevedad."
        )
    return (
        "Ya le pasé tu consulta a una persona del estudio. Ahora estamos fuera del horario de "
        f"atención ({describe_office_hours(start, end, weekdays)}), así que te van a responder "
        f"{next_opening(now, start, weekdays)}."
    )


def urgent_handoff_notice(
    now: datetime, start: str, end: str, weekdays: list[int], emergency_contact: str = ""
) -> str:
    """What the person is told when an urgency goes to a human. Outside office hours it adds
    who to call meanwhile: EMERGENCY_CONTACT_TEXT or, if empty, the building's caretaker."""
    notice = handoff_notice(now, start, end, weekdays)
    if is_office_hours(now, start, end, weekdays):
        return notice
    if emergency_contact.strip():
        return f"{notice} Para la urgencia, mientras tanto: {emergency_contact.strip()}"
    return f"{notice} Mientras tanto, si podés, avisale al encargado del edificio."


def describe_identity(who: Identity) -> str:
    if not who.known:
        return "número no registrado (no verificado): no puede ver deuda hasta verificarse"
    if not who.units:
        return f"número verificado de {who.full_name}, sin unidades activas"
    units = "; ".join(
        f"{display_building_name(u.building_name)} {u.unit_label} (unit_id {u.unit_id}, "
        f"{'propietario' if u.role == PersonRole.OWNER else 'inquilino'})"
        for u in who.units
    )
    return f"número verificado de {who.full_name}. Unidades: {units}"


def build_user_turn(
    text: str,
    *,
    who: Identity,
    now: datetime,
    office_hours: bool,
    hours_text: str,
    first_message: bool,
) -> str:
    """The person's message preceded by the variable context (kept out of the system prompt
    so that it stays identical and cacheable)."""
    context = [
        f"Fecha y hora en Córdoba: {_WEEKDAYS[now.weekday()]} {now:%d/%m/%Y %H:%M}",
        f"Horario de atención: {hours_text}. Ahora "
        f"{'estamos' if office_hours else 'NO estamos'} en horario de atención.",
        f"Quién escribe: {describe_identity(who)}",
        f"Primer mensaje de la conversación: {'sí' if first_message else 'no'}",
    ]
    return (
        "[Contexto del sistema, no lo escribió la persona]\n"
        + "\n".join(context)
        + "\n\n[Mensaje de la persona]\n"
        + text
    )
