"""sync runs canary job

Revision ID: 8d4b2a6e9f13
Revises: 3c8e1f0a7b21
Create Date: 2026-09-30 19:05:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d4b2a6e9f13"
down_revision: str | Sequence[str] | None = "3c8e1f0a7b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f("ck_sync_runs_job_valid"), "sync_runs", type_="check")
    op.create_check_constraint(
        op.f("ck_sync_runs_job_valid"), "sync_runs", "job IN ('debt', 'roster', 'canary')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM sync_runs WHERE job = 'canary'")
    op.drop_constraint(op.f("ck_sync_runs_job_valid"), "sync_runs", type_="check")
    op.create_check_constraint(
        op.f("ck_sync_runs_job_valid"), "sync_runs", "job IN ('debt', 'roster')"
    )
