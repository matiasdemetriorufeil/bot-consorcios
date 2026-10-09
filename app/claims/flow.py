"""Reporting a claim by WhatsApp, step by step, run by the code (never by the model).

The agent starts it (the tool start_claim, or the menu's "Registrar reclamo") and from then on
it hands every message to handle() while a draft (ClaimDraft, one per phone) is in a step. The
steps go with buttons and lists:

    unit (if there are several)  ->  kind of problem  ->  safety text (if any)
    -> follow-up question (if any)  ->  description  ->  photos  ->  summary "¿Lo registro?"

Only "Sí, registrar" in the summary creates the claim (app.claims.service.create_claim, source
bot) and, if it has a provider with WhatsApp, sends it to it (app.claims.notify). "cancelar"
ends it at any step. An answer that fits no option of a list or of buttons gets the same step
once more ("No te entendí..."); a second one drops the draft with a short notice and the
message goes on to the agent (FlowReply.handled False). "Registrar reclamo" tapped in the
notice of a solved claim starts with that problem proposed and links the new claim to it. In
the photos step a text is added to the description instead; in the description step a photo is
kept and the description asked again. A draft unanswered for 30 minutes expires.

Who may: an identified owner or tenant with a unit in a building with "Reclamos por el bot".
An unknown number waits in WAITING_IDENTITY while the agent verifies it as usual; resume()
picks up after confirm_email_code. The safety text of an urgent kind goes out first, even to an
unknown number or from a building without claims by the bot.

Every step is logged in bot_events ("claim_flow": step and action, never what the person wrote
nor the photos).
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.bot.choices import Choice
from app.bot.identity import Identity, identify_by_phone, to_e164
from app.bot.unit_search import display_building_name
from app.claims import payloads, texts
from app.claims.service import ClaimProblem, Reporter, create_claim
from app.claims.setup import enabled_categories, split_for_whatsapp
from app.db.models import (
    BotEvent,
    Building,
    Claim,
    ClaimAttachment,
    ClaimCategory,
    ClaimDraft,
    ClaimDraftStep,
    ClaimScope,
    ClaimSource,
    ClaimStatus,
    Unit,
    WaMediaStatus,
    WaMessage,
)

EXPIRES_AFTER = timedelta(minutes=30)
MAX_DESCRIPTION = 1000
MAX_PHOTOS = 5
# What the bot takes as the "Sí" / "No" buttons when typed.
YES_WORDS = frozenset({"si", "dale", "ok", "bueno", "claro"})
NO_WORDS = frozenset({"no"})

Step = ClaimDraftStep
CHOICE_STEPS = (Step.CHOOSE_UNIT, Step.CONFIRM_CATEGORY, Step.CHOOSE_CATEGORY, Step.CONFIRM)
FREE_TEXT_STEPS = (Step.FOLLOW_UP, Step.DESCRIPTION)
PHOTO_STEPS = (Step.FOLLOW_UP, Step.DESCRIPTION, Step.PHOTOS)


@dataclass
class FlowReply:
    """What the bot answers. blocks go before text, as is (safety texts, notices). handled
    False: the message was not for the flow and goes on to the agent (blocks are a notice to
    send before the agent's answer). status: for start_claim's result (the model reads it)."""

    text: str = ""
    choices: tuple[Choice, ...] = ()
    blocks: list[str] = field(default_factory=list)
    handled: bool = True
    status: str = "ok"
    urgent: bool = False


# --- Helpers --------------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Lowercase, without accents nor punctuation: "¡Sí, registrar!" -> "si registrar"."""
    plain = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in plain if not unicodedata.combining(c)).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", plain))


