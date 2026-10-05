"""admin panel

Information about each building (loaded in the admin panel) and the bot settings row whose
values win over the .env ones when not empty. The row is created empty.

Revision ID: f2b8d6a41c93
Revises: e4a1c7b9d2f5
Create Date: 2026-10-05 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b8d6a41c93"
down_revision: str | Sequence[str] | None = "e4a1c7b9d2f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "building_infos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("building_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["building_id"],
            ["buildings.id"],
            name=op.f("fk_building_infos_building_id_buildings"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "category IN ('reglamento', 'horarios', 'contactos', 'emergencias', 'otros')",
            name=op.f("ck_building_infos_category_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_building_infos")),
    )
    op.create_index(op.f("ix_building_infos_building_id"), "building_infos", ["building_id"])

    op.create_table(
        "bot_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("welcome_message", sa.Text(), nullable=True),
        sa.Column("office_hours_start", sa.String(length=5), nullable=True),
        sa.Column("office_hours_end", sa.String(length=5), nullable=True),
        sa.Column("office_weekdays", sa.String(length=20), nullable=True),
        sa.Column("out_of_hours_text", sa.Text(), nullable=True),
        sa.Column("autogestion_url", sa.String(length=500), nullable=True),
        sa.Column("emergency_contact_text", sa.Text(), nullable=True),
        sa.Column("payment_code_how_to", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_bot_settings_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bot_settings")),
    )
    op.execute("INSERT INTO bot_settings (id) VALUES (1)")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("bot_settings")
    op.drop_index(op.f("ix_building_infos_building_id"), table_name="building_infos")
    op.drop_table("building_infos")
