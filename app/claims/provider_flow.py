"""What a provider writes, answered by the code (never by the agent of the owners). The processor
(app.channels.processor) hands every message of an active provider's WhatsApp here, before
anything else.

- A button of a claim (its signed payload, app.claims.payloads):
  "Recibido" (ack): from sent, the claim is acknowledged; the provider gets thanks with the
  button "Ya está solucionado" and the claim's photos, and the neighbors are told. Already
  acknowledged: only the thanks again.
  "No puedo atenderlo" (decline): the studio attends it (an alert in the panel); the neighbors
  are not told.
  "Ya está solucionado" (solved): from sent or acknowledged, the claim is closed and the
  neighbors are told.
  A payload that is not valid, of a closed or cancelled claim, or of a claim that has another
  provider now: "Ese reclamo ya no está a tu cargo..." and nothing changes.
- Free text is never read as an answer. With one open claim of this provider it goes to the
  claim's history; if that claim is acknowledged, the provider is reminded (at most once every
  NUDGE_EVERY) to tap "Ya está solucionado". With several, it only stays in the conversation.

The provider's conversation stays in the state "provider": the employees see it in
"Conversaciones" and may answer by hand.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.identity import to_e164
from app.claims import payloads, texts
from app.claims.notify import Notifier
from app.claims.service import (
    ClaimProblem,
    close_claim,
    mark_acknowledged,
    provider_declined,
    record_event,
)
from app.db.models import (
    OPEN_CLAIM_STATUSES,
    Claim,
    ClaimActor,
    ClaimEventKind,
    ClaimStatus,
    Provider,
    WaConversation,
    WaConversationStatus,
)

NUDGE_EVERY = timedelta(hours=12)
MAX_PROVIDER_TEXT = 1000


@dataclass(frozen=True)
class ProviderMessage:
    text: str
    payload: str
    conversation_id: int


def provider_for_phone(session: Session, phone: str) -> Provider | None:
    """The active provider whose WhatsApp this is (None for everybody else)."""
    e164 = to_e164(phone) if phone else None
    if e164 is None:
        return None
    return session.scalars(
        select(Provider).where(Provider.whatsapp_e164 == e164, Provider.active.is_(True)).limit(1)
    ).first()


def keep_as_provider(session: Session, conversation_id: int) -> None:
    """A provider's conversation never goes to the bot (an employee's stays hers)."""
    conversation = session.get(WaConversation, conversation_id, with_for_update=True)
    if conversation is not None and conversation.status in (
        WaConversationStatus.BOT,
        WaConversationStatus.RESOLVED,
    ):
        conversation.status = WaConversationStatus.PROVIDER


def handle(
    session: Session,
    provider: Provider,
    message: ProviderMessage,
    notifier: Notifier,
    *,
    now: datetime | None = None,
) -> str:
    """Answers the provider and changes the claim if it is a valid button. Commits. Returns
    what happened (for bot_events)."""
    now = now or datetime.now(UTC)
    keep_as_provider(session, message.conversation_id)
    if payloads.is_claim_payload(message.payload):
        action = _button(session, provider, message.payload, notifier, now)
    else:
        action = _free_text(session, provider, message.text, notifier, now)
    session.commit()
    return action


def _not_yours(session: Session, provider: Provider, notifier: Notifier) -> str:
    notifier.to_provider(session, provider, texts.PROVIDER_NOT_YOURS)
    return "not_yours"


def _button(
    session: Session, provider: Provider, payload: str, notifier: Notifier, now: datetime
) -> str:
    read = payloads.read(notifier.secret, payload, payloads.provider_subject(provider.id))
    claim = session.get(Claim, read.claim_id, with_for_update=True) if read else None
    if read is None or claim is None or claim.provider_id != provider.id or not claim.is_open:
        return _not_yours(session, provider, notifier)
    solved = [notifier.solved_button(claim, provider)]
    try:
        match read.action:
            case "ack" if claim.status == ClaimStatus.SENT:
                mark_acknowledged(session, claim, actor=ClaimActor.PROVIDER, now=now)
                notifier.to_provider(
                    session, provider, texts.PROVIDER_THANKS.format(number=claim.number), solved
                )
                notifier.send_photos(session, claim, provider)
                notifier.notify_neighbors(session, claim, "confirmed")
                return "acknowledged"
            case "ack" if claim.status == ClaimStatus.ACKNOWLEDGED:
                notifier.to_provider(
                    session, provider, texts.PROVIDER_THANKS.format(number=claim.number), solved
                )
                return "acknowledged_again"
            case "decline":
                provider_declined(session, claim, now=now)
                notifier.to_provider(session, provider, texts.PROVIDER_DECLINED)
                return "declined"
            case "solved" if claim.status in (ClaimStatus.SENT, ClaimStatus.ACKNOWLEDGED):
                close_claim(
                    session, claim, ClaimStatus.SOLVED, f"Avisado por {provider.name}",
                    actor=ClaimActor.PROVIDER, now=now,
                )  # fmt: skip
                notifier.to_provider(
                    session, provider, texts.PROVIDER_CLOSED.format(number=claim.number)
                )
                notifier.notify_neighbors(session, claim, "solved")
                return "solved"
    except ClaimProblem:
        session.rollback()
    return _not_yours(session, provider, notifier)


def _free_text(
    session: Session, provider: Provider, text: str, notifier: Notifier, now: datetime
) -> str:
    text = (text or "").strip()
    if not text:
        return "nothing"
    open_claims = list(
        session.scalars(
            select(Claim).where(
                Claim.provider_id == provider.id, Claim.status.in_(OPEN_CLAIM_STATUSES)
            )
        )
    )
    if len(open_claims) != 1:
        return "text_several" if open_claims else "text_none"
    claim = open_claims[0]
    record_event(
        session, claim, ClaimEventKind.PROVIDER_MESSAGE, actor=ClaimActor.PROVIDER,
        text=text[:MAX_PROVIDER_TEXT], now=now,
    )  # fmt: skip
    nudged = claim.provider_nudged_at
    if claim.status == ClaimStatus.ACKNOWLEDGED and (nudged is None or now - nudged >= NUDGE_EVERY):
        notifier.to_provider(
            session,
            provider,
            texts.PROVIDER_NUDGE.format(number=claim.number),
            [notifier.solved_button(claim, provider)],
        )
        claim.provider_nudged_at = now
        return "text_nudged"
    return "text_saved"
