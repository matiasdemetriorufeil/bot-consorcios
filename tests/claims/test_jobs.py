"""The providers' hours, the reminder and the studio's alerts (app.claims.notify.dispatch and
app.claims.jobs), with a fixed clock and the Cloud API faked. Invented data only (fictitious
buildings, people and providers, phones 351 555-01xx)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.claims import payloads, texts
from app.claims.claim_config import invalidate_claim_config
from app.claims.jobs import LOCK_KEY, JobReport, run_claim_jobs
from app.claims.notify import Notifier
from app.claims.service import (
    Reporter,
    change_provider,
    close_claim,
    create_claim,
    mark_acknowledged,
    mark_sent,
)
from app.config import Settings
from app.db.models import (
    Claim,
    ClaimActor,
    ClaimAttention,
    ClaimCategory,
    ClaimEvent,
    ClaimEventKind,
    ClaimScope,
    ClaimSettings,
    ClaimSource,
    ClaimStatus,
    Provider,
    WaMessage,
)
from tests.bot import factories as f
from tests.claims import factories as cf
from tests.whatsapp.fakes import FakeWhatsApp

TZ = ZoneInfo("America/Argentina/Cordoba")
LIFTS_WA, OTHER_WA = "+5493515550150", "+5493515550151"


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment of October 2026 in Córdoba (the 2nd is a Friday, the 7th a Wednesday)."""
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


@dataclass
class World:
    session: Session
    fake: FakeWhatsApp
    clock: list[datetime]
    notifier: Notifier
    sessions: Any
    lift: ClaimCategory  # urgent
    damp: ClaimCategory  # not urgent
    lifts: Provider
    other: Provider
    building_id: int
    unit_id: int

    def jobs(self, now: datetime) -> JobReport:
        self.clock[0] = now
        report = run_claim_jobs(self.sessions, self.notifier, now, "America/Argentina/Cordoba")
        self.session.expire_all()
        return report

    def templates(self) -> list[tuple[str, str]]:
        return [(to, content[0]) for kind, to, content in self.fake.sent if kind == "template"]


@pytest.fixture
def w(db_session: Session, tmp_path: Any) -> World:
    s = db_session
    building = f.building(s, "001 TORRE INVENTADA")
    unit = f.unit(s, building, "02-B")
    lift = cf.category(s, "Ascensor inventado", list_title="Ascensor")
    lift.urgent = True
    damp = cf.category(s, "Humedad inventada", list_title="Humedad", scope=ClaimScope.UNIT)
    lifts = cf.provider(s, "Ascensores Ficticios SRL", LIFTS_WA)
    other = cf.provider(s, "Otra Empresa Ficticia", OTHER_WA)
    cf.assign(s, building.id, lift, lifts)
    cf.assign(s, building.id, damp, lifts)
    s.commit()
    fake = FakeWhatsApp()
    settings = Settings(
        _env_file=None,
        claims_payload_secret="secreto-inventado-de-prueba",
        whatsapp_media_dir=str(tmp_path),
    )
    clock = [at(7, 11)]
    notifier = Notifier(fake, settings, now=lambda: clock[0])
    sessions = sessionmaker(bind=s.get_bind(), join_transaction_mode="create_savepoint")
    return World(s, fake, clock, notifier, sessions, lift, damp, lifts, other, building.id, unit.id)


def _claim(w: World, category: ClaimCategory, when: datetime) -> Claim:
    w.clock[0] = when
    claim = create_claim(
        w.session,
        building_id=w.building_id,
        category_id=category.id,
        unit_id=w.unit_id,
        description="Inventado",
        reporter=Reporter(name="Ana Inventada", phone_e164="+5493515550101", unit_id=w.unit_id),
        source=ClaimSource.BOT,
        now=when,
    ).claim
    w.session.flush()
    return claim


def _sent(w: World, category: ClaimCategory, when: datetime) -> Claim:
    claim = _claim(w, category, when)
    assert w.notifier.dispatch(w.session, claim, mode="now").sent.ok
    w.session.commit()
    return claim


def _kinds(w: World, claim: Claim) -> list[str]:
    return [
        str(e.kind)
        for e in w.session.scalars(
            select(ClaimEvent).where(ClaimEvent.claim_id == claim.id).order_by(ClaimEvent.id)
        )
    ]


def _settings(w: World, **values: Any) -> None:
    row = w.session.get(ClaimSettings, 1)
    for name, value in values.items():
        setattr(row, name, value)
    w.session.commit()
    invalidate_claim_config()