CANCEL_WORDS = frozenset(normalize(w) for w in texts.CANCEL_WORDS)
_STOP = frozenset(
    [
        "de",
        "del",
        "la",
        "las",
        "el",
        "los",
        "en",
        "un",
        "una",
        "y",
        "o",
        "a",
        "no",
        "hay",
        "mi",
        "mis",
        "se",
        "que",
        "por",
        "con",
        "funciona",
        "anda",
        "tengo",
        "solo",
        "todo",
        "esta",
        "estan",
        # Where it happens, not what: "no hay agua en el edificio" is about the water.
        "edificio",
        "departamento",
        "depto",
        "unidad",
        "casa",
    ]
)


def _stems(text: str) -> set[str]:
    return {w[:5] for w in normalize(text).split() if w not in _STOP and len(w) > 2}


def match_category(categories: Sequence[ClaimCategory], hint: str) -> ClaimCategory | None:
    """The one kind of problem the hint clearly names ("no anda el ascensor" -> the lifts),
    or None when none or several fit equally. A word of the list's title counts double: "no
    hay agua" is "No hay agua", not "Humedad o filtración de agua"."""
    words = _stems(hint)
    if not words:
        return None

    def score(c: ClaimCategory) -> int:
        title = words & _stems(c.list_title)
        rest = words & _stems(f"{c.name} {c.list_description or ''}")
        return 2 * len(title) + len(rest - title)

    scored = sorted(((score(c), c) for c in categories), key=lambda item: -item[0])
    if not scored or scored[0][0] == 0:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


def safety_block(category: ClaimCategory | None) -> str | None:
    """The kind's safety text, as is, with its emergency phone if it has one."""
    if category is None or not (category.safety_text or "").strip():
        return None
    text = category.safety_text.strip()
    if (category.emergency_phone or "").strip():
        text += "\n" + texts.EMERGENCY_PHONE.format(phone=category.emergency_phone.strip())
    return text


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _log(session: Session, draft: ClaimDraft | str, action: str, **payload: Any) -> None:
    if isinstance(draft, ClaimDraft):
        phone, conversation_id = draft.phone_e164, draft.conversation_id
        payload = {"step": str(draft.step), **payload}
    else:
        phone, conversation_id = draft, None
    session.add(
        BotEvent(
            conversation_id=conversation_id,
            phone_e164=phone,
            event_type="claim_flow",
            payload={"action": action, **payload},
        )
    )


@dataclass(frozen=True)
class EligibleUnit:
    unit_id: int
    label: str
    building: str


def eligible_units(session: Session, who: Identity) -> list[EligibleUnit]:
    """The person's units in active buildings with "Reclamos por el bot" (once each)."""
    if not who.units:
        return []
    enabled = set(
        session.scalars(
            select(Building.id).where(
                Building.id.in_({u.building_id for u in who.units}),
                Building.active.is_(True),
                Building.claims_bot_enabled.is_(True),
            )
        )
    )
    found: dict[int, EligibleUnit] = {}
    for u in who.units:
        if u.building_id in enabled and u.unit_id not in found:
            found[u.unit_id] = EligibleUnit(
                u.unit_id, u.unit_label, display_building_name(u.building_name)
            )
    return list(found.values())


def _hinted_anywhere(session: Session, hint: str) -> ClaimCategory | None:
    """Before knowing the building: among every active kind (for the safety text only)."""
    if not hint.strip():
        return None
    active = session.scalars(select(ClaimCategory).where(ClaimCategory.active.is_(True))).all()
    return match_category(active, hint)


# --- The draft ------------------------------------------------------------------------------


def active_draft(
    session: Session, phone: str, now: datetime | None = None
) -> tuple[ClaimDraft | None, bool]:
    """(the phone's draft, whether one just expired). An expired draft is deleted."""
    e164 = to_e164(phone) if phone else None
    if e164 is None:
        return None, False
    draft = session.scalar(select(ClaimDraft).where(ClaimDraft.phone_e164 == e164))
    if draft is None:
        return None, False
    if draft.updated_at < _now(now) - EXPIRES_AFTER:
        _drop(session, draft, "expired")
        return None, True
    return draft, False


