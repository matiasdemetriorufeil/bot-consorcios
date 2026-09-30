"""Debt snapshot storage and retention, against the Postgres test database. Invented data."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Building, DebtLine, DebtSnapshot, SyncKind, Unit
from app.sync.snapshots import local_day_bounds, purge_snapshots, save_snapshot
from tests.sync.fakes import debt

TZ = "America/Argentina/Cordoba"
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def make_unit(session: Session, value: str = "7001") -> Unit:
    building = session.scalar(select(Building).where(Building.consorplus_code == 7))
    if building is None:
        building = Building(consorplus_code=7, name="007 CONSORCIO FICTICIO")
    unit = Unit(building=building, consorplus_unit_value=value, label=value)
    session.add(unit)
    session.flush()
    return unit


def snapshots(session: Session, unit: Unit) -> list[DebtSnapshot]:
    return list(
        session.scalars(
            select(DebtSnapshot)
            .where(DebtSnapshot.unit_id == unit.id)
            .order_by(DebtSnapshot.fetched_at)
        )
    )


def test_local_day_bounds_use_cordoba_time() -> None:
    # 02:00 UTC is still the previous day in Córdoba (UTC-3).
    start, end = local_day_bounds(datetime(2026, 3, 10, 2, 0, tzinfo=UTC), TZ)

    assert start == datetime(2026, 3, 9, 3, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 10, 3, 0, tzinfo=UTC)


def test_save_snapshot_stores_total_and_lines(db_session: Session) -> None:
    unit = make_unit(db_session)

    snapshot, replaced = save_snapshot(
        db_session, unit.id, debt("7", "7001", "100.50", "200"), SyncKind.NIGHTLY, NOW
    )

    assert replaced == 0
    assert snapshot.total_amount == Decimal("300.50")
    assert snapshot.is_up_to_date is False
    assert snapshot.fetched_at == NOW
    assert [(line.period, line.accumulated) for line in snapshot.lines] == [
        ("01/2026", Decimal("100.50")),
        ("02/2026", Decimal("300.50")),
    ]


def test_save_snapshot_keeps_empty_amounts_as_null(db_session: Session) -> None:
    unit = make_unit(db_session)
    base = debt("7", "7001", "100")
    line = replace(base.lines[0], concept_amount=None, accumulated=None)

    snapshot, _ = save_snapshot(
        db_session, unit.id, replace(base, lines=(line,)), SyncKind.NIGHTLY, NOW
    )

    assert (snapshot.lines[0].concept_amount, snapshot.lines[0].accumulated) == (None, None)


def test_replace_day_only_replaces_same_source_and_local_day(db_session: Session) -> None:
    unit = make_unit(db_session)
    local_morning = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)  # 03:00 in Córdoba
    yesterday_local = datetime(2026, 3, 10, 2, 0, tzinfo=UTC)  # 23:00 of the 9th in Córdoba
    save_snapshot(db_session, unit.id, debt("7", "7001", "1"), SyncKind.NIGHTLY, yesterday_local)
    save_snapshot(db_session, unit.id, debt("7", "7001", "2"), SyncKind.NIGHTLY, local_morning)
    save_snapshot(db_session, unit.id, debt("7", "7001", "3"), SyncKind.LIVE, local_morning)

    _, replaced = save_snapshot(
        db_session,
        unit.id,
        debt("7", "7001", "4"),
        SyncKind.NIGHTLY,
        local_morning + timedelta(hours=1),
        replace_day_of=TZ,
    )

    assert replaced == 1
    assert [(s.source, s.total_amount) for s in snapshots(db_session, unit)] == [
        (SyncKind.NIGHTLY, Decimal(1)),
        (SyncKind.LIVE, Decimal(3)),
        (SyncKind.NIGHTLY, Decimal(4)),
    ]
    # The replaced snapshot's lines went with it.
    assert db_session.scalar(select(func.count()).select_from(DebtLine)) == 3


def ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def test_purge_applies_retention_but_keeps_latest_of_each_unit(db_session: Session) -> None:
    busy, idle = make_unit(db_session, "7001"), make_unit(db_session, "7002")
    live_only = make_unit(db_session, "7003")

    for days, source in [
        (70, SyncKind.NIGHTLY),  # purged
        (59, SyncKind.NIGHTLY),  # kept
        (40, SyncKind.LIVE),  # purged
        (20, SyncKind.LIVE),  # kept
        (1, SyncKind.NIGHTLY),  # kept
    ]:
        save_snapshot(db_session, busy.id, debt("7", "7001", "10"), source, ago(days))
    # Only very old snapshots: the newest one stays, whatever its age.
    save_snapshot(db_session, idle.id, debt("7", "7002", "5"), SyncKind.NIGHTLY, ago(120))
    save_snapshot(db_session, idle.id, debt("7", "7002", "6"), SyncKind.NIGHTLY, ago(90))
    save_snapshot(db_session, live_only.id, debt("7", "7003", "7"), SyncKind.LIVE, ago(45))

    purged = purge_snapshots(db_session, NOW)
    db_session.expire_all()

    assert purged == 3
    assert [s.fetched_at for s in snapshots(db_session, busy)] == [ago(59), ago(20), ago(1)]
    assert [s.fetched_at for s in snapshots(db_session, idle)] == [ago(90)]
    assert [s.fetched_at for s in snapshots(db_session, live_only)] == [ago(45)]
    assert db_session.scalar(select(func.count()).select_from(DebtLine)) == 5
