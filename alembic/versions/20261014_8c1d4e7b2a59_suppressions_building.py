"""suppressions building

Step 5h.2: «Quitados a mano» of each building in «Vecinos». roster_suppressions.building_id
(where it is listed: a link's unit's building; a phone, the one of the person's first unit) and
person_name (the name shown when it was removed). Links get their building here; phones removed
before this step have none (they are not listed, but they still apply).

Revision ID: 8c1d4e7b2a59
Revises: 3f6b8d2a1c94
Create Date: 2026-10-14 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c1d4e7b2a59"
down_revision: str | Sequence[str] | None = "3f6b8d2a1c94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BACKFILL_LINK_BUILDING = (
    "UPDATE roster_suppressions SET building_id = units.building_id FROM units "
    "WHERE roster_suppressions.unit_id = units.id AND roster_suppressions.kind = 'link'"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("roster_suppressions", sa.Column("building_id", sa.Integer(), nullable=True))
    op.add_column(
        "roster_suppressions", sa.Column("person_name", sa.String(length=200), nullable=True)
    )
    op.create_index(
        op.f("ix_roster_suppressions_building_id"),
        "roster_suppressions",
        ["building_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_roster_suppressions_building_id_buildings"),
        "roster_suppressions",
        "buildings",
        ["building_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute(_BACKFILL_LINK_BUILDING)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("fk_roster_suppressions_building_id_buildings"),
        "roster_suppressions",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_roster_suppressions_building_id"), table_name="roster_suppressions")
    op.drop_column("roster_suppressions", "person_name")
    op.drop_column("roster_suppressions", "building_id")
