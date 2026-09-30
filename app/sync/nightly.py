"""Nightly sync: roster first, then the debt of every active unit. Read-only on ConsorPlus.

Usage:
    docker compose run --rm api python -m app.sync.nightly [--buildings 1,2]

`--buildings` takes the ConsorPlus building codes (combo values); without it, all of them.
Logs, reports and sync_runs hold counters and ids only, never personal data.
"""

import argparse
import logging
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.consorplus import ConsorPlusClient
from app.consorplus.errors import ConsorPlusError, ConsorPlusUnavailableError, LoginError
from app.consorplus.models import UnitDebt
from app.db.models import Building, SyncJob, SyncKind, SyncRun, SyncStatus, Unit
from app.db.session import SessionLocal, engine
from app.sync.roster import MAX_ERROR_SUMMARY, RosterReport, RosterSource, sync_roster
from app.sync.snapshots import purge_snapshots, save_snapshot

logger = logging.getLogger(__name__)

# ConsorPlus down: stop after this many units in a row fail with it, instead of spending
# hours on retries. A login error stops right away (retrying could lock the account).
MAX_CONSECUTIVE_OUTAGES = 10
MAX_DETAILED_ERRORS = 50
# pg advisory lock key: only one nightly sync at a time (scheduled or manual).
NIGHTLY_LOCK_KEY = 0x6E69676874  # "night"


class DebtSource(RosterSource, Protocol):
    """What the nightly sync needs from ConsorPlusClient (a fake in tests)."""

    def get_debt(self, building_code: str, unit_value: str) -> UnitDebt: ...


class AlreadyRunningError(RuntimeError):
    """Another nightly sync holds the lock."""


@dataclass
class DebtReport:
    """Counters only: safe to print and to store in sync_runs.stats."""

    units: int = 0
    units_ok: int = 0
    units_failed: int = 0
    units_skipped: int = 0  # not queried because ConsorPlus was down
    units_with_debt: int = 0
    debt_lines: int = 0
    snapshots_replaced: int = 0  # today's nightly snapshots replaced by this run
    snapshots_purged: int = 0
    elapsed_seconds: float = 0.0
    errors_by_type: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> SyncStatus:
        if self.units_failed == 0 and self.units_skipped == 0:
            return SyncStatus.OK
        return SyncStatus.PARTIAL if self.units_ok else SyncStatus.FAILED

    def counters(self) -> dict[str, int | float]:
        return {k: v for k, v in asdict(self).items() if isinstance(v, int | float)}

    def stats(self) -> dict[str, Any]:
        return {**self.counters(), "errors_by_type": dict(self.errors_by_type)}

    def add_error(self, message: str, exc: BaseException) -> None:
        self.errors_by_type[type(exc).__name__] += 1
        if len(self.errors) < MAX_DETAILED_ERRORS:
            self.errors.append(message)


@dataclass
class NightlyReport:
    roster: RosterReport
    debt: DebtReport


def _error_detail(exc: BaseException) -> str:
    # Only our own messages: DB errors may echo row values (personal data).
    return (
        f"{type(exc).__name__}: {exc}" if isinstance(exc, ConsorPlusError) else type(exc).__name__
    )


def _units_to_sync(
    session: Session, building_codes: Sequence[str] | None
) -> list[tuple[int, str, str]]:
    """(unit id, building combo value, unit combo value) of the active units, in order."""
    stmt = (
        select(Unit.id, Building.consorplus_code, Unit.consorplus_unit_value)
        .join(Building, Building.id == Unit.building_id)
        .where(Unit.active.is_(True), Building.active.is_(True))
        .order_by(Building.consorplus_code, Unit.id)
    )
    if building_codes is not None:
        stmt = stmt.where(Building.consorplus_code.in_([int(c) for c in building_codes]))
    return [(unit_id, str(code), value) for unit_id, code, value in session.execute(stmt)]


