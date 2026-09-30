"""Identity and verification against the Postgres test database. All data here is invented."""

import json
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.bot.identity import (
    MAX_CODE_ATTEMPTS,
    MAX_STARTS_PER_DAY,
    ConfirmStatus,
    IdentityError,
    RequestStatus,
    StartStatus,
    approve_verification_request,
    can_view_unit_finance,
    confirm_email_code,
    identify_by_phone,
    reject_verification_request,
    request_operator_verification,
    start_email_verification,
)
from app.consorplus.models import RosterContact, RosterRow
from app.db.models import (
    BotEvent,
    DataSource,
    PersonRole,
    Phone,
    UnitPerson,
    VerificationCode,
    VerificationRequest,
    VerificationRequestStatus,
)
from app.notify.email import EmailError
from app.sync.roster import RosterReport, sync_building_rows
from tests.bot import factories as f

JUAN_PHONE = "+5493515550101"
UNKNOWN_PHONE = "+5493515550999"


@dataclass
class FakeSender:
    sent: list[tuple[str, str]] = field(default_factory=list)
    failing: set[str] = field(default_factory=set)

    def send_verification_code(self, to: str, code: str, valid_minutes: int) -> None:
        if to in self.failing:
            raise EmailError("envío SMTP falló: SMTPRecipientsRefused")
        self.sent.append((to, code))

    def code_for(self, email: str) -> str:
        return next(code for to, code in reversed(self.sent) if to == email)


@dataclass
class World:
    ids: dict[str, int]


@pytest.fixture
def world(db_session: Session) -> World:
    """Two buildings. Juan owns two units and rents a third; 4C has two owners with email
    and a tenant with email; 7A has no owner email (only its tenant has one)."""
    s = db_session
    rodas = f.building(s, "045 RODAS II")
    sol = f.building(s, "050 TORRE DEL SOL")
    u4c = f.unit(s, rodas, "04-C")
    u5b = f.unit(s, rodas, "5º B")
    u7a = f.unit(s, rodas, "07-A")
    sol_pb = f.unit(s, sol, "PB A")
    old = f.unit(s, sol, "03-C", active=False)

    juan = f.person(s, "PEREZ JUAN", email="juan@example.com", phone=JUAN_PHONE)
    ana = f.person(s, "LOPEZ ANA", email="ana.lopez@example.org")
    marta = f.person(s, "SOSA MARTA", email="marta@example.net", phone="+5493515550303")
    ines = f.person(s, "DIAZ INES", email="ines.inquilina@example.com", phone="+5493515550404")
    raul = f.person(s, "RUIZ RAUL")  # owner without email
    conflict = f.person(s, "GARCIA LUIS", phone="+5493515550505", conflict=True)

    f.link(s, u4c, juan)
    f.link(s, u4c, ana)
    f.link(s, u4c, ines, PersonRole.TENANT)
    f.link(s, sol_pb, juan)
    f.link(s, old, juan)
    f.link(s, u5b, juan, PersonRole.TENANT)
    f.link(s, u5b, marta)
    f.link(s, u7a, raul)
    f.link(s, u7a, ines, PersonRole.TENANT)
    f.link(s, u5b, conflict)
    return World(
        {
            "u4c": u4c.id,
            "u5b": u5b.id,
            "u7a": u7a.id,
            "sol_pb": sol_pb.id,
            "old": old.id,
            "juan": juan.id,
            "ana": ana.id,
            "marta": marta.id,
            "ines": ines.id,
            "raul": raul.id,
            "conflict": conflict.id,
        }
    )


def events(session: Session) -> list[BotEvent]:
    return list(session.scalars(select(BotEvent).order_by(BotEvent.id)))


def phone_row(session: Session, e164: str) -> Phone | None:
    session.expire_all()
    return session.scalar(select(Phone).where(Phone.e164 == e164))


# --- identify_by_phone ------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["0351 15 555-0101", "+54 9 351 555-0101", "351 5550101", "+5493515550101", "03515550101"],
)
def test_identify_accepts_phone_formats(db_session: Session, world: World, raw: str) -> None:
    identity = identify_by_phone(db_session, raw)

    assert identity.known
    assert identity.person_id == world.ids["juan"]


