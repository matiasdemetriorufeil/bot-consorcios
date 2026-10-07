"""The "Conversaciones" pages: list, conversation, poll and buttons, as an operator.
Invented people, phones and messages."""

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    QuickReply,
    WaAuthor,
    WaConversation,
    WaConversationStatus,
    WaMediaStatus,
    WaMessage,
    WaTemplate,
)
from tests.admin.conftest import (
    NOW,
    OPERATOR,
    OPERATOR_NAME,
    Panel,
    admin_settings,
    build_panel,
    make_user,
)
from tests.admin.wa_data import conversation, message

XSS_BODY = "<script>alert('cuerpo')</script>"
XSS_PROFILE = '<img src=x onerror="alert(1)">'
XSS_FILE = "<b>recibo</b>.pdf"


def _page(panel: Panel, conv: WaConversation) -> str:
    response = panel.client.get(f"/admin/conversations/{conv.id}")
    assert response.status_code == 200
    return response.text


def _act(panel: Panel, conv: WaConversation, action: str, **data: Any) -> Any:
    return panel.client.post(
        f"/admin/conversations/{conv.id}/action", data={"action": action, **data}
    )


def _token(page: str) -> str:
    match = re.search(r'name="form_token" value="([^"]+)"', page)
    assert match
    return match.group(1)


def _reload(panel: Panel, conv: WaConversation) -> WaConversation:
    panel.session.expire_all()
    row = panel.session.get(WaConversation, conv.id)
    assert row is not None
    return row


def _operator_messages(panel: Panel) -> list[WaMessage]:
    panel.session.expire_all()
    return list(
        panel.session.scalars(
            select(WaMessage).where(WaMessage.author == WaAuthor.OPERATOR).order_by(WaMessage.id)
        )
    )


# --- Pages ------------------------------------------------------------------------------------


def test_requires_login(panel: Panel) -> None:
    for url in ("/admin/conversations", "/admin/conversations/poll"):
        response = panel.client.get(url, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].endswith("/admin/login")


def test_list_shows_waiting_conversations(operator: Panel) -> None:
    conv = conversation(
        operator.session,
        status=WaConversationStatus.WAITING_HUMAN,
        handoff_reason="window_closed",
        handed_off_at=NOW,
        unread_count=2,
    )
    message(operator.session, conv, "necesito ayuda")

    page = operator.client.get("/admin/conversations").text

    assert "Esperando persona" in page and "Mías" in page and "Con el bot" in page
    assert "Contacto Inventado" in page and "necesito ayuda" in page
    # The reason's short label in Spanish (it used to be the tag, "ventana-cerrada").
    assert ">Ventana cerrada</span>" in page
    assert "ventana-cerrada" not in page
    assert f"/admin/conversations/{conv.id}" in page


def test_conversation_shows_bubbles_notes_and_attachments(operator: Panel) -> None:
    conv = conversation(operator.session, status=WaConversationStatus.WAITING_HUMAN)
    message(operator.session, conv, "hola")
    message(operator.session, conv, "¡Hola! Soy el asistente.", author=WaAuthor.BOT)
    message(operator.session, conv, "Te ayudo yo", author=WaAuthor.OPERATOR, operator=OPERATOR)
    message(
        operator.session,
        conv,
        "🤖 Derivado por el bot\nMotivo: Pidió hablar con una persona",
        author=WaAuthor.SYSTEM,
        message_type="note",
        is_internal_note=True,
        status=None,
    )
    photo = message(
        operator.session,
        conv,
        None,
        message_type="image",
        media_id="m1",
        media_mime="image/jpeg",
        media_status=WaMediaStatus.STORED,
        media_path="2026/09/a.jpg",
    )
    document = message(
        operator.session,
        conv,
        None,
        message_type="document",
        media_id="m2",
        media_mime="application/pdf",
        media_status=WaMediaStatus.STORED,
        media_path="2026/09/a.pdf",
        media_filename="expensas.pdf",
    )
    message(
        operator.session,
        conv,
        None,
        message_type="video",
        media_id="m3",
        media_status=WaMediaStatus.TOO_LARGE,
    )

    page = _page(operator, conv)

    assert 'class="msg msg-in msg-contact"' in page
    assert 'class="msg msg-out msg-bot"' in page
    assert 'class="msg msg-out msg-operator"' in page and OPERATOR_NAME in page
    assert "msg-note msg-system msg-handoff" in page and "Derivación del bot" in page
    assert f'<img class="msg-img" src="https://testserver/admin/wa/media/{photo.id}"' in page
    assert f'href="https://testserver/admin/wa/media/{document.id}"' in page
    assert "expensas.pdf" in page
    assert "demasiado grande" in page
    # The contact card is there too.
    assert "Número no identificado" in page


