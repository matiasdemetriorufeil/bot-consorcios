"""claims

Step 8.2: the claims themselves (claims, numbered from 1001 by the sequence
claims_number_seq, taken only when a claim is inserted), the neighbors who joined a repeated
claim (claim_reporters, one per phone), their photos (claim_attachments: a reference to the
WhatsApp attachment Conversaciones already stored) and their history (claim_events).

Revision ID: 3b9d2f6c8a14
Revises: 1f4c7a2e9b63
Create Date: 2026-10-07 22:04:08.137572

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b9d2f6c8a14"
down_revision: str | Sequence[str] | None = "1f4c7a2e9b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(sa.schema.CreateSequence(sa.Sequence("claims_number_seq", start=1001)))
    op.create_table(
        "claims",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "number",
            sa.Integer(),
            server_default=sa.text("nextval('claims_number_seq')"),
            nullable=False,
        ),
        sa.Column("building_id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("urgent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("follow_up_answer", sa.Text(), nullable=True),
        sa.Column("reporter_person_id", sa.Integer(), nullable=True),
        sa.Column("reporter_name", sa.String(length=200), nullable=True),
        sa.Column("reporter_phone_e164", sa.String(length=20), nullable=True),
        sa.Column("reporter_unit_id", sa.Integer(), nullable=True),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "status_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("created_by_user", sa.String(length=100), nullable=True),
        sa.Column("wa_conversation_id", sa.Integer(), nullable=True),
        sa.Column("previous_claim_id", sa.Integer(), nullable=True),
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
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("scope IN ('building', 'unit')", name=op.f("ck_claims_scope_valid")),
        sa.CheckConstraint("source IN ('bot', 'panel')", name=op.f("ck_claims_source_valid")),
        sa.CheckConstraint(
            "status IN ('pending_send', 'sent', 'acknowledged', 'studio', 'solved', 'cancelled')",
            name=op.f("ck_claims_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["building_id"],
            ["buildings.id"],
            name=op.f("fk_claims_building_id_buildings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["claim_categories.id"],
            name=op.f("fk_claims_category_id_claim_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_claim_id"],
            ["claims.id"],
            name=op.f("fk_claims_previous_claim_id_claims"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name=op.f("fk_claims_provider_id_providers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_person_id"],
            ["people.id"],
            name=op.f("fk_claims_reporter_person_id_people"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_unit_id"],
            ["units.id"],
            name=op.f("fk_claims_reporter_unit_id_units"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["units.id"], name=op.f("fk_claims_unit_id_units"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["wa_conversation_id"],
            ["wa_conversations.id"],
            name=op.f("fk_claims_wa_conversation_id_wa_conversations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claims")),
        sa.UniqueConstraint("number", name=op.f("uq_claims_number")),
    )
    op.create_index(
        "ix_claims_building_id_category_id_status",
        "claims",
        ["building_id", "category_id", "status"],
        unique=False,
    )
    op.create_index(op.f("ix_claims_category_id"), "claims", ["category_id"], unique=False)
    op.create_index(op.f("ix_claims_created_at"), "claims", ["created_at"], unique=False)
    op.create_index(op.f("ix_claims_provider_id"), "claims", ["provider_id"], unique=False)
    op.create_index(
        op.f("ix_claims_reporter_phone_e164"), "claims", ["reporter_phone_e164"], unique=False
    )
    op.create_index(op.f("ix_claims_status"), "claims", ["status"], unique=False)
    op.create_index(op.f("ix_claims_unit_id"), "claims", ["unit_id"], unique=False)
    op.create_table(
        "claim_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("wa_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name=op.f("fk_claim_attachments_claim_id_claims"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["wa_message_id"],
            ["wa_messages.id"],
            name=op.f("fk_claim_attachments_wa_message_id_wa_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim_attachments")),
        sa.UniqueConstraint(
            "claim_id", "wa_message_id", name=op.f("uq_claim_attachments_claim_id_wa_message_id")
        ),
    )
    op.create_index(
        op.f("ix_claim_attachments_wa_message_id"),
        "claim_attachments",
        ["wa_message_id"],
        unique=False,
    )
    op.create_table(
        "claim_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=20), nullable=False),
        sa.Column("panel_user", sa.String(length=100), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor IN ('bot', 'provider', 'panel', 'system')",
            name=op.f("ck_claim_events_actor_valid"),
        ),
        sa.CheckConstraint(
            "kind IN ('created', 'joined', 'provider_changed', 'sent', 'acknowledged', "
            "'solved', 'cancelled', 'note')",
            name=op.f("ck_claim_events_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name=op.f("fk_claim_events_claim_id_claims"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim_events")),
    )
    op.create_index("ix_claim_events_claim_id_id", "claim_events", ["claim_id", "id"], unique=False)
    op.create_table(
        "claim_reporters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=True),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name=op.f("fk_claim_reporters_claim_id_claims"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name=op.f("fk_claim_reporters_person_id_people"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["units.id"],
            name=op.f("fk_claim_reporters_unit_id_units"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim_reporters")),
    )
    op.create_index(
        op.f("ix_claim_reporters_claim_id"), "claim_reporters", ["claim_id"], unique=False
    )
    op.create_index(
        op.f("ix_claim_reporters_phone_e164"), "claim_reporters", ["phone_e164"], unique=False
    )
    op.create_index(
        "uq_claim_reporters_claim_id_phone_e164",
        "claim_reporters",
        ["claim_id", "phone_e164"],
        unique=True,
        postgresql_where=sa.text("phone_e164 IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_claim_reporters_claim_id_phone_e164",
        table_name="claim_reporters",
        postgresql_where=sa.text("phone_e164 IS NOT NULL"),
    )
    op.drop_index(op.f("ix_claim_reporters_phone_e164"), table_name="claim_reporters")
    op.drop_index(op.f("ix_claim_reporters_claim_id"), table_name="claim_reporters")
    op.drop_table("claim_reporters")
    op.drop_index("ix_claim_events_claim_id_id", table_name="claim_events")
    op.drop_table("claim_events")
    op.drop_index(op.f("ix_claim_attachments_wa_message_id"), table_name="claim_attachments")
    op.drop_table("claim_attachments")
    op.drop_index(op.f("ix_claims_unit_id"), table_name="claims")
    op.drop_index(op.f("ix_claims_status"), table_name="claims")
    op.drop_index(op.f("ix_claims_reporter_phone_e164"), table_name="claims")
    op.drop_index(op.f("ix_claims_provider_id"), table_name="claims")
    op.drop_index(op.f("ix_claims_created_at"), table_name="claims")
    op.drop_index(op.f("ix_claims_category_id"), table_name="claims")
    op.drop_index("ix_claims_building_id_category_id_status", table_name="claims")
    op.drop_table("claims")
    op.execute(sa.schema.DropSequence(sa.Sequence("claims_number_seq")))
