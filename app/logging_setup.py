"""Logging of the api: the app's own loggers ("app.*") to stdout, so they show up in
`docker compose logs api` next to uvicorn's access lines.

Uvicorn only configures its own loggers; without this, app INFO logs are dropped (the root
logger has no handler and defaults to WARNING). The app logs never carry message texts,
phones, emails or codes (except the console email backend in development).
"""

import logging
import sys

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MARK = "_bot_consorcios_handler"


def configure_logging(level: str = "INFO") -> None:
    """Idempotent: uvicorn --reload re-imports the app."""
    app_logger = logging.getLogger("app")
    app_logger.setLevel(level.upper())
    if not any(getattr(h, _MARK, False) for h in app_logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(FORMAT))
        setattr(handler, _MARK, True)
        app_logger.addHandler(handler)
    # propagate stays on (pytest's caplog listens on the root logger). Under uvicorn the root
    # logger has no handler, so nothing is printed twice.
