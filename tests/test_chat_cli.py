"""Safety pieces of scripts/chat_cli.py (development CLI), against the test database."""

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.identity import can_view_unit_finance, identify_by_phone
from app.config import Settings
from app.db.models import DataSource, Phone
from tests.bot import factories as f

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "chat_cli.py"
_spec = importlib.util.spec_from_file_location("chat_cli", _PATH)
chat_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chat_cli)


@pytest.fixture
def unit(db_session: Session):
    building = f.building(db_session, "777 EDIFICIO FICTICIO")
    u = f.unit(db_session, building, "03-B")
    f.link(db_session, u, f.person(db_session, "Dueña Inventada"))
    db_session.commit()
    return u


def test_refuses_without_console_email(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, email_backend="smtp")
    monkeypatch.setattr(chat_cli, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["chat_cli.py", "--phone", "+5493515550977"])
    assert chat_cli.main() == 2


def test_resolve_unit_by_codes_and_by_text(db_session: Session, unit) -> None:
    code = str(unit.building.consorplus_code)
    assert chat_cli.resolve_unit(db_session, code, "03-B").id == unit.id
    assert chat_cli.resolve_unit(db_session, "Edificio Ficticio", "3 B").id == unit.id
    with pytest.raises(chat_cli.CliError):
        chat_cli.resolve_unit(db_session, "Inexistente", "9Z")


def test_test_owner_phone_is_a_verified_owner_and_is_removed(db_session: Session, unit) -> None:
    phone = chat_cli.create_test_owner_phone(db_session, unit)
    assert phone.e164.startswith("+5491100000")
    assert phone.source == DataSource.MANUAL
    assert identify_by_phone(db_session, phone.e164).known
    assert can_view_unit_finance(db_session, phone.e164, unit.id)

    assert chat_cli.remove_leftover_test_phones(db_session) == 1
    assert not can_view_unit_finance(db_session, phone.e164, unit.id)


def test_unit_without_owner(db_session: Session) -> None:
    lonely = f.unit(db_session, f.building(db_session, "778 OTRO FICTICIO"), "01-A")
    with pytest.raises(chat_cli.CliError):
        chat_cli.create_test_owner_phone(db_session, lonely)


def test_leftover_cleanup_only_touches_its_own_rows(db_session: Session) -> None:
    f.person(db_session, "Otra Persona", phone="+5491100000555")  # same range, not ours
    assert chat_cli.remove_leftover_test_phones(db_session) == 0


def test_phone_state_removes_a_phone_linked_during_the_test(db_session: Session, unit) -> None:
    owner_id = unit.people[0].person_id
    state = chat_cli.PhoneState.capture(db_session, "+5493515550977")
    db_session.add(Phone(person_id=owner_id, e164="+5493515550977", source=DataSource.BOT_VERIFIED))
    db_session.commit()
    assert "borré" in state.restore(db_session)
    assert db_session.scalar(select(Phone).where(Phone.e164 == "+5493515550977")) is None


def test_phone_state_restores_an_existing_phone(db_session: Session, unit) -> None:
    other = f.person(db_session, "Antes Inventado", phone="+5493515550966")
    state = chat_cli.PhoneState.capture(db_session, "+5493515550966")
    row = db_session.scalar(select(Phone).where(Phone.e164 == "+5493515550966"))
    row.person_id = unit.people[0].person_id
    row.source = DataSource.BOT_VERIFIED
    db_session.commit()

    assert "restauré" in state.restore(db_session)
    db_session.refresh(row)
    assert (row.person_id, row.source) == (other.id, DataSource.CONSORPLUS)
    assert state.restore(db_session) is None  # nothing left to undo