def in_steps(draft: ClaimDraft | None) -> bool:
    """Whether the flow (not the agent) answers this phone's next message."""
    return draft is not None and draft.step != Step.WAITING_IDENTITY


def accepts_photos(session: Session, phone: str, now: datetime | None = None) -> bool:
    """Whether a photo sent alone goes to the flow (instead of the fixed "no veo imágenes")."""
    draft, _ = active_draft(session, phone, now)
    return draft is not None and draft.step in PHOTO_STEPS


def _drop(session: Session, draft: ClaimDraft, action: str) -> None:
    _log(session, draft, action)
    session.delete(draft)
    session.commit()


def _offer(
    draft: ClaimDraft, step: Step, text: str, choices: Sequence[Choice], now: datetime
) -> FlowReply:
    draft.step = step
    draft.options = {
        "items": [[c.title, c.value, c.description] for c in choices],
        "text": text,
    }
    draft.reprompted = False
    draft.updated_at = now
    return FlowReply(text=text, choices=tuple(choices))


def _offer_again(draft: ClaimDraft, now: datetime) -> FlowReply:
    """The same step once more, after an answer that fit none of its options."""
    items = draft.options.get("items", [])
    choices = tuple(Choice(item[0], item[1], item[2] if len(item) > 2 else "") for item in items)
    draft.reprompted = True
    draft.updated_at = now
    return FlowReply(
        text=f"{texts.REPROMPT}\n\n{draft.options.get('text', '')}".strip(), choices=choices
    )


def _pick(draft: ClaimDraft, text: str) -> str | None:
    """The value of the option the answer names: its title, its number, or a typed yes/no."""
    items = draft.options.get("items", [])
    answer = normalize(text)
    if not answer:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(items):
        return items[int(answer) - 1][1]
    for item in items:
        if normalize(item[0]) == answer:
            return item[1]
    values = [item[1] for item in items]
    if "yes" in values and answer in YES_WORDS:
        return "yes"
    if "no" in values and answer in NO_WORDS:
        return "no"
    return None


# --- Starting -------------------------------------------------------------------------------


def start(
    session: Session,
    phone: str,
    hint: str = "",
    *,
    conversation_id: int | None = None,
    now: datetime | None = None,
    again_payload: str = "",
    secret: bytes | None = None,
) -> FlowReply:
    """Begin a claim (a new draft replaces an old one). Commits. status: "ok" (the first step
    is the reply), "not_verified" (the agent verifies the number; the flow resumes after),
    "not_available" (no unit in a building with claims by the bot: text says so) or
    "no_phone". The safety text of an urgent kind the hint names goes in blocks anyway.
    again_payload: the payload of "Registrar reclamo" in the notice of a solved claim (signed
    for this phone): that claim's problem is proposed and the new one is linked to it."""
    now = _now(now)
    e164 = to_e164(phone) if phone else None
    if e164 is None:
        return FlowReply(status="no_phone", handled=False)
    session.execute(delete(ClaimDraft).where(ClaimDraft.phone_e164 == e164))
    previous_id = None
    if again_payload and secret is not None:
        read = payloads.read(secret, again_payload, e164)
        previous = session.get(Claim, read.claim_id) if read and read.action == "again" else None
        if previous is not None:
            previous_id = previous.id
            hint = hint or previous.category.list_title
    reply = _start(session, e164, hint, conversation_id, now)
    if previous_id is not None:
        draft = session.scalar(select(ClaimDraft).where(ClaimDraft.phone_e164 == e164))
        if draft is not None:
            draft.previous_claim_id = previous_id
            session.commit()
    return reply


