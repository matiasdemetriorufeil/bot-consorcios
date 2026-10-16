"""registration requests

Neighbors register by WhatsApp with the studio's approval: verification_requests.declared_role
(the role the person says: owner, tenant or resident; NULL in the old ones, which were all
owners) and one pending request per phone at most.

Revision ID: 6e2f9b4c8d17
Revises: 8c1d4e7b2a59
Create Date: 2026-10-15 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6e2f9b4c8d17"
down_revision: str | Sequence[str] | None = "8c1d4e7b2a59"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = "'owner', 'tenant', 'resident'"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "verification_requests",
        sa.Column("declared_role", sa.String(length=20), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_verification_requests_declared_role_valid"),
        "verification_requests",
        f"declared_role IN ({ROLES})",
    )
    op.create_index(
        "uq_verification_requests_pending_phone",
        "verification_requests",
        ["phone_e164"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_verification_requests_pending_phone",
        table_name="verification_requests",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_constraint(
        op.f("ck_verification_requests_declared_role_valid"),
        "verification_requests",
        type_="check",
    )
    op.drop_column("verification_requests", "declared_role")
