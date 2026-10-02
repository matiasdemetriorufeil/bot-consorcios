"""Live debt refresh of ONE unit, for the bot. Read-only on ConsorPlus.

ConsorPlus calls run on a single worker thread with its own long-lived client (one
ConsorPlus session per process, reused; the client is not thread-safe). The caller waits at
most DEFAULT_TIMEOUT_SECONDS when the session is ready, or LOGIN_TIMEOUT_SECONDS when the
fetch has to log in first (measured on 2026-10-02: ~1 s with the session, ~5.5 s with login,
and the login alone may take ~5 s). If the answer does not arrive in time, the caller gets
the last stored debt flagged as stale, and the fetch keeps going in the background and is
stored when it ends.

warm_up() logs in on the worker thread ahead of time (the Chatwoot bot calls it when an
identified owner writes), so that the login happens while the LLM is thinking.
"""

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.consorplus import ConsorPlusClient
from app.db.models import Building, DebtSnapshot, SyncKind, Unit
from app.db.queries import get_latest_debt
from app.db.session import SessionLocal
from app.sync.nightly import DebtSource
from app.sync.snapshots import save_snapshot

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 6.0
# When the fetch has to log in first. Keeps the person's wait around 10 s at most.
LOGIN_TIMEOUT_SECONDS = 10.0


class LiveDebtSource(DebtSource, Protocol):
    """What the live refresh needs from ConsorPlusClient (a fake in tests)."""

    @property
    def needs_login(self) -> bool: ...

    def ensure_session(self) -> None: ...


@dataclass(frozen=True)
class DebtResult:
    """The debt to show for a unit.

    `snapshot` is None only when nothing was ever stored for the unit. With `stale`, the
    live query failed or timed out and `snapshot` is the last stored one (see fetched_at).
    """

    snapshot: DebtSnapshot | None
    stale: bool
    error: str | None = None  # why it is stale (error type or "timeout"); no personal data
    cached: bool = False  # a recent live snapshot was reused, ConsorPlus was not queried

    @property
    def fetched_at(self) -> datetime | None:
        return self.snapshot.fetched_at if self.snapshot else None


def _live_client() -> LiveDebtSource:
    # Short HTTP timeouts and a single retry: the bot is waiting.
    return ConsorPlusClient.from_settings(get_settings(), timeout=(3.0, 6.0), max_retries=1)