def _start(
    session: Session, e164: str, hint: str, conversation_id: int | None, now: datetime
) -> FlowReply:
    hint = (hint or "").strip()[:200]
    hinted = _hinted_anywhere(session, hint)
    urgent = bool(hinted and hinted.urgent)
    early_safety = safety_block(hinted) if urgent else None
    who = identify_by_phone(session, e164)
    if not who.known:
        draft = ClaimDraft(
            phone_e164=e164,
            conversation_id=conversation_id,
            step=Step.WAITING_IDENTITY,
            category_hint=hint or None,
            safety_sent=early_safety is not None,
            created_at=now,
            updated_at=now,
        )
        session.add(draft)
        session.flush()
        _log(session, draft, "started", known=False, urgent=urgent)
        session.commit()
        blocks = [early_safety] if early_safety else []
        return FlowReply(blocks=blocks, status="not_verified", urgent=urgent)
    return _begin(session, e164, who, hint, conversation_id, now, early_safety, urgent)


def resume(session: Session, phone: str, now: datetime | None = None) -> FlowReply | None:
    """After the number got verified: the draft that waited goes on (None if there is none).
    Commits."""
    now = _now(now)
    draft, _ = active_draft(session, phone, now)
    if draft is None or draft.step != Step.WAITING_IDENTITY:
        return None
    who = identify_by_phone(session, draft.phone_e164)
    if not who.known:
        return None
    e164, hint = draft.phone_e164, draft.category_hint or ""
    conversation_id, safety_sent = draft.conversation_id, draft.safety_sent
    session.delete(draft)
    session.flush()
    return _begin(session, e164, who, hint, conversation_id, now, safety_sent=safety_sent)


def _begin(
    session: Session,
    e164: str,
    who: Identity,
    hint: str,
    conversation_id: int | None,
    now: datetime,
    early_safety: str | None = None,
    urgent: bool = False,
    *,
    safety_sent: bool = False,
) -> FlowReply:
    units = eligible_units(session, who)
    if not units:
        _log(session, e164, "not_available", urgent=urgent)
        session.commit()
        blocks = [early_safety] if early_safety else []
        return FlowReply(
            text=texts.NOT_AVAILABLE, blocks=blocks, status="not_available", urgent=urgent
        )
    draft = ClaimDraft(
        phone_e164=e164,
        conversation_id=conversation_id,
        step=Step.CHOOSE_UNIT,
        category_hint=hint or None,
        safety_sent=safety_sent or early_safety is not None,
        created_at=now,
        updated_at=now,
    )
    session.add(draft)
    session.flush()
    _log(session, draft, "started", known=True, units=len(units), urgent=urgent)
    if len(units) == 1:
        draft.unit_id = units[0].unit_id
        reply = _ask_category(session, draft, now)
    else:
        reply = _ask_unit(draft, units, now)
    if early_safety:
        reply.blocks.insert(0, early_safety)
    reply.urgent = urgent
    session.commit()
    return reply


# --- Asking ---------------------------------------------------------------------------------


def _ask_unit(draft: ClaimDraft, units: list[EligibleUnit], now: datetime) -> FlowReply:
    labels = [u.label for u in units]
    choices = []
    for u in units:
        title = u.label if labels.count(u.label) == 1 else f"{u.label} · {u.building}"
        choices.append(Choice(title[:24], f"unit:{u.unit_id}", u.building[:72]))
    if len(choices) <= 2:
        choices.append(Choice(texts.CANCEL, "cancel"))
    return _offer(draft, Step.CHOOSE_UNIT, texts.CHOOSE_UNIT, choices, now)


def _unit(session: Session, draft: ClaimDraft) -> Unit:
    unit = session.get(Unit, draft.unit_id) if draft.unit_id else None
    if unit is None:
        raise ClaimProblem("esa unidad ya no está.")
    return unit


def _kinds(session: Session, draft: ClaimDraft) -> list[ClaimCategory]:
    return enabled_categories(session, _unit(session, draft).building_id)


