"""claim drafts

Step 8.3: a claim being reported by WhatsApp, step by step (app.claims.flow): one per phone,
it expires 30 minutes after its last answer.

Revision ID: 5c1e8a3d7f20
Revises: 3b9d2f6c8a14
Create Date: 2026-10-08 09:55:20.226885

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5c1e8a3d7f20"
down_revision: str | Sequence[str] | None = "3b9d2f6c8a14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "claim_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("step", sa.String(length=20), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("category_hint", sa.String(length=200), nullable=True),
        sa.Column("category_page", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("safety_sent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("follow_up_answer", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "attachment_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "options",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "step IN ('choose_unit', 'confirm_category', 'choose_category', 'follow_up', "
            "'description', 'photos', 'confirm', 'waiting_identity')",
            name=op.f("ck_claim_drafts_step_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["claim_categories.id"],
            name=op.f("fk_claim_drafts_category_id_claim_categories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["wa_conversations.id"],
            name=op.f("fk_claim_drafts_conversation_id_wa_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["units.id"],
            name=op.f("fk_claim_drafts_unit_id_units"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim_drafts")),
        sa.UniqueConstraint("phone_e164", name=op.f("uq_claim_drafts_phone_e164")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("claim_drafts")
