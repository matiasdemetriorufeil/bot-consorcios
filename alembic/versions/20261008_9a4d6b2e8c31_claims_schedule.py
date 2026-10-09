"""claims schedule

Step 8.6: the providers' hours, reminders and the studio's alerts. claim_settings (one row,
created here with its defaults: Monday to Saturday 8 to 20, urgent ones at any time, reminder
4 h / 30 min, alert 8 h / 60 min, 3 days without a solution); claims.send_after (out of hours:
when it goes) and reminded_at; claim_events.provider_id (for the metrics); new claim event
kinds and alerts.

Revision ID: 9a4d6b2e8c31
Revises: 7e2a9c4b1d56
Create Date: 2026-10-08 20:01:39.740364

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a4d6b2e8c31"
down_revision: str | Sequence[str] | None = "7e2a9c4b1d56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_KINDS = (
    "'created', 'joined', 'provider_changed', 'sent', 'acknowledged', 'solved', 'cancelled', "
    "'note', 'notified', 'notify_failed', 'declined', 'provider_message'"
)
NEW_KINDS = f"{OLD_KINDS}, 'scheduled', 'reminded', 'alert'"
OLD_ATTENTION = "'declined', 'send_failed'"
NEW_ATTENTION = f"{OLD_ATTENTION}, 'no_ack', 'stale'"


def _check(table: str, name: str, column: str, values: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, f"{column} IN ({values})")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "claim_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column(
            "provider_weekdays", sa.String(length=20), server_default="0,1,2,3,4,5", nullable=False
        ),
        sa.Column(
            "provider_hours_start", sa.String(length=5), server_default="08:00", nullable=False
        ),
        sa.Column(
            "provider_hours_end", sa.String(length=5), server_default="20:00", nullable=False
        ),
        sa.Column("holidays", sa.Text(), nullable=True),
        sa.Column("urgent_any_time", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("reminder_hours", sa.Integer(), server_default=sa.text("4"), nullable=False),
        sa.Column(
            "reminder_urgent_minutes", sa.Integer(), server_default=sa.text("30"), nullable=False
        ),
        sa.Column("alert_hours", sa.Integer(), server_default=sa.text("8"), nullable=False),
        sa.Column(
            "alert_urgent_minutes", sa.Integer(), server_default=sa.text("60"), nullable=False
        ),
        sa.Column("stale_days", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_claim_settings_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim_settings")),
    )
    op.add_column("claim_events", sa.Column("provider_id", sa.Integer(), nullable=True))
    op.create_index(
        op.f("ix_claim_events_provider_id"), "claim_events", ["provider_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_claim_events_provider_id_providers"),
        "claim_events",
        "providers",
        ["provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("claims", sa.Column("send_after", sa.DateTime(timezone=True), nullable=True))
    op.add_column("claims", sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_claims_send_after"), "claims", ["send_after"], unique=False)
    op.execute("INSERT INTO claim_settings (id) VALUES (1)")
    _check("claim_events", "kind_valid", "kind", NEW_KINDS)
    _check("claims", "attention_valid", "attention", NEW_ATTENTION)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE claims SET attention = NULL WHERE attention IN ('no_ack', 'stale')")
    op.execute("DELETE FROM claim_events WHERE kind IN ('scheduled', 'reminded', 'alert')")
    _check("claims", "attention_valid", "attention", OLD_ATTENTION)
    _check("claim_events", "kind_valid", "kind", OLD_KINDS)
    op.drop_index(op.f("ix_claims_send_after"), table_name="claims")
    op.drop_column("claims", "reminded_at")
    op.drop_column("claims", "send_after")
    op.drop_constraint(
        op.f("fk_claim_events_provider_id_providers"), "claim_events", type_="foreignkey"
    )
    op.drop_index(op.f("ix_claim_events_provider_id"), table_name="claim_events")
    op.drop_column("claim_events", "provider_id")
    op.drop_table("claim_settings")