def _ask_category(session: Session, draft: ClaimDraft, now: datetime) -> FlowReply:
    categories = _kinds(session, draft)
    if not categories:
        _drop(session, draft, "no_kinds")
        return FlowReply(text=texts.NO_KINDS, status="not_available")
    hinted = match_category(categories, draft.category_hint or "")
    if hinted is None:
        return _category_page(session, draft, 0, now)
    choices = [
        Choice(texts.YES, f"cat:{hinted.id}"),
        Choice(texts.OTHER_KIND, "other"),
        Choice(texts.CANCEL, "cancel"),
    ]
    text = texts.CONFIRM_KIND.format(title=hinted.list_title)
    return _offer(draft, Step.CONFIRM_CATEGORY, text, choices, now)


def _category_page(session: Session, draft: ClaimDraft, page: int, now: datetime) -> FlowReply:
    """The kinds as WhatsApp lists: all in one if they fit, else 9 + "Más opciones" per page
    (app.claims.setup.split_for_whatsapp)."""
    rest = _kinds(session, draft)
    for _ in range(page):
        rest = split_for_whatsapp(rest)[1]
    if not rest:  # the list got shorter meanwhile: back to the first page
        page, rest = 0, _kinds(session, draft)
    first, more = split_for_whatsapp(rest)
    choices = [Choice(c.list_title, f"cat:{c.id}", c.list_description or "") for c in first]
    if more:
        choices.append(Choice(texts.MORE_OPTIONS, "more", texts.MORE_OPTIONS_DESCRIPTION))
    elif len(choices) < 2:
        choices.append(Choice(texts.CANCEL, "cancel"))
    draft.category_page = page
    text = texts.CHOOSE_KIND if page == 0 else texts.CHOOSE_KIND_MORE
    return _offer(draft, Step.CHOOSE_CATEGORY, f"{text}\n{texts.CANCEL_HINT}", choices, now)


def _category(session: Session, draft: ClaimDraft) -> ClaimCategory:
    category = session.get(ClaimCategory, draft.category_id) if draft.category_id else None
    if category is None:
        raise ClaimProblem("ese problema ya no está.")
    return category


def _after_category(session: Session, draft: ClaimDraft, now: datetime) -> FlowReply:
    category = _category(session, draft)
    blocks = []
    if not draft.safety_sent and (block := safety_block(category)):
        blocks.append(block)
        draft.safety_sent = True
    if (category.follow_up_question or "").strip():
        draft.step = Step.FOLLOW_UP
        reply = _ask_again(session, draft, now)
    else:
        reply = _ask_description(session, draft, now)
    reply.blocks = blocks + reply.blocks
    return reply


def _ask_description(session: Session, draft: ClaimDraft, now: datetime) -> FlowReply:
    draft.step = Step.DESCRIPTION
    return _ask_again(session, draft, now)


def _ask_again(
    session: Session, draft: ClaimDraft, now: datetime, note: str | None = None
) -> FlowReply:
    """The current free-text step (the follow-up question or the description), with a note
    before it ("Recibí la foto.", "Es un poco largo...")."""
    question = texts.DESCRIBE
    if draft.step == Step.FOLLOW_UP:
        question = (_category(session, draft).follow_up_question or "").strip() or question
    text = "\n".join(part for part in (note, question, texts.CANCEL_HINT) if part)
    return _offer(draft, Step(draft.step), text, (), now)


def _ask_photos(draft: ClaimDraft, now: datetime, text: str = texts.PHOTOS) -> FlowReply:
    choices = [Choice(texts.DONE, "done")]
    if not draft.attachment_ids:
        choices.append(Choice(texts.NO_PHOTOS, "done"))
    choices.append(Choice(texts.CANCEL, "cancel"))
    return _offer(draft, Step.PHOTOS, text, choices, now)


