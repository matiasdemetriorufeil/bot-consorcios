from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
    conversation_id: Mapped[int | None] = mapped_column(index=True)  # Chatwoot conversation
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


class ChatwootProcessedMessage(Base):
    """Incoming Chatwoot messages already accepted by the webhook (idempotency: Chatwoot may
    deliver the same message twice)."""

    __tablename__ = "chatwoot_processed_messages"

    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    conversation_id: Mapped[int] = mapped_column(index=True)  # Chatwoot display id
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