# --- Sending inside the providers' hours ----------------------------------------------------


def test_a_normal_claim_out_of_hours_waits_for_the_opening(w: World) -> None:
    claim = _claim(w, w.damp, at(2, 21))  # Friday 21:00
    done = w.notifier.dispatch(w.session, claim)
    w.session.commit()
    assert done.sent is None and done.when == "mañana a las 8:00"
    assert claim.status == ClaimStatus.PENDING_SEND and claim.send_after == at(3, 8)
    assert w.fake.sent == []
    [event] = w.session.scalars(
        select(ClaimEvent).where(ClaimEvent.kind == ClaimEventKind.SCHEDULED)
    ).all()
    assert event.text == (
        "Se le va a mandar a Ascensores Ficticios SRL mañana a las 8:00 (fuera del horario de "
        "proveedores)"
    )

    assert w.jobs(at(3, 7, 55)).sent == []
    assert w.jobs(at(3, 8, 5)).sent == [claim.id]
    claim = w.session.get(Claim, claim.id)
    assert claim.status == ClaimStatus.SENT and claim.send_after is None
    assert w.templates() == [(LIFTS_WA[1:], "reclamo_nuevo_proveedor")]
    assert w.jobs(at(3, 8, 10)).sent == []  # never twice
    assert len(w.templates()) == 1


def test_inside_the_hours_it_goes_at_once(w: World) -> None:
    claim = _claim(w, w.damp, at(7, 11))
    assert w.notifier.dispatch(w.session, claim).sent.ok
    assert claim.status == ClaimStatus.SENT


def test_an_urgent_claim_goes_at_any_time_unless_the_option_is_off(w: World) -> None:
    claim = _claim(w, w.lift, at(3, 3))  # Saturday 3:00
    assert w.notifier.dispatch(w.session, claim).sent.ok
    w.session.commit()

    _settings(w, urgent_any_time=False)
    close_claim(w.session, claim, ClaimStatus.SOLVED, "Listo", actor=ClaimActor.BOT, now=at(3, 3))
    other = _claim(w, w.lift, at(3, 3, 10))
    done = w.notifier.dispatch(w.session, other)
    assert done.sent is None and other.send_after == at(3, 8)


def test_the_panel_can_send_now_or_later(w: World) -> None:
    claim = _claim(w, w.lift, at(3, 3))
    _settings(w, urgent_any_time=False)
    assert w.notifier.dispatch(w.session, claim, mode="later").sent is None
    assert claim.send_after == at(3, 8)
    assert w.notifier.dispatch(w.session, claim, mode="now").sent.ok
    assert claim.status == ClaimStatus.SENT and claim.send_after is None


def test_holidays_and_the_days_are_skipped(w: World) -> None:
    _settings(w, holidays="03/10/2026\n05/10/2026")  # Saturday and Monday
    claim = _claim(w, w.damp, at(2, 21))
    done = w.notifier.dispatch(w.session, claim)
    assert claim.send_after == at(6, 8) and done.when == "el martes a las 8:00"


def test_a_failed_scheduled_send_is_not_retried_every_five_minutes(w: World) -> None:
    claim = _claim(w, w.damp, at(2, 21))
    w.notifier.dispatch(w.session, claim)
    w.session.commit()
    w.fake.fail_on.add("template")
    assert w.jobs(at(3, 8, 5)).sent == []
    claim = w.session.get(Claim, claim.id)
    assert claim.send_after is None and claim.attention == ClaimAttention.SEND_FAILED
    w.jobs(at(3, 8, 10))
    assert _kinds(w, claim).count(ClaimEventKind.NOTIFY_FAILED) == 1


# --- The reminder ---------------------------------------------------------------------------