def _ask_confirmation(session: Session, draft: ClaimDraft, now: datetime) -> FlowReply:
    unit = _unit(session, draft)
    category = _category(session, draft)
    building = session.get(Building, unit.building_id)
    photos = len(draft.attachment_ids or [])
    text = texts.SUMMARY.format(
        building=display_building_name(building.name) if building else "",
        unit=unit.label if category.scope == ClaimScope.UNIT else texts.WHOLE_BUILDING,
        problem=category.list_title,
        description=draft.description or "",
        photos=photos or "ninguna",
    )
    choices = [Choice(texts.REGISTER, "yes"), Choice(texts.DONT_REGISTER, "no")]
    return _offer(draft, Step.CONFIRM, text, choices, now)


# --- Answering ------------------------------------------------------------------------------


def _add_photos(session: Session, draft: ClaimDraft, photo_ids: Sequence[int]) -> str | None:
    """Keeps the stored images among photo_ids (up to MAX_PHOTOS). The text to say when one
    could not be kept (the limit, or not stored), else None."""
    kept = list(draft.attachment_ids or [])
    problem = None
    for message_id in photo_ids:
        message = session.get(WaMessage, message_id)
        stored = (
            message is not None
            and message.message_type == "image"
            and message.media_status == WaMediaStatus.STORED
        )
        if not stored:
            problem = texts.PHOTO_NOT_SAVED
        elif message_id in kept:
            continue
        elif len(kept) >= MAX_PHOTOS:
            problem = texts.PHOTO_LIMIT.format(limit=MAX_PHOTOS)
        else:
            kept.append(message_id)
    draft.attachment_ids = kept
    return problem


def handle(
    session: Session,
    draft: ClaimDraft,
    text: str,
    photo_ids: Sequence[int] = (),
    *,
    now: datetime | None = None,
    notifier: Any = None,
) -> FlowReply:
    """The next message of a draft in a step. Commits. notifier (app.claims.notify.Notifier):
    sends a new claim to its provider; without one, nothing is sent."""
    now = _now(now)
    text = (text or "").strip()
    if normalize(text) in CANCEL_WORDS or _pick(draft, text) == "cancel":
        _drop(session, draft, "cancelled")
        return FlowReply(text=texts.CANCELLED)
    try:
        reply = _answer(session, draft, text, photo_ids, now, notifier)
    except ClaimProblem as exc:
        session.rollback()
        _drop(session, draft, "failed")
        return FlowReply(text=texts.FAILED.format(reason=str(exc)))
    if reply.handled and reply.status == "ok":
        _log(session, draft, "asked")
    session.commit()
    return reply


def _off_step(session: Session, draft: ClaimDraft) -> FlowReply:
    """Something that is not an answer to this step: the draft goes, the agent answers."""
    _drop(session, draft, "dropped")
    return FlowReply(blocks=[texts.DROPPED], handled=False, status="dropped")


def _answer(
    session: Session,
    draft: ClaimDraft,
    text: str,
    photo_ids: Sequence[int],
    now: datetime,
    notifier: Any = None,
) -> FlowReply:
    step = Step(draft.step)
    if step in CHOICE_STEPS:
        value = _pick(draft, text)
        if value is None and not draft.reprompted:
            return _offer_again(draft, now)
        if value is None:
            return _off_step(session, draft)
        return _choice(session, draft, step, value, now, notifier)
    if step in FREE_TEXT_STEPS:
        if photo_ids:
            problem = _add_photos(session, draft, photo_ids)
            if not text:
                return _ask_again(session, draft, now, problem or texts.PHOTO_SAVED)
        if not text:
            return _ask_again(session, draft, now)
        if len(text) > MAX_DESCRIPTION:
            return _ask_again(session, draft, now, texts.TOO_LONG.format(limit=MAX_DESCRIPTION))
        if step == Step.FOLLOW_UP:
            draft.follow_up_answer = text
            return _ask_description(session, draft, now)
        draft.description = text
        return _ask_photos(draft, now)
    # Step.PHOTOS
    if photo_ids:
        problem = _add_photos(session, draft, photo_ids)
        if not text:
            count = len(draft.attachment_ids)
            return _ask_photos(
                draft, now, problem or texts.PHOTO_RECEIVED.format(count=count, limit=MAX_PHOTOS)
            )
    if _pick(draft, text) == "done":
        return _ask_confirmation(session, draft, now)
    if not text:
        return _ask_photos(draft, now)
    joined = f"{draft.description or ''}\n{text}".strip()
    if len(joined) > MAX_DESCRIPTION:
        return _ask_photos(draft, now, texts.DESCRIPTION_FULL.format(limit=MAX_DESCRIPTION))
    draft.description = joined
    return _ask_photos(draft, now, texts.ADDED_TO_DESCRIPTION)