def test_opening_marks_it_read(operator: Panel) -> None:
    conv = conversation(operator.session, unread_count=4)

    _page(operator, conv)

    assert _reload(operator, conv).unread_count == 0


def test_unknown_conversation_404(operator: Panel) -> None:
    assert operator.client.get("/admin/conversations/999999").status_code == 404


# --- What the contact sends is only text ------------------------------------------------------


def _hostile(panel: Panel) -> WaConversation:
    conv = conversation(
        panel.session,
        profile_name=XSS_PROFILE,
        status=WaConversationStatus.WAITING_HUMAN,
        handed_off_at=NOW,
    )
    message(panel.session, conv, XSS_BODY, at=NOW - timedelta(minutes=50))
    message(
        panel.session,
        conv,
        XSS_BODY,
        message_type="document",
        media_id="m",
        media_mime="text/html",
        media_status=WaMediaStatus.STORED,
        media_path="2026/09/x.html",
        media_filename=XSS_FILE,
        at=NOW - timedelta(minutes=40),
    )
    return conv


def _assert_escaped(html: str) -> None:
    for raw in (XSS_BODY, XSS_PROFILE, XSS_FILE):
        assert raw not in html
    assert "&lt;script&gt;" in html


def test_contact_texts_are_escaped_in_the_page(operator: Panel) -> None:
    conv = _hostile(operator)

    _assert_escaped(_page(operator, conv))
    _assert_escaped(operator.client.get("/admin/conversations").text)


def test_contact_texts_are_escaped_in_the_poll(operator: Panel) -> None:
    conv = _hostile(operator)

    data = operator.client.get(
        "/admin/conversations/poll", params={"open": conv.id, "after": 0}
    ).json()

    _assert_escaped(data["inbox_html"])
    _assert_escaped(data["conversation"]["new_html"])


# --- Buttons ----------------------------------------------------------------------------------


def test_take_return_resolve_and_note_are_audited_with_the_user(operator: Panel) -> None:
    conv = conversation(operator.session, status=WaConversationStatus.WAITING_HUMAN)

    _act(operator, conv, "take")
    row = _reload(operator, conv)
    assert (row.status, row.assigned_to) == (WaConversationStatus.HUMAN, OPERATOR)

    page = _page(operator, conv)
    _act(operator, conv, "note", text="Dejé mensaje", form_token=_token(page))
    _act(operator, conv, "return")
    assert _reload(operator, conv).status == WaConversationStatus.BOT
    _act(operator, conv, "resolve")
    assert _reload(operator, conv).status == WaConversationStatus.RESOLVED

    events = [e for e in operator.admin_events() if e["action"].startswith("conversation_")]
    assert [e["action"] for e in events] == [
        "conversation_taken",
        "conversation_note_added",
        "conversation_returned_to_bot",
        "conversation_resolved",
    ]
    assert {e["admin_user"] for e in events} == {OPERATOR}
    [note] = _operator_messages(operator)
    assert note.is_internal_note and note.body == "Dejé mensaje"


