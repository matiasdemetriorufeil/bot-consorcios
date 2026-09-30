"""drop coupons

Coupons were dropped from the scope: the bot reports the debt and units.payment_code.

Revision ID: 3c8e1f0a7b21
Revises: 617916e98a84
Create Date: 2026-09-30 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c8e1f0a7b21"
down_revision: str | Sequence[str] | None = "617916e98a84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table("coupons")


def downgrade() -> None:
    """Downgrade schema (empty table, as it was before the upgrade minus its rows)."""
    op.create_table(
        "coupons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("coupon_id", sa.String(length=50), nullable=False),
        sa.Column("amount_1", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("due_date_1", sa.Date(), nullable=False),
        sa.Column("amount_2", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("due_date_2", sa.Date(), nullable=True),
        sa.Column("detail_url", sa.String(length=500), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["units.id"], name=op.f("fk_coupons_unit_id_units"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coupons")),
        sa.UniqueConstraint(
            "unit_id", "period", "coupon_id", name=op.f("uq_coupons_unit_id_period_coupon_id")
        ),
    )