def _choice(
    session: Session,
    draft: ClaimDraft,
    step: Step,
    value: str,
    now: datetime,
    notifier: Any = None,
) -> FlowReply:
    if step == Step.CHOOSE_UNIT and value.startswith("unit:"):
        draft.unit_id = int(value.removeprefix("unit:"))
        return _ask_category(session, draft, now)
    if value == "other":
        return _category_page(session, draft, 0, now)
    if value == "more":
        return _category_page(session, draft, draft.category_page + 1, now)
    if value.startswith("cat:"):
        draft.category_id = int(value.removeprefix("cat:"))
        return _after_category(session, draft, now)
    if step == Step.CONFIRM and value == "no":
        _drop(session, draft, "not_registered")
        return FlowReply(text=texts.NOT_REGISTERED, status="not_registered")
    if step == Step.CONFIRM and value == "yes":
        return _register(session, draft, now, notifier)
    return _off_step(session, draft)


def _register(
    session: Session, draft: ClaimDraft, now: datetime, notifier: Any = None
) -> FlowReply:
    who = identify_by_phone(session, draft.phone_e164)
    unit = _unit(session, draft)
    if not who.known or all(u.unit_id != unit.id for u in who.units):
        raise ClaimProblem("no pude confirmar que la unidad sea tuya.")
    category = _category(session, draft)
    result = create_claim(
        session,
        building_id=unit.building_id,
        category_id=category.id,
        unit_id=unit.id,
        description=draft.description or "",
        reporter=Reporter(
            name=who.full_name,
            phone_e164=draft.phone_e164,
            person_id=who.person_id,
            unit_id=unit.id,
        ),
        source=ClaimSource.BOT,
        wa_conversation_id=draft.conversation_id,
        follow_up_answer=draft.follow_up_answer,
        previous_claim_id=draft.previous_claim_id,
        now=now,
    )
    claim = result.claim
    attached = {a.wa_message_id for a in claim.attachments}
    for message_id in draft.attachment_ids or []:
        if message_id not in attached:
            claim.attachments.append(ClaimAttachment(wa_message_id=message_id))
    if result.already_reporter:
        text = texts.ALREADY_JOINED.format(number=claim.number)
    elif result.repeated:
        text = texts.JOINED.format(number=claim.number)
    else:
        session.flush()
        done = (
            notifier.dispatch(session, claim)
            if notifier is not None and claim.status == ClaimStatus.PENDING_SEND
            else None
        )
        if done is not None and done.sent is not None and done.sent.ok:
            text = texts.CREATED_AND_SENT.format(number=claim.number, problem=category.list_title)
        elif done is not None and done.scheduled_at is not None:
            text = texts.CREATED_SCHEDULED.format(
                number=claim.number, problem=category.list_title, when=done.when
            )
        else:
            template = (
                texts.CREATED_WITH_PROVIDER if claim.provider_id else texts.CREATED_FOR_STUDIO
            )
            text = template.format(number=claim.number, problem=category.list_title)
        if claim.previous is not None:
            text += " " + texts.PREVIOUS.format(number=claim.previous.number)
    _log(
        session,
        draft,
        "registered",
        claim_id=claim.id,
        repeated=result.repeated,
        photos=len(draft.attachment_ids or []),
    )
    session.delete(draft)
    session.flush()
    return FlowReply(text=text, status="registered")
