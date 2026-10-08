from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Sequence,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PersonRole(StrEnum):
    OWNER = "owner"
    TENANT = "tenant"


class DataSource(StrEnum):
    CONSORPLUS = "consorplus"
    BOT_VERIFIED = "bot_verified"
    MANUAL = "manual"


class SyncKind(StrEnum):
    NIGHTLY = "nightly"
    LIVE = "live"


class SyncJob(StrEnum):
    DEBT = "debt"
    ROSTER = "roster"
    CANARY = "canary"


class SyncStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class BuildingInfoCategory(StrEnum):
    REGLAMENTO = "reglamento"
    HORARIOS = "horarios"
    CONTACTOS = "contactos"
    EMERGENCIAS = "emergencias"
    OTROS = "otros"


def _str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK constraint instead of a native Postgres ENUM (easier to migrate)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda e: [member.value for member in e],
        validate_strings=True,
    )


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[int] = mapped_column(primary_key=True)
    consorplus_code: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    pilot: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # "Reclamos por el bot": the bot takes claims in this building (used from step 8.3 on).
    claims_bot_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    units: Mapped[list["Unit"]] = relationship(back_populates="building")
    infos: Mapped[list["BuildingInfo"]] = relationship(
        back_populates="building", cascade="all, delete-orphan"
    )

    def __str__(self) -> str:  # shown in the admin panel's selects
        return self.name


class BuildingInfo(Base):
    """Information about a building loaded in the admin panel (rules, hours, contacts...)."""

    __tablename__ = "building_infos"

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(
        ForeignKey("buildings.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    category: Mapped[BuildingInfoCategory] = mapped_column(
        _str_enum(BuildingInfoCategory, "category_valid")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    building: Mapped[Building] = relationship(back_populates="infos")


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("building_id", "consorplus_unit_value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(
        ForeignKey("buildings.id", ondelete="RESTRICT"), index=True
    )
    consorplus_unit_value: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(100))
    unit_type: Mapped[str | None] = mapped_column(String(50))  # ConsorPlus "Tipo Unidad"
    # ConsorPlus "Cód.Electrónico": Siro payment code, exactly 19 digits. Never logged.
    payment_code: Mapped[str | None] = mapped_column(String(19))
    owner_name: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    building: Mapped[Building] = relationship(back_populates="units")
    people: Mapped[list["UnitPerson"]] = relationship(
        back_populates="unit", cascade="all, delete-orphan"
    )
    debt_snapshots: Mapped[list["DebtSnapshot"]] = relationship(
        back_populates="unit",
        cascade="all, delete-orphan",
        order_by="DebtSnapshot.fetched_at",
    )


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    dni: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))

    units: Mapped[list["UnitPerson"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )
    phones: Mapped[list["Phone"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )


class UnitPerson(Base):
    """Association between a unit and a person, with the person's role in that unit."""

    __tablename__ = "unit_people"

    unit_id: Mapped[int] = mapped_column(
        ForeignKey("units.id", ondelete="CASCADE"), primary_key=True
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[PersonRole] = mapped_column(_str_enum(PersonRole, "role_valid"), primary_key=True)
    source: Mapped[DataSource] = mapped_column(_str_enum(DataSource, "source_valid"))

    unit: Mapped[Unit] = relationship(back_populates="people")
    person: Mapped[Person] = relationship(back_populates="units")


class Phone(Base):
    __tablename__ = "phones"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    # Unique constraint also creates the index used to identify people by phone.
    e164: Mapped[str] = mapped_column(String(20), unique=True)
    raw: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[DataSource] = mapped_column(_str_enum(DataSource, "source_valid"))
    verified: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # The area code was assumed (the number came without one). It still identifies.
    needs_review: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # ConsorPlus lists the number for another person too: it does not identify anyone.
    conflict: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    person: Mapped[Person] = relationship(back_populates="phones")


class DebtSnapshot(Base):
    __tablename__ = "debt_snapshots"
    __table_args__ = (Index("ix_debt_snapshots_unit_id_fetched_at", "unit_id", "fetched_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"))
    fetched_at: Mapped[datetime] = mapped_column(server_default=func.now())
    source: Mapped[SyncKind] = mapped_column(_str_enum(SyncKind, "source_valid"))
    total_amount: Mapped[Decimal]
    is_up_to_date: Mapped[bool]

    unit: Mapped[Unit] = relationship(back_populates="debt_snapshots")
    lines: Mapped[list["DebtLine"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", order_by="DebtLine.id"
    )


class DebtLine(Base):
    __tablename__ = "debt_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("debt_snapshots.id", ondelete="CASCADE"), index=True
    )
    concept: Mapped[str] = mapped_column(String(200))
    period: Mapped[str] = mapped_column(String(7))  # "MM/AAAA"
    # Empty cells in ConsorPlus are stored as NULL, never as 0.
    concept_amount: Mapped[Decimal | None]
    balance_due: Mapped[Decimal]
    accumulated: Mapped[Decimal | None]

    snapshot: Mapped[DebtSnapshot] = relationship(back_populates="lines")


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[SyncKind] = mapped_column(_str_enum(SyncKind, "kind_valid"))
    job: Mapped[SyncJob] = mapped_column(
        _str_enum(SyncJob, "job_valid"), default=SyncJob.DEBT, server_default=SyncJob.DEBT.value
    )
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    # NULL while the run is still in progress.
    status: Mapped[SyncStatus | None] = mapped_column(_str_enum(SyncStatus, "status_valid"))
    units_ok: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    units_failed: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    error_summary: Mapped[str | None] = mapped_column(Text)
    # Job-specific counters (never personal data).
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))


class BotEvent(Base):
    """Audit log of bot activity, also used for metrics."""

    __tablename__ = "bot_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(index=True)  # wa_conversations.id
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class VerificationCode(Base):
    """One-time code emailed to ONE owner of a unit to link an unknown phone to that owner.

    All the codes created by the same start share `verification_id` (one per owner email) and
    their attempt counter. Only a salted hash of the code is stored, never the code itself.
    """

    __tablename__ = "verification_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    verification_id: Mapped[str] = mapped_column(String(32), index=True)
    phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"))
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    used_at: Mapped[datetime | None]
    # Set when a newer start, a lockout or the use of a sibling code invalidates it.
    revoked_at: Mapped[datetime | None]


class VerificationRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class VerificationRequest(Base):
    """Request for an operator to link a phone to an owner (units without owner email)."""

    __tablename__ = "verification_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"), index=True)
    claimed_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[VerificationRequestStatus] = mapped_column(
        _str_enum(VerificationRequestStatus, "status_valid"),
        default=VerificationRequestStatus.PENDING,
        server_default=VerificationRequestStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    resolved_at: Mapped[datetime | None]
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    # The owner the phone was linked to (only when approved).
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))

    unit: Mapped[Unit] = relationship()


