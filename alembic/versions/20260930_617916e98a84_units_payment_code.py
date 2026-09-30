"""units payment code

Revision ID: 617916e98a84
Revises: dfa57d9f461e
Create Date: 2026-09-30 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "617916e98a84"
down_revision: str | Sequence[str] | None = "dfa57d9f461e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("units", sa.Column("payment_code", sa.String(length=19), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("units", "payment_code")
