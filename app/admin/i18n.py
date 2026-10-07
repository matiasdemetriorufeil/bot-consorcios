"""SQLAdmin in Spanish (voseo). SQLAdmin 0.32 ships no Spanish catalog and babel is not
installed, so it renders its templates with "null" translations (English). setup_admin
installs these callables in its Jinja environment instead: every text its templates pass
through _() comes out of ES, without copying those templates.

The templates we do copy (templates/sqladmin/list.html, edit.html, create.html) and the
texts SQLAdmin builds outside _() (filters, the failed login, two in its JavaScript) are
handled where they are. tests/admin/test_sqladmin_pin.py checks that every _() text of the
pinned SQLAdmin version is here.
"""

from typing import Any

from wtforms import Form

ES: dict[str, str] = {
    # Login and menu
    "Login to %(title)s": "Ingresar al panel del %(title)s",
    "Username": "Usuario",
    "Enter username": "Tu usuario",
    "Password": "Contraseña",
    "Login": "Ingresar",
    "Logout": "Salir",
    "Language": "Idioma",
    # Lists
    "Actions": "Acciones",
    "Delete selected items": "Borrar los elegidos",
    "Search": "Buscar",
    "Filters": "Filtros",
    "Apply Filter": "Aplicar filtro",
    "Clear filter": "Quitar filtro",
    "Select all": "Elegir todos",
    "Select item": "Elegir",
    "View": "Ver",
    "Edit": "Editar",
    "Delete": "Borrar",
    "New %(name)s": "Agregar %(name)s",
    "Showing %(start)s to %(end)s of %(count)s items": (
        "Mostrando del %(start)s al %(end)s de %(count)s"
    ),
    "Show": "Mostrar",
    "%(size)s / Page": "%(size)s por página",
    "prev": "anterior",
    "next": "siguiente",
    "Export": "Exportar",
    "You are not authorized to view these records.": "No tenés permiso para ver estos datos.",
    # Record page and forms
    "Column": "Dato",
    "Value": "Valor",
    "Go Back": "Volver",
    "Edit %(name)s": "Editar: %(name)s",
    "Save": "Guardar",
    "Save and continue editing": "Guardar y seguir editando",
    "Save and add another": "Guardar y agregar otro",
    "Save as new": "Guardar como nuevo",
    "Cancel": "Cancelar",
    # Confirmations
    "Please confirm": "Confirmá",
    "Yes": "Sí",
    "Close": "Cerrar",
    "Done": "Listo",
    # CSV import (not enabled in this panel)
    "Import": "Importar",
    "Import CSV": "Importar CSV",
    "Choose CSV file": "Elegí el archivo CSV",
    "No file selected": "No elegiste ningún archivo",
    "Import progress": "Avance de la importación",
    "Import result": "Resultado de la importación",
    "Missed rows": "Renglones salteados",
    "Refresh list": "Actualizar la lista",
    "Download CSV": "Descargar CSV",
    "Download TXT": "Descargar TXT",
    "Skip invalid rows and continue import": "Saltear los renglones inválidos y seguir",
    "%(processed)s/%(total)s rows processed": "%(processed)s/%(total)s renglones procesados",
    "%(imported)s imported, %(skipped)s skipped, %(processed)s/%(total)s rows processed": (
        "%(imported)s importados, %(skipped)s salteados, %(processed)s/%(total)s procesados"
    ),
    "%(imported)s imported, %(skipped)s skipped, %(processed)s/%(total)s rows processed"
    " - saving valid rows... (%(seconds)ss)": (
        "%(imported)s importados, %(skipped)s salteados, %(processed)s/%(total)s procesados:"
        " guardando los válidos… (%(seconds)s s)"
    ),
}


def gettext(message: str) -> str:
    return ES.get(message, message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return gettext(singular if n == 1 else plural)


def install(env: Any) -> None:
    """Replace SQLAdmin's null translations in its Jinja environment (newstyle: the
    templates' %(name)s placeholders are filled after translating)."""
    env.install_gettext_callables(gettext, ngettext, newstyle=True)


class SpanishForm(Form):
    """Base of SQLAdmin's forms (form_base_class, set by setup_admin): WTForms' own messages
    ("This field is required.") in Spanish, from the catalog WTForms ships."""

    class Meta:
        locales = ("es",)
