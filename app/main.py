from fastapi import FastAPI

from app import dev
from app.chatwoot import webhook as chatwoot_webhook
from app.config import get_settings
from app.logging_setup import configure_logging

configure_logging(get_settings().log_level)

app = FastAPI(title="Bot Consorcios - Estudio Diego Rufeil")
app.include_router(chatwoot_webhook.router)
app.include_router(dev.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
