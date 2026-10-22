"""sum slots until midnight

The SUM's slots loaded until 23:59 now end at 00:00 (midnight): the panel picks hours from a list
every 30 minutes (app.admin.hours) and 23:59 is not in it. A slot whose end is not after its
start ends the next day, so 20:00 to 00:00 ends at midnight, one minute later than before.

Only end_time 23:59 (any seconds) changes, in place: the same rows, so the ids of the slots and
the reservations that point to them stay. A slot starting at 00:00 is left alone (00:00 to
00:00 would be empty: check not_empty). Running it again changes nothing (no 23:59 is left).
The downgrade does nothing: a slot that already ended at 00:00 cannot be told apart.

Revision ID: d9a2f6c1b830
Revises: c4e8a1d7f362
Create Date: 2026-10-22 16:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9a2f6c1b830"
down_revision: str | Sequence[str] | None = "c4e8a1d7f362"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNTIL_MIDNIGHT = """
UPDATE amenity_slots SET end_time = time '00:00'
WHERE end_time >= time '23:59' AND start_time <> time '00:00'
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(UNTIL_MIDNIGHT)


def downgrade() -> None:
    """Downgrade schema: nothing (see the module doc)."""
