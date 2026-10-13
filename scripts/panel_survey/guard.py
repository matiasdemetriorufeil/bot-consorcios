"""The demo's safety rules (scripts/panel_survey/demo_server.py), alone so that a test can check
them without a browser nor a database (tests/test_panel_survey_guard.py):

- it only creates, fills or drops a database whose name ends in _panel_demo;
- it never connects to ConsorPlus: its settings force the credentials empty, whatever the
  environment says (and it warns when the environment has them).
"""

from collections.abc import Mapping

DEMO_SUFFIX = "_panel_demo"
CONSORPLUS_VARIABLES = ("CONSORPLUS_USER", "CONSORPLUS_PASSWORD")
# What the demo's Settings always get, over the environment and .env.
NO_CONSORPLUS: dict[str, object] = {"consorplus_user": "", "consorplus_password": None}


class NotADemoDatabase(SystemExit):
    """Raised (and the script ends) before touching a database that is not the demo's."""


def check_demo_database(name: str | None) -> str:
    """The demo database's name, or NotADemoDatabase."""
    if not name or not name.endswith(DEMO_SUFFIX) or name == DEMO_SUFFIX:
        raise NotADemoDatabase(
            f"La base «{name}» no es de demo (tiene que terminar en {DEMO_SUFFIX}): no se toca."
        )
    return name


def consorplus_in(environ: Mapping[str, str]) -> list[str]:
    """The ConsorPlus credentials present (not empty) in an environment: only their names."""
    return [name for name in CONSORPLUS_VARIABLES if environ.get(name, "").strip()]