def test_person_with_several_units_and_roles(db_session: Session, world: World) -> None:
    identity = identify_by_phone(db_session, JUAN_PHONE)

    by_unit = {(u.unit_id, u.role) for u in identity.units}
    assert by_unit == {
        (world.ids["u4c"], PersonRole.OWNER),
        (world.ids["sol_pb"], PersonRole.OWNER),
        (world.ids["u5b"], PersonRole.TENANT),
    }  # the inactive unit is left out
    assert "PEREZ JUAN" not in repr(identity)


@pytest.mark.parametrize("raw", [UNKNOWN_PHONE, "hola", "", "5550101"])
def test_unknown_or_invalid_phone(db_session: Session, world: World, raw: str) -> None:
    identity = identify_by_phone(db_session, raw)

    assert not identity.known
    assert identity.units == ()


def test_assumed_area_code_still_identifies(db_session: Session, world: World) -> None:
    phone = "+5493515550606"
    owner = f.person(db_session, "MOLINA PAULA", phone=phone, needs_review=True)
    db_session.add(
        UnitPerson(
            unit_id=world.ids["u7a"],
            person_id=owner.id,
            role=PersonRole.OWNER,
            source=DataSource.CONSORPLUS,
        )
    )
    db_session.flush()

    assert identify_by_phone(db_session, phone).person_id == owner.id
    assert can_view_unit_finance(db_session, phone, world.ids["u7a"])


def test_phone_in_conflict_is_unknown(db_session: Session, world: World) -> None:
    assert not identify_by_phone(db_session, "+5493515550505").known
    assert not can_view_unit_finance(db_session, "+5493515550505", world.ids["u5b"])


# --- can_view_unit_finance --------------------------------------------------------------


def test_owner_can_view_only_own_active_units(db_session: Session, world: World) -> None:
    assert can_view_unit_finance(db_session, "0351 15 555-0101", world.ids["u4c"])
    assert can_view_unit_finance(db_session, JUAN_PHONE, world.ids["sol_pb"])
    assert not can_view_unit_finance(db_session, JUAN_PHONE, world.ids["u7a"])  # not his
    assert not can_view_unit_finance(db_session, JUAN_PHONE, world.ids["old"])  # inactive


def test_tenant_cannot_view_debt(db_session: Session, world: World) -> None:
    assert not can_view_unit_finance(db_session, JUAN_PHONE, world.ids["u5b"])
    assert not can_view_unit_finance(db_session, "+5493515550404", world.ids["u4c"])


def test_unknown_phone_cannot_view(db_session: Session, world: World) -> None:
    assert not can_view_unit_finance(db_session, UNKNOWN_PHONE, world.ids["u4c"])


# --- Email verification -----------------------------------------------------------------


def test_two_owners_get_different_codes_and_tenant_email_is_never_used(
    db_session: Session, world: World
) -> None:
    sender = FakeSender()
    result = start_email_verification(db_session, UNKNOWN_PHONE, "Rodas 2", "4C", sender=sender)

    assert result.status == StartStatus.CODES_SENT
    assert result.unit is not None
    assert result.unit.unit_id == world.ids["u4c"]
    assert sorted(result.masked_emails) == ["a***@example.org", "j***@example.com"]
    assert sorted(to for to, _ in sender.sent) == ["ana.lopez@example.org", "juan@example.com"]
    codes = [code for _, code in sender.sent]
    assert len(set(codes)) == 2
    assert all(len(c) == 6 and c.isdigit() for c in codes)

    stored = db_session.scalars(select(VerificationCode)).all()
    assert {r.person_id for r in stored} == {world.ids["juan"], world.ids["ana"]}
    assert all(c not in r.code_hash for r in stored for c in codes)
    assert all(r.expires_at - r.created_at == timedelta(minutes=15) for r in stored)


