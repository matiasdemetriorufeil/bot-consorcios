"""Audit trail of the admin panel in bot_events: who did what (event_type "admin_action").

Payloads carry ids and the names of the changed fields, never their values (settings texts,
building information, codes).
"""

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.db.models import BotEvent

EVENT_TYPE = "admin_action"


def log_admin_action(
    session: Session,
    admin_user: str,
    action: str,
    *,
    phone_e164: str | None = None,
    conversation_id: int | None = None,
    **payload: Any,
) -> None:
    """Add the event to the session: it is committed together with the change."""
    session.add(
        BotEvent(
            conversation_id=conversation_id,
            phone_e164=phone_e164,
            event_type=EVENT_TYPE,
            payload={"admin_user": admin_user, "action": action, **payload},
        )
    )


def _comparable(value: Any) -> Any:
    """Form values next to model values: related objects by id, enums by value, blank text
    and None as the same."""
    mapper = inspect(value, raiseerr=False) if value is not None else None
    if mapper is not None and hasattr(mapper, "identity"):
        identity = mapper.identity
        return str(identity[0]) if identity else None
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def changed_fields(obj: Any, data: dict[str, Any]) -> list[str]:
    """Names of the form fields whose submitted value differs from the object's (call it
    before sqladmin applies the form)."""
    changed = []
    for name, new in data.items():
        old = getattr(obj, name, None)
        if _comparable(old) != _comparable(new):
            changed.append(name)
    return sorted(changed)
