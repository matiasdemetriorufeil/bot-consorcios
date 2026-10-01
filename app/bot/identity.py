"""Who is writing and what they may see. Deterministic rules only, no AI.

- identify_by_phone: the person behind a WhatsApp number and their units.
- Unknown numbers prove who they are with a code emailed to an OWNER of the unit
  (start_email_verification / confirm_email_code) or, when the unit has no owner email,
  through an operator (request_operator_verification, resolved in the panel).
- can_view_unit_finance is the ONLY gate to show debt or the payment code of a unit.

A phone flagged conflict (ConsorPlus lists it for two people) is treated as unknown; one with
an assumed area code (needs_review) still identifies. Codes are never stored or logged in clear
text.
"""

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.bot.unit_search import SearchStatus, UnitCandidate, search_unit
from app.config import get_settings
from app.db.models import (
    BotEvent,
    Building,
    DataSource,
    Person,
    PersonRole,
    Phone,
    Unit,
    UnitPerson,
    VerificationCode,
    VerificationRequest,
    VerificationRequestStatus,
)
from app.notify.email import EmailError, EmailSender, get_email_sender, mask_email
from app.sync.normalize import clean_name, normalize_phone

logger = logging.getLogger(__name__)

CODE_DIGITS = 6
CODE_VALID_MINUTES = 15
MAX_STARTS_PER_DAY = 3
MAX_CODE_ATTEMPTS = 5
MAX_OPERATOR_REQUESTS_PER_DAY = 3
RATE_WINDOW = timedelta(hours=24)
MAX_CLAIMED_NAME = 200


class IdentityError(Exception):
    """Invalid operation (e.g. approving a request that is not pending)."""


# --- Identification ---------------------------------------------------------------------


@dataclass(frozen=True)
class UnitAccess:
    unit_id: int
    building_name: str
    unit_label: str
    role: PersonRole


@dataclass(frozen=True, repr=False)
class Identity:
    """known=False means "desconocido": no person, no units."""

    person_id: int | None = None
    full_name: str | None = None
    units: tuple[UnitAccess, ...] = ()

    @property
    def known(self) -> bool:
        return self.person_id is not None

    def __repr__(self) -> str:
        return f"Identity(person_id={self.person_id}, units={len(self.units)})"


UNKNOWN = Identity()


def to_e164(phone: str) -> str | None:
    """Any Argentine format ("0351 15 555-0101", "+54 9 351 5550101", "3515550101") as
    WhatsApp E.164. None when invalid or when the area code would have to be guessed."""
    normalized = normalize_phone(phone or "")
    if normalized is None or normalized.needs_review:
        return None
    return normalized.e164


def _trusted_phone(session: Session, phone: str) -> Phone | None:
    """The phones row for this number, unless it is in conflict (then: unknown)."""
    e164 = to_e164(phone)
    if e164 is None:
        return None
    row = session.scalar(select(Phone).where(Phone.e164 == e164))
    if row is None or row.conflict:
        return None
    return row


def identify_by_phone(session: Session, phone: str) -> Identity:
    """The person that owns this phone and their active units (with role), or UNKNOWN."""
    row = _trusted_phone(session, phone)
    if row is None:
        return UNKNOWN
    links = session.execute(
        select(UnitPerson.role, Unit.id, Unit.label, Building.name)
        .join(Unit, Unit.id == UnitPerson.unit_id)
        .join(Building, Building.id == Unit.building_id)
        .where(UnitPerson.person_id == row.person_id, Unit.active.is_(True))
        .order_by(Building.name, Unit.label, UnitPerson.role)
    ).all()
    units = tuple(
        UnitAccess(unit_id=uid, building_name=bname, unit_label=label, role=PersonRole(role))
        for role, uid, label, bname in links
    )
    return Identity(person_id=row.person_id, full_name=row.person.full_name, units=units)


def can_view_unit_finance(session: Session, phone: str, unit_id: int) -> bool:
    """THE ONLY gate to show debt or payment code: the phone (not in conflict) belongs to an
    OWNER of this active unit, whether from the roster, bot verification or manual load."""
    row = _trusted_phone(session, phone)
    if row is None:
        return False
    owner_link = session.scalar(
        select(func.count())
        .select_from(UnitPerson)
        .join(Unit, Unit.id == UnitPerson.unit_id)
        .where(
            UnitPerson.unit_id == unit_id,
            UnitPerson.person_id == row.person_id,
            UnitPerson.role == PersonRole.OWNER,
            Unit.active.is_(True),
        )
    )
    return bool(owner_link)