class BotSettings(Base):
    """Bot texts and office hours set in the admin panel: one row (id 1). An empty value
    falls back to the .env setting of the same name (see app.bot.bot_config)."""

    __tablename__ = "bot_settings"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    welcome_message: Mapped[str | None] = mapped_column(Text)
    office_hours_start: Mapped[str | None] = mapped_column(String(5))  # "HH:MM"
    office_hours_end: Mapped[str | None] = mapped_column(String(5))
    office_weekdays: Mapped[str | None] = mapped_column(String(20))  # "0,1,2,3,4", 0 = Monday
    out_of_hours_text: Mapped[str | None] = mapped_column(Text)
    autogestion_url: Mapped[str | None] = mapped_column(String(500))
    emergency_contact_text: Mapped[str | None] = mapped_column(Text)
    payment_code_how_to: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


# --- Amenities (SUM) and their reservations ---------------------------------------------------


class ReservationStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class ReservationSource(StrEnum):
    BOT = "bot"
    PANEL = "panel"


class Amenity(Base):
    """A bookable common space of a building (the SUM). Rules are checked by
    app.amenities.booking, for the panel and the bot alike."""

    __tablename__ = "amenities"

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(
        ForeignKey("buildings.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), default="SUM", server_default="SUM")
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    bot_booking_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # What the person must know: cleaning, music, cost... (shown as is).
    rules_text: Mapped[str | None] = mapped_column(Text)
    min_advance_hours: Mapped[int] = mapped_column(default=24, server_default="24")
    max_advance_days: Mapped[int] = mapped_column(default=60, server_default="60")
    # Confirmed reservations per unit and calendar month; None = no limit.
    max_per_unit_per_month: Mapped[int | None] = mapped_column(default=2, server_default="2")
    # Until how many hours before the start the person can cancel (the panel, always).
    cancel_until_hours: Mapped[int] = mapped_column(default=48, server_default="48")
    # Units whose last stored debt is not up to date cannot book.
    blocks_debtors: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    building: Mapped[Building] = relationship()
    slots: Mapped[list["AmenitySlot"]] = relationship(
        back_populates="amenity",
        cascade="all, delete-orphan",
        order_by="(AmenitySlot.weekday, AmenitySlot.start_time)",
    )


class AmenitySlot(Base):
    """A bookable time of a weekday. end_time <= start_time means it ends the next day
    (20:00 to 02:00). Removed slots that have reservations stay, inactive."""

    __tablename__ = "amenity_slots"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_valid"),
        CheckConstraint("start_time <> end_time", name="not_empty"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    amenity_id: Mapped[int] = mapped_column(
        ForeignKey("amenities.id", ondelete="CASCADE"), index=True
    )
    weekday: Mapped[int] = mapped_column(SmallInteger)  # 0 = Monday
    start_time: Mapped[time]
    end_time: Mapped[time]
    label: Mapped[str | None] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    amenity: Mapped[Amenity] = relationship(back_populates="slots")

    @property
    def overnight(self) -> bool:
        return self.end_time <= self.start_time


class Reservation(Base):
    """A booking of one slot on one date (the date the slot starts). At most one confirmed
    reservation per slot and date (partial unique index)."""

    __tablename__ = "reservations"
    __table_args__ = (
        Index(
            "uq_reservations_slot_id_date_confirmed",
            "slot_id",
            "date",
            unique=True,
            postgresql_where=text("status = 'confirmed'"),
        ),
        Index("ix_reservations_amenity_id_date", "amenity_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    amenity_id: Mapped[int] = mapped_column(ForeignKey("amenities.id", ondelete="CASCADE"))
    slot_id: Mapped[int] = mapped_column(ForeignKey("amenity_slots.id", ondelete="RESTRICT"))
    date: Mapped[date]
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id", ondelete="RESTRICT"), index=True)
    status: Mapped[ReservationStatus] = mapped_column(
        _str_enum(ReservationStatus, "status_valid"), default=ReservationStatus.CONFIRMED
    )
    source: Mapped[ReservationSource] = mapped_column(_str_enum(ReservationSource, "source_valid"))
    created_by_phone: Mapped[str | None] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    cancelled_at: Mapped[datetime | None]

    amenity: Mapped[Amenity] = relationship()
    slot: Mapped[AmenitySlot] = relationship()
    unit: Mapped[Unit] = relationship()


# --- WhatsApp Cloud API channel (app.whatsapp) -----------------------------------------------


class WaConversationStatus(StrEnum):
    BOT = "bot"  # the bot answers
    WAITING_HUMAN = "waiting_human"  # handed off by the bot, nobody took it yet
    HUMAN = "human"  # an operator has it
    RESOLVED = "resolved"  # back to the bot with the contact's next message
    PROVIDER = "provider"  # a provider's (app.claims.provider_flow): the bot never answers


class WaDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class WaAuthor(StrEnum):
    CONTACT = "contact"
    BOT = "bot"
    OPERATOR = "operator"
    SYSTEM = "system"


class WaMessageStatus(StrEnum):
    RECEIVED = "received"  # incoming
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class WaMediaStatus(StrEnum):
    STORED = "stored"
    TOO_LARGE = "too_large"
    TYPE_NOT_ALLOWED = "type_not_allowed"
    FAILED = "failed"


class WaContact(Base):
    """Someone who wrote to the studio's WhatsApp number."""

    __tablename__ = "wa_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # "+" + the WhatsApp id Meta gives (549... for Argentine mobiles).
    phone_e164: Mapped[str] = mapped_column(String(20), unique=True)
    wa_id: Mapped[str] = mapped_column(String(20))
    profile_name: Mapped[str | None] = mapped_column(String(200))
    # A contact of the panel's test chat (development only, app.whatsapp.simulator): nothing
    # sent to it reaches Meta.
    simulated: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    conversation: Mapped["WaConversation | None"] = relationship(back_populates="contact")


class WaConversation(Base):
    """The one conversation of a contact (a single thread, as in WhatsApp)."""

    __tablename__ = "wa_conversations"
    __table_args__ = (
        Index("ix_wa_conversations_status_last_inbound_at", "status", "last_inbound_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("wa_contacts.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[WaConversationStatus] = mapped_column(
        _str_enum(WaConversationStatus, "status_valid"),
        default=WaConversationStatus.BOT,
        server_default=WaConversationStatus.BOT.value,
    )
    assigned_to: Mapped[str | None] = mapped_column(String(100))  # panel user
    # The contact's last message (WhatsApp's 24-hour window for free text starts there).
    last_inbound_at: Mapped[datetime | None]
    unread_count: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # First message of the bot's current stretch (set when it comes back from resolved): the
    # agent's history starts there. None: the whole conversation.
    bot_since_message_id: Mapped[int | None] = mapped_column(BigInteger)
    # The bot's last handoff (also left as an internal note).
    handoff_reason: Mapped[str | None] = mapped_column(String(50))
    handoff_priority: Mapped[str | None] = mapped_column(String(20))
    handoff_summary: Mapped[str | None] = mapped_column(Text)
    handoff_labels: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    handed_off_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    contact: Mapped[WaContact] = relationship(back_populates="conversation")


class WaMessage(Base):
    """A message of a conversation: the contact's, the bot's, an operator's or an internal
    note. wa_message_id (WhatsApp's wamid) is unique: a webhook delivered twice is stored once."""

    __tablename__ = "wa_messages"
    __table_args__ = (
        Index("ix_wa_messages_conversation_id_id", "conversation_id", "id"),
        Index(
            "ix_wa_messages_unprocessed",
            "created_at",
            postgresql_where=text("direction = 'inbound' AND processed_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("wa_conversations.id", ondelete="CASCADE")
    )
    direction: Mapped[WaDirection] = mapped_column(_str_enum(WaDirection, "direction_valid"))
    author: Mapped[WaAuthor] = mapped_column(_str_enum(WaAuthor, "author_valid"))
    operator: Mapped[str | None] = mapped_column(String(100))  # panel user, if any
    # WhatsApp's type (text, interactive, button, image, document, audio, video, sticker,
    # location, contacts, reaction, template...) or "note" for internal notes.
    message_type: Mapped[str] = mapped_column(String(30))
    # The message's text (a caption for media; for an option tapped, its title).
    body: Mapped[str | None] = mapped_column(Text)
    # Titles of the options offered (buttons or list), in order.
    choices: Mapped[list[str] | None] = mapped_column(JSONB)
    # The payload of each of our buttons (aligned with choices), and of the one tapped (a
    # template's button.payload or an interactive option's id): app.claims.notify.
    button_payloads: Mapped[list[str] | None] = mapped_column(JSONB)
    reply_payload: Mapped[str | None] = mapped_column(String(200))
    wa_message_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    status: Mapped[WaMessageStatus | None] = mapped_column(
        _str_enum(WaMessageStatus, "status_valid")
    )
    status_at: Mapped[datetime | None]
    error_code: Mapped[int | None]
    error_text: Mapped[str | None] = mapped_column(Text)
    is_internal_note: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # Attachment (WhatsApp messages carry one at most). media_path is relative to
    # WHATSAPP_MEDIA_DIR; media_filename is the name the person sent (only shown, never used).
    media_id: Mapped[str | None] = mapped_column(String(100))
    media_mime: Mapped[str | None] = mapped_column(String(100))
    media_size: Mapped[int | None] = mapped_column(BigInteger)
    media_filename: Mapped[str | None] = mapped_column(String(255))
    media_path: Mapped[str | None] = mapped_column(String(255))
    media_status: Mapped[WaMediaStatus | None] = mapped_column(
        _str_enum(WaMediaStatus, "media_status_valid")
    )
    # Incoming messages: claimed by a worker, then processed (answered, skipped, or left
    # unanswered after a restart: processing_note says why).
    processing_started_at: Mapped[datetime | None]
    processed_at: Mapped[datetime | None]
    processing_note: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    conversation: Mapped[WaConversation] = relationship()


# --- Admin panel: users, WhatsApp templates and quick replies --------------------------------


class PanelRole(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"


class PanelUser(Base):
    """A user of the admin panel (one per employee). The .env user is a fixed rescue admin
    and is not stored here (app.admin.auth)."""

    __tablename__ = "panel_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)  # stored in lowercase
    display_name: Mapped[str] = mapped_column(String(100))
    # hash_password's "scrypt:..." (never the password).
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[PanelRole] = mapped_column(_str_enum(PanelRole, "role_valid"))
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # Raised when the password changes or the user is deactivated: open sessions end.
    session_version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    last_login_at: Mapped[datetime | None]
    # When the tour of "Conversaciones" was seen (or skipped): until then it opens by itself.
    tour_seen_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class WaTemplate(Base):
    """An approved WhatsApp template (without variables) the panel can send outside the
    24-hour window."""

    __tablename__ = "wa_templates"
    __table_args__ = (UniqueConstraint("name", "language"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))  # exactly as approved in Meta
    language: Mapped[str] = mapped_column(String(10), default="es_AR", server_default="es_AR")
    label: Mapped[str] = mapped_column(String(100))  # what the operators see
    body: Mapped[str] = mapped_column(Text)  # its text, stored in the conversation's history
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    def __str__(self) -> str:
        return self.label


class QuickReply(Base):
    """A text saved by an admin that operators insert in a reply with one click."""

    __tablename__ = "quick_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(60))
    content: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


# --- Claims: providers, kinds of problem and who attends each one in each building ----------


class ClaimScope(StrEnum):
    BUILDING = "building"  # the whole building: repeated claims are merged into one
    UNIT = "unit"  # one unit: each claim on its own, the provider gets the neighbor's data


class Provider(Base):
    """A company that attends problems (lifts, plumbing...). Global: one provider may attend
    several buildings. Deactivated instead of deleted."""

    __tablename__ = "providers"
    __table_args__ = (
        # One active provider per WhatsApp number (its messages will go to the providers' flow).
        Index(
            "uq_providers_whatsapp_e164_active",
            "whatsapp_e164",
            unique=True,
            postgresql_where=text("active AND whatsapp_e164 IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))  # the company
    contact_name: Mapped[str | None] = mapped_column(String(200))
    # Normalized like the roster's phones (app.bot.identity.to_e164): "+549...".
    whatsapp_e164: Mapped[str | None] = mapped_column(String(20))
    other_phone: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(254))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    assignments: Mapped[list["BuildingClaimCategory"]] = relationship(back_populates="provider")

    def __str__(self) -> str:
        return self.name


class ClaimCategory(Base):
    """A kind of problem people report (global). list_title and list_description are what
    WhatsApp shows in a list row (24 and 72 characters at most)."""

    __tablename__ = "claim_categories"
    __table_args__ = (
        CheckConstraint("char_length(list_title) BETWEEN 1 AND 24", name="list_title_length"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    list_title: Mapped[str] = mapped_column(String(24))
    list_description: Mapped[str | None] = mapped_column(String(72))
    scope: Mapped[ClaimScope] = mapped_column(_str_enum(ClaimScope, "scope_valid"))
    urgent: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # Asked by the bot right after the problem is chosen ("¿Hay alguien encerrado?").
    follow_up_question: Mapped[str | None] = mapped_column(String(300))
    # Said by the bot before anything else (gas).
    safety_text: Mapped[str | None] = mapped_column(Text)
    emergency_phone: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    def __str__(self) -> str:
        return self.name


class BuildingClaimCategory(Base):
    """A kind of problem in one building: whether people can report it and who attends it
    (no provider: the studio)."""

    __tablename__ = "building_claim_categories"
    __table_args__ = (UniqueConstraint("building_id", "category_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id", ondelete="CASCADE"))
    category_id: Mapped[int] = mapped_column(
        ForeignKey("claim_categories.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), index=True
    )
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    building: Mapped[Building] = relationship()
    category: Mapped[ClaimCategory] = relationship()
    provider: Mapped[Provider | None] = relationship(back_populates="assignments")


# --- Claims ---------------------------------------------------------------------------------


class ClaimStatus(StrEnum):
    PENDING_SEND = "pending_send"  # has a provider, still to be notified (step 8.5)
    SENT = "sent"  # the provider was notified
    ACKNOWLEDGED = "acknowledged"  # the provider confirmed
    STUDIO = "studio"  # the studio attends it (no provider)
    SOLVED = "solved"  # closed: solved
    CANCELLED = "cancelled"  # closed: cancelled


OPEN_CLAIM_STATUSES = (
    ClaimStatus.PENDING_SEND,
    ClaimStatus.SENT,
    ClaimStatus.ACKNOWLEDGED,
    ClaimStatus.STUDIO,
)
CLOSED_CLAIM_STATUSES = (ClaimStatus.SOLVED, ClaimStatus.CANCELLED)


class ClaimSource(StrEnum):
    BOT = "bot"
    PANEL = "panel"


class ClaimActor(StrEnum):
    BOT = "bot"
    PROVIDER = "provider"
    PANEL = "panel"
    SYSTEM = "system"


class ClaimEventKind(StrEnum):
    CREATED = "created"
    JOINED = "joined"  # a neighbor joined a repeated claim
    PROVIDER_CHANGED = "provider_changed"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    SOLVED = "solved"
    CANCELLED = "cancelled"
    NOTE = "note"
    NOTIFIED = "notified"  # a WhatsApp message to the provider or a neighbor
    NOTIFY_FAILED = "notify_failed"
    DECLINED = "declined"  # the provider said it cannot attend it
    PROVIDER_MESSAGE = "provider_message"  # what the provider wrote


class ClaimAttention(StrEnum):
    """Why a claim needs someone of the studio (an alert in the panel)."""

    DECLINED = "declined"
    SEND_FAILED = "send_failed"


CLAIM_NUMBER = Sequence("claims_number_seq", start=1001)


class Claim(Base):
    """A problem a neighbor reported. Created and changed only through app.claims.service.
    scope, urgent, the reporter and the provider are copied when it is created: later changes
    of the kind of problem, the person or the building's table do not change it."""

    __tablename__ = "claims"
    __table_args__ = (
        Index("ix_claims_building_id_category_id_status", "building_id", "category_id", "status"),
    )
    # number and created_at come back with the INSERT (no extra query to show #1001).
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    # The number people see (#1001...): taken only when a claim is inserted.
    number: Mapped[int] = mapped_column(
        CLAIM_NUMBER, server_default=CLAIM_NUMBER.next_value(), unique=True
    )
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id", ondelete="RESTRICT"))
    # Only for a kind of problem of one unit ("Todo el edificio" otherwise).
    unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("units.id", ondelete="SET NULL"), index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("claim_categories.id", ondelete="RESTRICT"), index=True
    )
    scope: Mapped[ClaimScope] = mapped_column(_str_enum(ClaimScope, "scope_valid"))
    urgent: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    description: Mapped[str] = mapped_column(Text)
    follow_up_answer: Mapped[str | None] = mapped_column(Text)
    reporter_person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id", ondelete="SET NULL")
    )
    reporter_name: Mapped[str | None] = mapped_column(String(200))
    reporter_phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
    # Whoever reported first: their unit, in any scope (to contact them).
    reporter_unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("units.id", ondelete="SET NULL")
    )
    # None: the studio attends it.
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[ClaimStatus] = mapped_column(_str_enum(ClaimStatus, "status_valid"), index=True)
    status_at: Mapped[datetime] = mapped_column(server_default=func.now())
    source: Mapped[ClaimSource] = mapped_column(_str_enum(ClaimSource, "source_valid"))
    created_by_user: Mapped[str | None] = mapped_column(String(100))  # panel user
    wa_conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("wa_conversations.id", ondelete="SET NULL")
    )
    # The same neighbor's (or unit's) claim of the same kind closed in the last 30 days.
    previous_claim_id: Mapped[int | None] = mapped_column(
        ForeignKey("claims.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    sent_at: Mapped[datetime | None]
    acknowledged_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    close_reason: Mapped[str | None] = mapped_column(Text)
    attention: Mapped[ClaimAttention | None] = mapped_column(
        _str_enum(ClaimAttention, "attention_valid")
    )
    # When the provider was last reminded to tap "Ya está solucionado" (once every 12 h).
    provider_nudged_at: Mapped[datetime | None]

    building: Mapped[Building] = relationship()
    unit: Mapped[Unit | None] = relationship(foreign_keys=[unit_id])
    reporter_unit: Mapped[Unit | None] = relationship(foreign_keys=[reporter_unit_id])
    category: Mapped[ClaimCategory] = relationship()
    provider: Mapped[Provider | None] = relationship()
    previous: Mapped["Claim | None"] = relationship(remote_side=[id])
    reporters: Mapped[list["ClaimReporter"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", order_by="ClaimReporter.id"
    )
    attachments: Mapped[list["ClaimAttachment"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", order_by="ClaimAttachment.id"
    )
    events: Mapped[list["ClaimEvent"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", order_by="ClaimEvent.id"
    )

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_CLAIM_STATUSES


class ClaimReporter(Base):
    """A neighbor who joined a repeated claim (the first one is in Claim.reporter_*)."""

    __tablename__ = "claim_reporters"
    __table_args__ = (
        Index(
            "uq_claim_reporters_claim_id_phone_e164",
            "claim_id",
            "phone_e164",
            unique=True,
            postgresql_where=text("phone_e164 IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    claim: Mapped[Claim] = relationship(back_populates="reporters")
    unit: Mapped[Unit | None] = relationship()


class ClaimAttachment(Base):
    """A photo or file of a claim: the attachment Conversaciones already stored (never copied;
    served by /admin/wa/media/{wa_message_id}). Added from step 8.3."""

    __tablename__ = "claim_attachments"
    __table_args__ = (UniqueConstraint("claim_id", "wa_message_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    wa_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("wa_messages.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    claim: Mapped[Claim] = relationship(back_populates="attachments")
    message: Mapped[WaMessage] = relationship()


class ClaimEvent(Base):
    """The history of a claim. text: the detail (a name, a reason, a note, a provider); what
    happened is said in Spanish by app.admin.labels."""

    __tablename__ = "claim_events"
    __table_args__ = (Index("ix_claim_events_claim_id_id", "claim_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    kind: Mapped[ClaimEventKind] = mapped_column(_str_enum(ClaimEventKind, "kind_valid"))
    actor: Mapped[ClaimActor] = mapped_column(_str_enum(ClaimActor, "actor_valid"))
    panel_user: Mapped[str | None] = mapped_column(String(100))
    text: Mapped[str | None] = mapped_column(Text)
    # The WhatsApp message of a "notified" event (its delivery status shows in the history).
    wa_message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("wa_messages.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    claim: Mapped[Claim] = relationship(back_populates="events")
    message: Mapped[WaMessage | None] = relationship()


class ClaimDraftStep(StrEnum):
    CHOOSE_UNIT = "choose_unit"
    CONFIRM_CATEGORY = "confirm_category"
    CHOOSE_CATEGORY = "choose_category"
    FOLLOW_UP = "follow_up"
    DESCRIPTION = "description"
    PHOTOS = "photos"
    CONFIRM = "confirm"
    # Unknown number: the agent verifies it as usual, then the flow resumes.
    WAITING_IDENTITY = "waiting_identity"


class ClaimDraft(Base):
    """A claim being reported by WhatsApp, step by step (app.claims.flow): one per phone.
    Expires 30 minutes after its last answer. Nothing here is a claim yet."""

    __tablename__ = "claim_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_e164: Mapped[str] = mapped_column(String(20), unique=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("wa_conversations.id", ondelete="SET NULL")
    )
    step: Mapped[ClaimDraftStep] = mapped_column(_str_enum(ClaimDraftStep, "step_valid"))
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"))
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("claim_categories.id", ondelete="CASCADE")
    )
    category_hint: Mapped[str | None] = mapped_column(String(200))
    # "Registrar reclamo" from the notice of a solved claim: the new one is linked to it.
    previous_claim_id: Mapped[int | None] = mapped_column(
        ForeignKey("claims.id", ondelete="SET NULL")
    )
    # Whether the current step was already offered again after an answer that fit no option.
    reprompted: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    category_page: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # Whether the kind's safety text already went out (it goes once, first).
    safety_sent: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    follow_up_answer: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    # wa_messages.id of the photos (stored attachments of Conversaciones).
    attachment_ids: Mapped[list[int]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # What the current step offered: {title: value}, to read the answer.
    options: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
