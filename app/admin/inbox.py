"""The WhatsApp inbox of the panel ("Conversaciones"): what it lists and what its buttons do.
No HTTP here (app.admin.conversations renders it), so it is tested on its own.

Tabs: "Esperando persona" (handed off by the bot, urgent first, then the oldest handoff),
"Mías" (with a human, assigned to me), "Con el bot" and "Resueltas". A conversation an
operator has (or that was answered from the phone app) is found with the search, which looks
in every state: profile or person name, phone digits, unit or building.

Actions lock the conversation's row (SELECT ... FOR UPDATE), the same row the bot locks
before each message it sends (app.whatsapp.channel): an operator who takes a conversation
while the bot is answering wins, and nothing of the bot goes out after that. Every action is
audited in bot_events (admin_action) with the panel user, never with message texts.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import Select, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session, selectinload

from app.admin.audit import log_admin_action
from app.amenities.booking import describe_slot
from app.bot.identity import to_e164
from app.bot.tools import format_money
from app.bot.unit_search import display_building_name
from app.channels.handoff import REASONS
from app.db.models import (
    Building,
    DebtSnapshot,
    Person,
    PersonRole,
    Phone,
    Reservation,
    ReservationStatus,
    Unit,
    UnitPerson,
    WaAuthor,
    WaContact,
    WaConversation,
    WaConversationStatus,
    WaDirection,
    WaMediaStatus,
    WaMessage,
    WaMessageStatus,
    WaTemplate,
)
from app.whatsapp import conversations
from app.whatsapp.channel import window_open_at
from app.whatsapp.client import WhatsAppError
from app.whatsapp.media import INLINE_TYPES, base_mime

TABS = {
    "waiting": "Esperando persona",
    "mine": "Mías",
    "bot": "Con el bot",
    "resolved": "Resueltas",
}
DEFAULT_TAB = "waiting"
LIST_LIMIT = 100
# WhatsApp's limit for a text message.
MAX_TEXT = 4096
MAX_NOTE = 4000
STATUS_LABELS = {
    WaConversationStatus.BOT: "Con el bot",
    WaConversationStatus.WAITING_HUMAN: "Esperando persona",
    WaConversationStatus.HUMAN: "Con una persona",
    WaConversationStatus.RESOLVED: "Resuelta",
}
ROLE_LABELS = {PersonRole.OWNER: "propietario", PersonRole.TENANT: "inquilino"}
_KIND_LABELS = {
    "image": "Foto",
    "sticker": "Sticker",
    "document": "Documento",
    "audio": "Audio",
    "video": "Video",
    "location": "Ubicación",
    "contacts": "Contacto",
    "reaction": "Reacción",
}
_MEDIA_PROBLEMS = {
    WaMediaStatus.TOO_LARGE: "Adjunto no disponible: es demasiado grande.",
    WaMediaStatus.TYPE_NOT_ALLOWED: "Adjunto no disponible: tipo de archivo no permitido.",
    WaMediaStatus.FAILED: "Adjunto no disponible: no se pudo descargar.",
}
WINDOW_CLOSED_TEXT = (
    "Pasaron más de 24 h desde el último mensaje de esta persona: WhatsApp solo permite "
    "mandarle una plantilla aprobada."
)


class InboxError(ValueError):
    """Why an action could not be done, in Spanish (shown as is)."""


class Sender(Protocol):
    """What the inbox needs of app.whatsapp.client.WhatsAppClient."""

    def send_text(self, to: str, text: str) -> str: ...

    def send_template(self, to: str, name: str, language: str = "es_AR") -> str: ...


# --- Who writes -------------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitLine:
    unit_id: int
    building: str
    label: str
    role: str  # "propietario" | "inquilino"

    @property
    def text(self) -> str:
        return f"{self.building} · {self.label}"


@dataclass
class Known:
    """The person a WhatsApp number belongs to (phones table)."""

    person_id: int
    full_name: str
    verified: bool
    units: list[UnitLine] = field(default_factory=list)


def _e164(phone: str) -> str:
    return to_e164(phone) or phone


def identify_many(session: Session, phones: list[str]) -> dict[str, Known]:
    """WhatsApp phone -> its person and active units (phones in conflict identify nobody)."""
    by_e164 = {_e164(p): p for p in phones}
    if not by_e164:
        return {}
    rows = session.execute(
        select(Phone.e164, Phone.verified, Person.id, Person.full_name)
        .join(Person, Person.id == Phone.person_id)
        .where(Phone.e164.in_(by_e164), Phone.conflict.is_(False))
    ).all()
    known = {
        by_e164[e164]: Known(person_id=pid, full_name=name, verified=verified)
        for e164, verified, pid, name in rows
    }
    by_person = {k.person_id: k for k in known.values()}
    if by_person:
        links = session.execute(
            select(UnitPerson.person_id, UnitPerson.role, Unit.id, Unit.label, Building.name)
            .join(Unit, Unit.id == UnitPerson.unit_id)
            .join(Building, Building.id == Unit.building_id)
            .where(UnitPerson.person_id.in_(by_person), Unit.active.is_(True))
            .order_by(Building.name, Unit.label, UnitPerson.role)
        ).all()
        for person_id, role, unit_id, label, building in links:
            by_person[person_id].units.append(
                UnitLine(
                    unit_id=unit_id,
                    building=display_building_name(building),
                    label=label,
                    role=ROLE_LABELS.get(PersonRole(role), str(role)),
                )
            )
    return known


# --- The list ---------------------------------------------------------------------------------


def ago(when: datetime | None, now: datetime) -> str:
    """ "recién", "hace 5 min", "hace 3 h", "hace 2 días"."""
    if when is None:
        return ""
    seconds = max(0, (now - when).total_seconds())
    if seconds < 60:
        return "recién"
    if seconds < 3600:
        return f"hace {int(seconds // 60)} min"
    if seconds < 86400:
        return f"hace {int(seconds // 3600)} h"
    days = int(seconds // 86400)
    return "hace 1 día" if days == 1 else f"hace {days} días"


def reason_label(reason: str | None) -> tuple[str, str] | None:
    """(label, text) of a handoff reason: ("ventana-cerrada", "Ventana de 24 h cerrada...")."""
    if not reason:
        return None
    return REASONS.get(reason, (reason, reason))


@dataclass
class InboxRow:
    id: int
    name: str
    phone: str
    status: WaConversationStatus
    status_label: str
    units: list[UnitLine]
    unread: int
    last_at: datetime | None
    ago: str
    preview: str
    urgent: bool
    reason: tuple[str, str] | None
    assigned_to: str | None  # display name
    from_phone_app: bool  # with a human, nobody of the panel assigned


def _last_message() -> Select:
    """Per conversation, the id of its last message that is not an internal note."""
    return (
        select(WaMessage.conversation_id, func.max(WaMessage.id).label("last_id"))
        .where(WaMessage.is_internal_note.is_(False))
        .group_by(WaMessage.conversation_id)
    )


def _search_condition(term: str):  # type: ignore[no-untyped-def]
    like = f"%{term}%"
    people = (
        select(Phone.e164)
        .join(Person, Person.id == Phone.person_id)
        .outerjoin(UnitPerson, UnitPerson.person_id == Person.id)
        .outerjoin(Unit, Unit.id == UnitPerson.unit_id)
        .outerjoin(Building, Building.id == Unit.building_id)
        .where(
            Phone.conflict.is_(False),
            or_(
                Person.full_name.ilike(like),
                Unit.label.ilike(like),
                Building.name.ilike(like),
                Building.address.ilike(like),
            ),
        )
    )
    conditions = [WaContact.profile_name.ilike(like), WaContact.phone_e164.in_(people)]
    digits = re.sub(r"\D", "", term).lstrip("0")
    if len(digits) >= 3:
        conditions.append(WaContact.phone_e164.contains(digits, autoescape=True))
    return or_(*conditions)


def list_conversations(
    session: Session,
    tab: str,
    user: str,
    now: datetime,
    *,
    query: str = "",
    names: dict[str, str] | None = None,
) -> list[InboxRow]:
    """The rows of a tab (or, with a query, of every state)."""
    last = _last_message().subquery()
    stmt = (
        select(WaConversation, WaContact, WaMessage)
        .join(WaContact, WaContact.id == WaConversation.contact_id)
        .outerjoin(last, last.c.conversation_id == WaConversation.id)
        .outerjoin(WaMessage, WaMessage.id == last.c.last_id)
    )
    last_at = func.coalesce(WaMessage.created_at, WaConversation.created_at)
    term = query.strip()
    if term:
        stmt = stmt.where(_search_condition(term)).order_by(last_at.desc())
    elif tab == "mine":
        stmt = stmt.where(
            WaConversation.status == WaConversationStatus.HUMAN,
            WaConversation.assigned_to == user,
        ).order_by(last_at.desc())
    elif tab == "bot":
        stmt = stmt.where(WaConversation.status == WaConversationStatus.BOT).order_by(
            last_at.desc()
        )
    elif tab == "resolved":
        stmt = stmt.where(WaConversation.status == WaConversationStatus.RESOLVED).order_by(
            WaConversation.resolved_at.desc().nulls_last(), last_at.desc()
        )
    else:
        # A CASE: priority = 'urgent' would be NULL (and sort first) without a priority.
        urgent_first = case((WaConversation.handoff_priority == "urgent", 0), else_=1)
        stmt = stmt.where(WaConversation.status == WaConversationStatus.WAITING_HUMAN).order_by(
            urgent_first, WaConversation.handed_off_at.asc().nulls_last(), last_at.asc()
        )
    rows = session.execute(stmt.limit(LIST_LIMIT)).all()
    known = identify_many(session, [contact.phone_e164 for _, contact, _ in rows])
    names = names or {}
    result = []
    for conversation, contact, message in rows:
        who = known.get(contact.phone_e164)
        when = message.created_at if message is not None else None
        result.append(
            InboxRow(
                id=conversation.id,
                name=contact_name(contact, who),
                phone=contact.phone_e164,
                status=WaConversationStatus(conversation.status),
                status_label=STATUS_LABELS[WaConversationStatus(conversation.status)],
                units=who.units if who else [],
                unread=conversation.unread_count or 0,
                last_at=when,
                ago=ago(when, now),
                preview=preview(message) if message is not None else "",
                urgent=conversation.handoff_priority == "urgent"
                and conversation.status == WaConversationStatus.WAITING_HUMAN,
                reason=reason_label(conversation.handoff_reason)
                if conversation.status != WaConversationStatus.BOT
                else None,
                assigned_to=names.get(conversation.assigned_to or "", conversation.assigned_to),
                from_phone_app=conversation.status == WaConversationStatus.HUMAN
                and not conversation.assigned_to,
            )
        )
    return result


def tab_counts(session: Session, user: str) -> dict[str, int]:
    """How many conversations wait for a person, and how many I have (with unread)."""
    waiting = session.scalar(
        select(func.count())
        .select_from(WaConversation)
        .where(WaConversation.status == WaConversationStatus.WAITING_HUMAN)
    )
    mine = session.scalar(
        select(func.count())
        .select_from(WaConversation)
        .where(
            WaConversation.status == WaConversationStatus.HUMAN,
            WaConversation.assigned_to == user,
        )
    )
    return {"waiting": waiting or 0, "mine": mine or 0}


def waiting_ids(session: Session) -> list[int]:
    """Every conversation waiting for a person (the page beeps when a new one shows up)."""
    return list(
        session.scalars(
            select(WaConversation.id)
            .where(WaConversation.status == WaConversationStatus.WAITING_HUMAN)
            .order_by(WaConversation.id)
            .limit(500)
        )
    )


def mine_last_inbound(session: Session, user: str) -> dict[int, int]:
    """My conversations -> id of the contact's last message (the page beeps when it grows)."""
    rows = session.execute(
        select(WaMessage.conversation_id, func.max(WaMessage.id))
        .join(WaConversation, WaConversation.id == WaMessage.conversation_id)
        .where(
            WaConversation.status == WaConversationStatus.HUMAN,
            WaConversation.assigned_to == user,
            WaMessage.direction == WaDirection.INBOUND,
        )
        .group_by(WaMessage.conversation_id)
    ).all()
    return {conversation_id: last for conversation_id, last in rows}


