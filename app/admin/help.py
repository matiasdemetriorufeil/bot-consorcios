"""The panel's help texts, in one place: a line under each page's title (what it is for and
when to use it), what an empty page means and what to do, the confirmations before what
cannot be undone, and the steps of the tour of "Conversaciones".

Written for the two employees who use the panel every day: plain Spanish (voseo), about what
they see and do, never how the system works inside. tests/admin/test_help.py checks a list of
words that need an explanation (e.g. "sincronización", "característica") is not used here.
"""

from typing import Any

from starlette.requests import Request

# --- A line under each page's title ------------------------------------------------------------
# Own pages by their route's name ("view-<identity>"); SQLAdmin's pages by "<page>:<identity>"
# (page: list, details, edit or create), with the list's text for the other pages of a model.

PAGE_HELP: dict[str, str] = {
    # What the operators use
    "view-conversations": (
        "Las charlas de WhatsApp. En «Esperando persona» están las que el bot no pudo "
        "resolver y necesitan a alguien del estudio. Para un repaso, tocá «Cómo usar»."
    ),
    "view-conversation-example": (
        "Un ejemplo para aprender a usar Conversaciones. Nada de lo que toques acá se manda."
    ),
    "view-conversation": (
        "Tomá la conversación para contestar vos. Cuando termines, resolvela o devolvésela al bot."
    ),
    "view-phones": (
        "Números de los propietarios e inquilinos. En «A revisar» están los que hay que "
        "confirmar antes de que el bot los reconozca."
    ),
    "view-verifications": (
        "Personas que dijeron ser de una unidad y necesitan que alguien del estudio lo "
        "confirme. Aprobá solo si estás segura de que es el propietario."
    ),
    "view-verification-approve": (
        "Elegí el propietario de la unidad. Desde ese momento el bot le va a dar la "
        "información de esa unidad a este número."
    ),
    "view-amenities": "Los edificios con SUM. Abrí la planilla para ver los turnos de la semana.",
    "view-amenity-week": (
        "Los turnos de la semana. Tocá uno verde para reservarlo o uno azul para ver la reserva."
    ),
    "view-amenity-book": "Elegí la unidad que reserva el turno. Las notas las ve solo el estudio.",
    "view-amenity-reservation": "Los datos de la reserva. Si hace falta, podés cancelarla acá.",
    "view-amenity-config": (
        "Las reglas del SUM de este edificio y sus turnos. Lo que cambies acá vale para todos "
        "los que reservan, también por WhatsApp."
    ),
    "view-claims": "Pronto vas a poder ver acá los reclamos de los propietarios.",
    "view-guide": "Cómo hacer las tareas de todos los días, paso a paso.",
    # Admins
    "list:building": (
        "Los edificios del estudio, como figuran en ConsorPlus. Acá se elige cuáles atiende el "
        "bot (prueba piloto) y cuáles están activos."
    ),
    "edit:building": (
        "El nombre viene de ConsorPlus y no se cambia acá. Podés cambiar la dirección, si está "
        "activo y si está en la prueba piloto."
    ),
    "list:building-info": (
        "Lo que el bot sabe de cada edificio (reglamento, horarios, contactos) para contestar "
        "preguntas."
    ),
    "edit:building-info": (
        "El bot usa este texto tal cual para contestar. Escribilo como se lo dirías a un "
        "propietario."
    ),
    "create:building-info": (
        "El bot usa este texto tal cual para contestar. Escribilo como se lo dirías a un "
        "propietario."
    ),
    "edit:bot-settings": (
        "Los textos y horarios del bot. Los cambios se usan desde el próximo mensaje."
    ),
    "list:wa-template": (
        "Los mensajes ya preparados que se pueden mandar cuando pasó más de un día desde el "
        "último mensaje de la persona."
    ),
    "list:quick-reply": (
        "Textos guardados que las operadoras pegan con un clic al responder una conversación."
    ),
    "list:sync-run": (
        "Cada vez que el sistema leyó los datos de ConsorPlus (propietarios, unidades y "
        "deudas), y si salió bien."
    ),
    "view-users": "Las personas que entran al panel. Cada una tiene su usuario.",
    "view-user-edit": (
        "Cambiá el nombre, el rol o la contraseña. Si la desactivás, no puede entrar más."
    ),
    "view-metrics": "Cuánto se usó el bot este mes y cuántas conversaciones necesitaron a alguien.",
    "view-dev-chat": "Para probar el bot como si fueras otra persona. Solo existe en las pruebas.",
}


