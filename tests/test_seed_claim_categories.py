"""scripts/seed_claim_categories.py against the test database. All data is invented."""

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import BotEvent, BuildingClaimCategory, ClaimCategory, ClaimScope, Provider
from tests.bot import factories as f

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "seed_claim_categories.py"
_spec = importlib.util.spec_from_file_location("seed_claim_categories", _PATH)
seeder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seeder)


def _count(session: Session, model: Any) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _categories(session: Session) -> list[ClaimCategory]:
    session.expire_all()
    return list(session.scalars(select(ClaimCategory).order_by(ClaimCategory.sort_order)))


def _script_events(session: Session) -> list[dict[str, Any]]:
    events = session.scalars(select(BotEvent).where(BotEvent.event_type == "admin_action"))
    return [e.payload for e in events if e.payload.get("admin_user") == "script"]


def test_the_seeds_fit_in_a_whatsapp_list_row() -> None:
    assert len(seeder.SEEDS) == 12
    assert len({s.name for s in seeder.SEEDS}) == 12
    for item in seeder.SEEDS:
        assert 1 <= len(item.list_title) <= 24, item.list_title
        assert len(item.list_description or "") <= 72, item.list_description


def test_creates_the_kinds_of_problem_in_order(db_session: Session) -> None:
    result = seeder.seed(db_session)

    categories = _categories(db_session)
    assert [c.name for c in categories] == [s.name for s in seeder.SEEDS]
    assert [c.sort_order for c in categories] == list(range(10, 130, 10))
    assert len(result.created) == 12 and result.unchanged == []
    by_name = {c.name: c for c in categories}
    lift = by_name["No funciona el o los ascensores"]
    assert lift.urgent and lift.follow_up_question == "¿Hay alguien encerrado?"
    gas = by_name["Siento olor a gas"]
    assert gas.urgent and gas.safety_text == seeder.GAS_SAFETY_TEXT
    assert gas.emergency_phone is None
    assert by_name["Se escuchan ruidos molestos"].scope == ClaimScope.UNIT
    assert by_name["No hay agua"].scope == ClaimScope.BUILDING
    assert len(_script_events(db_session)) == 12


def test_is_idempotent_and_keeps_what_the_panel_changed(db_session: Session) -> None:
    building = f.building(db_session, "012 TORRE INVENTADA")
    seeder.seed(db_session, building_code=building.consorplus_code)
    water = next(c for c in _categories(db_session) if c.name == "No hay agua")
    water.list_title = "Sin agua (editado)"
    db_session.commit()
    events = len(_script_events(db_session))

    result = seeder.seed(db_session, building_code=building.consorplus_code)

    assert result.created == [] and len(result.unchanged) == 12
    assert result.assigned == [] and len(result.already_assigned) == 12
    assert _count(db_session, ClaimCategory) == 12
    assert _count(db_session, BuildingClaimCategory) == 12
    assert water.list_title == "Sin agua (editado)"
    assert len(_script_events(db_session)) == events  # nothing new to log


def test_the_building_gets_every_kind_enabled_without_providers(db_session: Session) -> None:
    building = f.building(db_session, "012 TORRE INVENTADA")
    other = f.building(db_session, "013 OTRA INVENTADA")

    seeder.seed(db_session, building_code=building.consorplus_code)

    rows = list(db_session.scalars(select(BuildingClaimCategory)))
    assert len(rows) == 12
    assert {r.building_id for r in rows} == {building.id}
    assert all(r.enabled and r.provider_id is None for r in rows)
    orders = {r.category_id: r.sort_order for r in rows}
    assert orders == {c.id: c.sort_order for c in _categories(db_session)}
    assert _count(db_session, Provider) == 0
    assert not any(r.building_id == other.id for r in rows)
    [event] = [e for e in _script_events(db_session) if e["action"] == "building_claims_seeded"]
    assert event["building_id"] == building.id and len(event["category_ids"]) == 12


def test_the_report_shows_the_second_whatsapp_list(db_session: Session) -> None:
    building = f.building(db_session, "012 TORRE INVENTADA")
    result = seeder.seed(db_session, building_code=building.consorplus_code)

    text = seeder.report(db_session, result, dry_run=False)

    lines = text.splitlines()
    split = lines.index("  -- Estos aparecen en una segunda lista («Más opciones») --")
    after = [line for line in lines[split + 1 :] if "habilitado" in line]
    assert [line.split()[1] for line in after] == ["Ruidos", "Limpieza", "Otras"]
    assert "Lo atiende el estudio" in text


def test_dry_run_writes_nothing(db_session: Session) -> None:
    building = f.building(db_session, "012 TORRE INVENTADA")
    result = seeder.seed(db_session, building_code=building.consorplus_code, dry_run=True)
    assert len(result.created) == 12 and len(result.assigned) == 12
    assert _count(db_session, ClaimCategory) == 0
    assert _count(db_session, BuildingClaimCategory) == 0
    assert "no escribí nada" in seeder.report(db_session, result, dry_run=True)


def test_an_unknown_building_loads_nothing(db_session: Session) -> None:
    with pytest.raises(seeder.SeedError, match="código 4321"):
        seeder.seed(db_session, building_code=4321)
    assert _count(db_session, ClaimCategory) == 0
