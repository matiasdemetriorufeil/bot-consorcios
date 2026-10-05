from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app import dev
from app.admin import setup_admin
from app.chatwoot import webhook as chatwoot_webhook
from app.config import get_settings
from app.db.session import engine
from app.logging_setup import configure_logging

configure_logging(get_settings().log_level)

app = FastAPI(title="Bot Consorcios - Estudio Diego Rufeil")
app.include_router(chatwoot_webhook.router)
app.include_router(dev.router)
# The panel gets its own sessionmaker (SQLAdmin reconfigures the one it receives).
setup_admin(app, sessionmaker(bind=engine), get_settings())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
