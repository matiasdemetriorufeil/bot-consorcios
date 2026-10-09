"""The claims' periodic job (every JOB_MINUTES, app.scheduler; the panel's test chat can run it
"as if it were" another moment):

1. Scheduled claims whose time came (send_after <= now) go to their provider.
2. A provider that did not confirm gets ONE reminder (reclamo_recordatorio_proveedor), after
   reminder_urgent_minutes for urgent claims (at any time) or reminder_hours of the providers'
   hours for the rest (only inside them).
3. The studio is told in the panel (attention; nothing goes by WhatsApp), once each:
   - not confirmed after alert_urgent_minutes / alert_hours of the hours (no_ack), and the same
     for a claim still waiting to be told to its provider (no WhatsApp, a send that failed);
   - confirmed, or with the studio, and not solved after stale_days (stale).

Two runs at once never send anything twice: a Postgres advisory lock lets only one run, and
every claim is handled in its own transaction under SELECT ... FOR UPDATE SKIP LOCKED with its
condition checked again inside.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.claims import texts
from app.claims.claim_config import ClaimConfig, load_claim_config
from app.claims.notify import Notifier
from app.claims.service import raise_attention
from app.db.models import Claim, ClaimAttention, ClaimStatus
from app.whatsapp.client import WhatsAppError

logger = logging.getLogger(__name__)

JOB_MINUTES = 5
LOCK_KEY = 0x0C1A_1305  # pg advisory lock of the job


@dataclass
class JobReport:
    ran: bool = True
    sent: list[int] = field(default_factory=list)
    reminded: list[int] = field(default_factory=list)
    alerted: list[tuple[int, str]] = field(default_factory=list)


@contextmanager
def _job_lock(session: Session) -> Iterator[bool]:
    got = bool(session.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}))
    try:
        yield got
    finally:
        if got:
            session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
        # It only read; ending it with a commit keeps what the steps' sessions committed when
        # they share its connection (the panel's tests).
        session.commit()


def run_claim_jobs(
    session_factory: Callable[[], AbstractContextManager[Session]],
    notifier: Notifier,
    now: datetime,
    timezone: str,
) -> JobReport:
    """One run (see the module doc). Never raises for one claim: it logs and goes on."""
    report = JobReport()
    with session_factory() as lock_session, _job_lock(lock_session) as got:
        if not got:
            logger.info("Claims job skipped: another run is going on")
            report.ran = False
            return report
        config = load_claim_config(lock_session, timezone)
        for step in (_send_due, _remind, _alert):
            ids = step.candidates(lock_session, now, config)
            for claim_id in ids:
                with session_factory() as session:
                    try:
                        step.run(session, claim_id, notifier, now, config, report)
                        session.commit()
                    except Exception:
                        session.rollback()
                        logger.exception("Claims job: claim %s failed (%s)", claim_id, step)
    if report.sent or report.reminded or report.alerted:
        logger.info(
            "Claims job: %d sent, %d reminded, %d alerts",
            len(report.sent), len(report.reminded), len(report.alerted),
        )  # fmt: skip
    return report


def _locked(session: Session, claim_id: int) -> Claim | None:
    return session.scalars(
        select(Claim).where(Claim.id == claim_id).with_for_update(skip_locked=True)
    ).first()


def _ids(session: Session, *conditions: object) -> list[int]:
    return list(session.scalars(select(Claim.id).where(*conditions).order_by(Claim.id)))


# --- The steps ------------------------------------------------------------------------------


class _SendDue:
    """1. Scheduled claims whose time came."""

    def candidates(self, session: Session, now: datetime, config: ClaimConfig) -> list[int]:
        return _ids(
            session,
            Claim.status == ClaimStatus.PENDING_SEND,
            Claim.send_after.is_not(None),
            Claim.send_after <= now,
        )

    def run(self, session: Session, claim_id: int, notifier: Notifier, now: datetime,
            config: ClaimConfig, report: JobReport) -> None:  # fmt: skip
        claim = _locked(session, claim_id)
        if (
            claim is None
            or claim.status != ClaimStatus.PENDING_SEND
            or claim.send_after is None
            or claim.send_after > now
        ):
            return
        sent = notifier.notify_provider(session, claim)
        if sent.ok:
            report.sent.append(claim.id)


def _reminder_due(claim: Claim, now: datetime, config: ClaimConfig) -> bool:
    if claim.sent_at is None:
        return False
    if claim.urgent:
        return claim.sent_at + config.reminder_urgent <= now
    return config.hours.is_open(now) and (
        config.hours.add_open_time(claim.sent_at, config.reminder) <= now
    )


class _Remind:
    """2. One reminder to a provider that did not confirm."""

    def candidates(self, session: Session, now: datetime, config: ClaimConfig) -> list[int]:
        return _ids(session, Claim.status == ClaimStatus.SENT, Claim.reminded_at.is_(None))

    def run(self, session: Session, claim_id: int, notifier: Notifier, now: datetime,
            config: ClaimConfig, report: JobReport) -> None:  # fmt: skip
        claim = _locked(session, claim_id)
        if claim is None or claim.status != ClaimStatus.SENT or claim.reminded_at is not None:
            return
        if not _reminder_due(claim, now, config):
            return
        if notifier.remind(session, claim).ok:
            report.reminded.append(claim.id)


def _passed(since: datetime | None, now: datetime, config: ClaimConfig, urgent: bool,
            open_time: timedelta, real_time: timedelta) -> bool:  # fmt: skip
    if since is None:
        return False
    if urgent:
        return since + real_time <= now
    return config.hours.add_open_time(since, open_time) <= now


class _Alert:
    """3. The studio is told in the panel."""

    def candidates(self, session: Session, now: datetime, config: ClaimConfig) -> list[int]:
        return _ids(
            session,
            Claim.status.in_(
                (
                    ClaimStatus.PENDING_SEND,
                    ClaimStatus.SENT,
                    ClaimStatus.ACKNOWLEDGED,
                    ClaimStatus.STUDIO,
                )
            ),
            Claim.attention.is_(None),
        )

    def run(self, session: Session, claim_id: int, notifier: Notifier, now: datetime,
            config: ClaimConfig, report: JobReport) -> None:  # fmt: skip
        claim = _locked(session, claim_id)
        if claim is None or claim.attention is not None or not claim.is_open:
            return
        attention = _attention_for(claim, now, config)
        if attention is None:
            return
        reason = texts.attention_text(claim, now, attention)
        if raise_attention(session, claim, attention, reason, now=now):
            report.alerted.append((claim.id, attention.value))


def _attention_for(claim: Claim, now: datetime, config: ClaimConfig) -> ClaimAttention | None:
    """What the studio should be told about this open claim now (None: nothing)."""
    status = ClaimStatus(claim.status)
    times = (config.alert, config.alert_urgent)
    if status == ClaimStatus.SENT:
        not_confirmed = _passed(claim.sent_at, now, config, claim.urgent, *times)
        return ClaimAttention.NO_ACK if not_confirmed else None
    if status == ClaimStatus.PENDING_SEND:
        # Not scheduled and nobody told the provider (no WhatsApp, a send that failed).
        not_told = claim.send_after is None and _passed(
            claim.status_at, now, config, claim.urgent, *times
        )
        return ClaimAttention.NO_ACK if not_told else None
    since = claim.acknowledged_at if status == ClaimStatus.ACKNOWLEDGED else claim.status_at
    if since is not None and since + config.stale <= now:
        return ClaimAttention.STALE
    return None


class NotConfiguredClient:
    """Stands in for the WhatsApp client where it is not configured: every send fails (the
    claim keeps its alert), the studio's alerts still work."""

    def __getattr__(self, name: str) -> Callable[..., str]:
        def fail(*args: object, **kwargs: object) -> str:
            raise WhatsAppError("WhatsApp no está configurado")

        return fail


_send_due, _remind, _alert = _SendDue(), _Remind(), _Alert()
