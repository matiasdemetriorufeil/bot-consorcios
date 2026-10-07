"""Every page of the panel renders once logged in. Invented data."""

import pytest
from sqlalchemy.orm import Session

from app.db.models import BuildingInfo, BuildingInfoCategory, SyncJob, SyncKind, SyncRun
from tests.admin.conftest import Panel
from tests.bot import factories as f


@pytest.fixture
def data(db_session: Session) -> dict[str, int]:
    building = f.building(db_session, "099 EDIFICIO PAGINAS")
    info = BuildingInfo(
        building_id=building.id,
        title="Horario de pileta",
        content="De 9 a 21.",
        category=BuildingInfoCategory.HORARIOS,
    )
    run = SyncRun(kind=SyncKind.NIGHTLY, job=SyncJob.ROSTER, stats={"units": 3})
    db_session.add_all([info, run])
    db_session.commit()
    return {"building": building.id, "info": info.id, "run": run.id}


def test_every_page_renders(logged_in: Panel, data: dict[str, int]) -> None:
    pages = [
        "/admin/",
        "/admin/building/list",
        f"/admin/building/details/{data['building']}",
        f"/admin/building/edit/{data['building']}",
        "/admin/building-info/list",
        "/admin/building-info/create",
        f"/admin/building-info/details/{data['info']}",
        f"/admin/building-info/edit/{data['info']}",
        "/admin/phones",
        "/admin/verifications",
        "/admin/sync-run/list",
        f"/admin/sync-run/details/{data['run']}",
        "/admin/bot-settings/list",
        "/admin/bot-settings/details/1",
        "/admin/bot-settings/edit/1",
        "/admin/metrics",
    ]
    for url in pages:
        response = logged_in.client.get(url)
        assert response.status_code == 200, url
    assert "Horario de pileta" in logged_in.client.get("/admin/building-info/list").text


def test_sync_runs_are_read_only(logged_in: Panel, data: dict[str, int]) -> None:
    assert logged_in.client.get("/admin/sync-run/create").status_code == 403
    assert logged_in.client.get(f"/admin/sync-run/edit/{data['run']}").status_code == 403
    logged_in.client.delete(f"/admin/sync-run/delete?pks={data['run']}")
    logged_in.session.expire_all()
    assert logged_in.session.get(SyncRun, data["run"]) is not None
