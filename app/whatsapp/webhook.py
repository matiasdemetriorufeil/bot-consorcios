"""GET/POST /webhooks/whatsapp: the WhatsApp Cloud API's webhook (the bot's only channel).

GET: Meta's verification when the webhook is set up (hub.mode=subscribe, hub.verify_token =
WHATSAPP_VERIFY_TOKEN): answers hub.challenge.

POST: Meta signs every delivery with the app secret: X-Hub-Signature-256 =
"sha256=" + HMAC-SHA256(WHATSAPP_APP_SECRET, raw body). Without a valid signature, 401;
without WHATSAPP_APP_SECRET, 503 for everything. Messages, echoes and statuses are stored in
the request (quick, app.whatsapp.store) and the 200 goes right away: Meta retries what does
not get it, and a message stored twice is not possible (unique wamid). The answer runs in a
background task (app.whatsapp.bot), one per new message.
"""

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.bot.agent import Agent
from app.channels.locks import advisory_lock
from app.channels.processor import BotProcessor
from app.config import Settings, get_settings
from app.db.session import SessionLocal, engine, get_session
from app.llm import get_prices, get_provider
from app.sync import live
from app.whatsapp import store
from app.whatsapp.bot import WhatsAppBot
from app.whatsapp.channel import WhatsAppChannel
from app.whatsapp.client import WhatsAppError
from app.whatsapp.events import parse_delivery
from app.whatsapp.media import MediaStore
from app.whatsapp.simulator import build_client

logger = logging.getLogger(__name__)

router = APIRouter()


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected}")


def build_whatsapp_bot(settings: Settings) -> WhatsAppBot:
    """The bot of the webhook, the recovery and the panel's test chat (in development its
    client simulates what goes to the test chat's contacts: app.whatsapp.simulator)."""
    client = build_client(settings, SessionLocal)
    processor = BotProcessor(
        WhatsAppChannel(client, SessionLocal),
        SessionLocal,
        lambda: Agent(get_provider(settings), prices=get_prices(settings), settings=settings),
        settings,
        warm_up=live.warm_up,
        lock=advisory_lock(engine),
    )
    media = MediaStore(client, settings.whatsapp_media_dir, settings.whatsapp_media_max_bytes)
    return WhatsAppBot(processor, SessionLocal, media)


@lru_cache
def _cached_bot() -> WhatsAppBot:
    return build_whatsapp_bot(get_settings())


def get_whatsapp_bot() -> WhatsAppBot:
    try:
        return _cached_bot()
    except WhatsAppError as exc:
        raise HTTPException(503, str(exc)) from exc


async def _raw_body(request: Request) -> bytes:
    return await request.body()


@router.get("/webhooks/whatsapp", response_class=PlainTextResponse)
def verify_webhook(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> str:
    if settings.whatsapp_verify_token is None:
        raise HTTPException(503, "WHATSAPP_VERIFY_TOKEN no configurado")
    params = request.query_params
    token = params.get("hub.verify_token") or ""
    expected = settings.whatsapp_verify_token.get_secret_value()
    if params.get("hub.mode") != "subscribe" or not hmac.compare_digest(
        token.encode(), expected.encode()
    ):
        logger.warning("WhatsApp webhook verification rejected")
        raise HTTPException(403, "token inválido")
    logger.info("WhatsApp webhook verified by Meta")
    return params.get("hub.challenge") or ""


@router.post("/webhooks/whatsapp")
def whatsapp_webhook(
    request: Request,
    background: BackgroundTasks,
    body: Annotated[bytes, Depends(_raw_body)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    bot: Annotated[WhatsAppBot, Depends(get_whatsapp_bot)],
) -> dict[str, Any]:
    if settings.whatsapp_app_secret is None:
        raise HTTPException(503, "WHATSAPP_APP_SECRET no configurado")
    if not verify_signature(
        settings.whatsapp_app_secret.get_secret_value(),
        body,
        request.headers.get("X-Hub-Signature-256"),
    ):
        logger.warning("WhatsApp webhook rejected: invalid or missing signature")
        raise HTTPException(401, "firma inválida")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(400, "JSON inválido") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "JSON inválido")

    delivery = parse_delivery(payload, settings.whatsapp_phone_number_id)
    if not (delivery.messages or delivery.statuses or delivery.echoes):
        logger.debug("WhatsApp webhook without anything for us")
        return {"status": "ignored"}
    now = datetime.now(UTC)
    new: list[int] = []
    for message in delivery.messages:
        message_id = store.record_incoming(session, message, now)
        if message_id is None:
            logger.info("WhatsApp message already received: duplicate ignored")
        else:
            new.append(message_id)
    for echo in delivery.echoes:
        store.record_echo(session, echo, now)
    for status in delivery.statuses:
        store.record_status(session, status, now)
    session.commit()
    if new:
        logger.info("WhatsApp webhook accepted: %d new message(s)", len(new))
    for message_id in new:
        background.add_task(bot.process, message_id)
    return {"status": "accepted"}
