"""WhatsApp attachments in the panel: only for logged-in users. Invented data and files."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models import (
    WaAuthor,
    WaContact,
    WaConversation,
    WaDirection,
    WaMediaStatus,
    WaMessage,
)
from tests.admin.conftest import Panel, admin_settings, build_panel


@pytest.fixture
def media_panel(db_session: Session, tmp_path: Path) -> Iterator[Panel]:
    built = build_panel(db_session, admin_settings(whatsapp_media_dir=str(tmp_path)))
    with built.client:
        yield built


def _message(session: Session, media_dir: Path, **values: object) -> WaMessage:
    contact = WaContact(phone_e164="+5493515550909", wa_id="5493515550909")
    session.add(contact)
    session.flush()
    conversation = WaConversation(contact_id=contact.id)
    session.add(conversation)
    session.flush()
    (media_dir / "2026" / "09").mkdir(parents=True, exist_ok=True)
    (media_dir / "2026" / "09" / "abc.jpg").write_bytes(b"jpg inventado")
    (media_dir / "2026" / "09" / "abc.pdf").write_bytes(b"%PDF inventado")
    fields: dict[str, object] = {
        "media_mime": "image/jpeg",
        "media_path": "2026/09/abc.jpg",
        "media_status": WaMediaStatus.STORED,
        **values,
    }
    message = WaMessage(
        conversation_id=conversation.id,
        direction=WaDirection.INBOUND,
        author=WaAuthor.CONTACT,
        message_type="image",
        media_id="1",
        **fields,
    )
    session.add(message)
    session.flush()
    return message


def test_requires_login(media_panel: Panel, tmp_path: Path) -> None:
    message = _message(media_panel.session, tmp_path)

    response = media_panel.client.get(f"/admin/wa/media/{message.id}", follow_redirects=False)

    assert response.status_code in (302, 303)
    assert "/admin/login" in response.headers["location"]


def test_an_image_is_shown_inline(media_panel: Panel, tmp_path: Path) -> None:
    message = _message(media_panel.session, tmp_path)
    media_panel.login()

    response = media_panel.client.get(f"/admin/wa/media/{message.id}")

    assert response.status_code == 200
    assert response.content == b"jpg inventado"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline")


def test_a_document_is_a_download(media_panel: Panel, tmp_path: Path) -> None:
    message = _message(
        media_panel.session,
        tmp_path,
        media_mime="application/pdf",
        media_path="2026/09/abc.pdf",
        media_filename='recibo "marzo".pdf',
    )
    media_panel.login()

    response = media_panel.client.get(f"/admin/wa/media/{message.id}")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment") and "recibo%20marzo.pdf" in disposition


@pytest.mark.parametrize(
    "values",
    [
        {"media_path": "../../fuera.jpg"},
        {"media_path": "2026/09/no-existe.jpg"},
        {"media_status": WaMediaStatus.TOO_LARGE},
    ],
)
def test_not_found(media_panel: Panel, tmp_path: Path, values: dict[str, object]) -> None:
    message = _message(media_panel.session, tmp_path, **values)
    media_panel.login()

    assert media_panel.client.get(f"/admin/wa/media/{message.id}").status_code == 404
    assert media_panel.client.get("/admin/wa/media/999999").status_code == 404


def test_not_in_the_menu(media_panel: Panel) -> None:
    media_panel.login()

    assert "Adjuntos de WhatsApp" not in media_panel.client.get("/admin/").text


@pytest.mark.parametrize(
    ("mime", "name", "content"),
    [
        ("image/svg+xml", "x.svg", b"<svg onload='alert(1)'></svg>"),
        ("text/html", "x.html", b"<script>alert(1)</script>"),
    ],
)
def test_svg_or_html_is_never_opened_in_the_panel(
    media_panel: Panel, tmp_path: Path, mime: str, name: str, content: bytes
) -> None:
    """Even if one were stored, it goes as a download that the browser must not sniff."""
    message = _message(media_panel.session, tmp_path, media_mime=mime, media_path=f"2026/09/{name}")
    (tmp_path / "2026" / "09" / name).write_bytes(content)
    media_panel.login()

    response = media_panel.client.get(f"/admin/wa/media/{message.id}")

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"
