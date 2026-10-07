"""panel users tour seen

When a user of the panel saw (or skipped) the short tour of "Conversaciones": until then it
opens by itself (app.admin.conversations). NULL: not seen yet.

Revision ID: e8b3f1a6c247
Revises: d5f1a3c8e902
Create Date: 2026-10-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8b3f1a6c247"
down_revision: str | Sequence[str] | None = "d5f1a3c8e902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "panel_users", sa.Column("tour_seen_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("panel_users", "tour_seen_at")
