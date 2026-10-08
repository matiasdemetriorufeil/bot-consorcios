"""A claim reported by WhatsApp end to end: webhook, store, processor, the claim flow and the
channel, with the Cloud API faked and no model call after the first one. Only the neighbor gets
messages (nothing goes to the provider in this step). Invented data only."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims import texts
from app.db.models import Building, Claim, ClaimStatus
from tests.claims import factories as cf
from tests.llm.fakes import Call
from tests.whatsapp.conftest import MakeWa, Wa
from tests.whatsapp.fakes import (
    CONTACT_WA_ID,
    FakeWhatsApp,
    button_reply,
    incoming,
    list_reply,
    media_message,
    text_message,
)

PROVIDER_PHONE = "+5493515550150"
URL = "https://lookaside.example/foto"
MEDIA_ID = "900000000000077"


def _enable(session: Session) -> None:
    building = session.scalar(select(Building).where(Building.name == "031 RODAS II"))
    assert building is not None
    building.claims_bot_enabled = True
    lift = cf.category(session, "No funciona el ascensor inventado", list_title="Ascensor")
    damp = cf.category(session, "Humedad inventada", list_title="Humedad o filtración")
    provider = cf.provider(session, "Plomería Ficticia", PROVIDER_PHONE)
    cf.assign(session, building.id, lift)
    cf.assign(session, building.id, damp, provider)
    session.commit()


def _post(wa: Wa, payload: dict) -> None:
    assert wa.post(incoming(payload)).status_code == 200


def test_a_claim_with_a_photo_by_whatsapp(make_wa: MakeWa, db_session: Session) -> None:
    fake = FakeWhatsApp()
    fake.media[MEDIA_ID] = {"url": URL, "mime_type": "image/jpeg", "id": MEDIA_ID}
    fake.files[URL] = b"\xff\xd8 foto inventada"
    wa = make_wa([Call("start_claim", {"category_hint": "humedad en el techo"})], fake=fake)
    _enable(db_session)

    _post(wa, text_message("Hola, tengo humedad en el techo", wa.now))
    _post(wa, button_reply("Sí", wa.now))
    _post(wa, text_message("Mancha grande en el baño", wa.now))
    _post(wa, media_message("image", wa.now, media_id=MEDIA_ID))
    _post(wa, button_reply("Listo", wa.now))
    _post(wa, button_reply("Sí, registrar", wa.now))

    [claim] = list(db_session.scalars(select(Claim)))
    assert claim.status == ClaimStatus.PENDING_SEND  # a provider, still not notified (8.5)
    assert len(claim.attachments) == 1 and claim.wa_conversation_id == wa.conversation().id
    sent = fake.sent
    assert {to for _, to, _ in sent} == {CONTACT_WA_ID}  # only the neighbor, never the provider
    last_kind, _, last = sent[-1]
    assert last_kind == "text"
    assert last == texts.CREATED_WITH_PROVIDER.format(
        number=claim.number, problem="Humedad o filtración"
    )
    assert any(
        kind == "choices" and content[0] == texts.PHOTO_RECEIVED.format(count=1, limit=5)
        for kind, _, content in sent
    )
    assert wa.llm_calls == 1


def test_a_photo_without_a_claim_still_gets_the_fixed_reply(make_wa: MakeWa) -> None:
    fake = FakeWhatsApp()
    fake.media[MEDIA_ID] = {"url": URL, "mime_type": "image/jpeg", "id": MEDIA_ID}
    fake.files[URL] = b"\xff\xd8 foto inventada"
    wa = make_wa(fake=fake)
    _post(wa, media_message("image", wa.now, media_id=MEDIA_ID))
    assert wa.llm_calls == 0 and len(fake.texts()) == 1


def test_the_menu_list_goes_to_the_flow(make_wa: MakeWa, db_session: Session) -> None:
    wa = make_wa()
    _enable(db_session)
    _post(wa, list_reply("Registrar reclamo", wa.now))
    kind, _, (text, titles) = wa.fake.sent[-1]
    assert kind == "choices" and titles[:2] == ["Ascensor", "Humedad o filtración"]
    assert texts.CHOOSE_KIND in text and wa.llm_calls == 0  # after the greeting (first message)
