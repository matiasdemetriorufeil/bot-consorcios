"""phones conflict

Splits "the number is listed for another person too" (conflict: does not identify) from
"the area code was assumed" (needs_review: still identifies).

Existing needs_review rows whose raw text is a full number (no area code missing) can only
come from a conflict: they move to conflict. A row that was both keeps needs_review only; the
next roster sync flags its conflict again.

Revision ID: c3f5a8e27d14
Revises: b7e2c4d91a60
Create Date: 2026-09-30 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f5a8e27d14"
down_revision: str | Sequence[str] | None = "b7e2c4d91a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same rule as normalize_phone (frozen here): the raw digits without the trunk "0" are 6-8
# digits or "15" + 7 digits when the area code was assumed.
_MOVE_CONFLICTS = r"""
UPDATE phones SET conflict = true, needs_review = false
WHERE needs_review
  AND NOT (
    length(regexp_replace(regexp_replace(coalesce(raw, ''), '\D', '', 'g'), '^0', ''))
      BETWEEN 6 AND 8
    OR regexp_replace(regexp_replace(coalesce(raw, ''), '\D', '', 'g'), '^0', '')
      ~ '^15\d{7}$'
  )
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "phones",
        sa.Column("conflict", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.execute(_MOVE_CONFLICTS)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE phones SET needs_review = true WHERE conflict")
    op.drop_column("phones", "conflict")