# --- Email verification -----------------------------------------------------------------


class StartStatus(StrEnum):
    CODES_SENT = "codes_sent"
    AMBIGUOUS = "ambiguous"  # ask the person which unit (see candidates)
    NOT_FOUND = "not_found"
    NO_EMAIL = "no_email"  # no owner email: request_operator_verification
    RATE_LIMITED = "rate_limited"
    INVALID_PHONE = "invalid_phone"
    SEND_FAILED = "send_failed"


@dataclass(frozen=True)
class StartResult:
    status: StartStatus
    unit: UnitCandidate | None = None
    candidates: tuple[UnitCandidate, ...] = ()
    masked_emails: tuple[str, ...] = ()


class ConfirmStatus(StrEnum):
    VERIFIED = "verified"
    WRONG_CODE = "wrong_code"
    INVALID_CODE = "invalid_code"  # not 6 digits: not counted as an attempt
    EXPIRED = "expired"
    LOCKED = "locked"  # too many attempts: a new verification must be started
    NO_PENDING = "no_pending"
    INVALID_PHONE = "invalid_phone"


@dataclass(frozen=True)
class ConfirmResult:
    status: ConfirmStatus
    identity: Identity = UNKNOWN
    attempts_left: int = 0


def _now() -> datetime:
    return datetime.now(UTC)


def _log_event(session: Session, phone: str | None, event_type: str, **payload: Any) -> None:
    """Audit trail in bot_events. Never put codes or email addresses in the payload."""
    session.add(BotEvent(phone_e164=phone, event_type=event_type, payload=payload))


