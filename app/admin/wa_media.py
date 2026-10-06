"""Attachments of WhatsApp messages (app.whatsapp.media), only for logged-in panel users:
GET /admin/wa/media/{message_id}. Not in the menu (the inbox will link to it).

Served with the stored type and nosniff; anything but an image goes as a download, so a file
sent by anyone never runs as a page of the panel.
"""

from pathlib import Path
from typing import Any, ClassVar

from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response

from app.config import Settings
from app.db.models import WaMediaStatus, WaMessage
from app.whatsapp.media import INLINE_TYPES, base_mime, resolve_path


class WaMediaView(BaseView):
    name = "Adjuntos de WhatsApp"
    session_maker: ClassVar[Any] = None
    media_dir: ClassVar[str] = Settings.model_fields["whatsapp_media_dir"].default

    def is_visible(self, request: Request) -> bool:
        return False

    @expose("/wa/media/{message_id:int}", methods=["GET"], identity="wa-media")
    async def media(self, request: Request) -> Response:
        message_id = int(request.path_params["message_id"])
        not_found = PlainTextResponse("No encontrado", status_code=404)
        with self.session_maker() as session:
            message = session.get(WaMessage, message_id)
            if message is None or message.media_status != WaMediaStatus.STORED:
                return not_found
            relative = message.media_path or ""
            mime = base_mime(message.media_mime)
            filename = message.media_filename
        path = resolve_path(self.media_dir, relative) if relative else None
        if path is None or not path.is_file():
            return not_found
        inline = mime in INLINE_TYPES
        return FileResponse(
            path,
            media_type=mime or "application/octet-stream",
            filename=_download_name(filename, message_id, path),
            content_disposition_type="inline" if inline else "attachment",
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
        )


def _download_name(filename: str | None, message_id: int, path: Path) -> str:
    """The name the person sent (only for the download dialog), else one of ours."""
    if filename:
        clean = "".join(c for c in filename if c.isprintable() and c not in '\\/"')
        if clean.strip():
            return clean.strip()[:150]
    return f"adjunto-{message_id}{path.suffix}"