def test_reply_takes_it_and_sends(operator: Panel) -> None:
    conv = conversation(operator.session, status=WaConversationStatus.BOT)
    page = _page(operator, conv)
    assert 'id="reply-text"' in page

    response = _act(operator, conv, "reply", text="Hola, te ayudo", form_token=_token(page))

    assert response.status_code == 200
    assert "Mensaje enviado" in response.text
    assert operator.whatsapp.texts() == ["Hola, te ayudo"]
    row = _reload(operator, conv)
    assert (row.status, row.assigned_to) == (WaConversationStatus.HUMAN, OPERATOR)
    [sent] = _operator_messages(operator)
    assert sent.operator == OPERATOR and sent.body == "Hola, te ayudo"
    [event] = operator.admin_events("conversation_replied")
    assert event["admin_user"] == OPERATOR and event["message_id"] == sent.id


def test_a_double_submit_sends_once(operator: Panel) -> None:
    conv = conversation(operator.session)
    token = _token(_page(operator, conv))

    _act(operator, conv, "reply", text="Una sola vez", form_token=token)
    again = _act(operator, conv, "reply", text="Una sola vez", form_token=token)

    assert "ya se había enviado" in again.text
    assert operator.whatsapp.texts() == ["Una sola vez"]
    assert len(_operator_messages(operator)) == 1


def test_a_send_without_token_is_refused(operator: Panel) -> None:
    conv = conversation(operator.session)

    _act(operator, conv, "reply", text="Sin token")

    assert operator.whatsapp.sent == []


def test_window_closed_offers_templates_only(operator: Panel) -> None:
    conv = conversation(operator.session, last_inbound_at=NOW - timedelta(hours=30))
    operator.session.add(
        WaTemplate(name="seguimiento", label="Seguimiento", body="¿Pudiste resolverlo?")
    )
    operator.session.add(WaTemplate(name="vieja", label="Vieja", body="x", active=False))
    operator.session.flush()

    page = _page(operator, conv)

    assert 'id="reply-text"' not in page
    assert "solo permite mandarle una plantilla aprobada" in page
    assert "Seguimiento" in page and "Vieja" not in page

    _act(operator, conv, "reply", text="Hola", form_token=_token(page))
    assert operator.whatsapp.sent == []

    page = _page(operator, conv)
    template_id = re.search(r'<option value="(\d+)"', page)
    assert template_id
    response = _act(
        operator, conv, "template", template_id=template_id.group(1), form_token=_token(page)
    )
    assert "Mensaje enviado" in response.text
    assert operator.whatsapp.kinds() == ["template"]
    assert operator.admin_events("conversation_template_sent")[0]["admin_user"] == OPERATOR


def test_quick_replies_are_offered(operator: Panel) -> None:
    conv = conversation(operator.session)
    operator.session.add(QuickReply(title="Saludo", content="¡Hola! ¿En qué te ayudo?"))
    operator.session.add(QuickReply(title="Oculta", content="no", active=False))
    operator.session.flush()

    page = _page(operator, conv)

    assert "Respuestas rápidas" in page
    assert 'data-text="¡Hola! ¿En qué te ayudo?"' in page
    assert "Oculta" not in page


def test_another_operators_conversation_stays_hers(operator: Panel) -> None:
    make_user(operator.session, "lucia", display_name="Lucía Inventada")
    conv = conversation(operator.session, status=WaConversationStatus.HUMAN, assigned_to="lucia")
    page = _page(operator, conv)
    assert "La tiene Lucía Inventada" in page

    response = _act(operator, conv, "reply", text="Te escribo yo", form_token=_token(page))

    assert "sigue asignada a Lucía Inventada" in response.text
    assert _reload(operator, conv).assigned_to == "lucia"


def test_without_whatsapp_configured(db_session: Session) -> None:
    panel = build_panel(db_session, admin_settings(), whatsapp_ready=False)
    with panel.client:
        panel.login()
        conv = conversation(db_session)

        page = _page(panel, conv)

        assert "WhatsApp no está configurado" in page and 'id="reply-text"' not in page


# --- Poll -------------------------------------------------------------------------------------


