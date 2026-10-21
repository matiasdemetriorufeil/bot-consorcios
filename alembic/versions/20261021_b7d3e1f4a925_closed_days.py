"""closed days

The business days of the bot and the claims (app.business_hours): closed_days holds the days
the studio closes besides the national holidays, and the national holidays it opens anyway.
The holidays typed in «Configuración de reclamos» (claim_settings.holidays) move here as
closed days (except the ones that already are national holidays) and the column goes.

Revision ID: b7d3e1f4a925
Revises: 6e2f9b4c8d17
Create Date: 2026-10-21 12:00:00.000000

"""

from collections.abc import Sequence
from datetime import date, datetime

import holidays
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d3e1f4a925"
down_revision: str | Sequence[str] | None = "6e2f9b4c8d17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MOVED_REASON = "Cargado en Configuración de reclamos"


def _dates(text: str | None) -> list[date]:
    found = []
    for line in (text or "").splitlines():
        try:
            found.append(datetime.strptime(line.strip(), "%d/%m/%Y").date())
        except ValueError:
            continue  # the form only saved valid ones; never fail the migration for a line
    return found


def upgrade() -> None:
    """Upgrade schema."""
    closed_days = op.create_table(
        "closed_days",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="closed", nullable=False),
        sa.Column("reason", sa.String(length=200), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('closed', 'open_anyway')", name=op.f("ck_closed_days_kind_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_closed_days")),
        sa.UniqueConstraint("day", name=op.f("uq_closed_days_day")),
    )
    bind = op.get_bind()
    text = bind.execute(sa.text("SELECT holidays FROM claim_settings WHERE id = 1")).scalar()
    days = sorted(set(_dates(text)))
    national = holidays.country_holidays("AR", years={d.year for d in days} or None)
    rows = [{"day": d, "kind": "closed", "reason": MOVED_REASON} for d in days if d not in national]
    if rows:
        op.bulk_insert(closed_days, rows)
    op.drop_column("claim_settings", "holidays")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("claim_settings", sa.Column("holidays", sa.Text(), nullable=True))
    bind = op.get_bind()
    days = bind.execute(
        sa.text("SELECT day FROM closed_days WHERE kind = 'closed' ORDER BY day")
    ).scalars()
    text = "\n".join(f"{d:%d/%m/%Y}" for d in days)
    bind.execute(
        sa.text("UPDATE claim_settings SET holidays = :text WHERE id = 1"), {"text": text or None}
    )
    op.drop_table("closed_days")