def contact_name(contact: WaContact, who: Known | None) -> str:
    if who is not None:
        return who.full_name
    return contact.profile_name or contact.phone_e164


def preview(message: WaMessage, length: int = 80) -> str:
    text = " ".join((message.body or "").split())
    if not text:
        text = f"[{_KIND_LABELS.get(message.message_type, message.message_type)}]"
    if message.author != WaAuthor.CONTACT:
        text = ("🤖 " if message.author == WaAuthor.BOT else "Vos/estudio: ") + text
    return text if len(text) <= length else text[: length - 1] + "…"


# --- One conversation -------------------------------------------------------------------------


@dataclass
class DebtLine:
    total: str  # "$165.060,00"
    up_to_date: bool
    fetched_at: str


@dataclass
class CardUnit:
    unit: UnitLine
    debt: DebtLine | None


@dataclass
class CardReservation:
    building: str
    unit: str
    day: date
    slot: str


@dataclass
class ContactCard:
    name: str
    profile_name: str | None
    phone: str
    known: bool
    verified: bool
    units: list[CardUnit]
    reservations: list[CardReservation]


def _local(value: datetime | None, timezone: str, fmt: str = "%d/%m/%Y %H:%M") -> str:
    return value.astimezone(ZoneInfo(timezone)).strftime(fmt) if value else ""


