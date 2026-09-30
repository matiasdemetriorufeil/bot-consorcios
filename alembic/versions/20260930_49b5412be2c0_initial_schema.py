"""initial schema

Revision ID: 49b5412be2c0
Revises:
Create Date: 2026-09-30 14:43:13.303755

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "49b5412be2c0"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "bot_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("phone_e164", sa.String(length=20), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bot_events")),
    )
    op.create_index(
        op.f("ix_bot_events_conversation_id"), "bot_events", ["conversation_id"], unique=False
    )
    op.create_index(op.f("ix_bot_events_created_at"), "bot_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_bot_events_phone_e164"), "bot_events", ["phone_e164"], unique=False)
    op.create_table(
        "buildings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("consorplus_code", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("pilot", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_buildings")),
        sa.UniqueConstraint("consorplus_code", name=op.f("uq_buildings_consorplus_code")),
    )
    op.create_table(
        "people",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("dni", sa.String(length=20), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_people")),
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=True,
        ),
        sa.Column("units_ok", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("units_failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('nightly', 'live')", name=op.f("ck_sync_runs_kind_valid")),
        sa.CheckConstraint(
            "status IN ('ok', 'partial', 'failed')", name=op.f("ck_sync_runs_status_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_runs")),
    )
    op.create_table(
        "phones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("e164", sa.String(length=20), nullable=False),
        sa.Column("raw", sa.String(length=100), nullable=True),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('consorplus', 'bot_verified', 'manual')",
            name=op.f("ck_phones_source_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name=op.f("fk_phones_person_id_people"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_phones")),
        sa.UniqueConstraint("e164", name=op.f("uq_phones_e164")),
    )
    op.create_index(op.f("ix_phones_person_id"), "phones", ["person_id"], unique=False)
    op.create_table(
        "units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("building_id", sa.Integer(), nullable=False),
        sa.Column("consorplus_unit_value", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("owner_name", sa.String(length=200), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["building_id"],
            ["buildings.id"],
            name=op.f("fk_units_building_id_buildings"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_units")),
        sa.UniqueConstraint(
            "building_id",
            "consorplus_unit_value",
            name=op.f("uq_units_building_id_consorplus_unit_value"),
        ),
    )
    op.create_index(op.f("ix_units_building_id"), "units", ["building_id"], unique=False)
    op.create_table(
        "coupons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("coupon_id", sa.String(length=50), nullable=False),
        sa.Column("amount_1", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("due_date_1", sa.Date(), nullable=False),
        sa.Column("amount_2", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("due_date_2", sa.Date(), nullable=True),
        sa.Column("detail_url", sa.String(length=500), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["units.id"], name=op.f("fk_coupons_unit_id_units"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coupons")),
    )
    op.create_index("ix_coupons_unit_id_period", "coupons", ["unit_id", "period"], unique=False)
    op.create_table(
        "debt_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("is_up_to_date", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "source IN ('nightly', 'live')", name=op.f("ck_debt_snapshots_source_valid")
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["units.id"],
            name=op.f("fk_debt_snapshots_unit_id_units"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_debt_snapshots")),
    )
    op.create_index(
        "ix_debt_snapshots_unit_id_fetched_at",
        "debt_snapshots",
        ["unit_id", "fetched_at"],
        unique=False,
    )
    op.create_table(
        "unit_people",
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('owner', 'tenant')", name=op.f("ck_unit_people_role_valid")),
        sa.CheckConstraint(
            "source IN ('consorplus', 'bot_verified', 'manual')",
            name=op.f("ck_unit_people_source_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name=op.f("fk_unit_people_person_id_people"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["units.id"], name=op.f("fk_unit_people_unit_id_units"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("unit_id", "person_id", "role", name=op.f("pk_unit_people")),
    )
    op.create_index(op.f("ix_unit_people_person_id"), "unit_people", ["person_id"], unique=False)
    op.create_table(
        "debt_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("concept", sa.String(length=200), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("concept_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("balance_due", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("accumulated", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["debt_snapshots.id"],
            name=op.f("fk_debt_lines_snapshot_id_debt_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_debt_lines")),
    )
    op.create_index(op.f("ix_debt_lines_snapshot_id"), "debt_lines", ["snapshot_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_debt_lines_snapshot_id"), table_name="debt_lines")
    op.drop_table("debt_lines")
    op.drop_index(op.f("ix_unit_people_person_id"), table_name="unit_people")
    op.drop_table("unit_people")
    op.drop_index("ix_debt_snapshots_unit_id_fetched_at", table_name="debt_snapshots")
    op.drop_table("debt_snapshots")
    op.drop_index("ix_coupons_unit_id_period", table_name="coupons")
    op.drop_table("coupons")
    op.drop_index(op.f("ix_units_building_id"), table_name="units")
    op.drop_table("units")
    op.drop_index(op.f("ix_phones_person_id"), table_name="phones")
    op.drop_table("phones")
    op.drop_table("sync_runs")
    op.drop_table("people")
    op.drop_table("buildings")
    op.drop_index(op.f("ix_bot_events_phone_e164"), table_name="bot_events")
    op.drop_index(op.f("ix_bot_events_created_at"), table_name="bot_events")
    op.drop_index(op.f("ix_bot_events_conversation_id"), table_name="bot_events")
    op.drop_table("bot_events")
