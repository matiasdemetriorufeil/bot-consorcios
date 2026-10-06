"""whatsapp channel

The WhatsApp Cloud API channel (CHANNEL=whatsapp): contacts, their one conversation each
(bot / waiting_human / human / resolved) and its messages, unique by WhatsApp id.

Revision ID: 57a8ea5a524c
Revises: a9c3e5f71b28
Create Date: 2026-10-06 17:15:27.138057

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "57a8ea5a524c"
down_revision: str | Sequence[str] | None = "a9c3e5f71b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wa_contacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column("wa_id", sa.String(length=20), nullable=False),
        sa.Column("profile_name", sa.String(length=200), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wa_contacts")),
        sa.UniqueConstraint("phone_e164", name=op.f("uq_wa_contacts_phone_e164")),
    )
    op.create_table(
        "wa_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="bot",
            nullable=False,
        ),
        sa.Column("assigned_to", sa.String(length=100), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unread_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("bot_since_message_id", sa.BigInteger(), nullable=True),
        sa.Column("handoff_reason", sa.String(length=50), nullable=True),
        sa.Column("handoff_priority", sa.String(length=20), nullable=True),
        sa.Column("handoff_summary", sa.Text(), nullable=True),
        sa.Column(
            "handoff_labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("handed_off_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('bot', 'waiting_human', 'human', 'resolved')",
            name=op.f("ck_wa_conversations_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["wa_contacts.id"],
            name=op.f("fk_wa_conversations_contact_id_wa_contacts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wa_conversations")),
        sa.UniqueConstraint("contact_id", name=op.f("uq_wa_conversations_contact_id")),
    )
    op.create_index(
        "ix_wa_conversations_status_last_inbound_at",
        "wa_conversations",
        ["status", "last_inbound_at"],
        unique=False,
    )
    op.create_table(
        "wa_messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column(
            "direction",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "author",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column("operator", sa.String(length=100), nullable=True),
        sa.Column("message_type", sa.String(length=30), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("choices", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("wa_message_id", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=True,
        ),
        sa.Column("status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.Integer(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "is_internal_note", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("media_id", sa.String(length=100), nullable=True),
        sa.Column("media_mime", sa.String(length=100), nullable=True),
        sa.Column("media_size", sa.BigInteger(), nullable=True),
        sa.Column("media_filename", sa.String(length=255), nullable=True),
        sa.Column("media_path", sa.String(length=255), nullable=True),
        sa.Column(
            "media_status",
            sa.String(length=20),
            nullable=True,
        ),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_note", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "author IN ('contact', 'bot', 'operator', 'system')",
            name=op.f("ck_wa_messages_author_valid"),
        ),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound')", name=op.f("ck_wa_messages_direction_valid")
        ),
        sa.CheckConstraint(
            "media_status IN ('stored', 'too_large', 'type_not_allowed', 'failed')",
            name=op.f("ck_wa_messages_media_status_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('received', 'sent', 'delivered', 'read', 'failed')",
            name=op.f("ck_wa_messages_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["wa_conversations.id"],
            name=op.f("fk_wa_messages_conversation_id_wa_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wa_messages")),
        sa.UniqueConstraint("wa_message_id", name=op.f("uq_wa_messages_wa_message_id")),
    )
    op.create_index(
        "ix_wa_messages_conversation_id_id", "wa_messages", ["conversation_id", "id"], unique=False
    )
    op.create_index(
        "ix_wa_messages_unprocessed",
        "wa_messages",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("direction = 'inbound' AND processed_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_wa_messages_unprocessed",
        table_name="wa_messages",
        postgresql_where=sa.text("direction = 'inbound' AND processed_at IS NULL"),
    )
    op.drop_index("ix_wa_messages_conversation_id_id", table_name="wa_messages")
    op.drop_table("wa_messages")
    op.drop_index("ix_wa_conversations_status_last_inbound_at", table_name="wa_conversations")
    op.drop_table("wa_conversations")
    op.drop_table("wa_contacts")
