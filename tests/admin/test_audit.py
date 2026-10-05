"""Every change made in the panel is audited in bot_events, without values. Invented data."""

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.bot_config import load_bot_config
from app.db.models import BotSettings, Building, BuildingInfo
from tests.admin.conftest import ADMIN, Panel, admin_settings
from tests.bot import factories as f

SETTINGS_FORM = {
    "welcome_message": "Bienvenida secreta inventada",
    "office_hours_start": "",
    "office_hours_end": "",
    "office_weekdays": "",
    "out_of_hours_text": "",
    "emergency_contact_text": "",
    "autogestion_url": "https://autogestion.example.com",
    "payment_code_how_to": "",
    "save": "Save",
}


def _post(panel: Panel, url: str, data: dict[str, Any]) -> Any:
    return panel.client.post(url, data=data, follow_redirects=False)


@pytest.fixture
def building(db_session: Session) -> Building:
    b = f.building(db_session, "088 EDIFICIO AUDITADO")
    db_session.commit()
    return b


def test_settings_change_logs_the_fields_without_values(logged_in: Panel) -> None:
    response = _post(logged_in, "/admin/bot-settings/edit/1", SETTINGS_FORM)
    assert response.status_code == 302

    [event] = logged_in.admin_events("bot_settings_changed")
    assert event["admin_user"] == ADMIN
    assert event["fields"] == ["autogestion_url", "welcome_message"]
    assert "Bienvenida secreta" not in str(event) and "autogestion.example" not in str(event)

    # Saved and seen by the bot right away (the cache was invalidated).
    cfg = load_bot_config(logged_in.session, admin_settings())
    assert cfg.welcome_message == "Bienvenida secreta inventada"
    assert cfg.autogestion_url == "https://autogestion.example.com"

    # Saving the same values again changes nothing: no new event.
    _post(logged_in, "/admin/bot-settings/edit/1", SETTINGS_FORM)
    assert len(logged_in.admin_events("bot_settings_changed")) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("office_hours_start", "9 de la mañana"),
        ("office_weekdays", "lunes a viernes"),
        ("autogestion_url", "javascript:alert(1)"),
    ],
)
def test_settings_form_validates(logged_in: Panel, field: str, value: str) -> None:
    response = _post(logged_in, "/admin/bot-settings/edit/1", {**SETTINGS_FORM, field: value})
    assert response.status_code == 400
    logged_in.session.expire_all()
    assert getattr(logged_in.session.get(BotSettings, 1), field) is None
    assert logged_in.admin_events() == []


def test_settings_page_never_shows_env_values(db_session: Session) -> None:
    from tests.admin.conftest import build_panel

    panel = build_panel(db_session, admin_settings(emergency_contact_text="SECRETO-DEL-ENV"))
    with panel.client:
        panel.login()
        for url in (
            "/admin/bot-settings/list",
            "/admin/bot-settings/edit/1",
            "/admin/bot-settings/details/1",
        ):
            page = panel.client.get(url).text
            assert "SECRETO-DEL-ENV" not in page
            assert "clave-de-sesion-inventada" not in page


def test_building_info_create_edit_delete(logged_in: Panel, building: Building) -> None:
    form = {
        "building": str(building.id),
        "category": "reglamento",
        "title": "Uso del SUM",
        "content": "Texto largo inventado del reglamento.",
        "save": "Save",
    }
    assert _post(logged_in, "/admin/building-info/create", form).status_code == 302
    info = logged_in.session.scalar(select(BuildingInfo).where(BuildingInfo.title == "Uso del SUM"))
    assert info is not None and info.building_id == building.id
    info_id = info.id

    edit = {**form, "content": "Texto corregido."}
    assert _post(logged_in, f"/admin/building-info/edit/{info_id}", edit).status_code == 302
    assert logged_in.client.delete(f"/admin/building-info/delete?pks={info_id}").status_code == 200

    ids = {"building_info_id": info_id, "building_id": building.id, "category": "reglamento"}
    events = logged_in.admin_events()
    assert [e["action"] for e in events] == [
        "building_info_created",
        "building_info_updated",
        "building_info_deleted",
    ]
    for event in events:
        assert event["admin_user"] == ADMIN
        assert {k: event[k] for k in ids} == ids
        assert "Texto" not in str(event)
    assert events[1]["fields"] == ["content"]
    logged_in.session.expire_all()
    assert logged_in.session.get(BuildingInfo, info_id) is None


def test_building_edit(logged_in: Panel, building: Building) -> None:
    form = {"name": building.name, "address": "", "active": "y", "pilot": "y", "save": "Save"}
    assert _post(logged_in, f"/admin/building/edit/{building.id}", form).status_code == 302

    logged_in.session.expire_all()
    assert logged_in.session.get(Building, building.id).pilot
    [event] = logged_in.admin_events("building_updated")
    assert event == {
        "admin_user": ADMIN,
        "action": "building_updated",
        "building_id": building.id,
        "fields": ["pilot"],
        "phone_e164": None,
    }


def test_buildings_cannot_be_created_or_deleted(logged_in: Panel, building: Building) -> None:
    assert logged_in.client.get("/admin/building/create").status_code == 403
    logged_in.client.delete(f"/admin/building/delete?pks={building.id}")
    logged_in.session.expire_all()
    assert logged_in.session.get(Building, building.id) is not None
