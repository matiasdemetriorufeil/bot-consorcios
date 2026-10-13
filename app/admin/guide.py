"""The menu's "Guía": docs/guia-empleadas.md shown inside the panel, for every panel user.

The guide is converted here with only what it uses (headings, paragraphs, lists, bold, links,
images): every text is escaped, links go only to http(s), the panel or a section of the guide
(#anchor), and images only to the guide's own folder (docs/guia-empleadas/, served at
/admin/guide-files by setup_admin).

Its two parts ("#") and their sections ("##") carry an id made from their title (slug: no
accents, lowercase, hyphens), the same in the index on top (guide_contents) and in the links
"Ver en la guía" of each page (app.admin.help.PAGE_GUIDE).
"""

import html
import re
import unicodedata
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
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_ANCHOR = re.compile(r"^#[a-z0-9-]+$")


def slug(text: str) -> str:
    """ "Empezá el día en Pendientes" -> "empeza-el-dia-en-pendientes": the title's plain text
    (no ** nor link marks), without accents, lowercase, anything else a hyphen."""
    plain = _LINK.sub(r"\1", text).replace("**", "")
    ascii_text = unicodedata.normalize("NFKD", plain).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-") or "seccion"


class _Slugs:
    """The ids of a page's headings: a repeated title gets -2, -3..."""

    def __init__(self) -> None:
        self.seen: dict[str, int] = {}

    def __call__(self, text: str) -> str:
        base = slug(text)
        self.seen[base] = self.seen.get(base, 0) + 1
        return base if self.seen[base] == 1 else f"{base}-{self.seen[base]}"


def guide_contents(source: str) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """The index on top of the guide: [(part, its id, [(section, its id), ...]), ...], from the
    "#" and "##" titles, with the same ids markdown_to_html gives them."""
    slugs = _Slugs()
    parts: list[tuple[str, str, list[tuple[str, str]]]] = []
    for raw in source.splitlines():
        heading = _HEADING.match(raw.strip())
        if not heading:
            continue
        level, title = len(heading.group(1)), heading.group(2)
        anchor = slugs(title)
        plain = _LINK.sub(r"\1", title).replace("**", "")
        if level == 1:
            parts.append((plain, anchor, []))
        elif level == 2 and parts:
            parts[-1][2].append((plain, anchor))
    return parts


def _png_size(path: Path) -> tuple[int, int] | None:
    """A PNG's width and height, from its header (None if it is not one, or not there)."""
    try:
        with path.open("rb") as file:
            head = file.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _inline(text: str, files_url: str) -> str:
    """One line: escaped, then bold, links and images (only to allowed places)."""
    images: list[str] = []

    def image(match: re.Match[str]) -> str:
        alt, src = match.group(1), match.group(2)
        if not src.startswith(IMAGE_PREFIX) or ".." in src:
            return html.escape(alt)
        name = src[len(IMAGE_PREFIX) :]
        size = _png_size(GUIDE_FILES_DIR / name)
        # Its size, so the page keeps its place before it loads (a link to a section lands
        # on it, not where it was before the pictures above it came).
        sized = f' width="{size[0]}" height="{size[1]}"' if size else ""
        images.append(
            f'<img class="guide-image" src="{html.escape(files_url + name)}" '
            f'alt="{html.escape(alt)}"{sized} loading="lazy">'
        )
        return f"\x00{len(images) - 1}\x00"

    text = _IMAGE.sub(image, text)
    text = html.escape(text, quote=False)

    def link(match: re.Match[str]) -> str:
        label, href = match.group(1), html.unescape(match.group(2))
        if not (href.startswith(("https://", "http://", "/admin/")) or _ANCHOR.match(href)):
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
    item: list[str] = []  # the lines of the list item being read (converted whole)
    slugs = _Slugs()

    def close_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph), files_url)}</p>")
            paragraph.clear()

    def close_item() -> None:
        if item:
            out.append(f"<li>{_inline(' '.join(item), files_url)}</li>")
            item.clear()

    def close_list() -> None:
        nonlocal list_tag
        close_item()
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            close_paragraph()
            close_list()
            continue
        heading = _HEADING.match(line)
        ordered = _ORDERED.match(line)
        if heading:
            close_paragraph()
            close_list()
            level = len(heading.group(1)) + 1  # the page already has its <h2> title
            anchor = slugs(heading.group(2))  # only [a-z0-9-]: nothing to escape
            title = _inline(heading.group(2), files_url)
            out.append(f'<h{level} id="{anchor}">{title}</h{level}>')
        elif line.startswith(("- ", "* ")) or ordered:
            close_paragraph()
            tag = "ol" if ordered else "ul"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            close_item()
            item.append(ordered.group(1) if ordered else line[2:])
        elif list_tag and raw.startswith(("  ", "\t")):  # the item goes on (2 spaces or more)
            item.append(line)
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
            {
                "title": "Guía",
                "guide": markdown_to_html(source, files_url),
                "contents": guide_contents(source),
            },
        )
