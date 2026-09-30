"""verification codes and requests

Email codes to link unknown phones to unit owners, and requests for an operator to do it
when the unit has no owner email.

Revision ID: b7e2c4d91a60
Revises: 5a7c9e3d1b40
Create Date: 2026-09-30 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2c4d91a60"
down_revision: str | Sequence[str] | None = "5a7c9e3d1b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "verification_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("verification_id", sa.String(length=32), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name=op.f("fk_verification_codes_person_id_people"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["units.id"],
            name=op.f("fk_verification_codes_unit_id_units"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_codes")),
    )
    op.create_index(op.f("ix_verification_codes_phone_e164"), "verification_codes", ["phone_e164"])
    op.create_index(
        op.f("ix_verification_codes_verification_id"), "verification_codes", ["verification_id"]
    )

    op.create_table(
        "verification_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("claimed_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name=op.f("fk_verification_requests_person_id_people"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["units.id"],
            name=op.f("fk_verification_requests_unit_id_units"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_verification_requests_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_requests")),
    )
    op.create_index(
        op.f("ix_verification_requests_phone_e164"), "verification_requests", ["phone_e164"]
    )
    op.create_index(op.f("ix_verification_requests_unit_id"), "verification_requests", ["unit_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_verification_requests_unit_id"), table_name="verification_requests")
    op.drop_index(op.f("ix_verification_requests_phone_e164"), table_name="verification_requests")
    op.drop_table("verification_requests")
    op.drop_index(op.f("ix_verification_codes_verification_id"), table_name="verification_codes")
    op.drop_index(op.f("ix_verification_codes_phone_e164"), table_name="verification_codes")
    op.drop_table("verification_codes")
