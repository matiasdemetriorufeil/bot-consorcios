"""A claim reported by WhatsApp end to end: webhook, store, processor, the claim flow and the
channel, with the Cloud API faked and no model call after the first one. Then the provider's
side: its buttons come back through the webhook and never reach the agent. Invented data only."""

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
    template_button,
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
    assert claim.status == ClaimStatus.SENT  # it went to the provider at once
    assert len(claim.attachments) == 1 and claim.wa_conversation_id == wa.conversation().id
    sent = fake.sent
    assert {to for _, to, _ in sent} == {CONTACT_WA_ID, PROVIDER_PHONE[1:]}
    to_provider = [(kind, content) for kind, to, content in sent if to == PROVIDER_PHONE[1:]]
    assert to_provider == [("template", ("reclamo_nuevo_proveedor", "es_AR"))]
    last_kind, _, last = sent[-1]
    assert last_kind == "text"
    assert last == texts.CREATED_AND_SENT.format(
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


# --- The provider's side --------------------------------------------------------------------


def _registered(wa: Wa, db_session: Session) -> Claim:
    """A claim of "Humedad" (its provider has WhatsApp) registered by the neighbor."""
    _enable(db_session)
    _post(wa, list_reply("Registrar reclamo", wa.now))
    _post(wa, list_reply("Humedad o filtración", wa.now))
    _post(wa, text_message("Mancha grande en el baño", wa.now))
    _post(wa, button_reply("Sin fotos", wa.now))
    _post(wa, button_reply("Sí, registrar", wa.now))
    db_session.expire_all()
    return db_session.scalar(select(Claim))


def _template_payloads(wa: Wa) -> list[str]:
    """The payloads of the buttons of the last template sent (the provider's)."""
    buttons = [c for c in wa.fake.components[-1] if c["type"] == "button"]
    return [b["parameters"][0]["payload"] for b in buttons]


def _provider_says(wa: Wa, payload_or_text: dict) -> None:
    assert wa.post(incoming(payload_or_text, wa_id=PROVIDER_PHONE[1:])).status_code == 200


def test_the_whole_circuit_by_whatsapp(make_wa: MakeWa, db_session: Session) -> None:
    wa = make_wa()
    claim = _registered(wa, db_session)
    assert claim.status == ClaimStatus.SENT
    ack, _ = _template_payloads(wa)
    wa.fake.sent.clear()

    _provider_says(wa, template_button("Recibido", ack, wa.now))
    db_session.expire_all()
    assert db_session.get(Claim, claim.id).status == ClaimStatus.ACKNOWLEDGED
    to = {to for _, to, _ in wa.fake.sent}
    assert to == {PROVIDER_PHONE[1:], CONTACT_WA_ID}  # thanks to the provider, notice to her
    neighbor = [c for k, t, c in wa.fake.sent if t == CONTACT_WA_ID]
    assert neighbor == [
        texts.NEIGHBOR_CONFIRMED.format(number=claim.number, problem="Humedad o filtración")
    ]
    [solved] = wa.fake.payloads[-1]

    wa.fake.sent.clear()
    _provider_says(wa, button_reply("Ya está solucionado", wa.now, reply_id=solved))
    db_session.expire_all()
    assert db_session.get(Claim, claim.id).status == ClaimStatus.SOLVED
    kind, _, (text, titles) = next(s for s in wa.fake.sent if s[1] == CONTACT_WA_ID)
    assert kind == "choices" and titles == ["Registrar reclamo"]
    assert wa.llm_calls == 0  # neither the neighbor's steps nor the provider used the model
    conversation = wa.conversation(PROVIDER_PHONE[1:])
    assert conversation is not None and conversation.status == "provider"


def test_a_provider_never_reaches_the_agent(make_wa: MakeWa, db_session: Session) -> None:
    wa = make_wa([Call("start_claim", {"category_hint": "humedad"})])
    _enable(db_session)
    _provider_says(wa, text_message("Hola, ¿cómo están?", wa.now))
    _provider_says(wa, template_button("Recibido", "clm1.1.ack.inventado00000000000000", wa.now))
    assert wa.llm_calls == 0
    assert [content for _, _, content in wa.fake.sent] == [texts.PROVIDER_NOT_YOURS]
    assert wa.conversation(PROVIDER_PHONE[1:]).status == "provider"


def test_registrar_reclamo_from_the_notice_links_the_new_claim(
    make_wa: MakeWa, db_session: Session
) -> None:
    wa = make_wa()
    claim = _registered(wa, db_session)
    ack, _ = _template_payloads(wa)
    _provider_says(wa, template_button("Recibido", ack, wa.now))
    [solved] = wa.fake.payloads[-1]
    _provider_says(wa, button_reply("Ya está solucionado", wa.now, reply_id=solved))
    [again] = wa.fake.payloads[-1]

    _post(wa, button_reply("Registrar reclamo", wa.now, reply_id=again))
    _, _, (text, titles) = wa.fake.sent[-1]
    assert text.endswith("¿Es por *Humedad o filtración*?") and titles[0] == "Sí"
    _post(wa, button_reply("Sí", wa.now))
    _post(wa, text_message("Volvió la mancha", wa.now))
    _post(wa, button_reply("Sin fotos", wa.now))
    _post(wa, button_reply("Sí, registrar", wa.now))
    db_session.expire_all()
    new = db_session.scalar(select(Claim).where(Claim.id != claim.id))
    assert new is not None and new.previous_claim_id == claim.id
    assert texts.PREVIOUS.format(number=claim.number) in wa.fake.sent[-1][2]
