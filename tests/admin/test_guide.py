"""The employees' guide (docs/guia-empleadas.md) inside the panel, and its minimal Markdown
converter. Its pictures are in the repository: they must show invented data only."""

import re

from app.admin.guide import GUIDE_FILE, GUIDE_FILES_DIR, IMAGE_PREFIX, markdown_to_html
from tests.admin.conftest import Panel

TASKS = [
    "Atender una conversación que necesita a alguien",
    "Responder cuando pasó más de un día",
    "Aprobar un teléfono",
    "Aprobar o rechazar una verificación",
    "Reservar y cancelar el SUM",
    "Si algo sale raro",
]


def _images() -> list[str]:
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", GUIDE_FILE.read_text(encoding="utf-8"))


def test_the_guide_in_the_panel(operator: Panel) -> None:
    response = operator.client.get("/admin/guide")
    assert response.status_code == 200
    for task in TASKS:
        assert task in response.text, task
    assert '<img class="guide-image" src="https://testserver/admin/guide-files/' in response.text


def test_every_picture_exists_and_is_served(operator: Panel) -> None:
    images = _images()
    assert len(images) >= 6
    for src in images:
        assert src.startswith(IMAGE_PREFIX), src
        name = src[len(IMAGE_PREFIX) :]
        assert (GUIDE_FILES_DIR / name).is_file(), name
        assert operator.client.get(f"/admin/guide-files/{name}").status_code == 200


def test_the_guide_has_no_real_data() -> None:
    text = GUIDE_FILE.read_text(encoding="utf-8")
    # No phone numbers nor long digit runs (codes, documents).
    assert not re.search(r"\+?\d[\d\s-]{6,}\d", text)
    assert "@" not in text  # no emails


def test_the_guide_needs_login(panel: Panel) -> None:
    response = panel.client.get("/admin/guide", follow_redirects=False)
    assert response.status_code == 302 and "/admin/login" in response.headers["location"]


# --- The converter ----------------------------------------------------------------------------


def test_headings_paragraphs_lists_bold_and_links() -> None:
    out = str(
        markdown_to_html(
            "## Título\n\nUn **párrafo**\nque sigue.\n\n1. uno\n2. dos\n   y más\n\n- a\n- b\n\n"
            "[Panel](/admin/phones)",
            "/files/",
        )
    )
    assert "<h3>Título</h3>" in out
    assert "<p>Un <strong>párrafo</strong> que sigue.</p>" in out
    assert "<ol>\n<li>uno</li>\n<li>dos y más</li>\n</ol>" in out
    assert "<ul>\n<li>a</li>\n<li>b</li>\n</ul>" in out
    assert '<a href="/admin/phones">Panel</a>' in out


def test_images_only_from_the_guide_folder() -> None:
    out = str(markdown_to_html("![bien](guia-empleadas/x.png) ![mal](../../.env)", "/f/"))
    assert '<img class="guide-image" src="/f/x.png" alt="bien" loading="lazy">' in out
    assert ".env" not in out and "mal" in out


def test_everything_is_escaped() -> None:
    out = str(
        markdown_to_html(
            '<script>alert(1)</script> [x](javascript:alert(1)) ![a"b](guia-empleadas/y.png)',
            "/f/",
        )
    )
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "javascript:" not in out
    assert 'alt="a&quot;b"' in out
