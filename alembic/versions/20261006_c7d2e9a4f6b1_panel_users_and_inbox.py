"""panel users and inbox

Users of the admin panel (admin | operator), the approved WhatsApp templates the inbox can
send outside the 24-hour window, and the quick replies.

Revision ID: c7d2e9a4f6b1
Revises: 57a8ea5a524c
Create Date: 2026-10-06 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d2e9a4f6b1"
down_revision: str | Sequence[str] | None = "57a8ea5a524c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "panel_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("session_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("role IN ('admin', 'operator')", name=op.f("ck_panel_users_role_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_panel_users")),
        sa.UniqueConstraint("username", name=op.f("uq_panel_users_username")),
    )
    op.create_table(
        "wa_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("language", sa.String(length=10), server_default="es_AR", nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wa_templates")),
        sa.UniqueConstraint("name", "language", name=op.f("uq_wa_templates_name_language")),
    )
    op.create_table(
        "quick_replies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=60), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quick_replies")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("quick_replies")
    op.drop_table("wa_templates")
    op.drop_table("panel_users")
