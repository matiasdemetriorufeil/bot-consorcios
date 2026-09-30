"""units unit type

Revision ID: dfa57d9f461e
Revises: daea9d7a35b0
Create Date: 2026-09-30 14:33:55.646574

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dfa57d9f461e"
down_revision: str | Sequence[str] | None = "daea9d7a35b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("units", sa.Column("unit_type", sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("units", "unit_type")
