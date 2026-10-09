"""WhatsApp messages of the claims: the provider gets the claim (a template with the buttons
"Recibido" / "No puedo atenderlo"), the photos when it confirms, and the neighbors are told when
the provider confirms or the claim is solved. Used by the claim flow (app.claims.flow), the
provider's answers (app.claims.provider_flow) and the panel. The claim only changes through
app.claims.service.

- Template variables are built here (template_params): one line each, never empty ("—"), the
  detail cut at DETAIL_LIMIT. The contact of the neighbor goes to the provider only for a kind
  of one unit (decision of 08/10); for the whole building, "Todo el edificio".
- Every button carries a signed payload (app.claims.payloads): a made-up one does nothing.
- dispatch() respects the providers' hours ("Configuración de reclamos"): out of them a normal
  claim is scheduled for the next opening (app.claims.jobs sends it); an urgent one goes at
  once if "urgentes a cualquier hora" is on. The jobs also send the reminder (remind()).
- A neighbor whose 24-hour window is open gets free text; otherwise the template. A neighbor
  without a phone is not told (the history says so). The same phone is told once.
- Every message is stored in the recipient's conversation (created if it did not exist); a
  provider's is in the state "provider" (the bot never answers it). Every notice is an event of
  the claim (with its WhatsApp message, whose delivery the webhook updates) and a bot_events
  row ("claim_notify": who, what, claim, whether it went; never texts).

Nothing here commits: the caller does.
"""

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import phonenumbers
from sqlalchemy.orm import Session

from app.bot.choices import Choice
from app.bot.unit_search import display_building_name
from app.claims import payloads, texts
from app.claims.claim_config import ClaimConfig, load_claim_config
from app.claims.schedule import when_text
from app.claims.service import (
    mark_reminded,
    mark_sent,
    needs_attention,
    record_event,
    schedule_send,
)
from app.config import Settings
from app.db.models import (
    BotEvent,
    Claim,
    ClaimActor,
    ClaimAttention,
    ClaimEventKind,
    ClaimScope,
    ClaimStatus,
    Provider,
    WaAuthor,
    WaConversation,
    WaConversationStatus,
    WaDirection,
    WaMediaStatus,
    WaMessage,
    WaMessageStatus,
)
from app.whatsapp import store
from app.whatsapp.channel import window_open_at
from app.whatsapp.client import WhatsAppError, WindowClosedError
from app.whatsapp.media import base_mime, resolve_path

logger = logging.getLogger(__name__)

DETAIL_LIMIT = 300
EMPTY = "—"


@dataclass(frozen=True)
class Dispatched:
    """What dispatch did: sent (how it went) or scheduled (when it goes, and how to say it)."""

    sent: "Sent | None" = None
    scheduled_at: datetime | None = None
    when: str = ""


@dataclass(frozen=True)
class Sent:
    """How one message went: ok, its stored message, and the error in Spanish."""

    ok: bool
    message_id: int | None = None
    error: str = ""


# --- Texts ----------------------------------------------------------------------------------


