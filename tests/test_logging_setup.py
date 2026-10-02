import logging

from app import logging_setup
from app.logging_setup import configure_logging


def test_app_logs_go_to_stdout_once(capsys) -> None:
    app_logger = logging.getLogger("app")
    # Importing app.main elsewhere already added the handler, bound to the real stdout.
    previous = [h for h in app_logger.handlers if getattr(h, logging_setup._MARK, False)]
    for handler in previous:
        app_logger.removeHandler(handler)
    try:
        configure_logging("INFO")
        configure_logging("INFO")  # uvicorn --reload imports the app again

        logging.getLogger("app.test").info("hola desde la app")
        logging.getLogger("app.test").debug("no se ve")

        out = capsys.readouterr().out
        assert out.count("hola desde la app") == 1
        assert "INFO app.test: hola desde la app" in out
        assert "no se ve" not in out
    finally:
        for handler in [h for h in app_logger.handlers if getattr(h, logging_setup._MARK, False)]:
            app_logger.removeHandler(handler)
        for handler in previous:
            app_logger.addHandler(handler)