def contact_card(session: Session, contact: WaContact, timezone: str, today: date) -> ContactCard:
    who = identify_many(session, [contact.phone_e164]).get(contact.phone_e164)
    units = who.units if who else []
    unit_ids = list(dict.fromkeys(u.unit_id for u in units))
    debts: dict[int, DebtSnapshot] = {}
    if unit_ids:
        latest = (
            select(DebtSnapshot)
            .where(DebtSnapshot.unit_id.in_(unit_ids))
            .order_by(DebtSnapshot.unit_id, DebtSnapshot.fetched_at.desc(), DebtSnapshot.id.desc())
            .ext(distinct_on(DebtSnapshot.unit_id))
        )
        debts = {s.unit_id: s for s in session.scalars(latest)}
    card_units = []
    for unit in units:
        snapshot = debts.get(unit.unit_id)
        card_units.append(
            CardUnit(
                unit=unit,
                debt=DebtLine(
                    total=format_money(snapshot.total_amount),
                    up_to_date=snapshot.is_up_to_date,
                    fetched_at=_local(snapshot.fetched_at, timezone),
                )
                if snapshot
                else None,
            )
        )
    reservations = []
    if unit_ids:
        rows = session.scalars(
            select(Reservation)
            .where(
                Reservation.unit_id.in_(unit_ids),
                Reservation.status == ReservationStatus.CONFIRMED,
                Reservation.date >= today,
            )
            .order_by(Reservation.date)
            .limit(10)
            .options(
                selectinload(Reservation.slot),
                selectinload(Reservation.unit).selectinload(Unit.building),
            )
        )
        reservations = [
            CardReservation(
                building=display_building_name(r.unit.building.name),
                unit=r.unit.label,
                day=r.date,
                slot=describe_slot(r.slot),
            )
            for r in rows
        ]
    return ContactCard(
        name=contact_name(contact, who),
        profile_name=contact.profile_name,
        phone=contact.phone_e164,
        known=who is not None,
        verified=bool(who and who.verified),
        units=card_units,
        reservations=reservations,
    )