def sync_debts(
    session: Session,
    source: DebtSource,
    building_codes: Sequence[str] | None = None,
    *,
    timezone: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DebtReport:
    """Store today's nightly debt snapshot of every active unit and apply the retention.

    One failing unit does not stop the rest. Commits after each unit.
    """
    started = time.monotonic()
    report = DebtReport()
    run = SyncRun(kind=SyncKind.NIGHTLY, job=SyncJob.DEBT)
    session.add(run)
    session.commit()

    units = _units_to_sync(session, building_codes)
    report.units = len(units)
    outages = 0
    for index, (unit_id, building_code, unit_value) in enumerate(units):
        try:
            debt = source.get_debt(building_code, unit_value)
            with session.begin_nested():
                _, replaced = save_snapshot(
                    session, unit_id, debt, SyncKind.NIGHTLY, now(), replace_day_of=timezone
                )
            session.commit()
        except Exception as exc:  # one unit must not stop the others
            session.rollback()
            report.units_failed += 1
            report.add_error(
                f"unidad {unit_id} (edificio {building_code}): {_error_detail(exc)}", exc
            )
            logger.warning(
                "Debt sync failed for unit %s (building %s): %s",
                unit_id,
                building_code,
                type(exc).__name__,
            )
            outages = outages + 1 if isinstance(exc, ConsorPlusUnavailableError) else 0
            if isinstance(exc, LoginError) or outages >= MAX_CONSECUTIVE_OUTAGES:
                report.units_skipped = len(units) - index - 1
                report.errors.append(
                    f"se cortó la sincronización ({type(exc).__name__}): "
                    f"{report.units_skipped} unidades sin consultar"
                )
                logger.error("Debt sync aborted: %s", type(exc).__name__)
                break
            continue
        outages = 0
        report.units_ok += 1
        report.units_with_debt += not debt.is_up_to_date
        report.debt_lines += len(debt.lines)
        report.snapshots_replaced += replaced

    report.snapshots_purged = purge_snapshots(session, now())
    report.elapsed_seconds = round(time.monotonic() - started, 1)
    run.finished_at = datetime.now(UTC)
    run.status = report.status
    run.units_ok = report.units_ok
    run.units_failed = report.units_failed + report.units_skipped
    run.stats = report.stats()
    run.error_summary = "\n".join(report.errors)[:MAX_ERROR_SUMMARY] or None
    session.commit()
    return report


def run_nightly(
    session: Session,
    source: DebtSource,
    building_codes: Sequence[str] | None = None,
    *,
    timezone: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> NightlyReport:
    """Roster sync for the buildings (all if None), then their debts. Each has its sync_run."""
    roster = sync_roster(session, source, building_codes)
    logger.info("Roster sync finished: %s", roster.status.value)
    debt = sync_debts(session, source, building_codes, timezone=timezone, now=now)
    logger.info(
        "Debt sync finished: %s (%s ok, %s failed, %s skipped, %.0fs)",
        debt.status.value,
        debt.units_ok,
        debt.units_failed,
        debt.units_skipped,
        debt.elapsed_seconds,
    )
    return NightlyReport(roster=roster, debt=debt)


@contextmanager
def advisory_lock(engine: Engine, key: int = NIGHTLY_LOCK_KEY) -> Iterator[None]:
    """Hold a Postgres session-level advisory lock, or raise AlreadyRunningError."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if not conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}):
            raise AlreadyRunningError("Ya hay una sincronización nocturna en curso")
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def run_nightly_job(building_codes: Sequence[str] | None = None) -> NightlyReport:
    """Entry point of the scheduler and the command line: one ConsorPlus session for all."""
    settings = get_settings()
    with advisory_lock(engine):
        client = ConsorPlusClient.from_settings(settings)
        with SessionLocal() as session:
            return run_nightly(session, client, building_codes, timezone=settings.timezone)


# --- Command line ------------------------------------------------------------------------

DEBT_LABELS = {
    "units": "unidades activas",
    "units_ok": "unidades consultadas ok",
    "units_failed": "unidades con error",
    "units_skipped": "unidades sin consultar (corte)",
    "units_with_debt": "unidades con deuda",
    "debt_lines": "líneas de deuda",
    "snapshots_replaced": "snapshots de hoy reemplazados",
    "snapshots_purged": "snapshots viejos borrados",
    "elapsed_seconds": "segundos",
}


def _parse_codes(value: str) -> list[str]:
    codes = [c.strip() for c in value.split(",") if c.strip()]
    if not codes or not all(c.isdigit() for c in codes):
        raise argparse.ArgumentTypeError("usá códigos numéricos separados por coma, ej. 1,2")
    return codes


def _print_report(report: NightlyReport) -> None:
    roster = report.roster
    print(
        f"Padrón: {roster.status.value} ({roster.buildings_ok} edificios ok, "
        f"{roster.buildings_failed} con error, {roster.units} unidades)"
    )
    for error in roster.errors:
        print(f"  ERROR padrón {error}")
    debt = report.debt
    print(f"Deuda: {debt.status.value}")
    for key, value in debt.counters().items():
        print(f"  {DEBT_LABELS.get(key, key):34} {value}")
    if debt.units_ok:
        print(f"  {'segundos por unidad':34} {debt.elapsed_seconds / debt.units_ok:.2f}")
    for name, count in debt.errors_by_type.most_common():
        print(f"  errores {name}: {count}")
    for error in debt.errors:
        print(f"  ERROR {error}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sincronización nocturna con ConsorPlus")
    parser.add_argument(
        "--buildings", type=_parse_codes, help="códigos de edificio separados por coma (ej. 1,2)"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    try:
        report = run_nightly_job(args.buildings)
    except AlreadyRunningError as exc:
        print(exc, file=sys.stderr)
        return 2
    _print_report(report)
    ok = report.roster.status is SyncStatus.OK and report.debt.status is SyncStatus.OK
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
