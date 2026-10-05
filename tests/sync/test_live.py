"""Live debt refresh with a fake ConsorPlus, against the Postgres test database."""

import threading
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.consorplus.errors import ConsorPlusUnavailableError
from app.db.models import Building, DebtSnapshot, SyncKind, Unit
from app.sync import live
from app.sync.live import DEFAULT_TIMEOUT_SECONDS, LOGIN_TIMEOUT_SECONDS, LiveRefresher
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
    for gate in (source.gate, source.login_gate):
        if gate is not None:
            gate.set()
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


class InlineExecutor:
    """Runs the task on submit: the future is already done when it is returned."""

    def submit(self, fn, *args) -> Future:
        future: Future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, wait: bool = True) -> None:
        pass


@pytest.mark.timeout(5)
@pytest.mark.parametrize("fails", [False, True], ids=["ok", "error"])
def test_fetch_that_ends_before_submit_returns_does_not_deadlock(
    db_session, unit, refresher, source, fails
) -> None:
    # The worker may finish before _submit registers its done callback (ConsorPlus failing
    # at once). The callback then runs in the caller thread and takes the lock.
    refresher._executor = InlineExecutor()
    if fails:
        source.debts[("7", "7001")] = ConsorPlusUnavailableError("sin respuesta")

    result = refresher.refresh_unit(unit.id)

    assert result.stale is fails
    assert refresher._pending == {}


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


# --- Session and time budget ------------------------------------------------------------


def test_time_budget_is_longer_only_when_a_login_is_needed(unit, refresher, source) -> None:
    assert refresher.time_budget() == LOGIN_TIMEOUT_SECONDS  # no client yet

    refresher.refresh_unit(unit.id)  # logs in and stays logged in
    assert source.logins == 1
    assert refresher.time_budget() == DEFAULT_TIMEOUT_SECONDS

    source.logged_in = False  # session expired (bounced or idle)
    assert refresher.time_budget() == LOGIN_TIMEOUT_SECONDS


def test_session_is_reused_between_queries(unit, make_refresher, source) -> None:
    refresher = make_refresher(cache_minutes=0)

    refresher.refresh_unit(unit.id)
    refresher.refresh_unit(unit.id)

    assert source.logins == 1 and len(source.debt_calls) == 2


def test_slow_login_fits_in_the_login_budget(monkeypatch, unit, make_refresher, source) -> None:
    monkeypatch.setattr(live, "DEFAULT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(live, "LOGIN_TIMEOUT_SECONDS", 5.0)
    refresher = make_refresher(cache_minutes=0)
    source.login_gate = threading.Event()
    threading.Timer(0.3, source.login_gate.set).start()  # the login takes 0.3 s

    result = refresher.refresh_unit(unit.id)

    assert result.stale is False and result.snapshot.total_amount == Decimal(1500)


def test_slow_answer_with_a_ready_session_uses_the_short_budget(
    monkeypatch, unit, make_refresher, source
) -> None:
    monkeypatch.setattr(live, "DEFAULT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(live, "LOGIN_TIMEOUT_SECONDS", 5.0)
    refresher = make_refresher(cache_minutes=0)
    refresher.refresh_unit(unit.id)  # session ready
    source.gate = threading.Event()  # ConsorPlus hangs

    result = refresher.refresh_unit(unit.id)

    assert result.stale is True and result.error == "timeout"


def test_warm_up_logs_in_once_in_the_background(unit, refresher, source) -> None:
    source.login_gate = threading.Event()

    assert refresher.warm_up() is True
    assert refresher.warm_up() is False  # already logging in
    assert refresher.time_budget() == LOGIN_TIMEOUT_SECONDS  # login still running
    source.login_gate.set()
    refresher._warming.result(timeout=5)

    assert source.logins == 1
    assert refresher.warm_up() is False  # session ready: nothing to do
    assert refresher.time_budget() == DEFAULT_TIMEOUT_SECONDS
    refresher.refresh_unit(unit.id)
    assert source.logins == 1


def test_warm_up_failure_is_logged_and_does_not_break_queries(
    caplog, unit, refresher, source
) -> None:
    source.login_error = ConsorPlusUnavailableError("caído")

    assert refresher.warm_up() is True
    refresher._warming.result(timeout=5)
    assert "warm-up failed: ConsorPlusUnavailableError" in caplog.text

    source.login_error = None
    assert refresher.refresh_unit(unit.id).stale is False
