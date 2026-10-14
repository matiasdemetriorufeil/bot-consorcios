"""The one text of each role of a person in a unit, for the bot, the handoffs and the panel.

Here and not in app.admin.labels (which re-exports it as PERSON_ROLE) so that app.bot and
app.channels need not import the panel.
"""

from app.db.models import PersonRole

PERSON_ROLE = {
    PersonRole.OWNER: "propietario/a",
    PersonRole.TENANT: "inquilino/a",
    PersonRole.RESIDENT: "familiar u otro",
}


def role_text(role: PersonRole | str) -> str:
    return PERSON_ROLE.get(PersonRole(role), str(role))


def role_title(role: PersonRole | str) -> str:
    """The same text with a capital, for a column: «Propietario/a»."""
    text = role_text(role)
    return text[:1].upper() + text[1:]
