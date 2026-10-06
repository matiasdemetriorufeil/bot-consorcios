"""Incoming attachments: downloaded at once to the media directory, with a size limit and a
closed list of types. Invented data and files only."""

from pathlib import Path

import pytest

from app.db.models import WaMediaStatus
from app.whatsapp.media import resolve_path
from tests.whatsapp.conftest import MakeWa
from tests.whatsapp.fakes import FakeWhatsApp, incoming, media_message

MEDIA_ID = "900000000000001"
URL = "https://lookaside.example/media/1"


def _fake(mime: str, content: bytes = b"contenido", size: int | None = None) -> FakeWhatsApp:
    fake = FakeWhatsApp()
    info = {"url": URL, "mime_type": mime, "id": MEDIA_ID}
    if size is not None:
        info["file_size"] = size
    fake.media[MEDIA_ID] = info
    fake.files[URL] = content
    return fake


def test_an_allowed_file_is_stored_with_our_name(make_wa: MakeWa) -> None:
    wa = make_wa(fake=_fake("application/pdf", b"%PDF-1.7 inventado"))

    wa.post(incoming(media_message("document", wa.now, mime="application/pdf",
                                   filename="../../expensas.pdf")))  # fmt: skip

    message = wa.messages()[0]
    assert message.media_status == WaMediaStatus.STORED
    assert message.media_mime == "application/pdf"
    assert message.media_filename == "../../expensas.pdf"  # only kept, never used as a path
    assert message.media_path is not None and message.media_path.startswith("2026/09/")
    assert message.media_path.endswith(".pdf") and "expensas" not in message.media_path
    stored = Path(wa.settings.whatsapp_media_dir) / message.media_path
    assert stored.read_bytes() == b"%PDF-1.7 inventado"
    assert message.media_size == len(b"%PDF-1.7 inventado")


def test_audio_with_codecs_is_stored_and_the_bot_still_answers(make_wa: MakeWa) -> None:
    wa = make_wa(fake=_fake("audio/ogg; codecs=opus", b"ogg"))

    wa.post(incoming(media_message("audio", wa.now, mime="audio/ogg; codecs=opus")))

    message = wa.messages()[0]
    assert message.media_status == WaMediaStatus.STORED
    assert message.media_path is not None and message.media_path.endswith(".ogg")
    assert len(wa.fake.texts()) == 1  # the fixed "escribilo" reply


@pytest.mark.parametrize(
    ("fake", "expected"),
    [
        (_fake("image/jpeg", size=5000), WaMediaStatus.TOO_LARGE),  # Meta says it is big
        (_fake("image/jpeg", b"x" * 5000), WaMediaStatus.TOO_LARGE),  # it turns out big
        (_fake("application/x-msdownload"), WaMediaStatus.TYPE_NOT_ALLOWED),
        (_fake("text/html"), WaMediaStatus.TYPE_NOT_ALLOWED),
    ],
)
def test_too_large_or_not_allowed_is_not_stored(
    make_wa: MakeWa, fake: FakeWhatsApp, expected: WaMediaStatus
) -> None:
    wa = make_wa(fake=fake)  # the limit in these tests is 1000 bytes

    wa.post(incoming(media_message("document", wa.now)))

    message = wa.messages()[0]
    assert message.media_status == expected
    assert message.media_path is None
    assert not any(Path(wa.settings.whatsapp_media_dir).rglob("*.*"))


def test_videos_and_stickers_are_never_downloaded(make_wa: MakeWa) -> None:
    wa = make_wa(fake=_fake("video/mp4"))

    wa.post(incoming(media_message("video", wa.now, mime="video/mp4")))

    assert wa.messages()[0].media_status == WaMediaStatus.TYPE_NOT_ALLOWED


def test_a_meta_failure_is_recorded_and_the_bot_answers(make_wa: MakeWa) -> None:
    fake = _fake("image/jpeg")
    fake.fail_on.add("get_media")
    wa = make_wa(fake=fake)

    wa.post(incoming(media_message("image", wa.now)))

    assert wa.messages()[0].media_status == WaMediaStatus.FAILED
    assert len(wa.fake.texts()) == 1


def test_resolve_path_stays_inside(tmp_path: Path) -> None:
    assert resolve_path(tmp_path, "2026/09/a.pdf") == (tmp_path / "2026/09/a.pdf").resolve()
    assert resolve_path(tmp_path, "../fuera.pdf") is None
    assert resolve_path(tmp_path, "/etc/passwd") is None
    assert resolve_path(tmp_path, ".") is None