def test_unit_with_only_tenant_email_goes_to_operator(db_session: Session, world: World) -> None:
    sender = FakeSender()
    result = start_email_verification(db_session, UNKNOWN_PHONE, "Rodas II", "7A", sender=sender)

    assert result.status == StartStatus.NO_EMAIL
    assert result.unit is not None
    assert result.unit.unit_id == world.ids["u7a"]
    assert sender.sent == []  # the tenant's email is never used
    assert db_session.scalars(select(VerificationCode)).all() == []


def test_confirm_links_phone_to_the_owner_of_that_code(db_session: Session, world: World) -> None:
    sender = FakeSender()
    start_email_verification(db_session, UNKNOWN_PHONE, "4 C de Rodas II", sender=sender)

    result = confirm_email_code(
        db_session, "0351 15 555-0999", sender.code_for("ana.lopez@example.org")
    )

    assert result.status == ConfirmStatus.VERIFIED
    assert result.identity.person_id == world.ids["ana"]
    row = phone_row(db_session, UNKNOWN_PHONE)
    assert row is not None
    assert (row.person_id, row.source, row.verified) == (
        world.ids["ana"],
        DataSource.BOT_VERIFIED,
        True,
    )
    assert can_view_unit_finance(db_session, UNKNOWN_PHONE, world.ids["u4c"])

    # The code is used and the sibling code (Juan's) no longer works.
    again = confirm_email_code(db_session, UNKNOWN_PHONE, sender.code_for("juan@example.com"))
    assert again.status == ConfirmStatus.NO_PENDING
    assert identify_by_phone(db_session, UNKNOWN_PHONE).person_id == world.ids["ana"]


def test_expired_code(db_session: Session, world: World) -> None:
    sender = FakeSender()
    start_email_verification(db_session, UNKNOWN_PHONE, "Rodas II", "4C", sender=sender)
    db_session.execute(
        update(VerificationCode).values(expires_at=VerificationCode.created_at - timedelta(1))
    )

    result = confirm_email_code(db_session, UNKNOWN_PHONE, sender.code_for("juan@example.com"))

    assert result.status == ConfirmStatus.EXPIRED
    assert phone_row(db_session, UNKNOWN_PHONE) is None


def test_wrong_codes_lock_the_verification(db_session: Session, world: World) -> None:
    sender = FakeSender()
    start_email_verification(db_session, UNKNOWN_PHONE, "Rodas II", "4C", sender=sender)
    good = sender.code_for("juan@example.com")
    wrong = next(
        f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in {c for _, c in sender.sent}
    )

    garbage = confirm_email_code(db_session, UNKNOWN_PHONE, "hola")
    assert garbage.status == ConfirmStatus.INVALID_CODE  # not counted
    for left in range(MAX_CODE_ATTEMPTS - 1, 0, -1):
        result = confirm_email_code(db_session, UNKNOWN_PHONE, wrong)
        assert (result.status, result.attempts_left) == (ConfirmStatus.WRONG_CODE, left)

    assert confirm_email_code(db_session, UNKNOWN_PHONE, wrong).status == ConfirmStatus.LOCKED
    # Even the right code does not work anymore.
    assert confirm_email_code(db_session, UNKNOWN_PHONE, good).status == ConfirmStatus.LOCKED
    assert phone_row(db_session, UNKNOWN_PHONE) is None


def test_new_start_invalidates_previous_codes(db_session: Session, world: World) -> None:
    sender = FakeSender()
    start_email_verification(db_session, UNKNOWN_PHONE, "Rodas II", "4C", sender=sender)
    old_code = sender.code_for("juan@example.com")
    start_email_verification(db_session, UNKNOWN_PHONE, "Rodas II", "4C", sender=sender)
    new_code = sender.code_for("juan@example.com")

    if old_code != new_code:
        result = confirm_email_code(db_session, UNKNOWN_PHONE, old_code)
        assert result.status == ConfirmStatus.WRONG_CODE
    assert confirm_email_code(db_session, UNKNOWN_PHONE, new_code).status == (
        ConfirmStatus.VERIFIED
    )