def flat(value: object, limit: int | None = None) -> str:
    """A template variable: one line (no newlines nor tabs, no runs of spaces), never empty,
    cut at limit with "…"."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit is not None and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text or EMPTY


def phone_text(e164: str | None) -> str:
    """ "351 555-0101" (as people say it)."""
    if not e164:
        return EMPTY
    try:
        number = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return e164
    text = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    for prefix in ("+54 9 ", "+54 "):
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def problem_text(claim: Claim) -> str:
    title = claim.category.list_title
    return title + texts.URGENT_SUFFIX if claim.urgent else title


def unit_and_contact(claim: Claim) -> str:
    """For a kind of one unit, the unit and who reported it; for the whole building, nothing
    personal."""
    if claim.scope != ClaimScope.UNIT:
        return texts.WHOLE_BUILDING_CONTACT
    unit = (
        claim.unit.label
        if claim.unit
        else (claim.reporter_unit.label if claim.reporter_unit else "")
    )
    phone = phone_text(claim.reporter_phone_e164) if claim.reporter_phone_e164 else ""
    parts = [f"Unidad {unit}" if unit else "", claim.reporter_name or "", phone]
    return " · ".join(p for p in parts if p) or EMPTY


def template_params(claim: Claim) -> list[str]:
    """reclamo_nuevo_proveedor: number, building, address, problem, detail, unit and contact."""
    return [
        flat(claim.number),
        flat(display_building_name(claim.building.name)),
        flat(claim.building.address),
        flat(problem_text(claim)),
        flat(claim.description, DETAIL_LIMIT),
        flat(unit_and_contact(claim)),
    ]


def neighbor_params(claim: Claim) -> list[str]:
    """reclamo_confirmado_vecino and reclamo_solucionado_vecino: number and problem."""
    return [flat(claim.number), flat(claim.category.list_title)]


def components(params: Sequence[str], button_payloads: Sequence[str] = ()) -> list[dict[str, Any]]:
    """A template's variables (body) and the payload of each quick-reply button, in order."""
    result: list[dict[str, Any]] = [
        {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
    ]
    for index, payload in enumerate(button_payloads):
        result.append(
            {
                "type": "button",
                "sub_type": "quick_reply",
                "index": str(index),
                "parameters": [{"type": "payload", "payload": payload}],
            }
        )
    return result


def spanish_error(exc: Exception) -> str:
    if isinstance(exc, WindowClosedError):
        return "pasaron más de 24 horas desde su último mensaje"
    code = getattr(exc, "code", None)
    if "no está configurado" in str(exc) or "faltan WHATSAPP" in str(exc):
        return "WhatsApp no está configurado en el servidor"
    return f"WhatsApp no aceptó el mensaje (código {code})" if code else "WhatsApp no respondió"


# --- The notifier ---------------------------------------------------------------------------


class Notifier:
    """Sends the claims' WhatsApp messages with the bot's client (in development the DevClient:
    the panel's test chat contacts never reach Meta)."""

    def __init__(
        self,
        client: Any,
        settings: Settings,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self.secret = payloads.secret_of(settings)
        self._now = now or (lambda: datetime.now(UTC))

    # --- Sending and storing ----------------------------------------------------------------

    def _conversation(self, session: Session, e164: str, *, provider: bool) -> WaConversation:
        conversation = store.contact_conversation(session, e164.lstrip("+"))
        if provider and conversation.status in (
            WaConversationStatus.BOT,
            WaConversationStatus.RESOLVED,
        ):
            conversation.status = WaConversationStatus.PROVIDER
        return conversation

    def window_open(self, session: Session, e164: str) -> bool:
        conversation = store.contact_conversation(session, e164.lstrip("+"))
        return window_open_at(conversation.last_inbound_at, self._now())

    def _deliver(
        self,
        session: Session,
        e164: str,
        *,
        kind: str,
        body: str,
        send: Callable[[str], str],
        choices: Sequence[Choice] = (),
        provider: bool = False,
        media: dict[str, Any] | None = None,
    ) -> Sent:
        conversation = self._conversation(session, e164, provider=provider)
        row = WaMessage(
            conversation_id=conversation.id,
            direction=WaDirection.OUTBOUND,
            author=WaAuthor.BOT,
            message_type=kind,
            body=body,
            choices=[c.title for c in choices] or None,
            button_payloads=[c.payload for c in choices] or None,
            **(media or {}),
        )
        error = ""
        try:
            row.wa_message_id = send(e164.lstrip("+"))
            row.status = WaMessageStatus.SENT
        except (WhatsAppError, ValueError) as exc:
            logger.warning("Claim message (%s) to a WhatsApp contact failed: %s", kind, exc)
            row.status = WaMessageStatus.FAILED
            row.error_code = getattr(exc, "code", None)
            row.error_text = str(exc)[:1000]
            error = spanish_error(exc)
        row.status_at = self._now()
        session.add(row)
        session.flush()
        return Sent(ok=not error, message_id=row.id, error=error)

    def _template(
        self,
        session: Session,
        e164: str,
        name: str,
        preview: str,
        params: Sequence[str],
        buttons: Sequence[Choice] = (),
        *,
        provider: bool = False,
    ) -> Sent:
        comps = components(params, [b.payload for b in buttons])
        language = self.settings.whatsapp_template_lang
        return self._deliver(
            session, e164, kind="template", body=preview.format(*params), choices=buttons,
            provider=provider,
            send=lambda to: self.client.send_template(to, name, language, comps),
        )  # fmt: skip

    def _text(
        self,
        session: Session,
        e164: str,
        text: str,
        choices: Sequence[Choice] = (),
        *,
        provider: bool = False,
    ) -> Sent:
        if choices:
            return self._deliver(
                session, e164, kind="interactive", body=text, choices=choices, provider=provider,
                send=lambda to: self.client.send_choices(to, text, tuple(choices)),
            )  # fmt: skip
        return self._deliver(
            session, e164, kind="text", body=text, provider=provider,
            send=lambda to: self.client.send_text(to, text),
        )  # fmt: skip

    def _log(self, session: Session, claim: Claim, to: str, what: str, sent: Sent) -> None:
        session.add(
            BotEvent(
                conversation_id=claim.wa_conversation_id,
                event_type="claim_notify",
                payload={"to": to, "what": what, "claim_id": claim.id, "sent": sent.ok},
            )
        )

    def button(self, claim: Claim, title: str, action: str, subject: str) -> Choice:
        return Choice(title, title, payload=payloads.sign(self.secret, claim.id, action, subject))

    # --- The provider ---------------------------------------------------------------------------

    def config(self, session: Session) -> ClaimConfig:
        return load_claim_config(session, self.settings.timezone)

    def must_wait(self, session: Session, claim: Claim) -> datetime | None:
        """When it should go if not now (out of the providers' hours), else None."""
        config = self.config(session)
        now = self._now()
        if (claim.urgent and config.urgent_any_time) or config.hours.is_open(now):
            return None
        return config.hours.next_opening(now)

    def dispatch(
        self, session: Session, claim: Claim, user: str | None = None, *, mode: str = "auto"
    ) -> Dispatched:
        """Sends the claim to its provider now, or schedules it for the next opening of the
        providers' hours. mode: "auto" (the hours decide), "now" or "later" (the panel's
        choice out of hours)."""
        provider = claim.provider
        if claim.status != ClaimStatus.PENDING_SEND or provider is None or not provider.active:
            return Dispatched(sent=Sent(ok=False, error="no está para avisar"))
        if not provider.whatsapp_e164:
            return Dispatched(sent=Sent(ok=False, error="el proveedor no tiene WhatsApp cargado"))
        opening = None if mode == "now" else self.must_wait(session, claim)
        if opening is None:
            return Dispatched(sent=self.notify_provider(session, claim, user))
        when = when_text(opening, self._now(), self.settings.timezone)
        schedule_send(
            session, claim, opening,
            texts.SCHEDULED_EVENT.format(provider=provider.name, when=when), now=self._now(),
        )  # fmt: skip
        return Dispatched(scheduled_at=opening, when=when)

    def remind(self, session: Session, claim: Claim) -> Sent:
        """reclamo_recordatorio_proveedor to the claim's provider (number, building, problem),
        with "Recibido" and "Ya está solucionado"."""
        provider = claim.provider
        if provider is None or not provider.whatsapp_e164:
            return Sent(ok=False, error="el proveedor no tiene WhatsApp cargado")
        subject = payloads.provider_subject(provider.id)
        buttons = [
            self.button(claim, texts.ACK_BUTTON, "ack", subject),
            self.button(claim, texts.SOLVED_BUTTON, "solved", subject),
        ]
        params = template_params(claim)[:2] + [flat(problem_text(claim))]
        sent = self._template(
            session, provider.whatsapp_e164, self.settings.claim_template_reminder,
            texts.TEMPLATE_PREVIEWS["reminder"], params, buttons, provider=True,
        )  # fmt: skip
        if sent.ok:
            mark_reminded(
                session, claim, texts.REMINDED_EVENT.format(provider=provider.name),
                wa_message_id=sent.message_id, now=self._now(),
            )  # fmt: skip
        else:
            record_event(
                session, claim, ClaimEventKind.NOTIFY_FAILED,
                text=f"No se pudo mandar el recordatorio a {provider.name}: {sent.error}",
                wa_message_id=sent.message_id, provider_id=provider.id, now=self._now(),
            )  # fmt: skip
            claim.reminded_at = self._now()  # once: the alert to the studio follows anyway
        self._log(session, claim, "provider", "reminder", sent)
        session.flush()
        return sent

    def notify_provider(self, session: Session, claim: Claim, user: str | None = None) -> Sent:
        """Sends the claim to its provider (only pending_send, a provider with WhatsApp).
        Went: the claim is sent. Did not: it stays pending_send, with the error in its history
        and an alert in the panel."""
        provider = claim.provider
        if claim.status != ClaimStatus.PENDING_SEND or provider is None or not provider.active:
            return Sent(ok=False, error="no está para avisar")
        if not provider.whatsapp_e164:
            return Sent(ok=False, error="el proveedor no tiene WhatsApp cargado")
        subject = payloads.provider_subject(provider.id)
        buttons = [
            self.button(claim, texts.ACK_BUTTON, "ack", subject),
            self.button(claim, texts.DECLINE_BUTTON, "decline", subject),
        ]
        sent = self._template(
            session, provider.whatsapp_e164, self.settings.claim_template_provider,
            texts.TEMPLATE_PREVIEWS["provider"], template_params(claim), buttons, provider=True,
        )  # fmt: skip
        actor = ClaimActor.PANEL if user else ClaimActor.SYSTEM
        if sent.ok:
            mark_sent(
                session, claim, actor=actor, user=user,
                text=f"Avisado a {provider.name} por WhatsApp", wa_message_id=sent.message_id,
                now=self._now(),
            )  # fmt: skip
        else:
            record_event(
                session, claim, ClaimEventKind.NOTIFY_FAILED,
                text=f"No se pudo avisar a {provider.name} por WhatsApp: {sent.error}",
                wa_message_id=sent.message_id, provider_id=provider.id, now=self._now(),
            )  # fmt: skip
            claim.send_after = None  # not tried again by itself: the panel shows the alert
            needs_attention(session, claim, ClaimAttention.SEND_FAILED)
        self._log(session, claim, "provider", "new_claim", sent)
        session.flush()
        return sent

    def to_provider(
        self, session: Session, provider: Provider, text: str, choices: Sequence[Choice] = ()
    ) -> Sent:
        """A free message to a provider who just wrote (its window is open)."""
        if not provider.whatsapp_e164:
            return Sent(ok=False, error="el proveedor no tiene WhatsApp cargado")
        return self._text(session, provider.whatsapp_e164, text, choices, provider=True)

    def solved_button(self, claim: Claim, provider: Provider) -> Choice:
        return self.button(
            claim, texts.SOLVED_BUTTON, "solved", payloads.provider_subject(provider.id)
        )

    def send_photos(self, session: Session, claim: Claim, provider: Provider) -> int:
        """The claim's photos to the provider (uploaded again: a received media id cannot be
        sent). Returns how many went."""
        if not provider.whatsapp_e164 or not claim.attachments:
            return 0
        to = provider.whatsapp_e164.lstrip("+")
        total, went = len(claim.attachments), 0
        for count, attachment in enumerate(claim.attachments, 1):
            photo = attachment.message
            path = (
                resolve_path(self.settings.whatsapp_media_dir, photo.media_path)
                if photo.media_path
                else None
            )
            if path is None or not path.is_file():
                continue
            caption = texts.PHOTO_CAPTION.format(number=claim.number, count=count, total=total)
            mime = base_mime(photo.media_mime) or "image/jpeg"
            sent = self._deliver(
                session, provider.whatsapp_e164, kind="image", body=caption, provider=True,
                media={"media_path": photo.media_path, "media_mime": mime,
                       "media_status": WaMediaStatus.STORED},
                send=lambda to_, p=path, m=mime, c=caption: self.client.send_image(
                    to_, self._upload(to, p, m), c
                ),
            )  # fmt: skip
            went += sent.ok
        return went

    def _upload(self, to: str, path: Path, mime: str) -> str:
        upload_for = getattr(self.client, "upload_media_for", None)
        content = path.read_bytes()
        if upload_for is not None:
            return upload_for(to, content, mime, path.name)
        return self.client.upload_media(content, mime, path.name)

    # --- The neighbors --------------------------------------------------------------------------

    def notify_neighbors(self, session: Session, claim: Claim, kind: str) -> list[Sent]:
        """Who reported it and who joined (each phone once): kind "confirmed" (the provider
        confirmed) or "solved". Free text if their window is open, else the template."""
        people = [(claim.reporter_name or "", claim.reporter_phone_e164)]
        people += [(r.name, r.phone_e164) for r in claim.reporters]
        seen: set[str] = set()
        results = []
        what = "la empresa confirmó" if kind == "confirmed" else "está solucionado"
        for name, phone in people:
            shown = name or phone_text(phone)
            if not phone:
                record_event(
                    session, claim, ClaimEventKind.NOTIFY_FAILED,
                    text=f"No se le pudo avisar a {shown}: no tiene teléfono", now=self._now(),
                )  # fmt: skip
                continue
            if phone in seen:
                continue
            seen.add(phone)
            sent = self._tell_neighbor(session, claim, phone, kind)
            if sent.ok:
                record_event(
                    session, claim, ClaimEventKind.NOTIFIED,
                    text=f"Avisado a {shown} por WhatsApp: {what}",
                    wa_message_id=sent.message_id, now=self._now(),
                )  # fmt: skip
            else:
                record_event(
                    session, claim, ClaimEventKind.NOTIFY_FAILED,
                    text=f"No se le pudo avisar a {shown}: {sent.error}",
                    wa_message_id=sent.message_id, now=self._now(),
                )  # fmt: skip
            self._log(session, claim, "neighbor", kind, sent)
            results.append(sent)
        session.flush()
        return results

    def _tell_neighbor(self, session: Session, claim: Claim, phone: str, kind: str) -> Sent:
        number, problem = claim.number, claim.category.list_title
        again = (
            [self.button(claim, texts.REGISTER_BUTTON, "again", phone)] if kind == "solved" else []
        )
        if self.window_open(session, phone):
            text = texts.NEIGHBOR_CONFIRMED if kind == "confirmed" else texts.NEIGHBOR_SOLVED
            return self._text(session, phone, text.format(number=number, problem=problem), again)
        name = (
            self.settings.claim_template_confirmed
            if kind == "confirmed"
            else self.settings.claim_template_solved
        )
        return self._template(
            session, phone, name, texts.TEMPLATE_PREVIEWS[kind], neighbor_params(claim), again
        )
