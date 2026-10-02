"""scripts/set_pilot.py against the test database. All data is invented."""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tests.bot import factories as f

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "set_pilot.py"
_spec = importlib.util.spec_from_file_location("set_pilot", _PATH)
set_pilot_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(set_pilot_script)


def test_parse_codes() -> None:
    assert set_pilot_script.parse_codes("1, 2,31,2") == [1, 2, 31]
    with pytest.raises(set_pilot_script.PilotError):
        set_pilot_script.parse_codes("1,a")
    with pytest.raises(set_pilot_script.PilotError):
        set_pilot_script.parse_codes(" , ")


def test_mark_and_unmark(db_session: Session) -> None:
    first = f.building(db_session, "EDIFICIO UNO")
    second = f.building(db_session, "EDIFICIO DOS")
    codes = [first.consorplus_code, second.consorplus_code]

    changed = set_pilot_script.set_pilot(db_session, codes, pilot=True)
    assert [b.pilot for b in changed] == [True, True]
    assert set(set_pilot_script.pilot_buildings(db_session)) >= {first, second}

    set_pilot_script.set_pilot(db_session, [first.consorplus_code], pilot=False)
    assert not first.pilot and second.pilot


def test_unknown_code_changes_nothing(db_session: Session) -> None:
    building = f.building(db_session, "EDIFICIO TRES")

    with pytest.raises(set_pilot_script.PilotError, match="99999"):
        set_pilot_script.set_pilot(db_session, [building.consorplus_code, 99999], pilot=True)

    db_session.refresh(building)
    assert building.pilot is False