def _hash_code(e164: str, code: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hmac.new(salt.encode(), f"{e164}:{code}".encode(), hashlib.sha256).hexdigest()
    return f"{salt}${digest}"


def _code_matches(stored: str, e164: str, code: str) -> bool:
    salt, _, _ = stored.partition("$")
    return hmac.compare_digest(stored, _hash_code(e164, code, salt))


def _new_codes(count: int) -> list[str]:
    codes: set[str] = set()
    while len(codes) < count:
        codes.add(f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}")
    return list(codes)


def parse_email_exclusions(text: str) -> frozenset[str]:
    """ "a@b.com, otro.com.ar, @x.com" -> {"a@b.com", "@otro.com.ar", "@x.com"}: whole
    addresses, or domains as "@domain"."""
    rules: set[str] = set()
    for item in (text or "").split(","):
        item = item.strip().lower()
        if item:
            rules.add(item if "@" in item else f"@{item}")
    return frozenset(rules)


def is_excluded_email(email: str, exclusions: frozenset[str]) -> bool:
    email = email.strip().lower()
    domain = "@" + email.rpartition("@")[2]
    return email in exclusions or domain in exclusions


def _owner_emails(
    session: Session, unit_id: int, exclusions: frozenset[str] = frozenset()
) -> list[tuple[int, str]]:
    """(person_id, email) of the unit's OWNERS with email, one per distinct address.
    Tenants' emails and excluded addresses (VERIFICATION_EMAIL_EXCLUDE) are never used."""
    rows = session.execute(
        select(Person.id, Person.email)
        .join(UnitPerson, UnitPerson.person_id == Person.id)
        .where(
            UnitPerson.unit_id == unit_id,
            UnitPerson.role == PersonRole.OWNER,
            Person.email.is_not(None),
            Person.email != "",
        )
        .order_by(Person.id)
    ).all()
    seen: set[str] = set()
    result: list[tuple[int, str]] = []
    for person_id, email in rows:
        key = email.strip().lower()
        if key and key not in seen and not is_excluded_email(key, exclusions):
            seen.add(key)
            result.append((person_id, email.strip()))
    return result


def _active_unit(session: Session, unit_id: int) -> UnitCandidate | None:
    row = session.execute(
        select(Unit.id, Building.name, Unit.label)
        .join(Building, Building.id == Unit.building_id)
        .where(Unit.id == unit_id, Unit.active.is_(True))
    ).first()
    return UnitCandidate(row[0], row[1], row[2]) if row else None


def start_email_verification(
    session: Session,
    phone: str,
    building_text: str = "",
    unit_text: str = "",
    *,
    unit_id: int | None = None,
    sender: EmailSender | None = None,
    exclusions: frozenset[str] | None = None,
) -> StartResult:
    """Find the unit (or use unit_id, once the person picked a candidate) and email a
    different code to each owner email. Excluded emails (default: settings
    VERIFICATION_EMAIL_EXCLUDE) never get a code; with only those it is NO_EMAIL. Commits."""
    e164 = to_e164(phone)
    if e164 is None:
        return StartResult(StartStatus.INVALID_PHONE)

    if unit_id is not None:
        unit = _active_unit(session, unit_id)
        if unit is None:
            return _start_rejected(session, e164, StartStatus.NOT_FOUND)
    else:
        found = search_unit(session, building_text, unit_text)
        if found.status == SearchStatus.AMBIGUOUS:
            return _start_rejected(
                session, e164, StartStatus.AMBIGUOUS, candidates=found.candidates
            )
        if found.unit is None:
            return _start_rejected(session, e164, StartStatus.NOT_FOUND)
        unit = found.unit

    if exclusions is None:
        exclusions = parse_email_exclusions(get_settings().verification_email_exclude)
    owners = _owner_emails(session, unit.unit_id, exclusions)
    if not owners:
        return _start_rejected(session, e164, StartStatus.NO_EMAIL, unit=unit)

    now = _now()
    starts_today = session.scalar(
        select(func.count(func.distinct(VerificationCode.verification_id))).where(
            VerificationCode.phone_e164 == e164,
            VerificationCode.created_at >= now - RATE_WINDOW,
        )
    )
    if starts_today >= MAX_STARTS_PER_DAY:
        return _start_rejected(session, e164, StartStatus.RATE_LIMITED, unit=unit)

    # Only one live verification per phone: older pending codes stop working.
    session.execute(
        update(VerificationCode)
        .where(
            VerificationCode.phone_e164 == e164,
            VerificationCode.used_at.is_(None),
            VerificationCode.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    verification_id = uuid.uuid4().hex
    codes = _new_codes(len(owners))
    rows: list[tuple[VerificationCode, str, str]] = []
    for (person_id, email), code in zip(owners, codes, strict=True):
        row = VerificationCode(
            verification_id=verification_id,
            phone_e164=e164,
            unit_id=unit.unit_id,
            person_id=person_id,
            code_hash=_hash_code(e164, code),
            created_at=now,
            expires_at=now + timedelta(minutes=CODE_VALID_MINUTES),
        )
        session.add(row)
        rows.append((row, email, code))
    # Stored before sending: a code that reaches an inbox must always be checkable.
    session.commit()

    sender = sender or get_email_sender()
    masked: list[str] = []
    failed = 0
    for row, email, code in rows:
        try:
            sender.send_verification_code(email, code, CODE_VALID_MINUTES)
            masked.append(mask_email(email))
        except EmailError as exc:
            failed += 1
            row.revoked_at = _now()
            logger.warning("Verification email not sent (unit %s): %s", unit.unit_id, exc)

    status = StartStatus.CODES_SENT if masked else StartStatus.SEND_FAILED
    _log_event(
        session,
        e164,
        "email_verification_start",
        result=status.value,
        unit_id=unit.unit_id,
        verification_id=verification_id,
        codes_sent=len(masked),
        codes_failed=failed,
    )
    session.commit()
    return StartResult(status, unit=unit, masked_emails=tuple(masked))


def _start_rejected(
    session: Session,
    e164: str,
    status: StartStatus,
    *,
    unit: UnitCandidate | None = None,
    candidates: tuple[UnitCandidate, ...] = (),
) -> StartResult:
    _log_event(
        session,
        e164,
        "email_verification_start",
        result=status.value,
        unit_id=unit.unit_id if unit else None,
        candidates=len(candidates),
    )
    session.commit()
    return StartResult(status, unit=unit, candidates=candidates)


def confirm_email_code(session: Session, phone: str, code: str) -> ConfirmResult:
    """Check the code of the phone's latest verification. On success links the phone to the
    owner that code was sent to (source=bot_verified, verified=True). Commits."""
    e164 = to_e164(phone)
    if e164 is None:
        return ConfirmResult(ConfirmStatus.INVALID_PHONE)

    latest = session.scalar(
        select(VerificationCode.verification_id)
        .where(VerificationCode.phone_e164 == e164)
        .order_by(VerificationCode.created_at.desc(), VerificationCode.id.desc())
        .limit(1)
    )
    if latest is None:
        return _confirm_result(session, e164, ConfirmStatus.NO_PENDING)
    # Row lock: two parallel guesses cannot both slip under the attempt limit.
    rows = session.scalars(
        select(VerificationCode)
        .where(VerificationCode.verification_id == latest)
        .order_by(VerificationCode.id)
        .with_for_update()
    ).all()
    verification = {"verification_id": latest, "unit_id": rows[0].unit_id}

    if any(r.used_at for r in rows):
        return _confirm_result(session, e164, ConfirmStatus.NO_PENDING, **verification)
    if max(r.attempts for r in rows) >= MAX_CODE_ATTEMPTS:
        return _confirm_result(session, e164, ConfirmStatus.LOCKED, **verification)
    pending = [r for r in rows if r.revoked_at is None]
    if not pending:
        return _confirm_result(session, e164, ConfirmStatus.NO_PENDING, **verification)
    now = _now()
    if all(r.expires_at <= now for r in pending):
        return _confirm_result(session, e164, ConfirmStatus.EXPIRED, **verification)

    code = re.sub(r"\s+", "", code or "")
    if not (len(code) == CODE_DIGITS and code.isascii() and code.isdigit()):
        left = MAX_CODE_ATTEMPTS - rows[0].attempts
        return _confirm_result(
            session, e164, ConfirmStatus.INVALID_CODE, attempts_left=left, **verification
        )

    match = next((r for r in pending if _code_matches(r.code_hash, e164, code)), None)
    if match is None:
        for r in rows:
            r.attempts += 1
        attempts = rows[0].attempts
        if attempts >= MAX_CODE_ATTEMPTS:
            for r in pending:
                r.revoked_at = now
            return _confirm_result(session, e164, ConfirmStatus.LOCKED, **verification)
        return _confirm_result(
            session,
            e164,
            ConfirmStatus.WRONG_CODE,
            attempts_left=MAX_CODE_ATTEMPTS - attempts,
            **verification,
        )

    match.used_at = now
    for r in pending:
        if r is not match:
            r.revoked_at = now
    reassigned_from = _link_phone(session, e164, match.person_id, DataSource.BOT_VERIFIED)
    _confirm_result(
        session,
        e164,
        ConfirmStatus.VERIFIED,
        person_id=match.person_id,
        reassigned_from_person_id=reassigned_from,
        **verification,
    )
    return ConfirmResult(ConfirmStatus.VERIFIED, identity=identify_by_phone(session, e164))


def _confirm_result(
    session: Session,
    e164: str,
    status: ConfirmStatus,
    *,
    attempts_left: int = 0,
    **payload: Any,
) -> ConfirmResult:
    _log_event(session, e164, "email_verification_confirm", result=status.value, **payload)
    session.commit()
    return ConfirmResult(status, attempts_left=max(attempts_left, 0))


def _link_phone(session: Session, e164: str, person_id: int, source: DataSource) -> int | None:
    """Make the phone belong to this person (verified). Returns the previous owner's id when
    the number was taken from someone else. The proof (owner email / operator) wins over the
    roster, which never touches bot_verified or manual phones."""
    row = session.scalar(select(Phone).where(Phone.e164 == e164).with_for_update())
    if row is None:
        session.add(Phone(person_id=person_id, e164=e164, source=source, verified=True))
        session.flush()
        return None
    previous = row.person_id if row.person_id != person_id else None
    row.person_id = person_id
    row.source = source
    row.verified = True
    # Written from WhatsApp and proven: neither in conflict nor with a guessed area code.
    row.needs_review = False
    row.conflict = False
    session.flush()
    session.expire(row, ["person"])
    return previous


# --- Operator verification --------------------------------------------------------------


class RequestStatus(StrEnum):
    CREATED = "created"
    ALREADY_PENDING = "already_pending"
    RATE_LIMITED = "rate_limited"
    UNIT_NOT_FOUND = "unit_not_found"
    INVALID_PHONE = "invalid_phone"


@dataclass(frozen=True)
class OperatorRequestResult:
    status: RequestStatus
    request_id: int | None = None


def request_operator_verification(
    session: Session, phone: str, unit_id: int, claimed_name: str
) -> OperatorRequestResult:
    """Leave a pending request for an operator to link this phone to an owner of the unit
    (for units without owner email). Commits."""
    e164 = to_e164(phone)
    if e164 is None:
        return OperatorRequestResult(RequestStatus.INVALID_PHONE)
    if _active_unit(session, unit_id) is None:
        return OperatorRequestResult(RequestStatus.UNIT_NOT_FOUND)

    existing = session.scalar(
        select(VerificationRequest.id).where(
            VerificationRequest.phone_e164 == e164,
            VerificationRequest.unit_id == unit_id,
            VerificationRequest.status == VerificationRequestStatus.PENDING,
        )
    )
    if existing is not None:
        return OperatorRequestResult(RequestStatus.ALREADY_PENDING, existing)

    recent = session.scalar(
        select(func.count())
        .select_from(VerificationRequest)
        .where(
            VerificationRequest.phone_e164 == e164,
            VerificationRequest.created_at >= _now() - RATE_WINDOW,
        )
    )
    if recent >= MAX_OPERATOR_REQUESTS_PER_DAY:
        _log_event(
            session, e164, "operator_verification_request", result="rate_limited", unit_id=unit_id
        )
        session.commit()
        return OperatorRequestResult(RequestStatus.RATE_LIMITED)

    request = VerificationRequest(
        phone_e164=e164,
        unit_id=unit_id,
        claimed_name=clean_name(claimed_name)[:MAX_CLAIMED_NAME] or "(sin nombre)",
        created_at=_now(),
    )
    session.add(request)
    session.flush()
    _log_event(
        session,
        e164,
        "operator_verification_request",
        result="created",
        unit_id=unit_id,
        request_id=request.id,
    )
    session.commit()
    return OperatorRequestResult(RequestStatus.CREATED, request.id)


def _pending_request(session: Session, request_id: int) -> VerificationRequest:
    request = session.get(VerificationRequest, request_id, with_for_update=True)
    if request is None:
        raise IdentityError(f"solicitud {request_id} inexistente")
    if request.status != VerificationRequestStatus.PENDING:
        raise IdentityError(f"solicitud {request_id} ya resuelta ({request.status})")
    return request


def approve_verification_request(
    session: Session, request_id: int, person_id: int, resolved_by: str
) -> VerificationRequest:
    """Operator approves: the phone is linked to `person_id` (an OWNER of the request's
    unit) with source="manual". Commits."""
    request = _pending_request(session, request_id)
    is_owner = session.scalar(
        select(func.count())
        .select_from(UnitPerson)
        .where(
            UnitPerson.unit_id == request.unit_id,
            UnitPerson.person_id == person_id,
            UnitPerson.role == PersonRole.OWNER,
        )
    )
    if not is_owner:
        raise IdentityError("la persona elegida no es propietaria de la unidad")
    reassigned_from = _link_phone(session, request.phone_e164, person_id, DataSource.MANUAL)
    request.status = VerificationRequestStatus.APPROVED
    request.person_id = person_id
    request.resolved_at = _now()
    request.resolved_by = resolved_by
    _log_event(
        session,
        request.phone_e164,
        "operator_verification_resolved",
        result="approved",
        request_id=request.id,
        unit_id=request.unit_id,
        person_id=person_id,
        reassigned_from_person_id=reassigned_from,
    )
    session.commit()
    return request


def reject_verification_request(
    session: Session, request_id: int, resolved_by: str
) -> VerificationRequest:
    request = _pending_request(session, request_id)
    request.status = VerificationRequestStatus.REJECTED
    request.resolved_at = _now()
    request.resolved_by = resolved_by
    _log_event(
        session,
        request.phone_e164,
        "operator_verification_resolved",
        result="rejected",
        request_id=request.id,
        unit_id=request.unit_id,
    )
    session.commit()
    return request