def test_max_starts_per_day(db_session: Session, world: World) -> None:
    sender = FakeSender()
    for _ in range(MAX_STARTS_PER_DAY):
        result = start_email_verification(
            db_session, UNKNOWN_PHONE, "Rodas II", "4C", sender=sender
        )
        assert result.status == StartStatus.CODES_SENT

    blocked = start_email_verification(db_session, UNKNOWN_PHONE, "Rodas II", "4C", sender=sender)
    assert blocked.status == StartStatus.RATE_LIMITED
    assert len(sender.sent) == 2 * MAX_STARTS_PER_DAY

    # Starts older than 24 hours no longer count.
    db_session.execute(
        update(VerificationCode).values(created_at=VerificationCode.created_at - timedelta(days=1))
    )
    again = start_email_verification(db_session, UNKNOWN_PHONE, "Rodas II", "4C", sender=sender)
    assert again.status == StartStatus.CODES_SENT


def test_ambiguous_unit_asks_and_sends_nothing(db_session: Session, world: World) -> None:
    f.unit(db_session, f.building(db_session, "046 RODAS I"), "4° C")
    sender = FakeSender()

    result = start_email_verification(db_session, UNKNOWN_PHONE, "Rodas", "4C", sender=sender)

    assert result.status == StartStatus.AMBIGUOUS
    assert len(result.candidates) == 2
    assert sender.sent == []
    # The person picks one and the bot starts again with that unit.
    picked = start_email_verification(
        db_session, UNKNOWN_PHONE, unit_id=world.ids["u4c"], sender=sender
    )
    assert picked.status == StartStatus.CODES_SENT


def test_not_found_and_invalid_phone(db_session: Session, world: World) -> None:
    sender = FakeSender()
    assert (
        start_email_verification(db_session, UNKNOWN_PHONE, "Rodas II", "9Z", sender=sender).status
        == StartStatus.NOT_FOUND
    )
    assert (
        start_email_verification(db_session, "123", "Rodas II", "4C", sender=sender).status
        == StartStatus.INVALID_PHONE
    )
    assert confirm_email_code(db_session, UNKNOWN_PHONE, "123456").status == (
        ConfirmStatus.NO_PENDING
    )


def test_send_failure_for_one_owner(db_session: Session, world: World) -> None:
    sender = FakeSender(failing={"juan@example.com"})
    result = start_email_verification(db_session, UNKNOWN_PHONE, "Rodas II", "4C", sender=sender)

    assert result.status == StartStatus.CODES_SENT
    assert result.masked_emails == ("a***@example.org",)

    sender_all_fail = FakeSender(failing={"juan@example.com", "ana.lopez@example.org"})
    failed = start_email_verification(
        db_session, "+5493515550888", "Rodas II", "4C", sender=sender_all_fail
    )
    assert failed.status == StartStatus.SEND_FAILED


def test_conflicting_phone_can_verify_and_is_moved(db_session: Session, world: World) -> None:
    sender = FakeSender()
    conflict_phone = "+5493515550505"
    start_email_verification(db_session, conflict_phone, "Rodas II", "4C", sender=sender)

    result = confirm_email_code(db_session, conflict_phone, sender.code_for("juan@example.com"))

    assert result.status == ConfirmStatus.VERIFIED
    row = phone_row(db_session, conflict_phone)
    assert row is not None
    assert (row.person_id, row.conflict, row.verified) == (world.ids["juan"], False, True)
    confirm_event = events(db_session)[-1]
    assert confirm_event.payload["reassigned_from_person_id"] == world.ids["conflict"]


def test_events_are_logged_without_codes_or_emails(db_session: Session, world: World) -> None:
    sender = FakeSender()
    start_email_verification(db_session, UNKNOWN_PHONE, "Rodas II", "4C", sender=sender)
    confirm_email_code(db_session, UNKNOWN_PHONE, "000000")
    confirm_email_code(db_session, UNKNOWN_PHONE, sender.code_for("juan@example.com"))

    logged = events(db_session)
    assert [e.event_type for e in logged] == ["email_verification_start"] + [
        "email_verification_confirm"
    ] * 2
    assert [e.payload["result"] for e in logged] == ["codes_sent", "wrong_code", "verified"]
    dumped = json.dumps([e.payload for e in logged])
    for email, code in sender.sent:
        assert code not in dumped
        assert email not in dumped


