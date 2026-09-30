"""Daily canary: query one known unit to detect changes in the ConsorPlus pages early.

The result goes to sync_runs (job="canary") and to the log with the "CANARIO" prefix. It
stores no debt. Messages hold the configured building/unit codes only, no personal data.
"""

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import get_settings
from app.consorplus import ConsorPlusClient
from app.consorplus.errors import (
    ConsorPlusError,
    ConsorPlusUnavailableError,
    LoginError,
    NotFoundError,
    ParseError,
)
from app.db.models import SyncJob, SyncKind, SyncRun, SyncStatus
from app.db.session import SessionLocal
from app.sync.nightly import DebtSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CanaryResult:
    status: SyncStatus
    message: str
    lines: int = 0


def _explain(exc: Exception, building: str, unit: str) -> str:
    where = f"edificio {building} / unidad {unit}"
    if isinstance(exc, ParseError):
        return (
            f"ESTRUCTURA INESPERADA: el parser no reconoce la página de deuda de ConsorPlus "
            f"({where}). Probablemente cambió la página: revisar app/consorplus/parsers.py. "
            f"Detalle: {exc}"
        )
    if isinstance(exc, NotFoundError):
        return f"La unidad canario ya no está en ConsorPlus ({where}): elegí otra. {exc}"
    if isinstance(exc, LoginError):
        return f"No se pudo iniciar sesión en ConsorPlus: {exc}"
    if isinstance(exc, ConsorPlusUnavailableError):
        return f"ConsorPlus no respondió: {exc}"
    if isinstance(exc, ConsorPlusError):
        return (
            f"La página de deuda no respondió como se esperaba ({where}): "
            f"{type(exc).__name__}: {exc}"
        )
    return f"Error inesperado del canario: {type(exc).__name__}"


def check_canary(
    source_factory: Callable[[], DebtSource], building: str, unit: str
) -> CanaryResult:
    if not building or not unit:
        return CanaryResult(
            SyncStatus.FAILED, "Canario no configurado: completá CANARY_BUILDING y CANARY_UNIT"
        )
    try:
        debt = source_factory().get_debt(building, unit)
    except Exception as exc:
        return CanaryResult(SyncStatus.FAILED, _explain(exc, building, unit))
    if not debt.lines:
        # Legit (the unit paid), but then the debt table itself went unchecked.
        return CanaryResult(
            SyncStatus.PARTIAL,
            f"La unidad canario (edificio {building} / unidad {unit}) no tiene deuda: no se "
            f"pudo validar la tabla de deuda. Conviene elegir una unidad con deuda.",
        )
    return CanaryResult(SyncStatus.OK, "Página de deuda reconocida", lines=len(debt.lines))


def run_canary(
    session: Session, source_factory: Callable[[], DebtSource], building: str, unit: str
) -> CanaryResult:
    """Check the canary unit and record the result as a sync_run (commits)."""
    run = SyncRun(kind=SyncKind.NIGHTLY, job=SyncJob.CANARY)
    session.add(run)
    session.commit()

    result = check_canary(source_factory, building, unit)
    if result.status is SyncStatus.OK:
        logger.info("CANARIO ok: %s líneas de deuda", result.lines)
    elif result.status is SyncStatus.PARTIAL:
        logger.warning("CANARIO: %s", result.message)
    else:
        logger.error("CANARIO FALLÓ: %s", result.message)

    run.finished_at = datetime.now(UTC)
    run.status = result.status
    run.units_ok = int(result.status is not SyncStatus.FAILED)
    run.units_failed = int(result.status is SyncStatus.FAILED)
    run.stats = {"lines": result.lines}
    run.error_summary = None if result.status is SyncStatus.OK else result.message
    session.commit()
    return result


def run_canary_job() -> CanaryResult:
    settings = get_settings()
    with SessionLocal() as session:
        return run_canary(
            session,
            lambda: ConsorPlusClient.from_settings(settings),
            settings.canary_building,
            settings.canary_unit,
        )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    result = run_canary_job()
    print(f"Canario: {result.status.value} - {result.message}")
    return 0 if result.status is SyncStatus.OK else 1


if __name__ == "__main__":
    sys.exit(main())
