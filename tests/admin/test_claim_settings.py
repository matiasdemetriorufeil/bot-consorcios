"""«Configuración de reclamos»: the form, its validations and that the change is used right away.
Invented data."""

from datetime import time
from typing import Any

import pytest
from sqlalchemy import select

from app.claims.claim_config import load_claim_config
from app.db.models import ClaimSettings
from tests.admin.conftest import ADMIN, Panel

TZ = "America/Argentina/Cordoba"

FORM: dict[str, Any] = {
    "provider_weekdays": ["0", "1", "2", "3", "4"],
    "provider_hours_start": "9:00",
    "provider_hours_end": "18:30",
    "holidays": "24/12/2026\n25/12/2026",
    "urgent_any_time": "y",
    "reminder_hours": "2",
    "reminder_urgent_minutes": "20",
    "alert_hours": "6",
    "alert_urgent_minutes": "45",
    "stale_days": "5",
    "save": "Save",
}


def _post(panel: Panel, data: dict[str, Any]) -> Any:
    return panel.client.post("/admin/claim-settings/edit/1", data=data, follow_redirects=False)


def test_the_menu_opens_the_form(logged_in: Panel) -> None:
    response = logged_in.client.get("/admin/claim-settings/list", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin/claim-settings/edit/1")


def test_the_form_shows_days_as_check_boxes_and_the_holidays_format(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/claim-settings/edit/1").text
    for day in ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"):
        assert day in page
    assert page.count('type="checkbox" name="provider_weekdays"') == 7
    assert "Una fecha por renglón, como 24/12/2026" in page
    # The defaults: Monday to Saturday checked, Sunday not.
    assert 'id="provider_weekdays-5" value="5" checked' in page
    assert 'id="provider_weekdays-6" value="6" checked' not in page


def test_saving_is_audited_and_used_right_away(logged_in: Panel) -> None:
    load_claim_config(logged_in.session, TZ)  # fills the cache
    assert _post(logged_in, FORM).status_code == 302

    logged_in.session.expire_all()
    row = logged_in.session.scalar(select(ClaimSettings))
    assert row is not None
    assert row.provider_weekdays == "0,1,2,3,4"
    assert (row.provider_hours_start, row.provider_hours_end) == ("09:00", "18:30")

    config = load_claim_config(logged_in.session, TZ)
    assert config.hours.weekdays == frozenset({0, 1, 2, 3, 4})
    assert (config.hours.start, config.hours.end) == (time(9), time(18, 30))
    assert len(config.hours.holidays) == 2
    assert config.stale.days == 5

    [event] = logged_in.admin_events("claim_settings_changed")
    assert event["admin_user"] == ADMIN
    assert "provider_weekdays" in event["fields"] and "holidays" in event["fields"]
    assert "24/12/2026" not in str(event)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"provider_weekdays": []}, "Elegí al menos un día"),
        ({"provider_hours_start": "8 de la mañana"}, "HH:MM"),
        ({"provider_hours_end": "07:00"}, "después del «desde»"),
        ({"holidays": "24/12/2026\n31 de diciembre"}, "31 de diciembre"),
        ({"reminder_hours": "0"}, "desde 1"),
        ({"alert_hours": "2"}, "después del recordatorio"),
        ({"alert_urgent_minutes": "20"}, "después de su recordatorio"),
    ],
)
def test_the_form_validates(logged_in: Panel, change: dict[str, Any], message: str) -> None:
    response = _post(logged_in, {**FORM, **change})
    assert response.status_code == 400
    assert message in response.text
    logged_in.session.expire_all()
    row = logged_in.session.scalar(select(ClaimSettings))
    assert row is not None and row.provider_weekdays == "0,1,2,3,4,5"
    assert logged_in.admin_events("claim_settings_changed") == []
