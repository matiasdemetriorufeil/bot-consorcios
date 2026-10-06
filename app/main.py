import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app import dev
from app.admin import setup_admin
from app.chatwoot import webhook as chatwoot_webhook
from app.config import get_settings
from app.db.session import engine
from app.logging_setup import configure_logging
from app.whatsapp import webhook as whatsapp_webhook

configure_logging(get_settings().log_level)
logger = logging.getLogger(__name__)


def recover_whatsapp() -> None:
    """Answers the WhatsApp messages a restart left unanswered (app.whatsapp.bot)."""
    settings = get_settings()
    try:
        bot = whatsapp_webhook.build_whatsapp_bot(settings)
    except Exception:
        logger.exception("Recovery of WhatsApp messages not started")
        return
    bot.recover_unanswered(timedelta(minutes=settings.whatsapp_recovery_minutes))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if get_settings().channel == "whatsapp":
        # In the background: startup does not wait for the LLM.
        threading.Thread(target=recover_whatsapp, name="wa-recovery", daemon=True).start()
    yield


app = FastAPI(title="Bot Consorcios - Estudio Diego Rufeil", lifespan=lifespan)
app.include_router(chatwoot_webhook.router)
app.include_router(whatsapp_webhook.router)
app.include_router(dev.router)
# The panel gets its own sessionmaker (SQLAdmin reconfigures the one it receives).
setup_admin(app, sessionmaker(bind=engine), get_settings())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
