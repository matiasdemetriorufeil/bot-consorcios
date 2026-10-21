"""phones conflict person

phones.conflict_person_id: the other person ConsorPlus lists the number for (conflict), so
Teléfonos can say «También figura en <persona> (<unidad>)». Filled by the roster sync from now
on; the conflicts that already exist get it on the next sync (ConsorPlus is only read there).

Revision ID: c4e8a1d7f362
Revises: b7d3e1f4a925
Create Date: 2026-10-22 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8a1d7f362"
down_revision: str | Sequence[str] | None = "b7d3e1f4a925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("phones", sa.Column("conflict_person_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_phones_conflict_person_id_people"),
        "phones",
        "people",
        ["conflict_person_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("fk_phones_conflict_person_id_people"), "phones", type_="foreignkey")
    op.drop_column("phones", "conflict_person_id")
