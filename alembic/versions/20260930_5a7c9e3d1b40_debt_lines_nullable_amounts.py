"""debt lines nullable amounts

ConsorPlus may leave "Imp.Concepto" or "Deuda Acumulada" empty: stored as NULL, not as 0.

Revision ID: 5a7c9e3d1b40
Revises: 8d4b2a6e9f13
Create Date: 2026-09-30 19:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a7c9e3d1b40"
down_revision: str | Sequence[str] | None = "8d4b2a6e9f13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AMOUNT = sa.Numeric(precision=14, scale=2)


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("debt_lines", "concept_amount", existing_type=AMOUNT, nullable=True)
    op.alter_column("debt_lines", "accumulated", existing_type=AMOUNT, nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE debt_lines SET concept_amount = 0 WHERE concept_amount IS NULL")
    op.execute("UPDATE debt_lines SET accumulated = balance_due WHERE accumulated IS NULL")
    op.alter_column("debt_lines", "accumulated", existing_type=AMOUNT, nullable=False)
    op.alter_column("debt_lines", "concept_amount", existing_type=AMOUNT, nullable=False)