@dataclass
class MessageView:
    """A message as the conversation page paints it (texts unescaped: Jinja escapes them)."""

    id: int
    side: str  # "in" (contact), "out" (bot, operator), "note"
    author: str  # "contact" | "bot" | "operator" | "system"
    author_name: str
    kind_label: str  # "" for plain text
    body: str
    choices: list[str]
    is_handoff_note: bool
    media_url_ok: bool  # the attachment is stored and can be linked
    media_inline: bool  # an image: shown with <img>
    media_problem: str
    media_filename: str
    status: str  # sent / delivered / read / failed / received / ""
    error_text: str
    time: str
    day: str


def _message_view(message: WaMessage, names: dict[str, str], timezone: str) -> MessageView:
    author = WaAuthor(message.author)
    if message.is_internal_note:
        side = "note"
    elif message.direction == WaDirection.INBOUND:
        side = "in"
    else:
        side = "out"
    if author == WaAuthor.OPERATOR:
        author_name = (
            names.get(message.operator, message.operator)
            if message.operator
            else "Desde el celular"
        )
    else:
        author_name = {"contact": "", "bot": "Bot", "system": "Sistema"}[author.value]
    kind = message.message_type
    media_status = WaMediaStatus(message.media_status) if message.media_status else None
    stored = media_status == WaMediaStatus.STORED
    has_media = bool(message.media_id or message.media_status)
    problem = ""
    if has_media and not stored:
        problem = _MEDIA_PROBLEMS.get(media_status, "Adjunto todavía no descargado.")  # type: ignore[arg-type]
    if kind in ("text", "interactive", "button", "note") or (kind == "template" and message.body):
        kind_label = "Plantilla" if kind == "template" else ""
    else:
        kind_label = _KIND_LABELS.get(kind, kind)
    return MessageView(
        id=message.id,
        side=side,
        author=author.value,
        author_name=author_name or "",
        kind_label=kind_label,
        body=message.body or "",
        choices=list(message.choices or []),
        is_handoff_note=message.is_internal_note and author == WaAuthor.SYSTEM,
        media_url_ok=stored,
        media_inline=stored and base_mime(message.media_mime) in INLINE_TYPES,
        media_problem=problem,
        media_filename=message.media_filename or "",
        status=WaMessageStatus(message.status).value if message.status else "",
        error_text=message.error_text or "",
        time=_local(message.created_at, timezone, "%H:%M"),
        day=_local(message.created_at, timezone, "%d/%m/%Y"),
    )