def page_help(request: Request) -> str:
    """The line under the page's title, by the route that served it ("" when none)."""
    route = request.scope.get("route")
    name = getattr(route, "name", None) or ""
    identity = request.path_params.get("identity")
    if identity and name in ("list", "details", "edit", "create"):
        return PAGE_HELP.get(f"{name}:{identity}") or PAGE_HELP.get(f"list:{identity}", "")
    return PAGE_HELP.get(name, "")


# --- Empty pages: what it means and what to do -------------------------------------------------

EMPTY: dict[str, str] = {
    "conversations:waiting": (
        "No hay conversaciones esperando. Cuando el bot necesite a alguien, va a aparecer acá "
        "con un aviso."
    ),
    "conversations:mine": (
        "No tenés conversaciones tomadas. Para tomar una, abrila y tocá «Tomar control»."
    ),
    "conversations:bot": "Ninguna conversación está con el bot en este momento.",
    "conversations:resolved": "Todavía no hay conversaciones resueltas.",
    "conversations:search": "No se encontró nada. Probá con otra parte del nombre o del número.",
    "phones:review": (
        "No hay teléfonos para revisar. Cuando aparezca un número que hay que confirmar, va a "
        "estar acá."
    ),
    "phones:search": (
        "No se encontró ningún teléfono. Probá con menos números o con otra parte del nombre, "
        "y fijate que los filtros no estén limitando la búsqueda."
    ),
    "phones:all": "Todavía no hay teléfonos cargados.",
    "verifications": (
        "No hay verificaciones pendientes. Aparecen cuando alguien se identifica por WhatsApp "
        "y en su unidad no hay un email del propietario para confirmarlo solo."
    ),
    "amenities:operator": (
        "Todavía ningún edificio tiene SUM cargado. Si hace falta uno, pedile a un admin que lo "
        "agregue."
    ),
    "amenities:admin": "Todavía ningún edificio tiene SUM cargado. Agregalo abajo.",
    "list:building": "No hay edificios. Aparecen solos cuando se leen los datos de ConsorPlus.",
    "list:building-info": (
        "Todavía no hay información cargada. Agregá el reglamento o los horarios de un "
        "edificio para que el bot pueda contestar sobre eso."
    ),
    "list:wa-template": (
        "No hay mensajes preparados. Sin ellos no se le puede escribir a alguien que no "
        "escribió en el último día."
    ),
    "list:quick-reply": (
        "Todavía no hay respuestas rápidas. Agregá una con el botón de arriba y va a aparecer "
        "al responder en Conversaciones."
    ),
    "list:sync-run": "Todavía no se leyeron datos de ConsorPlus.",
}
EMPTY_DEFAULT = "Todavía no hay nada acá."


def empty_for(identity: str) -> str:
    """The empty text of one of SQLAdmin's lists."""
    return EMPTY.get(f"list:{identity}", EMPTY_DEFAULT)


# --- Confirmations before what cannot be undone ------------------------------------------------
# What happens to the person on the other side, said plainly (none of these sends anything).

CONFIRM: dict[str, dict[str, str]] = {
    "unlink": {
        "title": "¿Desvincular este teléfono?",
        "text": (
            "Se borra este número. El bot ya no va a reconocer a la persona cuando escriba y "
            "va a tener que volver a identificarse por WhatsApp. Si el número sigue cargado en "
            "ConsorPlus, a la mañana siguiente vuelve a aparecer: corregilo también allá."
        ),
        "button": "Desvincular",
        "kind": "danger",
    },
    "reject": {
        "title": "¿Rechazar esta verificación?",
        "text": (
            "El número no se asocia a la unidad. A la persona no le llega ningún aviso: si "
            "querés que sepa, escribile desde Conversaciones."
        ),
        "button": "Rechazar",
        "kind": "danger",
    },
    "cancel_reservation": {
        "title": "¿Cancelar esta reserva?",
        "text": (
            "El turno queda libre para otra unidad. A la unidad no le llega ningún aviso: "
            "avisale vos."
        ),
        "button": "Cancelar reserva",
        "kind": "danger",
    },
    "resolve": {
        "title": "¿Resolver esta conversación?",
        "text": (
            "Pasa a «Resueltas». A la persona no le llega nada. Si vuelve a escribir, la "
            "atiende el bot."
        ),
        "button": "Resolver",
        "kind": "primary",
    },
    "remove_slot": {
        "title": "¿Eliminar este turno?",
        "text": "Nadie va a poder reservarlo más, ni desde el panel ni por WhatsApp.",
        "button": "Eliminar",
        "kind": "danger",
    },
    "deactivate_user": {
        "title": "¿Desactivar este usuario?",
        "text": "No va a poder entrar más al panel. Se puede volver a activar después.",
        "button": "Desactivar",
        "kind": "danger",
    },
    "restart_test_chat": {
        "title": "¿Empezar de nuevo?",
        "text": "Se borran los mensajes de esta conversación de prueba.",
        "button": "Empezar de nuevo",
        "kind": "danger",
    },
}


