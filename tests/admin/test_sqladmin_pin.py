"""The panel adapts SQLAdmin 0.32.0 (pinned in pyproject.toml): it copies or extends some of its
templates (app/admin/templates/sqladmin/), translates the texts its templates pass through _()
(app.admin.i18n) and rewrites two texts of its JavaScript (app/admin/static/panel.js). When
SQLAdmin is updated, these tests say what to review before trusting the panel."""

import hashlib
import importlib.metadata
import re
from pathlib import Path

import pytest
import sqladmin

from app.admin.i18n import ES

PINNED = "0.32.0"
PACKAGE = Path(sqladmin.__file__).parent
TEMPLATES = PACKAGE / "templates"
REVIEW = (
    "SQLAdmin changed {name}: review app/admin/templates/sqladmin/ (copies and block "
    "overrides), app/admin/static/panel.js and app.admin.i18n, then update this hash."
)
# sha256 of the originals the panel copies (list, details) or extends by blocks, and of the
# JavaScript whose texts panel.js rewrites. Line endings normalized.
ORIGINALS = {
    "sqladmin/_macros.html": "31efad250ae651e1bf837b626c91ffc5d25c7c48ef421eb033c3b0d5f8d1529b",
    "sqladmin/base.html": "160cbde24a2111f847816128497ef3eca2e147c3c24486b2baca948ed0a96a8e",
    "sqladmin/layout.html": "bffc98569593a14784ccbeee3360a693f7c7b50cb7f00f87e472e353df499911",
    "sqladmin/list.html": "173d8332d56c3623df6da1f5e009987245a2f56153a1564fa033d34b27e4e4c9",
    "sqladmin/details.html": "be8a642848e62144789de8b514c133014d720cbec2cd3d5235672dae8edcc4c2",
    "sqladmin/edit.html": "ab92794731459e9dd79723b9456ae76e311616e303c86782a85c8d2f1f1909b9",
    "sqladmin/create.html": "530935731c2db15abd2f6951f08b7872474f6e57852b1fc347cc1802a4e6a7ba",
    "sqladmin/error.html": "81bfb5ef648cb902a772fd1013c0d07505de33e7f0ee85c2d0b3cb3b789ad083",
}
MAIN_JS = "46159d7ac3fe0c40c8a2482b678b9a6d156708cb0517dbd26fb942892555f08e"
_MSGID = re.compile(r"""_\(\s*(["'])(.+?)\1""", re.S)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_the_installed_version_is_the_pinned_one() -> None:
    assert importlib.metadata.version("sqladmin") == PINNED
    pyproject = (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"sqladmin=={PINNED}"' in pyproject


@pytest.mark.parametrize(("name", "digest"), ORIGINALS.items())
def test_the_originals_we_adapt_did_not_change(name: str, digest: str) -> None:
    assert _sha(TEMPLATES / name) == digest, REVIEW.format(name=name)


def test_its_javascript_did_not_change() -> None:
    assert _sha(PACKAGE / "statics" / "js" / "main.js") == MAIN_JS, REVIEW.format(name="main.js")


def test_every_text_of_its_templates_is_translated() -> None:
    texts = {
        match.group(2)
        for path in TEMPLATES.rglob("*.html")
        for match in _MSGID.finditer(path.read_text(encoding="utf-8"))
    }
    assert texts, "no _() texts found: did SQLAdmin change how it translates?"
    missing = sorted(texts - ES.keys())
    assert missing == [], f"texts of SQLAdmin without Spanish in app.admin.i18n.ES: {missing}"


def test_the_translations_keep_their_placeholders() -> None:
    for english, spanish in ES.items():
        assert set(re.findall(r"%\(\w+\)s", english)) == set(re.findall(r"%\(\w+\)s", spanish))