def thread(
    session: Session,
    conversation_id: int,
    names: dict[str, str],
    timezone: str,
    *,
    after_id: int = 0,
    limit: int = 300,
) -> list[MessageView]:
    """The conversation's messages (the last `limit`, or those after after_id), in order."""
    stmt = (
        select(WaMessage)
        .where(WaMessage.conversation_id == conversation_id, WaMessage.id > after_id)
        .order_by(WaMessage.id.desc())
        .limit(limit)
    )
    rows = list(reversed(list(session.scalars(stmt))))
    return [_message_view(m, names, timezone) for m in rows]


def outbound_statuses(session: Session, conversation_id: int, limit: int = 60) -> dict[int, str]:
    """Status of the last messages sent (the page updates their ticks)."""
    rows = session.execute(
        select(WaMessage.id, WaMessage.status)
        .where(
            WaMessage.conversation_id == conversation_id,
            WaMessage.direction == WaDirection.OUTBOUND,
            WaMessage.is_internal_note.is_(False),
        )
        .order_by(WaMessage.id.desc())
        .limit(limit)
    ).all()
    return {mid: WaMessageStatus(s).value for mid, s in rows if s}


def window_open(conversation: WaConversation, now: datetime) -> bool:
    return window_open_at(conversation.last_inbound_at, now)