def test_poll_brings_new_messages_and_what_makes_it_beep(operator: Panel) -> None:
    waiting = conversation(
        operator.session,
        "5493515550201",
        status=WaConversationStatus.WAITING_HUMAN,
        handed_off_at=NOW,
    )
    mine = conversation(
        operator.session,
        "5493515550202",
        status=WaConversationStatus.HUMAN,
        assigned_to=OPERATOR,
        unread_count=1,
    )
    first = message(operator.session, mine, "primero")
    new = message(operator.session, mine, "segundo")

    data = operator.client.get(
        "/admin/conversations/poll",
        params={"open": mine.id, "after": first.id, "tab": "mine", "visible": "1"},
    ).json()

    assert data["waiting_ids"] == [waiting.id]
    assert data["mine_last"] == {str(mine.id): new.id}
    assert data["counts"] == {"waiting": 1, "mine": 1}
    c = data["conversation"]
    assert c["last_id"] == new.id and "segundo" in c["new_html"] and "primero" not in c["new_html"]
    assert c["status"] == "human" and c["window_open"] is True
    assert "Devolver al bot" in c["header_html"]
    assert _reload(operator, mine).unread_count == 0  # she is looking at it


def test_poll_with_the_tab_hidden_keeps_unread(operator: Panel) -> None:
    conv = conversation(operator.session, unread_count=2)

    operator.client.get("/admin/conversations/poll", params={"open": conv.id, "visible": "0"})

    assert _reload(operator, conv).unread_count == 2


def test_poll_reports_outbound_statuses(operator: Panel) -> None:
    conv = conversation(operator.session)
    sent = message(operator.session, conv, "listo", author=WaAuthor.BOT)

    data = operator.client.get(
        "/admin/conversations/poll", params={"open": conv.id, "after": sent.id}
    ).json()

    assert data["conversation"]["statuses"] == {str(sent.id): "sent"}
    assert data["conversation"]["new_html"] == ""


def test_the_page_has_the_alerts_and_double_click_guard(operator: Panel) -> None:
    conv = conversation(operator.session)

    page = _page(operator, conv)

    assert 'id="enable-alerts"' in page and "Notification" in page
    assert 'class="js-once"' in page and "Enviando…" in page
    assert "|safe" not in page


# --- A resolved conversation --------------------------------------------------------------------


def test_a_resolved_one_shows_no_take_nor_return(operator: Panel) -> None:
    conv = conversation(
        operator.session, status=WaConversationStatus.RESOLVED, handoff_reason="debt_claim"
    )
    message(operator.session, conv, "gracias")

    page = _page(operator, conv)

    assert "Tomar control" not in page and "Devolver al bot" not in page
    assert 'value="resolve"' not in page
    assert "Nota interna" in page and 'id="reply-text"' in page
    assert "Si respondés, la conversación se vuelve a abrir y queda en Mías." in page


def test_take_and_return_refuse_a_resolved_one(operator: Panel) -> None:
    conv = conversation(operator.session, status=WaConversationStatus.RESOLVED)

    for action in ("take", "return"):
        response = _act(operator, conv, action)
        assert "La conversación está resuelta: respondé para volver a abrirla." in response.text

    assert _reload(operator, conv).status == WaConversationStatus.RESOLVED
    assert operator.admin_events() == []


def test_replying_reopens_a_resolved_one_as_mine(operator: Panel) -> None:
    conv = conversation(operator.session, status=WaConversationStatus.RESOLVED)
    page = _page(operator, conv)

    _act(operator, conv, "reply", text="¿Pudiste resolverlo?", form_token=_token(page))

    row = _reload(operator, conv)
    assert (row.status, row.assigned_to) == (WaConversationStatus.HUMAN, OPERATOR)
    mine = operator.client.get("/admin/conversations", params={"tab": "mine"}).text
    assert f"/admin/conversations/{conv.id}" in mine


def test_the_reason_label_is_in_spanish_in_the_conversation(operator: Panel) -> None:
    conv = conversation(
        operator.session, status=WaConversationStatus.WAITING_HUMAN, handoff_reason="debt_claim"
    )

    page = _page(operator, conv)

    # A short label (the long text in its tooltip and in the handoff summary).
    assert (
        '<span class="badge bg-orange-lt chat-reason" title="Reclamo por la deuda">'
        "Reclamo de deuda</span>"
    ) in page
    assert "reclamo-deuda" not in page