def test_one_reminder_after_four_hours_of_the_hours(w: World) -> None:
    claim = _sent(w, w.damp, at(7, 11))  # Wednesday
    assert w.jobs(at(7, 14, 55)).reminded == []
    assert w.jobs(at(7, 15, 5)).reminded == [claim.id]
    assert w.templates() == [
        (LIFTS_WA[1:], "reclamo_nuevo_proveedor"),
        (LIFTS_WA[1:], "reclamo_recordatorio_proveedor"),
    ]
    # Its buttons: "Recibido" and "Ya esta solucionado" (as approved in Meta: no accent),
    # signed for this claim and provider.
    reminder = w.session.scalars(
        select(WaMessage).where(WaMessage.message_type == "template").order_by(WaMessage.id.desc())
    ).first()
    assert reminder.choices == ["Recibido", "Ya esta solucionado"]
    secret = w.notifier.secret
    actions = []
    for payload in w.fake.payloads[-1]:
        parsed = payloads.read(secret, payload, payloads.provider_subject(w.lifts.id))
        assert parsed is not None and parsed.claim_id == claim.id
        actions.append(parsed.action)
    assert actions == ["ack", "solved"]
    claim = w.session.get(Claim, claim.id)
    assert claim.reminded_at == at(7, 15, 5)
    assert w.jobs(at(7, 16)).reminded == []  # only once
    assert _kinds(w, claim).count(ClaimEventKind.REMINDED) == 1
    [event] = [e for e in claim.events if e.kind == ClaimEventKind.REMINDED]
    assert event.text == "Recordatorio a Ascensores Ficticios SRL por WhatsApp"
    assert event.provider_id == w.lifts.id


def test_no_reminder_out_of_the_hours(w: World) -> None:
    claim = _sent(w, w.damp, at(2, 19))  # Friday 19:00: due Saturday 11:00
    assert w.jobs(at(3, 10, 55)).reminded == []
    _settings(w, provider_weekdays="0,1,2,3,4")  # no Saturdays: due Monday 11:00
    assert w.jobs(at(3, 11, 5)).reminded == []
    assert w.jobs(at(4, 12)).reminded == []  # Sunday, closed
    assert w.jobs(at(5, 11, 5)).reminded == [claim.id]


def test_an_urgent_claim_is_reminded_after_thirty_minutes_at_any_time(w: World) -> None:
    claim = _sent(w, w.lift, at(3, 3))  # Saturday 3:00
    assert w.jobs(at(3, 3, 29)).reminded == []
    assert w.jobs(at(3, 3, 31)).reminded == [claim.id]
    report = w.jobs(at(3, 4, 1))
    assert report.alerted == [(claim.id, "no_ack")]


def test_no_reminder_for_a_confirmed_closed_or_moved_claim(w: World) -> None:
    confirmed = _sent(w, w.lift, at(7, 9))
    mark_acknowledged(w.session, confirmed, now=at(7, 9, 10))
    closed = _sent(w, w.damp, at(7, 9))
    close_claim(w.session, closed, ClaimStatus.SOLVED, "Listo", actor=ClaimActor.BOT, now=at(7, 9))
    w.session.commit()
    w.jobs(at(7, 16))
    assert w.templates() == [(LIFTS_WA[1:], "reclamo_nuevo_proveedor")] * 2


def test_a_moved_claim_never_reminds_the_old_provider(w: World) -> None:
    """Sent to the lifts at 9:00, moved to the other company at 10:00: the reminder counts
    from the new send and goes only to the new provider."""
    claim = _sent(w, w.damp, at(7, 9))
    change_provider(
        w.session, claim, w.other.id, actor=ClaimActor.PANEL, user="admin", now=at(7, 10)
    )
    w.clock[0] = at(7, 10)
    w.notifier.dispatch(w.session, claim)
    w.session.commit()
    assert w.jobs(at(7, 13, 30)).reminded == []  # 4.5 h of the first send, 3.5 h of this one
    assert w.jobs(at(7, 14, 5)).reminded == [claim.id]
    reminders = [to for to, name in w.templates() if name == "reclamo_recordatorio_proveedor"]
    assert reminders == [OTHER_WA[1:]]


def test_a_failed_reminder_is_not_retried(w: World) -> None:
    claim = _sent(w, w.damp, at(7, 9))
    w.fake.fail_on.add("template")
    assert w.jobs(at(7, 13, 5)).reminded == []
    claim = w.session.get(Claim, claim.id)
    assert claim.reminded_at is not None
    w.jobs(at(7, 13, 10))
    assert _kinds(w, claim).count(ClaimEventKind.NOTIFY_FAILED) == 1


# --- The studio's alerts --------------------------------------------------------------------


