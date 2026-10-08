"""Sending claims by WhatsApp (app.claims.notify) and the provider's answers
(app.claims.provider_flow), with the Cloud API faked: no network. Invented data only
(fictitious buildings, people and providers, phones 351 555-01xx)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims import payloads, texts
from app.claims.notify import DETAIL_LIMIT, Notifier, flat, template_params, unit_and_contact
from app.claims.provider_flow import ProviderMessage, handle
from app.claims.service import Reporter, close_claim, create_claim, mark_sent
from app.config import Settings
from app.db.models import (
    Claim,
    ClaimActor,
    ClaimAttachment,
    ClaimAttention,
    ClaimCategory,
    ClaimEvent,
    ClaimEventKind,
    ClaimScope,
    ClaimSource,
    ClaimStatus,
    Provider,
    WaConversation,
    WaConversationStatus,
    WaMediaStatus,
    WaMessage,
)
from tests.admin.wa_data import conversation, message
from tests.bot import factories as f
from tests.claims import factories as cf
from tests.whatsapp.fakes import FakeWhatsApp

NOW = datetime(2026, 10, 8, 11, 0, tzinfo=UTC)
ANA, BETO = "+5493515550101", "+5493515550102"
LIFTS_WA, OTHER_WA = "+5493515550150", "+5493515550151"


@dataclass
class World:
    session: Session
    fake: FakeWhatsApp
    notifier: Notifier
    lift: ClaimCategory
    damp: ClaimCategory
    lifts: Provider
    other: Provider
    building_id: int
    unit_id: int


@pytest.fixture
def w(db_session: Session, tmp_path: Any) -> World:
    s = db_session
    building = f.building(s, "001 TORRE INVENTADA")
    building.address = "Calle\tInventada   100\n(esquina)"
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
    notifier = Notifier(fake, settings, now=lambda: NOW)
    return World(s, fake, notifier, lift, damp, lifts, other, building.id, unit.id)


def _claim(
    w: World,
    category: ClaimCategory,
    phone: str | None = ANA,
    name: str = "Ana Inventada",
    description: str = "Se trabó en el 3",
) -> Claim:
    result = create_claim(
        w.session,
        building_id=w.building_id,
        category_id=category.id,
        unit_id=w.unit_id,
        description=description,
        reporter=Reporter(name=name, phone_e164=phone, unit_id=w.unit_id),
        source=ClaimSource.BOT,
        now=NOW,
    )
    w.session.flush()
    return result.claim


def _events(w: World, claim: Claim, kind: ClaimEventKind) -> list[ClaimEvent]:
    return list(
        w.session.scalars(
            select(ClaimEvent).where(ClaimEvent.claim_id == claim.id, ClaimEvent.kind == kind)
        )
    )


def _open_window(w: World, phone: str) -> None:
    conversation(w.session, phone[1:], last_inbound_at=NOW - timedelta(hours=1))


def _press(w: World, provider: Provider, payload: str, text: str = "") -> str:
    conv = w.session.scalar(
        select(WaConversation)
        .join(WaConversation.contact)
        .where(WaConversation.contact.has(phone_e164=provider.whatsapp_e164))
    )
    return handle(
        w.session,
        provider,
        ProviderMessage(text=text, payload=payload, conversation_id=conv.id),
        w.notifier,
        now=NOW,
    )


def _sent_claim(w: World, category: ClaimCategory | None = None, **kw: Any) -> Claim:
    claim = _claim(w, category or w.lift, **kw)
    assert w.notifier.notify_provider(w.session, claim).ok
    w.fake.sent.clear()
    w.fake.payloads.clear()
    return claim


def _payload(w: World, claim: Claim, action: str, provider: Provider | None = None) -> str:
    who = provider or w.lifts
    return payloads.sign(w.notifier.secret, claim.id, action, payloads.provider_subject(who.id))


# --- Variables -------------------------------------------------------------------------------


def test_variables_are_one_line_and_never_empty(w: World) -> None:
    claim = _claim(w, w.lift, description="Primera línea\n\tsegunda" + " x" * 400)
    params = template_params(claim)
    assert len(params) == 6
    assert params[0] == str(claim.number)
    assert params[1] == "TORRE INVENTADA"
    assert params[2] == "Calle Inventada 100 (esquina)"
    assert params[3] == "Ascensor (URGENTE)"
    assert params[4].startswith("Primera línea segunda") and params[4].endswith("…")
    assert len(params[4]) == DETAIL_LIMIT
    for value in params:
        assert value and "\n" not in value and "\t" not in value and "    " not in value
    assert flat("") == flat(None) == "—"


def test_the_contact_only_for_a_kind_of_one_unit(w: World) -> None:
    building_kind = _claim(w, w.lift)
    assert unit_and_contact(building_kind) == "Todo el edificio"
    assert "Ana" not in " ".join(template_params(building_kind))
    one_unit = _claim(w, w.damp, phone=BETO, name="Beto Ficticio")
    assert unit_and_contact(one_unit) == "Unidad 02-B · Beto Ficticio · 351 555-0102"


def test_a_building_without_address(w: World) -> None:
    claim = _claim(w, w.lift)
    claim.building.address = None
    assert template_params(claim)[2] == "—"


# --- Payloads ---------------------------------------------------------------------------------


def test_payloads_are_signed_for_one_provider_or_phone(w: World) -> None:
    secret = w.notifier.secret
    subject = payloads.provider_subject(w.lifts.id)
    good = payloads.sign(secret, 7, "ack", subject)
    assert payloads.read(secret, good, subject) == payloads.Payload(7, "ack")
    assert payloads.read(secret, good, payloads.provider_subject(w.other.id)) is None
    assert payloads.read(b"otro-secreto", good, subject) is None
    forged = good.replace(".7.", ".8.")
    assert payloads.read(secret, forged, subject) is None
    made_up = "clm1.7.solved.000000000000000000000000"
    assert payloads.read(secret, made_up, subject) is None
    assert payloads.read(secret, "opt-1", subject) is None
    again = payloads.sign(secret, 7, "again", ANA)
    assert payloads.read(secret, again, BETO) is None


# --- The provider -------------------------------------------------------------------------------


def test_the_claim_goes_to_its_provider(w: World) -> None:
    claim = _claim(w, w.lift)
    sent = w.notifier.notify_provider(w.session, claim)

    assert sent.ok and claim.status == ClaimStatus.SENT
    [(kind, to, (name, language))] = w.fake.sent
    assert (kind, to, name, language) == (
        "template",
        LIFTS_WA[1:],
        "reclamo_nuevo_proveedor",
        "es_AR",
    )
    body, ack, decline = w.fake.components[0]
    assert [p["text"] for p in body["parameters"]] == template_params(claim)
    subject = payloads.provider_subject(w.lifts.id)
    assert (
        payloads.read(w.notifier.secret, ack["parameters"][0]["payload"], subject).action == "ack"
    )
    assert (
        payloads.read(w.notifier.secret, decline["parameters"][0]["payload"], subject).action
        == "decline"
    )
    [event] = _events(w, claim, ClaimEventKind.SENT)
    assert event.text == "Avisado a Ascensores Ficticios SRL por WhatsApp"
    stored = w.session.get(WaMessage, event.wa_message_id)
    assert stored.message_type == "template" and stored.choices == [
        "Recibido",
        "No puedo atenderlo",
    ]
    conv = w.session.get(WaConversation, stored.conversation_id)
    assert conv.status == WaConversationStatus.PROVIDER


def test_a_failed_send_stays_pending_with_an_alert(w: World) -> None:
    w.fake.fail_on.add("template")
    claim = _claim(w, w.lift)
    sent = w.notifier.notify_provider(w.session, claim)
    assert not sent.ok and "código" in sent.error
    assert claim.status == ClaimStatus.PENDING_SEND
    assert claim.attention == ClaimAttention.SEND_FAILED
    [event] = _events(w, claim, ClaimEventKind.NOTIFY_FAILED)
    assert event.text.startswith("No se pudo avisar a Ascensores Ficticios SRL por WhatsApp")
    # Marked as told later: the alert goes away.
    mark_sent(w.session, claim, actor=ClaimActor.PANEL, user="marta")
    assert claim.attention is None


def test_a_provider_without_whatsapp_gets_nothing(w: World) -> None:
    w.lifts.whatsapp_e164 = None
    claim = _claim(w, w.lift)
    assert not w.notifier.notify_provider(w.session, claim).ok
    assert claim.status == ClaimStatus.PENDING_SEND and w.fake.sent == []


def test_only_a_pending_claim_is_sent(w: World) -> None:
    claim = _sent_claim(w)
    assert not w.notifier.notify_provider(w.session, claim).ok
    assert w.fake.sent == []


# --- What the provider answers ------------------------------------------------------------------


def test_received_acknowledges_sends_the_photos_and_tells_the_neighbors(
    w: World, tmp_path: Any
) -> None:
    claim = _sent_claim(w)
    conv = conversation(w.session, ANA[1:], last_inbound_at=NOW - timedelta(hours=1))
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "foto.jpg").write_bytes(b"\xff\xd8 foto inventada")
    photo = message(w.session, conv, None, message_type="image", media_mime="image/jpeg",
                    media_status=WaMediaStatus.STORED, media_path="x/foto.jpg")  # fmt: skip
    w.session.add(ClaimAttachment(claim_id=claim.id, wa_message_id=photo.id))
    w.session.flush()

    assert _press(w, w.lifts, _payload(w, claim, "ack")) == "acknowledged"

    assert claim.status == ClaimStatus.ACKNOWLEDGED
    kinds = [(kind, to) for kind, to, _ in w.fake.sent]
    assert kinds == [("choices", LIFTS_WA[1:]), ("image", LIFTS_WA[1:]), ("text", ANA[1:])]
    text, titles = w.fake.sent[0][2]
    assert text == texts.PROVIDER_THANKS.format(number=claim.number)
    assert titles == ["Ya está solucionado"]
    solved = w.fake.payloads[0][0]
    assert (
        payloads.read(w.notifier.secret, solved, payloads.provider_subject(w.lifts.id)).action
        == "solved"
    )
    assert w.fake.sent[2][2] == texts.NEIGHBOR_CONFIRMED.format(
        number=claim.number, problem="Ascensor"
    )
    [notice] = _events(w, claim, ClaimEventKind.NOTIFIED)
    assert notice.text == "Avisado a Ana Inventada por WhatsApp: la empresa confirmó"

    # Tapped again: only the thanks, nobody is told twice.
    w.fake.sent.clear()
    assert _press(w, w.lifts, _payload(w, claim, "ack")) == "acknowledged_again"
    assert [kind for kind, _, _ in w.fake.sent] == ["choices"]


def test_cannot_attend_goes_to_the_studio_without_telling_the_neighbor(w: World) -> None:
    claim = _sent_claim(w)
    _open_window(w, ANA)
    assert _press(w, w.lifts, _payload(w, claim, "decline")) == "declined"
    assert claim.status == ClaimStatus.STUDIO and claim.provider_id is None
    assert claim.attention == ClaimAttention.DECLINED
    [event] = _events(w, claim, ClaimEventKind.DECLINED)
    assert event.text == "Ascensores Ficticios SRL dijo que no puede atenderlo"
    assert [(kind, to) for kind, to, _ in w.fake.sent] == [("text", LIFTS_WA[1:])]
    assert w.fake.sent[0][2] == texts.PROVIDER_DECLINED


@pytest.mark.parametrize("acknowledged", [False, True])
def test_solved_closes_it_and_tells_the_neighbors(w: World, acknowledged: bool) -> None:
    claim = _sent_claim(w)
    if acknowledged:
        _press(w, w.lifts, _payload(w, claim, "ack"))
        w.fake.sent.clear()
    assert _press(w, w.lifts, _payload(w, claim, "solved")) == "solved"
    assert claim.status == ClaimStatus.SOLVED
    assert claim.close_reason == "Avisado por Ascensores Ficticios SRL"
    assert w.fake.sent[0][2] == texts.PROVIDER_CLOSED.format(number=claim.number)
    # Ana never wrote (no window): the template, with "Registrar reclamo" for her phone.
    kind, to, (name, _) = w.fake.sent[1]
    assert (kind, to, name) == ("template", ANA[1:], "reclamo_solucionado_vecino")
    again = w.fake.payloads[-1][0]
    assert payloads.read(w.notifier.secret, again, ANA).action == "again"


@pytest.mark.parametrize("case", ["forged", "other_provider", "closed", "moved"])
def test_a_button_of_a_claim_that_is_not_yours_changes_nothing(w: World, case: str) -> None:
    claim = _sent_claim(w)
    payload = _payload(w, claim, "solved")
    presser = w.lifts
    if case == "forged":
        payload = payload[:-4] + "0000"
    elif case == "other_provider":
        presser = w.other
        conversation(w.session, OTHER_WA[1:])
    elif case == "closed":
        close_claim(w.session, claim, ClaimStatus.CANCELLED, "Repetido", actor=ClaimActor.PANEL)
    elif case == "moved":
        claim.provider_id = w.other.id
        w.session.flush()
    status = claim.status
    assert _press(w, presser, payload) == "not_yours"
    assert claim.status == status
    assert w.fake.sent[-1][2] == texts.PROVIDER_NOT_YOURS


def test_free_text_with_one_open_claim_and_the_reminder_every_12_hours(w: World) -> None:
    claim = _sent_claim(w)
    _press(w, w.lifts, _payload(w, claim, "ack"))
    w.fake.sent.clear()

    assert _press(w, w.lifts, "", text="Mañana a las 9 vamos") == "text_nudged"
    [event] = _events(w, claim, ClaimEventKind.PROVIDER_MESSAGE)
    assert event.text == "Mañana a las 9 vamos" and event.actor == ClaimActor.PROVIDER
    assert w.fake.sent[-1][2][0] == texts.PROVIDER_NUDGE.format(number=claim.number)
    assert claim.status == ClaimStatus.ACKNOWLEDGED  # never read as a close

    w.fake.sent.clear()
    assert _press(w, w.lifts, "", text="Ya está todo listo") == "text_saved"
    assert w.fake.sent == []  # less than 12 h since the last reminder
    claim.provider_nudged_at = NOW - timedelta(hours=13)
    assert _press(w, w.lifts, "", text="Listo") == "text_nudged"


def test_free_text_with_several_open_claims_only_stays_in_the_conversation(w: World) -> None:
    first = _sent_claim(w)
    second = _sent_claim(w, w.damp, phone=BETO, name="Beto Ficticio")
    assert _press(w, w.lifts, "", text="Hola") == "text_several"
    assert _events(w, first, ClaimEventKind.PROVIDER_MESSAGE) == []
    assert _events(w, second, ClaimEventKind.PROVIDER_MESSAGE) == []
    assert w.fake.sent == []


# --- The neighbors ------------------------------------------------------------------------------


def test_each_phone_is_told_once_text_or_template(w: World) -> None:
    claim = _sent_claim(w)
    _claim(w, w.lift, phone=BETO, name="Beto Ficticio")  # Beto joins
    _claim(w, w.lift, phone=None, name="Carla Sin Teléfono")  # joins without a phone
    _open_window(w, BETO)
    w.session.refresh(claim)

    sent = w.notifier.notify_neighbors(w.session, claim, "confirmed")

    assert len(sent) == 2
    by_to = {to: (kind, content) for kind, to, content in w.fake.sent}
    assert by_to[ANA[1:]][0] == "template" and by_to[ANA[1:]][1][0] == "reclamo_confirmado_vecino"
    assert by_to[BETO[1:]] == (
        "text",
        texts.NEIGHBOR_CONFIRMED.format(number=claim.number, problem="Ascensor"),
    )
    [failed] = _events(w, claim, ClaimEventKind.NOTIFY_FAILED)
    assert failed.text == "No se le pudo avisar a Carla Sin Teléfono: no tiene teléfono"


def test_the_same_phone_twice_is_told_once(w: World) -> None:
    claim = _sent_claim(w)
    claim.reporters[:] = []
    w.session.flush()
    _claim(w, w.lift, phone=ANA, name="Ana Otra Vez")  # same phone: not added again
    w.notifier.notify_neighbors(w.session, claim, "solved")
    assert [to for _, to, _ in w.fake.sent] == [ANA[1:]]


def test_a_solved_notice_with_the_window_open_has_the_button(w: World) -> None:
    claim = _sent_claim(w)
    _open_window(w, ANA)
    w.notifier.notify_neighbors(w.session, claim, "solved")
    [(kind, to, (text, titles))] = w.fake.sent
    assert kind == "choices" and titles == ["Registrar reclamo"]
    assert text == texts.NEIGHBOR_SOLVED.format(number=claim.number, problem="Ascensor")
