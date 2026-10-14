"""neighbors roster

Step 5h: the roster edited by hand ("Vecinos"). unit_people.role accepts "resident" ("familiar
u otro"); people.consorplus_name (the name as ConsorPlus lists it, filled here with full_name
for everyone linked from ConsorPlus); roster_suppressions (links and phones removed by hand that
the roster sync must not create again).

Revision ID: 3f6b8d2a1c94
Revises: 9a4d6b2e8c31
Create Date: 2026-10-14 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f6b8d2a1c94"
down_revision: str | Sequence[str] | None = "9a4d6b2e8c31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_ROLES = "'owner', 'tenant'"
NEW_ROLES = f"{OLD_ROLES}, 'resident'"
# Everyone linked from ConsorPlus got their name from it (nobody could edit it until now).
_BACKFILL_CONSORPLUS_NAME = (
    "UPDATE people SET consorplus_name = full_name WHERE EXISTS ("
    "SELECT 1 FROM unit_people WHERE unit_people.person_id = people.id "
    "AND unit_people.source = 'consorplus')"
)


def _check(table: str, name: str, column: str, values: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, f"{column} IN ({values})")


def upgrade() -> None:
    """Upgrade schema."""
    _check("unit_people", "role_valid", "role", NEW_ROLES)
    op.add_column("people", sa.Column("consorplus_name", sa.String(length=200), nullable=True))
    op.execute(_BACKFILL_CONSORPLUS_NAME)
    op.create_table(
        "roster_suppressions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=True),
        sa.Column("e164", sa.String(length=20), nullable=True),
        sa.Column("name_key", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'link' AND unit_id IS NOT NULL AND role IS NOT NULL AND e164 IS NULL)"
            " OR (kind = 'phone' AND e164 IS NOT NULL AND unit_id IS NULL AND role IS NULL)",
            name=op.f("ck_roster_suppressions_shape"),
        ),
        sa.CheckConstraint(
            "kind IN ('link', 'phone')", name=op.f("ck_roster_suppressions_kind_valid")
        ),
        sa.CheckConstraint(
            f"role IN ({NEW_ROLES})", name=op.f("ck_roster_suppressions_role_valid")
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["units.id"],
            name=op.f("fk_roster_suppressions_unit_id_units"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roster_suppressions")),
    )
    op.create_index(
        "uq_roster_suppressions_link",
        "roster_suppressions",
        ["unit_id", "role", "name_key"],
        unique=True,
        postgresql_where=sa.text("kind = 'link'"),
    )
    op.create_index(
        "uq_roster_suppressions_phone",
        "roster_suppressions",
        ["e164", "name_key"],
        unique=True,
        postgresql_where=sa.text("kind = 'phone'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_roster_suppressions_phone",
        table_name="roster_suppressions",
        postgresql_where=sa.text("kind = 'phone'"),
    )
    op.drop_index(
        "uq_roster_suppressions_link",
        table_name="roster_suppressions",
        postgresql_where=sa.text("kind = 'link'"),
    )
    op.drop_table("roster_suppressions")
    op.drop_column("people", "consorplus_name")
    op.execute("DELETE FROM unit_people WHERE role = 'resident'")
    _check("unit_people", "role_valid", "role", OLD_ROLES)