def test_not_confirmed_after_eight_hours_and_cleared_when_it_is(w: World) -> None:
    claim = _sent(w, w.damp, at(7, 9))  # Wednesday: 8 hours of the hours = 17:00
    assert w.jobs(at(7, 16, 55)).alerted == []
    assert w.jobs(at(7, 17, 5)).alerted == [(claim.id, "no_ack")]
    claim = w.session.get(Claim, claim.id)
    assert claim.attention == ClaimAttention.NO_ACK
    assert texts.attention_text(claim, at(7, 17, 5)) == "El proveedor no confirmó"
    [alert] = [e for e in claim.events if e.kind == ClaimEventKind.ALERT]
    assert alert.text == "El proveedor no confirmó"
    assert w.jobs(at(7, 17, 10)).alerted == []  # once

    mark_acknowledged(w.session, claim, now=at(7, 18))
    assert claim.attention is None


def test_confirmed_but_not_solved_after_three_days(w: World) -> None:
    claim = _sent(w, w.damp, at(5, 9))
    mark_acknowledged(w.session, claim, now=at(5, 10))
    w.session.commit()
    assert w.jobs(at(8, 9, 55)).alerted == []
    assert w.jobs(at(8, 10, 5)).alerted == [(claim.id, "stale")]
    claim = w.session.get(Claim, claim.id)
    assert texts.attention_text(claim, at(8, 10, 5)) == "Sin solucionar hace 3 días"
    close_claim(w.session, claim, ClaimStatus.SOLVED, "Listo", actor=ClaimActor.BOT, now=at(8, 11))
    assert claim.attention is None


def test_with_the_studio_too_long(w: World) -> None:
    claim = _sent(w, w.damp, at(5, 9))
    change_provider(w.session, claim, None, actor=ClaimActor.PANEL, user="admin", now=at(5, 10))
    w.session.commit()
    assert w.jobs(at(8, 10, 5)).alerted == [(claim.id, "stale")]
    claim = w.session.get(Claim, claim.id)
    assert texts.attention_text(claim, at(8, 10, 5)) == "Lo tiene el estudio hace 3 días"
    change_provider(
        w.session, claim, w.lifts.id, actor=ClaimActor.PANEL, user="admin", now=at(8, 11)
    )
    assert claim.attention is None


def test_never_told_to_the_provider(w: World) -> None:
    """Waiting to be told (a provider without WhatsApp, nobody marked it as told)."""
    w.lifts.whatsapp_e164 = None
    claim = _claim(w, w.damp, at(7, 9))
    w.session.commit()
    assert w.jobs(at(7, 16, 55)).alerted == []
    assert w.jobs(at(7, 17, 5)).alerted == [(claim.id, "no_ack")]
    claim = w.session.get(Claim, claim.id)
    assert texts.attention_text(claim, at(7, 17, 5)) == "Todavía no se le avisó al proveedor"
    mark_sent(w.session, claim, text="Por teléfono", actor=ClaimActor.PANEL, now=at(7, 18))
    assert claim.attention is None


def test_a_scheduled_claim_is_not_an_alert(w: World) -> None:
    claim = _claim(w, w.damp, at(2, 21))
    w.notifier.dispatch(w.session, claim)
    w.session.commit()
    assert w.jobs(at(3, 7, 55)).alerted == []


def test_an_urgent_claim_not_told_after_an_hour(w: World) -> None:
    w.lifts.whatsapp_e164 = None
    claim = _claim(w, w.lift, at(3, 3))
    w.session.commit()
    assert w.jobs(at(3, 3, 55)).alerted == []
    assert w.jobs(at(3, 4, 5)).alerted == [(claim.id, "no_ack")]


# --- Two runs at once -----------------------------------------------------------------------


def test_a_run_waits_when_another_one_holds_the_lock(w: World, db_session: Session) -> None:
    claim = _claim(w, w.damp, at(2, 21))
    w.notifier.dispatch(w.session, claim)
    w.session.commit()
    engine = db_session.get_bind().engine
    with engine.connect() as other:
        assert other.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
        try:
            report = w.jobs(at(3, 8, 5))
        finally:
            other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    assert report.ran is False and report.sent == [] and w.fake.sent == []
    assert w.jobs(at(3, 8, 5)).sent == [claim.id]


def test_check_templates_expects_what_the_code_sends(w: World) -> None:
    """scripts/check_templates.py compares Meta against the same constants notify uses: the
    reminder's "solved" button without the accent, every other message with it."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_templates.py"
    spec = importlib.util.spec_from_file_location("check_templates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.EXPECTED["claim_template_reminder"] == (3, ["Recibido", "Ya esta solucionado"])
    assert module.EXPECTED["claim_template_provider"] == (6, ["Recibido", "No puedo atenderlo"])
    assert texts.SOLVED_BUTTON == "Ya está solucionado"  # the messages that are not templates
