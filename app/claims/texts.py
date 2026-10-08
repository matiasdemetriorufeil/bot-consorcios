"""Everything the bot tells a neighbor about claims, in one place (app.claims.flow and the
agent's claim tools use these: the model never writes them). Rioplatense Spanish, short, and
never a promise of when it will be solved."""

from app.db.models import ClaimStatus

# --- The steps ------------------------------------------------------------------------------

CANCEL = "Cancelar"
CANCEL_WORDS = ("cancelar", "cancelar reclamo", "cancela", "cancelá")
CANCEL_HINT = "Si querés dejarlo, escribí *cancelar*."
YES = "Sí"
OTHER_KIND = "Elegir otro"
MORE_OPTIONS = "Más opciones"
MORE_OPTIONS_DESCRIPTION = "Ver el resto de los problemas"
DONE = "Listo"
NO_PHOTOS = "Sin fotos"
REGISTER = "Sí, registrar"
DONT_REGISTER = "No"

CHOOSE_UNIT = "¿Por cuál de tus unidades es el reclamo?"
CONFIRM_KIND = "¿Es por *{title}*?"
CHOOSE_KIND = "¿Qué problema querés reportar?"
CHOOSE_KIND_MORE = "Estos son los demás problemas. ¿Cuál es?"
DESCRIBE = "Contanos en pocas palabras qué pasa."
PHOTO_SAVED = "Recibí la foto."
TOO_LONG = "Es un poco largo: contámelo en menos de {limit} caracteres."
PHOTOS = "Si querés, mandá fotos ahora. Cuando termines, tocá *Listo*."
PHOTO_RECEIVED = "Recibí la foto ({count} de {limit}). Podés mandar otra o tocar *Listo*."
PHOTO_LIMIT = "Ya tengo {limit} fotos, que es el máximo. Tocá *Listo* para seguir."
PHOTO_NOT_SAVED = "No pude guardar esa foto. Probá mandarla de nuevo o tocá *Listo*."
ADDED_TO_DESCRIPTION = "Lo sumé a la descripción. Si querés, mandá fotos o tocá *Listo*."
DESCRIPTION_FULL = (
    "Eso ya no entra en la descripción (hasta {limit} caracteres). Mandá fotos o tocá *Listo*."
)
SUMMARY = (
    "Revisá el reclamo:\n"
    "*Edificio:* {building}\n"
    "*Unidad:* {unit}\n"
    "*Problema:* {problem}\n"
    "*Qué pasa:* {description}\n"
    "*Fotos:* {photos}\n\n"
    "¿Lo registro?"
)
WHOLE_BUILDING = "todo el edificio"
EMERGENCY_PHONE = "Teléfono de emergencias: {phone}"

# --- How it ends ----------------------------------------------------------------------------

CREATED_WITH_PROVIDER = (
    "Listo, registramos tu reclamo *#{number}* ({problem}). Le vamos a avisar a la empresa que "
    "se encarga y te escribimos cuando lo confirme."
)
CREATED_FOR_STUDIO = (
    "Listo, registramos tu reclamo *#{number}* ({problem}). Lo va a ver una persona del estudio."
)
JOINED = (
    "Ya había un reclamo abierto por esto en el edificio (*#{number}*). Te sumamos para "
    "avisarte cuando se solucione."
)
ALREADY_JOINED = (
    "Ya había un reclamo abierto por esto en el edificio (*#{number}*) y ya estabas sumado: te "
    "vamos a avisar cuando se solucione."
)
PREVIOUS = "Lo vinculamos con tu reclamo anterior #{number}."
NOT_REGISTERED = "Listo, no registré el reclamo. Si querés, empezamos de nuevo cuando quieras."
CANCELLED = "Listo, dejé el reclamo sin registrar."
DROPPED = "Dejé sin registrar el reclamo que estabas cargando."
EXPIRED = "Pasó un rato largo, así que dejé sin registrar el reclamo que estabas cargando."
FAILED = "No pude registrar el reclamo: {reason}"

# --- Who may report -------------------------------------------------------------------------

NOT_AVAILABLE = (
    "Por acá todavía no puedo registrar reclamos de tu edificio. Si querés, te paso con una "
    "persona del estudio para que lo tome."
)
NO_KINDS = (
    "En tu edificio todavía no hay problemas cargados para reclamar por acá. Si querés, te paso "
    "con una persona del estudio."
)

# --- "Mis reclamos" and the status of one ---------------------------------------------------

# What a neighbor sees (the panel's labels are for the studio).
STATUS = {
    ClaimStatus.PENDING_SEND: "registrado, le vamos a avisar a la empresa",
    ClaimStatus.SENT: "avisado a la empresa",
    ClaimStatus.ACKNOWLEDGED: "la empresa lo confirmó",
    ClaimStatus.STUDIO: "lo está viendo el estudio",
    ClaimStatus.SOLVED: "solucionado",
    ClaimStatus.CANCELLED: "cancelado",
}
MY_CLAIMS = "Tus reclamos:"
MY_CLAIMS_LINE = "• *#{number}* {problem} ({building}): {status}"
MY_CLAIMS_NONE = "No tenés reclamos abiertos ni cerrados en los últimos 30 días."
CLAIM_STATUS = "Reclamo *#{number}* ({problem}, {building}), del {date}: {status}."
CLAIM_NOT_FOUND = "No encontré un reclamo tuyo con ese número."
