"""The menu's "Guía": docs/guia-empleadas.md shown inside the panel, for every panel user.

The guide is converted here with only what it uses (headings, paragraphs, lists, bold, links,
images): every text is escaped, links go only to http(s) or the panel, and images only to the
guide's own folder (docs/guia-empleadas/, served at /admin/guide-files by setup_admin).
"""

import html
import re
from pathlib import Path
from typing import ClassVar

from markupsafe import Markup
from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import Response

DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"
GUIDE_FILE = DOCS_DIR / "guia-empleadas.md"
GUIDE_FILES_DIR = DOCS_DIR / "guia-empleadas"
IMAGE_PREFIX = "guia-empleadas/"

_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ORDERED = re.compile(r"^\d+\.\s+(.*)$")


def _inline(text: str, files_url: str) -> str:
    """One line: escaped, then bold, links and images (only to allowed places)."""
    images: list[str] = []

    def image(match: re.Match[str]) -> str:
        alt, src = match.group(1), match.group(2)
        if not src.startswith(IMAGE_PREFIX) or ".." in src:
            return html.escape(alt)
        name = src[len(IMAGE_PREFIX) :]
        images.append(
            f'<img class="guide-image" src="{html.escape(files_url + name)}" '
            f'alt="{html.escape(alt)}" loading="lazy">'
        )
        return f"\x00{len(images) - 1}\x00"

    text = _IMAGE.sub(image, text)
    text = html.escape(text, quote=False)

    def link(match: re.Match[str]) -> str:
        label, href = match.group(1), html.unescape(match.group(2))
        if not (href.startswith(("https://", "http://", "/admin/"))):
            return label
        return f'<a href="{html.escape(href)}">{label}</a>'

    text = _LINK.sub(link, text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    return re.sub("\x00(\\d+)\x00", lambda m: images[int(m.group(1))], text)


def markdown_to_html(source: str, files_url: str) -> Markup:
    """The guide's Markdown (headings #-###, paragraphs, - and 1. lists, **bold**, links,
    images) as HTML."""
    out: list[str] = []
    paragraph: list[str] = []
    list_tag: str | None = None

    def close_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph), files_url)}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            close_paragraph()
            close_list()
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        ordered = _ORDERED.match(line)
        if heading:
            close_paragraph()
            close_list()
            level = len(heading.group(1)) + 1  # the page already has its <h2> title
            out.append(f"<h{level}>{_inline(heading.group(2), files_url)}</h{level}>")
        elif line.startswith(("- ", "* ")) or ordered:
            close_paragraph()
            tag = "ol" if ordered else "ul"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            item = ordered.group(1) if ordered else line[2:]
            out.append(f"<li>{_inline(item, files_url)}</li>")
        elif list_tag and raw.startswith(("   ", "\t")):
            out[-1] = out[-1][: -len("</li>")] + " " + _inline(line, files_url) + "</li>"
        else:
            close_list()
            paragraph.append(line)
    close_paragraph()
    close_list()
    return Markup("\n".join(out))  # noqa: S704 - built from escaped text only


class GuideView(BaseView):
    name = "Guía"
    icon = "fa-solid fa-book-open"
    guide_file: ClassVar[Path] = GUIDE_FILE

    @expose("/guide", methods=["GET"], identity="guide")
    async def guide_page(self, request: Request) -> Response:
        source = self.guide_file.read_text(encoding="utf-8")
        files_url = str(request.url_for("admin:guide-files", path="")).rstrip("/") + "/"
        # The guide's own first heading is the page's title.
        source = re.sub(r"^#\s+.*\n", "", source, count=1)
        return await self.templates.TemplateResponse(
            request,
            "guide.html",
            {"title": "Guía", "guide": markdown_to_html(source, files_url)},
        )
