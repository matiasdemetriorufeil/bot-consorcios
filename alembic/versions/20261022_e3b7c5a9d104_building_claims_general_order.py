"""building claims general order

The order of the kinds of problem is defined in one place, «Tipos de problema»
(claim_categories.sort_order): every building lists its enabled ones in that order. The column
building_claim_categories.sort_order goes. Checked before dropping it (2026-10-22): no building
had an order different from the general one, so nothing chosen by hand is lost.

The downgrade brings the column back with the general order of each kind.

Revision ID: e3b7c5a9d104
Revises: d9a2f6c1b830
Create Date: 2026-10-22 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3b7c5a9d104"
down_revision: str | Sequence[str] | None = "d9a2f6c1b830"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("building_claim_categories", "sort_order")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "building_claim_categories",
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.execute(
        "UPDATE building_claim_categories b SET sort_order = c.sort_order "
        "FROM claim_categories c WHERE c.id = b.category_id"
    )
