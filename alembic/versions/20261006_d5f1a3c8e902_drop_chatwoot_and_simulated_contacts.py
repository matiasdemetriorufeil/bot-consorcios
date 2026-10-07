"""drop chatwoot and simulated contacts

Chatwoot is gone (WhatsApp is the only channel): its idempotency table goes too. Contacts of
the panel's test chat (development only) are marked simulated: nothing sent to them reaches
Meta (app.whatsapp.simulator).

Revision ID: d5f1a3c8e902
Revises: c7d2e9a4f6b1
Create Date: 2026-10-06 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f1a3c8e902"
down_revision: str | Sequence[str] | None = "c7d2e9a4f6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "wa_contacts",
        sa.Column("simulated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.drop_index(
        op.f("ix_chatwoot_processed_messages_conversation_id"),
        table_name="chatwoot_processed_messages",
    )
    op.drop_table("chatwoot_processed_messages")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "chatwoot_processed_messages",
        sa.Column("message_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_chatwoot_processed_messages")),
    )
    op.create_index(
        op.f("ix_chatwoot_processed_messages_conversation_id"),
        "chatwoot_processed_messages",
        ["conversation_id"],
        unique=False,
    )
    op.drop_column("wa_contacts", "simulated")
