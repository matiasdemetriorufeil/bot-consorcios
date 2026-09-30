"""Live debt refresh with a fake ConsorPlus, against the Postgres test database."""

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.consorplus.errors import ConsorPlusUnavailableError
from app.db.models import Building, DebtSnapshot, SyncKind, Unit
from app.sync.live import LiveRefresher
from app.sync.snapshots import save_snapshot
from tests.sync.fakes import FakeConsorPlus, debt

NOW = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)


@pytest.fixture
def unit(db_session: Session) -> Unit:
    unit = Unit(
        building=Building(consorplus_code=7, name="007 CONSORCIO FICTICIO"),
        consorplus_unit_value="7001",
        label="01° A",
    )
    db_session.add(unit)
    db_session.commit()
    return unit


@pytest.fixture
def source() -> FakeConsorPlus:
    return FakeConsorPlus(debts={("7", "7001"): debt("7", "7001", "1200", "300")})


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make_refresher(db_session: Session, source: FakeConsorPlus, clock: Clock):
    # Sessions on the test connection: everything is rolled back after the test.
    connection = db_session.connection()
    refreshers: list[LiveRefresher] = []

    def factory(cache_minutes: float = 10) -> LiveRefresher:
        factory_calls = []

        def client_factory() -> FakeConsorPlus:
            factory_calls.append(True)
            return source

        refresher = LiveRefresher(
            lambda: Session(bind=connection, join_transaction_mode="create_savepoint"),
            client_factory,
            now=clock,
            cache_minutes=cache_minutes,
        )
        refresher.factory_calls = factory_calls  # type: ignore[attr-defined]
        refreshers.append(refresher)
        return refresher

    yield factory
    if source.gate is not None:
        source.gate.set()
    for refresher in refreshers:
        refresher.shutdown(wait=True)


@pytest.fixture
def refresher(make_refresher) -> LiveRefresher:
    return make_refresher()


def stored(db_session: Session, unit: Unit) -> list[tuple[SyncKind, Decimal]]:
    db_session.expire_all()
    return [
        (s.source, s.total_amount)
        for s in db_session.scalars(
            select(DebtSnapshot).where(DebtSnapshot.unit_id == unit.id).order_by(DebtSnapshot.id)
        )
    ]


def test_refresh_fetches_and_stores_live_snapshot(db_session, unit, refresher) -> None:
    result = refresher.refresh_unit(unit.id)

    assert result.stale is False
    assert result.error is None
    assert result.fetched_at == NOW
    assert result.snapshot.source is SyncKind.LIVE
    assert result.snapshot.total_amount == Decimal(1500)
    assert [line.period for line in result.snapshot.lines] == ["01/2026", "02/2026"]
    assert stored(db_session, unit) == [(SyncKind.LIVE, Decimal(1500))]


def test_refresh_reuses_one_client(db_session, unit, refresher, source, clock) -> None:
    refresher.refresh_unit(unit.id)
    clock.advance(11)
    refresher.refresh_unit(unit.id)

    assert len(source.debt_calls) == 2
    assert refresher.factory_calls == [True]


def test_failure_returns_last_stored_debt_as_stale(db_session, unit, refresher, source) -> None:
    save_snapshot(db_session, unit.id, debt("7", "7001", "800"), SyncKind.NIGHTLY, YESTERDAY)
    db_session.commit()
    source.debts[("7", "7001")] = ConsorPlusUnavailableError("sin respuesta")

    result = refresher.refresh_unit(unit.id)

    assert result.stale is True
    assert result.error == "ConsorPlusUnavailableError"
    assert result.fetched_at == YESTERDAY
    assert result.snapshot.total_amount == Decimal(800)
    assert [line.period for line in result.snapshot.lines] == ["01/2026"]
    assert stored(db_session, unit) == [(SyncKind.NIGHTLY, Decimal(800))]


def test_failure_without_stored_debt(db_session, unit, refresher, source) -> None:
    source.debts[("7", "7001")] = ConsorPlusUnavailableError("sin respuesta")

    result = refresher.refresh_unit(unit.id)

    assert result.stale is True
    assert result.snapshot is None
    assert result.fetched_at is None


def test_timeout_returns_stale_and_stores_the_late_answer(
    db_session, unit, refresher, source
) -> None:
    save_snapshot(db_session, unit.id, debt("7", "7001", "800"), SyncKind.NIGHTLY, YESTERDAY)
    db_session.commit()
    source.gate = threading.Event()  # ConsorPlus does not answer until the gate opens

    result = refresher.refresh_unit(unit.id, timeout_seconds=0.05)

    assert result.stale is True
    assert result.error == "timeout"
    assert result.snapshot.total_amount == Decimal(800)

    source.gate.set()
    refresher.shutdown(wait=True)
    assert stored(db_session, unit) == [
        (SyncKind.NIGHTLY, Decimal(800)),
        (SyncKind.LIVE, Decimal(1500)),
    ]


def test_pending_fetch_of_the_same_unit_is_joined(db_session, unit, refresher, source) -> None:
    source.gate = threading.Event()

    first = refresher.refresh_unit(unit.id, timeout_seconds=0.05)
    second = refresher.refresh_unit(unit.id, timeout_seconds=0.05)

    assert first.stale and second.stale
    source.gate.set()
    refresher.shutdown(wait=True)
    assert source.debt_calls == [("7", "7001")]


def test_unknown_unit_raises(refresher) -> None:
    with pytest.raises(LookupError):
        refresher.refresh_unit(999_999)


def test_recent_live_snapshot_is_returned_without_querying(
    db_session, unit, refresher, source, clock
) -> None:
    first = refresher.refresh_unit(unit.id)
    clock.advance(9)
    source.debts[("7", "7001")] = ConsorPlusUnavailableError("no debería consultarse")

    result = refresher.refresh_unit(unit.id)

    assert result.cached is True
    assert result.stale is False
    assert result.snapshot.id == first.snapshot.id
    assert [line.period for line in result.snapshot.lines] == ["01/2026", "02/2026"]
    assert source.debt_calls == [("7", "7001")]
    assert stored(db_session, unit) == [(SyncKind.LIVE, Decimal(1500))]


def test_live_snapshot_older_than_the_cache_is_refreshed(
    db_session, unit, refresher, source, clock
) -> None:
    refresher.refresh_unit(unit.id)
    clock.advance(10.5)

    result = refresher.refresh_unit(unit.id)

    assert result.cached is False
    assert result.fetched_at == clock.now
    assert len(source.debt_calls) == 2


def test_recent_nightly_snapshot_does_not_count_as_cache(
    db_session, unit, refresher, source, clock
) -> None:
    save_snapshot(db_session, unit.id, debt("7", "7001", "800"), SyncKind.NIGHTLY, NOW)
    db_session.commit()

    result = refresher.refresh_unit(unit.id)

    assert result.cached is False
    assert result.snapshot.source is SyncKind.LIVE
    assert source.debt_calls == [("7", "7001")]


def test_cache_can_be_disabled(db_session, unit, make_refresher, source) -> None:
    refresher = make_refresher(cache_minutes=0)

    refresher.refresh_unit(unit.id)
    result = refresher.refresh_unit(unit.id)

    assert result.cached is False
    assert len(source.debt_calls) == 2
