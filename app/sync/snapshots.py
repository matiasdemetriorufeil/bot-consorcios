"""Storage of debt snapshots (nightly and live) and their retention."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, aliased

from app.consorplus.models import UnitDebt
from app.db.models import DebtLine, DebtSnapshot, SyncKind

NIGHTLY_RETENTION = timedelta(days=60)
LIVE_RETENTION = timedelta(days=30)


def local_day_bounds(moment: datetime, timezone: str) -> tuple[datetime, datetime]:
    """Start and end (exclusive) of the local calendar day of `moment`."""
    tz = ZoneInfo(timezone)
    start = datetime.combine(moment.astimezone(tz).date(), time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def save_snapshot(
    session: Session,
    unit_id: int,
    debt: UnitDebt,
    source: SyncKind,
    fetched_at: datetime,
    *,
    replace_day_of: str | None = None,
) -> tuple[DebtSnapshot, int]:
    """Store the debt of a unit (no commit). Returns the snapshot and how many were replaced.

    With `replace_day_of` (a timezone name), the unit's snapshots of the same `source` taken
    on the same local day as `fetched_at` are deleted first, so re-runs do not duplicate.
    """
    replaced = 0
    if replace_day_of is not None:
        start, end = local_day_bounds(fetched_at, replace_day_of)
        replaced = session.execute(
            delete(DebtSnapshot).where(
                DebtSnapshot.unit_id == unit_id,
                DebtSnapshot.source == source,
                DebtSnapshot.fetched_at >= start,
                DebtSnapshot.fetched_at < end,
            )
        ).rowcount
    snapshot = DebtSnapshot(
        unit_id=unit_id,
        fetched_at=fetched_at,
        source=source,
        total_amount=debt.total,
        is_up_to_date=debt.is_up_to_date,
        lines=[
            DebtLine(
                concept=line.concept,
                period=line.period,
                concept_amount=line.concept_amount,
                balance_due=line.balance,
                accumulated=line.accumulated,
            )
            for line in debt.lines
        ],
    )
    session.add(snapshot)
    session.flush()
    return snapshot, replaced


def purge_snapshots(session: Session, now: datetime) -> int:
    """Delete nightly snapshots older than 60 days and live ones older than 30 (no commit).

    The latest snapshot of each unit is always kept, however old. Lines go with them
    (ON DELETE CASCADE).
    """
    latest = aliased(DebtSnapshot)
    latest_id = (
        select(latest.id)
        .where(latest.unit_id == DebtSnapshot.unit_id)
        .order_by(latest.fetched_at.desc(), latest.id.desc())
        .limit(1)
        .correlate(DebtSnapshot)
        .scalar_subquery()
    )
    result = session.execute(
        delete(DebtSnapshot)
        .where(
            or_(
                (DebtSnapshot.source == SyncKind.NIGHTLY)
                & (DebtSnapshot.fetched_at < now - NIGHTLY_RETENTION),
                (DebtSnapshot.source == SyncKind.LIVE)
                & (DebtSnapshot.fetched_at < now - LIVE_RETENTION),
            ),
            DebtSnapshot.id != latest_id,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount
