"""POST /webhooks/chatwoot: events of the Chatwoot Agent Bot.

Chatwoot v4.18 signs Agent Bot webhooks (lib/webhooks/trigger.rb) with the bot's "Webhook
Secret": X-Chatwoot-Timestamp = unix seconds and X-Chatwoot-Signature =
"sha256=" + HMAC-SHA256(secret, f"{timestamp}.{raw body}"). Requests without a valid
signature are rejected; without CHATWOOT_WEBHOOK_SECRET every request is.

The answer is generated in a background task: Chatwoot waits 5 seconds at most and, on a
500, delivers the same message again (the message id is stored, so it is answered once).
"""

import hashlib
import hmac
import json
import logging
import time
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.bot.agent import Agent
from app.chatwoot.client import ChatwootClient, ChatwootError
from app.chatwoot.events import ignore_reason, parse_incoming
from app.chatwoot.processor import ChatwootBot
from app.config import Settings, get_settings
from app.db.models import ChatwootProcessedMessage
from app.db.session import SessionLocal, get_session
from app.llm import get_prices, get_provider

logger = logging.getLogger(__name__)

router = APIRouter()

# How old a signed request may be (Chatwoot signs at send time, also on retries).
MAX_SIGNATURE_AGE_SECONDS = 300


def verify_signature(
    secret: str, body: bytes, timestamp: str | None, signature: str | None, now: float
) -> bool:
    if not timestamp or not signature or not timestamp.isdigit():
        return False
    if abs(now - int(timestamp)) > MAX_SIGNATURE_AGE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return hmac.compare_digest(signature, f"sha256={expected.hexdigest()}")


@lru_cache
def _build_bot() -> ChatwootBot:
    settings = get_settings()
    return ChatwootBot(
        ChatwootClient.from_settings(settings),
        SessionLocal,
        lambda: Agent(get_provider(settings), prices=get_prices(settings), settings=settings),
        settings,
    )


def get_chatwoot_bot() -> ChatwootBot:
    try:
        return _build_bot()
    except ChatwootError as exc:
        raise HTTPException(503, str(exc)) from exc


async def _raw_body(request: Request) -> bytes:
    return await request.body()


def _mark_new(session: Session, message_id: int, conversation_id: int) -> bool:
    """Stores the message id; False if it was already there (duplicate delivery)."""
    stmt = (
        pg_insert(ChatwootProcessedMessage)
        .values(message_id=message_id, conversation_id=conversation_id)
        .on_conflict_do_nothing(index_elements=["message_id"])
        .returning(ChatwootProcessedMessage.message_id)
    )
    inserted = session.execute(stmt).first() is not None
    session.commit()
    return inserted


@router.post("/webhooks/chatwoot")
def chatwoot_webhook(
    request: Request,
    background: BackgroundTasks,
    body: Annotated[bytes, Depends(_raw_body)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    bot: Annotated[ChatwootBot, Depends(get_chatwoot_bot)],
) -> dict[str, Any]:
    if settings.chatwoot_webhook_secret is None:
        raise HTTPException(503, "CHATWOOT_WEBHOOK_SECRET no configurado")
    if not verify_signature(
        settings.chatwoot_webhook_secret.get_secret_value(),
        body,
        request.headers.get("X-Chatwoot-Timestamp"),
        request.headers.get("X-Chatwoot-Signature"),
        time.time(),
    ):
        raise HTTPException(401, "firma inválida")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(400, "JSON inválido") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "JSON inválido")

    if reason := ignore_reason(payload):
        return {"status": "ignored", "reason": reason}
    message = parse_incoming(payload)
    if settings.chatwoot_account_id is not None and message.account_id not in (
        None,
        settings.chatwoot_account_id,
    ):
        return {"status": "ignored", "reason": "other_account"}
    if not _mark_new(session, message.message_id, message.conversation_id):
        return {"status": "ignored", "reason": "duplicate"}
    background.add_task(bot.handle, message)
    return {"status": "accepted"}
