from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import DebtSnapshot


def get_latest_debt(session: Session, unit_id: int) -> DebtSnapshot | None:
    """Return the most recent debt snapshot of a unit (with its lines), or None if there is none.

    Considers both nightly and live snapshots; ties on `fetched_at` are broken by id.
    """
    stmt = (
        select(DebtSnapshot)
        .where(DebtSnapshot.unit_id == unit_id)
        .order_by(DebtSnapshot.fetched_at.desc(), DebtSnapshot.id.desc())
        .limit(1)
        .options(selectinload(DebtSnapshot.lines))
    )
    return session.scalars(stmt).first()
