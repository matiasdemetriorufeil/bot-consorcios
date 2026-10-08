"""claims setup

Step 8.1 of the claims: providers (global, one active provider per WhatsApp number: partial
unique index), the kinds of problem (claim_categories, list_title of 24 characters at most as
WhatsApp lists allow) and, per building, which kinds are enabled and who attends them
(building_claim_categories: no provider = the studio). Plus buildings.claims_bot_enabled, not
used yet. No data here: scripts/seed_claim_categories.py loads the kinds of problem.

Revision ID: 1f4c7a2e9b63
Revises: e8b3f1a6c247
Create Date: 2026-10-07 21:24:12.224772

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1f4c7a2e9b63"
down_revision: str | Sequence[str] | None = "e8b3f1a6c247"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "claim_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("list_title", sa.String(length=24), nullable=False),
        sa.Column("list_description", sa.String(length=72), nullable=True),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("urgent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("follow_up_question", sa.String(length=300), nullable=True),
        sa.Column("safety_text", sa.Text(), nullable=True),
        sa.Column("emergency_phone", sa.String(length=100), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
        sa.CheckConstraint(
            "scope IN ('building', 'unit')", name=op.f("ck_claim_categories_scope_valid")
        ),
        sa.CheckConstraint(
            "char_length(list_title) BETWEEN 1 AND 24",
            name=op.f("ck_claim_categories_list_title_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim_categories")),
        sa.UniqueConstraint("name", name=op.f("uq_claim_categories_name")),
    )
    op.create_table(
        "providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("contact_name", sa.String(length=200), nullable=True),
        sa.Column("whatsapp_e164", sa.String(length=20), nullable=True),
        sa.Column("other_phone", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_providers")),
    )
    op.create_index(
        "uq_providers_whatsapp_e164_active",
        "providers",
        ["whatsapp_e164"],
        unique=True,
        postgresql_where=sa.text("active AND whatsapp_e164 IS NOT NULL"),
    )
    op.create_table(
        "building_claim_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("building_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["building_id"],
            ["buildings.id"],
            name=op.f("fk_building_claim_categories_building_id_buildings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["claim_categories.id"],
            name=op.f("fk_building_claim_categories_category_id_claim_categories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name=op.f("fk_building_claim_categories_provider_id_providers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_building_claim_categories")),
        sa.UniqueConstraint(
            "building_id",
            "category_id",
            name=op.f("uq_building_claim_categories_building_id_category_id"),
        ),
    )
    op.create_index(
        op.f("ix_building_claim_categories_category_id"),
        "building_claim_categories",
        ["category_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_building_claim_categories_provider_id"),
        "building_claim_categories",
        ["provider_id"],
        unique=False,
    )
    op.add_column(
        "buildings",
        sa.Column(
            "claims_bot_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("buildings", "claims_bot_enabled")
    op.drop_index(
        op.f("ix_building_claim_categories_provider_id"), table_name="building_claim_categories"
    )
    op.drop_index(
        op.f("ix_building_claim_categories_category_id"), table_name="building_claim_categories"
    )
    op.drop_table("building_claim_categories")
    op.drop_index(
        "uq_providers_whatsapp_e164_active",
        table_name="providers",
        postgresql_where=sa.text("active AND whatsapp_e164 IS NOT NULL"),
    )
    op.drop_table("providers")
    op.drop_table("claim_categories")