class LiveRefresher:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        client_factory: Callable[[], LiveDebtSource] = _live_client,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        cache_minutes: float = 10,
    ) -> None:
        self._session_factory = session_factory
        self._client_factory = client_factory
        self._now = now
        self._cache = timedelta(minutes=cache_minutes)
        # Only used from the worker thread (the caller thread only reads needs_login).
        self._client: LiveDebtSource | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="consorplus-live")
        self._lock = threading.Lock()
        self._pending: dict[int, Future[int]] = {}
        self._warming: Future[None] | None = None

    def refresh_unit(self, unit_id: int, timeout_seconds: float | None = None) -> DebtResult:
        """Fetch the unit's debt from ConsorPlus and store it (source="live").

        A live snapshot younger than `cache_minutes` is returned as is, without querying
        ConsorPlus. Waits `timeout_seconds` (default: time_budget()). Raises LookupError if
        the unit does not exist.
        """
        with self._session_factory() as session:
            row = session.execute(
                select(Building.consorplus_code, Unit.consorplus_unit_value)
                .join(Unit, Unit.building_id == Building.id)
                .where(Unit.id == unit_id)
            ).first()
            recent = self._recent_live(session, unit_id) if row is not None else None
        if recent is not None:
            return DebtResult(recent, stale=False, cached=True)
        if row is None:
            raise LookupError(f"No existe la unidad {unit_id}")
        building_code, unit_value = str(row[0]), row[1]

        budget = timeout_seconds if timeout_seconds is not None else self.time_budget()
        future = self._submit(unit_id, building_code, unit_value)
        try:
            snapshot_id = future.result(timeout=budget)
        except FutureTimeoutError:
            error = "timeout"
        except Exception as exc:
            error = type(exc).__name__
        else:
            return DebtResult(self._load(snapshot_id), stale=False)

        logger.info("Live debt of unit %s is stale: %s (waited %.0f s)", unit_id, error, budget)
        with self._session_factory() as session:
            return DebtResult(get_latest_debt(session, unit_id), stale=True, error=error)

    def time_budget(self) -> float:
        """How long refresh_unit waits: longer when the fetch has to log in first."""
        return LOGIN_TIMEOUT_SECONDS if self._login_ahead() else DEFAULT_TIMEOUT_SECONDS

    def warm_up(self) -> bool:
        """Log in to ConsorPlus in the background if the session is not ready. Never blocks
        nor raises. True if a login was queued."""
        with self._lock:
            if self._warming is not None and not self._warming.done():
                return False
            if self._client is not None and not self._client.needs_login:
                return False
            self._warming = self._executor.submit(self._warm)
            return True

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    def _login_ahead(self) -> bool:
        """The next fetch will (or may still) have to log in."""
        client, warming = self._client, self._warming
        if warming is not None and not warming.done():
            return True
        return client is None or client.needs_login

    def _submit(self, unit_id: int, building_code: str, unit_value: str) -> Future[int]:
        """Queue the fetch, or join the one already queued/running for the same unit."""
        with self._lock:
            future = self._pending.get(unit_id)
            if future is None or future.done():
                future = self._executor.submit(self._fetch, unit_id, building_code, unit_value)
                self._pending[unit_id] = future
                future.add_done_callback(lambda f: self._forget(unit_id, f))
            return future

    def _forget(self, unit_id: int, future: Future[int]) -> None:
        with self._lock:
            if self._pending.get(unit_id) is future:
                del self._pending[unit_id]

    def _worker_client(self) -> LiveDebtSource:
        """Runs on the worker thread."""
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _warm(self) -> None:
        """Runs on the worker thread."""
        started = time.monotonic()
        try:
            client = self._worker_client()
            if not client.needs_login:
                return
            client.ensure_session()
        except Exception as exc:
            logger.warning("ConsorPlus warm-up failed: %s", type(exc).__name__)
            return
        logger.info("ConsorPlus session ready (warm-up, %.1f s)", time.monotonic() - started)

    def _fetch(self, unit_id: int, building_code: str, unit_value: str) -> int:
        """Runs on the worker thread. Returns the id of the stored snapshot."""
        started = time.monotonic()
        try:
            client = self._worker_client()
            with_login = client.needs_login
            debt = client.get_debt(building_code, unit_value)
            with self._session_factory() as session:
                snapshot, _ = save_snapshot(session, unit_id, debt, SyncKind.LIVE, self._now())
                session.commit()
                snapshot_id = snapshot.id
        except Exception as exc:
            # Also logged when the caller already gave up waiting.
            logger.warning("Live fetch of unit %s failed: %s", unit_id, type(exc).__name__)
            raise
        logger.info(
            "Live debt of unit %s fetched in %.1f s%s",
            unit_id,
            time.monotonic() - started,
            " (with login)" if with_login else "",
        )
        return snapshot_id

    def _recent_live(self, session: Session, unit_id: int) -> DebtSnapshot | None:
        if self._cache <= timedelta(0):
            return None
        return session.scalars(
            select(DebtSnapshot)
            .where(
                DebtSnapshot.unit_id == unit_id,
                DebtSnapshot.source == SyncKind.LIVE,
                DebtSnapshot.fetched_at >= self._now() - self._cache,
            )
            .order_by(DebtSnapshot.fetched_at.desc(), DebtSnapshot.id.desc())
            .limit(1)
            .options(selectinload(DebtSnapshot.lines))
        ).first()

    def _load(self, snapshot_id: int) -> DebtSnapshot:
        with self._session_factory() as session:
            return session.scalars(
                select(DebtSnapshot)
                .where(DebtSnapshot.id == snapshot_id)
                .options(selectinload(DebtSnapshot.lines))
            ).one()


_default: LiveRefresher | None = None
_default_lock = threading.Lock()


def get_live_refresher() -> LiveRefresher:
    """The process-wide refresher: one ConsorPlus session shared by every conversation."""
    global _default
    with _default_lock:
        if _default is None:
            _default = LiveRefresher(SessionLocal, cache_minutes=get_settings().live_cache_minutes)
    return _default


def refresh_unit(unit_id: int, timeout_seconds: float | None = None) -> DebtResult:
    """Live debt of one unit, or the last stored one with stale=True (see LiveRefresher)."""
    return get_live_refresher().refresh_unit(unit_id, timeout_seconds)


def warm_up() -> bool:
    """Log in to ConsorPlus in the background if needed (see LiveRefresher.warm_up)."""
    return get_live_refresher().warm_up()
