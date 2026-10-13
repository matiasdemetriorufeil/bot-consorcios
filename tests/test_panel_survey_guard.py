"""The demo's safety rules (scripts/panel_survey/guard.py), without a browser nor a database: it
only touches a database named *_panel_demo and never uses the ConsorPlus credentials."""

import importlib.util
from pathlib import Path

import pytest

from app.config import Settings

GUARD = Path(__file__).resolve().parents[1] / "scripts" / "panel_survey" / "guard.py"
_spec = importlib.util.spec_from_file_location("panel_survey_guard", GUARD)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.mark.parametrize("name", ["bot_consorcios_panel_demo", "otra_base_panel_demo"])
def test_a_demo_database_is_accepted(name: str) -> None:
    assert guard.check_demo_database(name) == name


@pytest.mark.parametrize(
    "name", ["bot_consorcios", "bot_consorcios_test", "panel_demo_x", "_panel_demo", "", None]
)
def test_any_other_database_is_refused(name: str | None) -> None:
    with pytest.raises(guard.NotADemoDatabase):
        guard.check_demo_database(name)


def test_the_consorplus_credentials_are_noticed_by_name_only() -> None:
    environ = {"CONSORPLUS_USER": "usuario-inventado", "CONSORPLUS_PASSWORD": " ", "OTRA": "x"}
    assert guard.consorplus_in(environ) == ["CONSORPLUS_USER"]
    assert guard.consorplus_in({}) == []


def test_the_demo_never_gets_the_consorplus_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONSORPLUS_USER", "usuario-inventado")
    monkeypatch.setenv("CONSORPLUS_PASSWORD", "clave-inventada")
    settings = Settings(_env_file=None, **guard.NO_CONSORPLUS)
    assert settings.consorplus_user == ""
    assert settings.consorplus_password is None