def confirm_attrs(key: str, detail: str = "") -> dict[str, str]:
    """The data-confirm-* attributes of a form (panel.js opens the dialog with them)."""
    item = CONFIRM[key]
    text = f"{detail} {item['text']}".strip() if detail else item["text"]
    return {
        "data-confirm-title": item["title"],
        "data-confirm-text": text,
        "data-confirm-button": item["button"],
        "data-confirm-kind": item["kind"],
    }


# --- The tour of "Conversaciones" ----------------------------------------------------------------
# Each step points at the element with data-tour="<target>" (centered when it is not on the page,
# with the step's "missing" text, or TOUR_MISSING).

TOUR: list[dict[str, str]] = [
    {
        "target": "waiting",
        "title": "Esperando persona",
        "text": (
            "Acá llegan las conversaciones que el bot no pudo resolver. Las urgentes van "
            "primero, en rojo. Cuando entra una nueva, el panel te avisa con un sonido."
        ),
        # On a phone, with a conversation open, the list is hidden.
        "missing": "En el celular, la vas a ver al tocar «Volver».",
    },
    {
        "target": "take",
        "title": "Tomar control",
        "text": "El bot deja de contestar y la conversación queda en «Mías».",
    },
    {
        "target": "reply",
        "title": "Responder",
        "text": (
            "Escribí y tocá Enviar. Si pasó más de un día desde su último mensaje, WhatsApp "
            "solo deja mandar uno de los mensajes ya preparados."
        ),
    },
    {
        "target": "return",
        "title": "Devolver al bot",
        "text": "Si el bot puede seguir solo, por ejemplo para darle la deuda o el código de pago.",
    },
    {
        "target": "resolve",
        "title": "Resolver",
        "text": "Cuando terminaste. Si la persona vuelve a escribir, la atiende el bot.",
    },
]
TOUR_MISSING = "Abrí una conversación para ver este botón."

# --- The example of "Cómo usar" (app.admin.example) -----------------------------------------------
# What each button would do in a real conversation (the example's do nothing).

EXAMPLE_BANNER = "Esto es un ejemplo: nada de lo que toques acá se manda."
EXAMPLE_DONE = (
    "Esto era un ejemplo: en tu bandeja vas a ver tus conversaciones reales. Si querés "
    "repasarlo, tocá «Cómo usar» cuando quieras."
)
EXAMPLE_NOTES: dict[str, str] = {
    "take": "En una conversación real, esto la pasa a «Mías» y el bot deja de contestar.",
    "return": "En una conversación real, el bot vuelve a contestar solo.",
    "resolve": "En una conversación real, esto la pasa a «Resueltas».",
    "reply": "En una conversación real, esto le manda tu mensaje por WhatsApp.",
    "template": "En una conversación real, esto le manda el mensaje preparado por WhatsApp.",
    "note": "En una conversación real, la nota queda guardada y solo la ve el estudio.",
    "link": "En tu bandeja real, esto te lleva a esa conversación o pestaña.",
    "search": "En tu bandeja real, esto busca entre tus conversaciones.",
}


def globals_for_templates() -> dict[str, Any]:
    """What setup_admin adds to the templates."""
    return {
        "page_help": page_help,
        "empty_for": empty_for,
        "confirm_attrs": confirm_attrs,
        "empty_texts": EMPTY,
    }
