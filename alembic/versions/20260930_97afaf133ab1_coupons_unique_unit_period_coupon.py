"""coupons unique unit period coupon

Revision ID: 97afaf133ab1
Revises: 49b5412be2c0
Create Date: 2026-09-30 14:55:29.045433

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "97afaf133ab1"
down_revision: str | Sequence[str] | None = "49b5412be2c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f("ix_coupons_unit_id_period"), table_name="coupons")
    op.create_unique_constraint(
        op.f("uq_coupons_unit_id_period_coupon_id"), "coupons", ["unit_id", "period", "coupon_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("uq_coupons_unit_id_period_coupon_id"), "coupons", type_="unique")
    op.create_index(
        op.f("ix_coupons_unit_id_period"), "coupons", ["unit_id", "period"], unique=False
    )
