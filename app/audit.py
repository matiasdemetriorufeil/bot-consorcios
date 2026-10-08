"""The audit trail in bot_events of what people do from the panel (event_type "admin_action"):
who did what, with ids and the names of the changed fields, never their values. Outside
app.admin so the rules modules (app.claims.service) can log without importing the panel."""

from typing import Any

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
