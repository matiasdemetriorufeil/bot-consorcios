"""amenities and reservations

Bookable common spaces (the SUM) of a building, their weekly slots and the reservations.
At most one confirmed reservation per slot and date (partial unique index).

Revision ID: a9c3e5f71b28
Revises: f2b8d6a41c93
Create Date: 2026-10-06 13:50:10.210890

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c3e5f71b28"
down_revision: str | Sequence[str] | None = "f2b8d6a41c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "amenities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("building_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), server_default="SUM", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "bot_booking_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("rules_text", sa.Text(), nullable=True),
        sa.Column("min_advance_hours", sa.Integer(), server_default="24", nullable=False),
        sa.Column("max_advance_days", sa.Integer(), server_default="60", nullable=False),
        sa.Column("max_per_unit_per_month", sa.Integer(), server_default="2", nullable=True),
        sa.Column("cancel_until_hours", sa.Integer(), server_default="48", nullable=False),
        sa.Column("blocks_debtors", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["building_id"],
            ["buildings.id"],
            name=op.f("fk_amenities_building_id_buildings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_amenities")),
    )
    op.create_index(op.f("ix_amenities_building_id"), "amenities", ["building_id"], unique=False)
    op.create_table(
        "amenity_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("amenity_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("label", sa.String(length=50), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("start_time <> end_time", name=op.f("ck_amenity_slots_not_empty")),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name=op.f("ck_amenity_slots_weekday_valid")),
        sa.ForeignKeyConstraint(
            ["amenity_id"],
            ["amenities.id"],
            name=op.f("fk_amenity_slots_amenity_id_amenities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_amenity_slots")),
    )
    op.create_index(
        op.f("ix_amenity_slots_amenity_id"), "amenity_slots", ["amenity_id"], unique=False
    )
    op.create_table(
        "reservations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("amenity_id", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column("created_by_phone", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('bot', 'panel')", name=op.f("ck_reservations_source_valid")),
        sa.CheckConstraint(
            "status IN ('confirmed', 'cancelled')", name=op.f("ck_reservations_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["amenity_id"],
            ["amenities.id"],
            name=op.f("fk_reservations_amenity_id_amenities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["slot_id"],
            ["amenity_slots.id"],
            name=op.f("fk_reservations_slot_id_amenity_slots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["units.id"],
            name=op.f("fk_reservations_unit_id_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reservations")),
    )
    op.create_index(
        "ix_reservations_amenity_id_date", "reservations", ["amenity_id", "date"], unique=False
    )
    op.create_index(op.f("ix_reservations_unit_id"), "reservations", ["unit_id"], unique=False)
    op.create_index(
        "uq_reservations_slot_id_date_confirmed",
        "reservations",
        ["slot_id", "date"],
        unique=True,
        postgresql_where=sa.text("status = 'confirmed'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_reservations_slot_id_date_confirmed",
        table_name="reservations",
        postgresql_where=sa.text("status = 'confirmed'"),
    )
    op.drop_index(op.f("ix_reservations_unit_id"), table_name="reservations")
    op.drop_index("ix_reservations_amenity_id_date", table_name="reservations")
    op.drop_table("reservations")
    op.drop_index(op.f("ix_amenity_slots_amenity_id"), table_name="amenity_slots")
    op.drop_table("amenity_slots")
    op.drop_index(op.f("ix_amenities_building_id"), table_name="amenities")
    op.drop_table("amenities")