def test_roster_sync_keeps_a_verified_phone(db_session: Session, world: World) -> None:
    sender = FakeSender()
    start_email_verification(db_session, UNKNOWN_PHONE, "Rodas II", "4C", sender=sender)
    confirm_email_code(db_session, UNKNOWN_PHONE, sender.code_for("ana.lopez@example.org"))

    # Next night ConsorPlus lists that number for someone else.
    other = RosterContact(name="OTRA PERSONA", phone="0351 15 555-0999")
    row = RosterRow(
        unit_value="X1",
        building_code="777",
        building_name="777 OTRO EDIFICIO",
        unit_label="001 1º A",
        ph="1",
        unit_type="DPTO",
        owner=other,
    )
    report = RosterReport()
    sync_building_rows(db_session, "777", [row], report)

    assert report.verified_phones_kept == 1
    assert report.conflicts == 0
    kept = phone_row(db_session, UNKNOWN_PHONE)
    assert kept is not None
    assert (kept.person_id, kept.conflict) == (world.ids["ana"], False)
    assert identify_by_phone(db_session, UNKNOWN_PHONE).person_id == world.ids["ana"]


# --- Operator verification --------------------------------------------------------------


def test_operator_request_and_approval(db_session: Session, world: World) -> None:
    created = request_operator_verification(
        db_session, UNKNOWN_PHONE, world.ids["u7a"], "  Raúl   Ruiz "
    )
    assert created.status == RequestStatus.CREATED
    again = request_operator_verification(db_session, UNKNOWN_PHONE, world.ids["u7a"], "Raúl")
    assert (again.status, again.request_id) == (RequestStatus.ALREADY_PENDING, created.request_id)
    assert not can_view_unit_finance(db_session, UNKNOWN_PHONE, world.ids["u7a"])

    assert created.request_id is not None
    with pytest.raises(IdentityError):  # the tenant is not an owner of the unit
        approve_verification_request(db_session, created.request_id, world.ids["ines"], "op1")

    request = approve_verification_request(db_session, created.request_id, world.ids["raul"], "op1")

    assert request.status == VerificationRequestStatus.APPROVED
    assert request.claimed_name == "Raúl Ruiz"
    row = phone_row(db_session, UNKNOWN_PHONE)
    assert row is not None
    assert (row.person_id, row.source, row.verified) == (
        world.ids["raul"],
        DataSource.MANUAL,
        True,
    )
    assert can_view_unit_finance(db_session, UNKNOWN_PHONE, world.ids["u7a"])
    with pytest.raises(IdentityError):  # already resolved
        reject_verification_request(db_session, created.request_id, "op2")


def test_operator_rejection_and_limits(db_session: Session, world: World) -> None:
    first = request_operator_verification(db_session, UNKNOWN_PHONE, world.ids["u7a"], "X")
    assert first.request_id is not None
    rejected = reject_verification_request(db_session, first.request_id, "op1")
    assert rejected.status == VerificationRequestStatus.REJECTED
    assert phone_row(db_session, UNKNOWN_PHONE) is None

    for unit in ("u4c", "u5b"):
        result = request_operator_verification(db_session, UNKNOWN_PHONE, world.ids[unit], "X")
        assert result.status == RequestStatus.CREATED
    limited = request_operator_verification(db_session, UNKNOWN_PHONE, world.ids["sol_pb"], "X")
    assert limited.status == RequestStatus.RATE_LIMITED
    assert (
        request_operator_verification(db_session, UNKNOWN_PHONE, 999_999, "X").status
        == RequestStatus.UNIT_NOT_FOUND
    )
    assert len(db_session.scalars(select(VerificationRequest)).all()) == 3
