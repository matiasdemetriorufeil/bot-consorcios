"""chatwoot processed messages

Ids of the incoming Chatwoot messages the webhook already accepted, so that a message
delivered twice is answered once.

Revision ID: e4a1c7b9d2f5
Revises: c3f5a8e27d14
Create Date: 2026-10-01 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4a1c7b9d2f5"
down_revision: str | Sequence[str] | None = "c3f5a8e27d14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
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


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_chatwoot_processed_messages_conversation_id"),
        table_name="chatwoot_processed_messages",
    )
    op.drop_table("chatwoot_processed_messages")
