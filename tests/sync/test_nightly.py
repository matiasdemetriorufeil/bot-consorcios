"""Nightly sync (roster + debts) with a fake ConsorPlus, against the Postgres test database."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.consorplus.errors import ConsorPlusUnavailableError, LoginError, ParseError
from app.db.models import (
    Building,
    DebtSnapshot,
    SyncJob,
    SyncKind,
    SyncRun,
    SyncStatus,
    Unit,
)
from app.sync import nightly
from app.sync.nightly import (
    MAX_CONSECUTIVE_OUTAGES,
    AlreadyRunningError,
    advisory_lock,
    run_nightly,
    sync_debts,
)
from app.sync.snapshots import save_snapshot
from tests.sync.fakes import FakeConsorPlus, debt, roster_row

TZ = "America/Argentina/Cordoba"
NOW = datetime(2026, 3, 10, 6, 30, tzinfo=UTC)  # 03:30 in Córdoba


def at(moment: datetime):
    return lambda: moment


def snapshots(session: Session, unit_value: str | None = None) -> list[DebtSnapshot]:
    stmt = select(DebtSnapshot).join(Unit).order_by(Unit.consorplus_unit_value, DebtSnapshot.id)
    if unit_value is not None:
        stmt = stmt.where(Unit.consorplus_unit_value == unit_value)
    return list(session.scalars(stmt))


def runs(session: Session, job: SyncJob) -> list[SyncRun]:
    return list(session.scalars(select(SyncRun).where(SyncRun.job == job).order_by(SyncRun.id)))


def three_units() -> FakeConsorPlus:
    return FakeConsorPlus(
        rosters={"7": [roster_row("7001"), roster_row("7002"), roster_row("7003")]},
        debts={
            ("7", "7001"): debt("7", "7001", "1000", "500.50"),
            ("7", "7002"): ConsorPlusUnavailableError("ConsorPlus no respondió tras 4 intentos"),
            # 7003: no debt
        },
    )


def test_nightly_syncs_roster_then_stores_debts(db_session: Session) -> None:
    source = three_units()

    report = run_nightly(db_session, source, timezone=TZ, now=at(NOW))

    assert report.roster.status is SyncStatus.OK
    assert db_session.scalar(select(func.count()).select_from(Unit)) == 3
    assert source.debt_calls == [("7", "7001"), ("7", "7002"), ("7", "7003")]

    stored = snapshots(db_session)
    assert [(s.unit.consorplus_unit_value, s.total_amount) for s in stored] == [
        ("7001", Decimal("1500.50")),
        ("7003", Decimal(0)),
    ]
    assert all(s.source is SyncKind.NIGHTLY and s.fetched_at == NOW for s in stored)
    assert [line.balance_due for line in stored[0].lines] == [Decimal(1000), Decimal("500.50")]
    assert stored[1].is_up_to_date and stored[1].lines == []

    debt_report = report.debt
    assert (debt_report.units, debt_report.units_ok, debt_report.units_failed) == (3, 2, 1)
    assert debt_report.units_with_debt == 1
    assert debt_report.debt_lines == 2
    assert debt_report.errors_by_type == {"ConsorPlusUnavailableError": 1}


def test_nightly_records_debt_sync_run_without_personal_data(db_session: Session) -> None:
    run_nightly(db_session, three_units(), timezone=TZ, now=at(NOW))

    [roster_run] = runs(db_session, SyncJob.ROSTER)
    [run] = runs(db_session, SyncJob.DEBT)
    assert roster_run.id < run.id  # roster first
    assert run.kind is SyncKind.NIGHTLY
    assert run.status is SyncStatus.PARTIAL
    assert (run.units_ok, run.units_failed) == (2, 1)
    assert run.finished_at is not None
    assert run.stats["units_with_debt"] == 1
    assert run.stats["errors_by_type"] == {"ConsorPlusUnavailableError": 1}
    unit_id = db_session.scalar(select(Unit.id).where(Unit.consorplus_unit_value == "7002"))
    assert run.error_summary == (
        f"unidad {unit_id} (edificio 7): ConsorPlusUnavailableError: "
        "ConsorPlus no respondió tras 4 intentos"
    )
    assert "PEREZ" not in run.error_summary
    assert "3515550101" not in str(run.stats)


def test_nightly_is_idempotent_within_the_same_local_day(db_session: Session) -> None:
    source = FakeConsorPlus(
        rosters={"7": [roster_row("7001")]}, debts={("7", "7001"): debt("7", "7001", "100")}
    )
    run_nightly(db_session, source, timezone=TZ, now=at(NOW))
    unit_id = db_session.scalar(select(Unit.id))
    # A live snapshot of the same day and yesterday's nightly must survive.
    save_snapshot(db_session, unit_id, debt("7", "7001", "90"), SyncKind.LIVE, NOW)
    save_snapshot(
        db_session, unit_id, debt("7", "7001", "80"), SyncKind.NIGHTLY, NOW - timedelta(days=1)
    )
    source.debts[("7", "7001")] = debt("7", "7001", "150")

    report = run_nightly(db_session, source, timezone=TZ, now=at(NOW + timedelta(hours=2)))

    assert report.debt.snapshots_replaced == 1
    assert sorted((s.source, s.total_amount) for s in snapshots(db_session)) == [
        (SyncKind.LIVE, Decimal(90)),
        (SyncKind.NIGHTLY, Decimal(80)),
        (SyncKind.NIGHTLY, Decimal(150)),
    ]


def test_failed_unit_keeps_its_previous_snapshot(db_session: Session) -> None:
    source = FakeConsorPlus(
        rosters={"7": [roster_row("7001")]}, debts={("7", "7001"): debt("7", "7001", "100")}
    )
    run_nightly(db_session, source, timezone=TZ, now=at(NOW))
    source.debts[("7", "7001")] = ParseError("Faltan columnas en la tabla de deuda")

    report = run_nightly(db_session, source, timezone=TZ, now=at(NOW + timedelta(hours=1)))

    assert report.debt.status is SyncStatus.FAILED
    assert [s.total_amount for s in snapshots(db_session)] == [Decimal(100)]


def test_only_active_units_of_active_buildings_and_requested_buildings(
    db_session: Session,
) -> None:
    source = FakeConsorPlus(
        rosters={
            "7": [roster_row("7001"), roster_row("7002")],
            "8": [roster_row("8001", building="008")],
            "9": [roster_row("9001", building="009")],
        }
    )
    run_nightly(db_session, source, timezone=TZ, now=at(NOW))
    db_session.scalars(
        select(Unit).where(Unit.consorplus_unit_value == "7002")
    ).one().active = False
    db_session.scalars(select(Building).where(Building.consorplus_code == 8)).one().active = False
    db_session.commit()
    source.debt_calls.clear()

    sync_debts(db_session, source, timezone=TZ, now=at(NOW))
    assert source.debt_calls == [("7", "7001"), ("9", "9001")]

    source.debt_calls.clear()
    report = sync_debts(db_session, source, ["9"], timezone=TZ, now=at(NOW))
    assert source.debt_calls == [("9", "9001")]
    assert report.units == 1


def test_nightly_stops_when_consorplus_stays_down(db_session: Session) -> None:
    units = [f"70{i:02d}" for i in range(MAX_CONSECUTIVE_OUTAGES + 3)]
    source = FakeConsorPlus(rosters={"7": [roster_row(u) for u in units]})
    run_nightly(db_session, source, timezone=TZ, now=at(NOW))
    source.debt_calls.clear()
    down = ConsorPlusUnavailableError("sin respuesta")
    source.debts = {("7", u): down for u in units}
    source.debts[("7", units[0])] = debt("7", units[0], "1")  # the first one works

    report = sync_debts(db_session, source, timezone=TZ, now=at(NOW))

    assert len(source.debt_calls) == 1 + MAX_CONSECUTIVE_OUTAGES
    assert (report.units_ok, report.units_failed, report.units_skipped) == (
        1,
        MAX_CONSECUTIVE_OUTAGES,
        2,
    )
    assert report.status is SyncStatus.PARTIAL
    run = runs(db_session, SyncJob.DEBT)[-1]
    assert run.units_failed == MAX_CONSECUTIVE_OUTAGES + 2
    assert "2 unidades sin consultar" in run.error_summary


def test_nightly_stops_at_the_first_login_error(db_session: Session) -> None:
    source = FakeConsorPlus(rosters={"7": [roster_row("7001"), roster_row("7002")]})
    run_nightly(db_session, source, timezone=TZ, now=at(NOW))
    source.debt_calls.clear()
    source.debts = {("7", "7001"): LoginError("ConsorPlus rechazó el login")}

    report = sync_debts(db_session, source, timezone=TZ, now=at(NOW))

    assert source.debt_calls == [("7", "7001")]
    assert (report.units_failed, report.units_skipped) == (1, 1)
    assert report.status is SyncStatus.FAILED


def test_nightly_purges_old_snapshots(db_session: Session) -> None:
    source = FakeConsorPlus(rosters={"7": [roster_row("7001")]})
    run_nightly(db_session, source, timezone=TZ, now=at(NOW - timedelta(days=61)))

    report = run_nightly(db_session, source, timezone=TZ, now=at(NOW))

    assert report.debt.snapshots_purged == 1
    assert [s.fetched_at for s in snapshots(db_session)] == [NOW]


def test_roster_failure_does_not_stop_the_debt_sync(db_session: Session) -> None:
    source = FakeConsorPlus(rosters={"7": [roster_row("7001")]})
    run_nightly(db_session, source, timezone=TZ, now=at(NOW))
    source.rosters["7"] = ConsorPlusUnavailableError("sin respuesta")

    report = run_nightly(db_session, source, timezone=TZ, now=at(NOW + timedelta(days=1)))

    assert report.roster.status is SyncStatus.FAILED
    assert report.debt.status is SyncStatus.OK
    assert len(snapshots(db_session)) == 2


def test_advisory_lock_allows_a_single_nightly_sync(db_engine: Engine) -> None:
    with advisory_lock(db_engine), pytest.raises(AlreadyRunningError), advisory_lock(db_engine):
        pass
    with advisory_lock(db_engine):  # released
        pass


def test_command_line_parses_building_codes(monkeypatch, capsys) -> None:
    received = []

    def fake_job(codes):
        received.append(codes)
        raise AlreadyRunningError("Ya hay una sincronización nocturna en curso")

    monkeypatch.setattr(nightly, "run_nightly_job", fake_job)

    assert nightly.main(["--buildings", "1, 2"]) == 2
    assert received == [["1", "2"]]
    assert "en curso" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        nightly.main(["--buildings", "1,x"])
