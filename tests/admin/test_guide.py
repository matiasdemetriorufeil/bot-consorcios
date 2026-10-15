"""The employees' guide (docs/guia-empleadas.md) inside the panel, and its minimal Markdown
converter. Its pictures are in the repository: they must show invented data only."""

import html
import re

import pytest

from app.admin import help
from app.admin.guide import (
    GUIDE_FILE,
    GUIDE_FILES_DIR,
    IMAGE_PREFIX,
    guide_contents,
    markdown_to_html,
    slug,
)
from tests.admin.conftest import Panel

# The guide's two parts and their sections, in this order (its index on top).
CONTENTS = [
    ("Todos los días", [
        "Empezá el día en Pendientes", "Cómo es un día", "Conversaciones",
        "Responder cuando pasó más de un día", "Reclamos", "Cargar el teléfono de un vecino",
        "Teléfonos", "Verificaciones", "Reservas del SUM",
    ]),
    ("Si pasa algo raro", [
        "Cuando algo no anda", "El número ya es de otra persona", "Quité a alguien por error",
        "Reclamos: el proveedor y los avisos",
    ]),
]  # fmt: skip
TASKS = [section for _, sections in CONTENTS for section in sections]


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
    assert '<h3 id="titulo">Título</h3>' in out
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


# --- 5e.4: the index, the anchors and «Ver en la guía» ------------------------------------------


def _guide(panel: Panel) -> str:
    return panel.client.get("/admin/guide").text


def test_the_index_has_both_parts_and_their_sections(operator: Panel) -> None:
    page = _guide(operator)
    index = page.split('id="guide-index"', 1)[1].split("</nav>", 1)[0]
    parts = re.findall(r'<a class="guide-index-part" href="#[a-z0-9-]+">([^<]+)</a>', index)
    assert parts == [part for part, _ in CONTENTS]
    sections = re.findall(r'<li><a href="#[a-z0-9-]+">([^<]+)</a></li>', index)
    assert [html.unescape(s) for s in sections] == TASKS
    # Every link of the index goes to a title of the guide.
    ids = set(re.findall(r'<h[23] id="([a-z0-9-]+)">', page))
    links = re.findall(r'href="#([^"]+)"', index)
    assert links and set(links) <= ids


def test_each_ver_en_la_guia_goes_to_a_section(operator: Panel) -> None:
    ids = set(re.findall(r'<h[23] id="([a-z0-9-]+)">', _guide(operator)))
    assert set(help.PAGE_GUIDE.values()) <= ids
    # The link in the guide itself to its part about the providers, too.
    assert 'href="#reclamos-el-proveedor-y-los-avisos"' in _guide(operator)


@pytest.mark.parametrize(
    ("url", "anchor"),
    [
        ("/admin/pending", "empeza-el-dia-en-pendientes"),
        ("/admin/conversations", "conversaciones"),
        ("/admin/claims", "reclamos"),
        ("/admin/claims/new", "reclamos"),
        ("/admin/phones", "telefonos"),
        ("/admin/neighbors", "cargar-el-telefono-de-un-vecino"),
        ("/admin/verifications", "verificaciones"),
        ("/admin/amenities", "reservas-del-sum"),
    ],
)
def test_the_operators_pages_link_to_their_section(operator: Panel, url: str, anchor: str) -> None:
    page = operator.client.get(url).text
    expected = (
        f'<a class="page-help-guide" href="https://testserver/admin/guide#{anchor}">'
        "Ver en la guía</a>"
    )
    assert expected in html.unescape(page)


@pytest.mark.parametrize("url", ["/admin/users", "/admin/metrics", "/admin/building/list"])
def test_the_admins_pages_have_no_link(logged_in: Panel, url: str) -> None:
    assert "page-help-guide" not in logged_in.client.get(url).text


def test_the_guide_has_no_link_to_itself(operator: Panel) -> None:
    assert "page-help-guide" not in _guide(operator)


@pytest.mark.parametrize(
    ("title", "anchor"),
    [
        ("Empezá el día en Pendientes", "empeza-el-dia-en-pendientes"),
        ("Cómo es un día", "como-es-un-dia"),
        ("Reclamos: el proveedor y los avisos", "reclamos-el-proveedor-y-los-avisos"),
        ("Año del ñandú, ¡ya!", "ano-del-nandu-ya"),
        ("**Teléfonos** [y más](#x)", "telefonos-y-mas"),
        ("¿?", "seccion"),
    ],
)
def test_the_anchors_are_plain_and_stable(title: str, anchor: str) -> None:
    assert slug(title) == anchor


def test_a_repeated_title_gets_its_own_anchor() -> None:
    out = str(markdown_to_html("# Uno\n## Dos\n## Dos", "/f/"))
    assert '<h2 id="uno">' in out and '<h3 id="dos">' in out and '<h3 id="dos-2">' in out
    contents = guide_contents("# Uno\n## Dos\n## Dos\n# Tres")
    assert contents == [("Uno", "uno", [("Dos", "dos"), ("Dos", "dos-2")]), ("Tres", "tres", [])]


def test_titles_and_anchor_links_are_still_escaped() -> None:
    out = str(markdown_to_html('## <script>alert(1)</script> "x"', "/f/"))
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert '<h3 id="script-alert-1-script-x">' in out
    links = str(markdown_to_html("[a](#bien) [b](#<script>) [c](#Mal) [d](javascript:x)", "/f/"))
    assert '<a href="#bien">a</a>' in links
    assert links.count("<a ") == 1 and "javascript" not in links and "<script>" not in links


def test_the_pictures_keep_their_place_before_they_load(operator: Panel) -> None:
    """With their size: a link to a section lands on it, not where it was before the pictures
    above it loaded."""
    tags = re.findall(r'<img class="guide-image"[^>]*>', _guide(operator))
    assert tags and all(re.search(r'width="\d+" height="\d+"', tag) for tag in tags)


def test_a_list_item_goes_on_in_its_next_lines() -> None:
    out = str(
        markdown_to_html("- uno que sigue\n  en otra línea\n- dos\n\n1. tres\n   y más", "/f/")
    )
    assert "<li>uno que sigue en otra línea</li>" in out and "<li>dos</li>" in out
    assert "<li>tres y más</li>" in out and "<p>" not in out


def test_bold_across_the_lines_of_an_item() -> None:
    out = str(markdown_to_html("- estado: **Lo tiene el\n  estudio** o **Cancelado**.", "/f/"))
    assert (
        "<li>estado: <strong>Lo tiene el estudio</strong> o <strong>Cancelado</strong>.</li>" in out
    )
