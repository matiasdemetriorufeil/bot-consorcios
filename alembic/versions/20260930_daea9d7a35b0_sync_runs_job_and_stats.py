"""sync runs job and stats

Revision ID: daea9d7a35b0
Revises: 97afaf133ab1
Create Date: 2026-09-30 14:11:14.495353

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "daea9d7a35b0"
down_revision: str | Sequence[str] | None = "97afaf133ab1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "sync_runs",
        sa.Column("job", sa.String(length=20), server_default="debt", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_sync_runs_job_valid"), "sync_runs", "job IN ('debt', 'roster')"
    )
    op.add_column(
        "sync_runs",
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("sync_runs", "stats")
    op.drop_constraint(op.f("ck_sync_runs_job_valid"), "sync_runs", type_="check")
    op.drop_column("sync_runs", "job")