def mark_read(session: Session, conversation_id: int) -> None:
    """Somebody of the panel is looking at it (unread is shared: read for everyone)."""
    session.execute(
        update(WaConversation)
        .where(WaConversation.id == conversation_id, WaConversation.unread_count > 0)
        .values(unread_count=0)
    )
    session.commit()


def active_templates(session: Session) -> list[WaTemplate]:
    return list(
        session.scalars(
            select(WaTemplate).where(WaTemplate.active.is_(True)).order_by(WaTemplate.label)
        )
    )


# --- Actions ----------------------------------------------------------------------------------


def _locked(session: Session, conversation_id: int) -> WaConversation:
    conversation = session.scalar(
        select(WaConversation)
        .where(WaConversation.id == conversation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if conversation is None:
        raise InboxError("La conversación no existe.")
    return conversation


def _contact(session: Session, conversation: WaConversation) -> WaContact:
    contact = session.get(WaContact, conversation.contact_id)
    if contact is None:  # pragma: no cover - the foreign key prevents it
        raise InboxError("La conversación no tiene contacto.")
    return contact


def _audit(
    session: Session,
    conversation: WaConversation,
    user: str,
    action: str,
    **payload: object,
) -> None:
    log_admin_action(
        session,
        user,
        action,
        phone_e164=_contact(session, conversation).phone_e164,
        conversation_id=conversation.id,
        **payload,
    )


RESOLVED_TEXT = "La conversación está resuelta: respondé para volver a abrirla."


def take(session: Session, conversation_id: int, user: str) -> None:
    """ "Tomar control": with a human, assigned to whoever took it (the bot stops). Not on a
    resolved one: replying reopens it (assigned to whoever replies)."""
    conversation = _locked(session, conversation_id)
    if conversation.status == WaConversationStatus.RESOLVED:
        raise InboxError(RESOLVED_TEXT)
    previous_status = WaConversationStatus(conversation.status).value
    previous_assignee = conversation.assigned_to
    conversations.assign(conversation, user)
    _audit(
        session, conversation, user, "conversation_taken",
        previous_status=previous_status, previous_assignee=previous_assignee,
    )  # fmt: skip
    session.commit()


def return_to_bot(session: Session, conversation_id: int, user: str) -> None:
    conversation = _locked(session, conversation_id)
    if conversation.status == WaConversationStatus.BOT:
        raise InboxError("La conversación ya está con el bot.")
    if conversation.status == WaConversationStatus.RESOLVED:
        raise InboxError(RESOLVED_TEXT)
    previous_status = WaConversationStatus(conversation.status).value
    conversations.return_to_bot(conversation)
    _audit(
        session, conversation, user, "conversation_returned_to_bot",
        previous_status=previous_status,
    )  # fmt: skip
    session.commit()


def resolve(session: Session, conversation_id: int, user: str, now: datetime) -> None:
    conversation = _locked(session, conversation_id)
    if conversation.status == WaConversationStatus.RESOLVED:
        raise InboxError("La conversación ya estaba resuelta.")
    previous_status = WaConversationStatus(conversation.status).value
    conversations.resolve(conversation, now)
    _audit(session, conversation, user, "conversation_resolved", previous_status=previous_status)
    session.commit()


def add_note(session: Session, conversation_id: int, user: str, text: str) -> WaMessage:
    text = text.strip()
    if not text:
        raise InboxError("Escribí la nota.")
    if len(text) > MAX_NOTE:
        raise InboxError(f"La nota es demasiado larga (máximo {MAX_NOTE} caracteres).")
    conversation = _locked(session, conversation_id)
    note = WaMessage(
        conversation_id=conversation.id,
        direction=WaDirection.OUTBOUND,
        author=WaAuthor.OPERATOR,
        operator=user,
        message_type="note",
        body=text,
        is_internal_note=True,
    )
    session.add(note)
    session.flush()
    _audit(session, conversation, user, "conversation_note_added", message_id=note.id)
    session.commit()
    return note


@dataclass(frozen=True)
class SendResult:
    message_id: int
    sent: bool
    error: str = ""
    # The conversation was assigned to someone else: it stays with them.
    assigned_to_other: str | None = None


def _take_for_reply(conversation: WaConversation, user: str) -> str | None:
    """Replying takes the conversation (the bot stops), assigned to whoever replies, unless
    another user of the panel has it: it stays with them (returned: who)."""
    other = conversation.assigned_to
    if conversation.status == WaConversationStatus.HUMAN and other and other != user:
        return other
    conversations.assign(conversation, user)
    return None


def _send(
    session: Session,
    conversation_id: int,
    user: str,
    now: datetime,
    sender: Sender | None,
    *,
    kind: str,
    body: str,
    send,  # type: ignore[no-untyped-def]  # (sender, wa_id) -> wamid
    action: str,
    **audit: object,
) -> SendResult:
    if sender is None:
        raise InboxError(
            "WhatsApp no está configurado en este servidor (faltan sus claves en .env)."
        )
    conversation = _locked(session, conversation_id)
    contact = _contact(session, conversation)
    if kind != "template" and not window_open(conversation, now):
        raise InboxError(WINDOW_CLOSED_TEXT)
    other = _take_for_reply(conversation, user)
    row = WaMessage(
        conversation_id=conversation.id,
        direction=WaDirection.OUTBOUND,
        author=WaAuthor.OPERATOR,
        operator=user,
        message_type=kind,
        body=body,
    )
    error = ""
    try:
        row.wa_message_id = send(sender, contact.wa_id)
        row.status = WaMessageStatus.SENT
    except WhatsAppError as exc:
        row.status = WaMessageStatus.FAILED
        row.error_code = exc.code
        row.error_text = str(exc)[:1000]
        error = str(exc)
    row.status_at = now
    session.add(row)
    session.flush()
    _audit(session, conversation, user, action, message_id=row.id, sent=not error, **audit)
    session.commit()
    return SendResult(message_id=row.id, sent=not error, error=error, assigned_to_other=other)


def reply(
    session: Session,
    conversation_id: int,
    user: str,
    text: str,
    sender: Sender | None,
    now: datetime,
) -> SendResult:
    """Free text (only inside the 24-hour window). Takes the conversation."""
    text = text.strip()
    if not text:
        raise InboxError("Escribí el mensaje.")
    if len(text) > MAX_TEXT:
        raise InboxError(f"El mensaje es demasiado largo (máximo {MAX_TEXT} caracteres).")
    return _send(
        session, conversation_id, user, now, sender,
        kind="text", body=text, send=lambda s, to: s.send_text(to, text),
        action="conversation_replied",
    )  # fmt: skip


def send_template(
    session: Session,
    conversation_id: int,
    user: str,
    template_id: int,
    sender: Sender | None,
    now: datetime,
) -> SendResult:
    """An approved template (what can go outside the window). Takes the conversation."""
    template = session.get(WaTemplate, template_id)
    if template is None or not template.active:
        raise InboxError("Elegí una plantilla.")
    name, language = template.name, template.language
    return _send(
        session, conversation_id, user, now, sender,
        kind="template", body=template.body,
        send=lambda s, to: s.send_template(to, name, language),
        action="conversation_template_sent", template_id=template.id,
    )  # fmt: skip


def conversation_with_contact(
    session: Session, conversation_id: int
) -> tuple[WaConversation, WaContact] | None:
    row = session.execute(
        select(WaConversation, WaContact)
        .join(WaContact, WaContact.id == WaConversation.contact_id)
        .where(WaConversation.id == conversation_id)
    ).first()
    return (row[0], row[1]) if row else None
