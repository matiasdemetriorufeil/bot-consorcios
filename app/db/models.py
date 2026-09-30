from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
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


class SyncStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


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


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("building_id", "consorplus_unit_value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(
        ForeignKey("buildings.id", ondelete="RESTRICT"), index=True
    )
    consorplus_unit_value: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(100))
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
    coupons: Mapped[list["Coupon"]] = relationship(
        back_populates="unit", cascade="all, delete-orphan"
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
    needs_review: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
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
    concept_amount: Mapped[Decimal]
    balance_due: Mapped[Decimal]
    accumulated: Mapped[Decimal]

    snapshot: Mapped[DebtSnapshot] = relationship(back_populates="lines")


class Coupon(Base):
    __tablename__ = "coupons"
    # Upsert key for the sync. Its index also serves lookups by (unit_id) and (unit_id, period).
    __table_args__ = (UniqueConstraint("unit_id", "period", "coupon_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String(7))  # "MM/AAAA"
    coupon_id: Mapped[str] = mapped_column(String(50))  # "Id. cupón" de Siro
    amount_1: Mapped[Decimal]
    due_date_1: Mapped[date]
    amount_2: Mapped[Decimal | None]
    due_date_2: Mapped[date | None]
    detail_url: Mapped[str | None] = mapped_column(String(500))
    fetched_at: Mapped[datetime] = mapped_column(server_default=func.now())

    unit: Mapped[Unit] = relationship(back_populates="coupons")


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[SyncKind] = mapped_column(_str_enum(SyncKind, "kind_valid"))
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    # NULL while the run is still in progress.
    status: Mapped[SyncStatus | None] = mapped_column(_str_enum(SyncStatus, "status_valid"))
    units_ok: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    units_failed: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    error_summary: Mapped[str | None] = mapped_column(Text)


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
