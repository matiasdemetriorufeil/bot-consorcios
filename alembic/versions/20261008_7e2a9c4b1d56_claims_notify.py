"""claims notify

Step 8.5: the provider gets the claim by WhatsApp and answers with buttons; the neighbors are
told. wa_messages keeps the payload of the button tapped (reply_payload) and of ours
(button_payloads); claim_events links a notice to its WhatsApp message (delivery status);
claims gets attention (an alert in the panel) and provider_nudged_at; claim_drafts gets the
claim a new one is linked to and whether the step was offered again. New claim event kinds and
the conversation state "provider" (the bot never answers it).

Revision ID: 7e2a9c4b1d56
Revises: 5c1e8a3d7f20
Create Date: 2026-10-08 13:43:13.630165

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7e2a9c4b1d56"
down_revision: str | Sequence[str] | None = "5c1e8a3d7f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "'created', 'joined', 'provider_changed', 'sent', 'acknowledged', 'solved', " \
    "'cancelled', 'note'"  # fmt: skip
NEW_KINDS = f"{OLD_KINDS}, 'notified', 'notify_failed', 'declined', 'provider_message'"
OLD_STATUSES = "'bot', 'waiting_human', 'human', 'resolved'"
NEW_STATUSES = f"{OLD_STATUSES}, 'provider'"


def _check(table: str, name: str, column: str, values: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, f"{column} IN ({values})")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("claim_drafts", sa.Column("previous_claim_id", sa.Integer(), nullable=True))
    op.add_column(
        "claim_drafts",
        sa.Column("reprompted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_claim_drafts_previous_claim_id_claims"),
        "claim_drafts",
        "claims",
        ["previous_claim_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("claim_events", sa.Column("wa_message_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_claim_events_wa_message_id_wa_messages"),
        "claim_events",
        "wa_messages",
        ["wa_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("claims", sa.Column("attention", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        op.f("ck_claims_attention_valid"), "claims", "attention IN ('declined', 'send_failed')"
    )
    op.add_column(
        "claims", sa.Column("provider_nudged_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "wa_messages",
        sa.Column("button_payloads", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("wa_messages", sa.Column("reply_payload", sa.String(length=200), nullable=True))
    _check("claim_events", "kind_valid", "kind", NEW_KINDS)
    _check("wa_conversations", "status_valid", "status", NEW_STATUSES)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE wa_conversations SET status = 'human' WHERE status = 'provider'")
    op.execute(
        "DELETE FROM claim_events WHERE kind IN "
        "('notified', 'notify_failed', 'declined', 'provider_message')"
    )
    _check("wa_conversations", "status_valid", "status", OLD_STATUSES)
    _check("claim_events", "kind_valid", "kind", OLD_KINDS)
    op.drop_column("wa_messages", "reply_payload")
    op.drop_column("wa_messages", "button_payloads")
    op.drop_column("claims", "provider_nudged_at")
    op.drop_constraint(op.f("ck_claims_attention_valid"), "claims", type_="check")
    op.drop_column("claims", "attention")
    op.drop_constraint(
        op.f("fk_claim_events_wa_message_id_wa_messages"), "claim_events", type_="foreignkey"
    )
    op.drop_column("claim_events", "wa_message_id")
    op.drop_constraint(
        op.f("fk_claim_drafts_previous_claim_id_claims"), "claim_drafts", type_="foreignkey"
    )
    op.drop_column("claim_drafts", "reprompted")
    op.drop_column("claim_drafts", "previous_claim_id")
