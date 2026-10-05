"""scripts/load_building_info.py against the test database. All data is invented."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BotEvent, Building, BuildingInfo, BuildingInfoCategory
from tests.bot import factories as f

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "load_building_info.py"
_spec = importlib.util.spec_from_file_location("load_building_info", _PATH)
loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loader)


def _data(building: str = "Torre Ficticia", **overrides: Any) -> dict[str, Any]:
    data = {
        "building": building,
        "source": "Reglamento inventado",
        "entries": [
            {"title": "Mascotas", "category": "reglamento", "content": "Se permiten gatos."},
            {"title": "Horario de silencio", "category": "horarios", "content": "De 22 a 8."},
        ],
    }
    data.update(overrides)
    return data


def _infos(session: Session, building: Building) -> dict[str, BuildingInfo]:
    rows = session.scalars(select(BuildingInfo).where(BuildingInfo.building_id == building.id))
    return {r.title: r for r in rows}


def _script_events(session: Session) -> list[dict[str, Any]]:
    events = session.scalars(
        select(BotEvent).where(BotEvent.event_type == "admin_action").order_by(BotEvent.id)
    )
    return [e.payload for e in events if e.payload.get("admin_user") == "script"]


def test_parse_validates_categories_and_titles() -> None:
    info = loader.parse_info(_data())
    assert [e.category for e in info.entries] == [
        BuildingInfoCategory.REGLAMENTO,
        BuildingInfoCategory.HORARIOS,
    ]

    bad = _data(entries=[{"title": "X", "category": "chismes", "content": "y"}])
    with pytest.raises(loader.LoadError, match="chismes"):
        loader.parse_info(bad)

    repeated = _data(
        entries=[
            {"title": "Mascotas", "category": "otros", "content": "a"},
            {"title": "mascotas ", "category": "otros", "content": "b"},
        ]
    )
    with pytest.raises(loader.LoadError, match="repetido"):
        loader.parse_info(repeated)

    with pytest.raises(loader.LoadError, match="content"):
        loader.parse_info(_data(entries=[{"title": "X", "category": "otros", "content": " "}]))
    with pytest.raises(loader.LoadError, match="entries"):
        loader.parse_info(_data(entries=[]))


def test_read_info_file(tmp_path: Path) -> None:
    path = tmp_path / "info.json"
    path.write_text(json.dumps(_data(), ensure_ascii=False), encoding="utf-8")
    assert loader.read_info_file(path).building == "Torre Ficticia"

    path.write_text("{no es json", encoding="utf-8")
    with pytest.raises(loader.LoadError, match="JSON"):
        loader.read_info_file(path)


def test_creates_then_is_idempotent(db_session: Session) -> None:
    building = f.building(db_session, "TORRE FICTICIA")

    result = loader.load_info(db_session, loader.parse_info(_data("torre ficticia")))

    assert result.building.id == building.id
    assert result.created == ["Mascotas", "Horario de silencio"]
    infos = _infos(db_session, building)
    assert infos["Horario de silencio"].category is BuildingInfoCategory.HORARIOS
    events = _script_events(db_session)
    assert [e["action"] for e in events] == ["building_info_created"] * 2
    assert {e["building_info_id"] for e in events} == {i.id for i in infos.values()}

    again = loader.load_info(db_session, loader.parse_info(_data("torre ficticia")))

    assert again.created == [] and again.updated == []
    assert again.unchanged == ["Mascotas", "Horario de silencio"]
    assert len(_infos(db_session, building)) == 2
    assert len(_script_events(db_session)) == 2


def test_updates_existing_title(db_session: Session) -> None:
    building = f.building(db_session, "TORRE FICTICIA")
    db_session.add(
        BuildingInfo(
            building=building,
            title="Mascotas",
            category=BuildingInfoCategory.OTROS,
            content="Texto viejo.",
        )
    )
    db_session.flush()

    result = loader.load_info(db_session, loader.parse_info(_data()))

    assert result.created == ["Horario de silencio"]
    assert result.updated == [("Mascotas", ["category", "content"])]
    infos = _infos(db_session, building)
    assert len(infos) == 2
    assert infos["Mascotas"].content == "Se permiten gatos."
    updated = [e for e in _script_events(db_session) if e["action"] == "building_info_updated"]
    assert updated == [
        {
            "admin_user": "script",
            "action": "building_info_updated",
            "building_info_id": infos["Mascotas"].id,
            "fields": ["category", "content"],
        }
    ]


def test_dry_run_writes_nothing(db_session: Session) -> None:
    building = f.building(db_session, "TORRE FICTICIA")
    db_session.add(
        BuildingInfo(
            building=building, title="Mascotas", category=BuildingInfoCategory.OTROS, content="x"
        )
    )
    db_session.flush()

    result = loader.load_info(db_session, loader.parse_info(_data()), dry_run=True)

    assert result.created == ["Horario de silencio"]
    assert result.updated == [("Mascotas", ["category", "content"])]
    infos = _infos(db_session, building)
    assert list(infos) == ["Mascotas"] and infos["Mascotas"].content == "x"
    assert _script_events(db_session) == []
    assert "no escribí nada" in loader.report(result, dry_run=True)


def test_ambiguous_building_loads_nothing(db_session: Session) -> None:
    f.building(db_session, "TORRE FICTICIA I")
    f.building(db_session, "TORRE FICTICIA II")

    with pytest.raises(loader.LoadError, match="varios edificios") as error:
        loader.load_info(db_session, loader.parse_info(_data("torre ficticia")))

    assert "TORRE FICTICIA I\n" in str(error.value) + "\n"
    assert "TORRE FICTICIA II" in str(error.value)
    assert db_session.scalars(select(BuildingInfo)).all() == []
    assert _script_events(db_session) == []


def test_unknown_building_loads_nothing(db_session: Session) -> None:
    f.building(db_session, "TORRE FICTICIA")

    with pytest.raises(loader.LoadError, match="ningún edificio"):
        loader.load_info(db_session, loader.parse_info(_data("Palacio Inexistente Zeta")))

    assert db_session.scalars(select(BuildingInfo)).all() == []
