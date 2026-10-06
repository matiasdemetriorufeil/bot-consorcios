"""Incoming attachments: downloaded right away (Meta's links expire in minutes) to
WHATSAPP_MEDIA_DIR, with a size limit and a closed list of types. The stored name is ours
(AAAA/MM/<uuid>.<ext>), never the one the person sent. Served only to logged-in panel users
(app.admin.wa_media).
"""

import logging
import uuid
from datetime import datetime
from pathlib import Path

from app.db.models import WaMediaStatus, WaMessage
from app.whatsapp.client import MediaTooLargeError, WhatsAppClient, WhatsAppError

logger = logging.getLogger(__name__)

# MIME type -> extension of the stored file. Anything else is not downloaded.
ALLOWED_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/plain": ".txt",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
}
# WhatsApp types whose files are never downloaded (only their metadata is kept).
NOT_DOWNLOADED = frozenset({"video", "sticker"})
# Shown inline in the panel; anything else is served as a download.
INLINE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def base_mime(mime: str | None) -> str:
    """ "audio/ogg; codecs=opus" -> "audio/ogg"."""
    return (mime or "").split(";")[0].strip().lower()


def resolve_path(media_dir: str | Path, relative: str) -> Path | None:
    """The stored file, or None if the path would leave the media directory."""
    root = Path(media_dir).resolve()
    path = (root / relative).resolve()
    return path if path.is_relative_to(root) and path != root else None


class MediaStore:
    def __init__(self, client: WhatsAppClient, media_dir: str | Path, max_bytes: int) -> None:
        self.client = client
        self.media_dir = Path(media_dir)
        self.max_bytes = max_bytes

    def fetch(self, message: WaMessage, now: datetime) -> None:
        """Downloads the message's attachment and records how it went on the message (the
        caller commits). Never raises."""
        if not message.media_id or message.media_status is not None:
            return
        if message.message_type in NOT_DOWNLOADED:
            message.media_status = WaMediaStatus.TYPE_NOT_ALLOWED
            return
        try:
            info = self.client.get_media(message.media_id)
            mime = base_mime(info.get("mime_type") or message.media_mime)
            message.media_mime = mime or message.media_mime
            size = info.get("file_size")
            if isinstance(size, int):
                message.media_size = size
            extension = ALLOWED_TYPES.get(mime)
            if extension is None:
                message.media_status = WaMediaStatus.TYPE_NOT_ALLOWED
                logger.info("Message %s: attachment type %s not stored", message.id, mime)
                return
            if isinstance(size, int) and size > self.max_bytes:
                message.media_status = WaMediaStatus.TOO_LARGE
                logger.info("Message %s: attachment of %d bytes not stored", message.id, size)
                return
            url = info.get("url")
            if not url:
                raise WhatsAppError("GET media: sin url")
            content = self.client.download(str(url), self.max_bytes)
        except MediaTooLargeError:
            message.media_status = WaMediaStatus.TOO_LARGE
            logger.info("Message %s: attachment over the limit, not stored", message.id)
            return
        except WhatsAppError as exc:
            message.media_status = WaMediaStatus.FAILED
            logger.warning("Message %s: attachment not downloaded: %s", message.id, exc)
            return
        relative = f"{now:%Y}/{now:%m}/{uuid.uuid4().hex}{extension}"
        path = self.media_dir / relative
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        except OSError as exc:
            message.media_status = WaMediaStatus.FAILED
            logger.error("Message %s: attachment not saved: %s", message.id, exc)
            return
        message.media_path = relative
        message.media_size = len(content)
        message.media_status = WaMediaStatus.STORED
        logger.info("Message %s: attachment stored (%s, %d bytes)", message.id, mime, len(content))
