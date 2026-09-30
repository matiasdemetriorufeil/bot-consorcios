"""Daily canary, with fake ConsorPlus servers (no network). All data here is invented."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.consorplus.client import ConsorPlusClient
from app.consorplus.errors import LoginError, NotFoundError
from app.db.models import SyncJob, SyncRun, SyncStatus
from app.sync.canary import check_canary, run_canary
from tests.consorplus.conftest import BASE_URL, PASSWORD, USERNAME
from tests.consorplus.conftest import FakeConsorPlus as FakeServer
from tests.sync.fakes import FakeConsorPlus, debt


def http_client(server: FakeServer) -> ConsorPlusClient:
    return ConsorPlusClient(BASE_URL, USERNAME, PASSWORD, session=server, sleep=lambda _: None)


def test_canary_ok_records_sync_run(db_session: Session) -> None:
    source = FakeConsorPlus(debts={("1", "9001"): debt("1", "9001", "100", "200")})

    result = run_canary(db_session, lambda: source, "1", "9001")

    assert result.status is SyncStatus.OK
    run = db_session.scalars(select(SyncRun).where(SyncRun.job == SyncJob.CANARY)).one()
    assert run.status is SyncStatus.OK
    assert (run.units_ok, run.units_failed) == (1, 0)
    assert run.stats == {"lines": 2}
    assert run.error_summary is None
    assert run.finished_at is not None


def test_canary_through_the_client_reads_the_real_page_flow(db_session: Session) -> None:
    result = run_canary(db_session, lambda: http_client(FakeServer()), "1", "9001")

    assert result.status is SyncStatus.OK
    assert result.lines == 3


def test_canary_reports_unexpected_page_structure_clearly(db_session: Session, caplog) -> None:
    server = FakeServer(debt_panel="panel_debt_renamed_column.html")

    result = run_canary(db_session, lambda: http_client(server), "1", "9001")

    assert result.status is SyncStatus.FAILED
    assert result.message.startswith("ESTRUCTURA INESPERADA")
    assert "Saldo Adeudado" in result.message
    run = db_session.scalars(select(SyncRun).where(SyncRun.job == SyncJob.CANARY)).one()
    assert run.status is SyncStatus.FAILED
    assert run.error_summary == result.message
    assert any(r.levelname == "ERROR" and "CANARIO FALLÓ" in r.getMessage() for r in caplog.records)


def test_canary_without_debt_is_partial() -> None:
    result = check_canary(lambda: FakeConsorPlus(), "1", "9001")

    assert result.status is SyncStatus.PARTIAL
    assert "no tiene deuda" in result.message


def test_canary_unit_gone() -> None:
    source = FakeConsorPlus(debts={("1", "9001"): NotFoundError("La unidad '9001' no existe")})

    result = check_canary(lambda: source, "1", "9001")

    assert result.status is SyncStatus.FAILED
    assert "elegí otra" in result.message


def test_canary_not_configured() -> None:
    result = check_canary(lambda: FakeConsorPlus(), "", "")

    assert result.status is SyncStatus.FAILED
    assert "CANARY_BUILDING" in result.message


def test_canary_without_credentials() -> None:
    def no_credentials():
        raise LoginError("Faltan el usuario o la clave de ConsorPlus")

    result = check_canary(no_credentials, "1", "9001")

    assert result.status is SyncStatus.FAILED
    assert "iniciar sesión" in result.message
