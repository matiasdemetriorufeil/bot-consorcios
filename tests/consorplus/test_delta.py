import pytest

from app.consorplus.delta import DeltaNode, DeltaParseError, apply_delta, parse_delta
from app.consorplus.parsers import parse_form, parse_units
from tests.consorplus.conftest import load, make_delta, panel_delta


def test_parse_delta_nodes() -> None:
    text = "5|updatePanel|P1|hello|0|hiddenField|__EVENTTARGET||4|hiddenField|__VIEWSTATE|abcd|"

    assert parse_delta(text) == [
        DeltaNode("updatePanel", "P1", "hello"),
        DeltaNode("hiddenField", "__EVENTTARGET", ""),
        DeltaNode("hiddenField", "__VIEWSTATE", "abcd"),
    ]


def test_content_may_contain_pipes_and_newlines() -> None:
    content = "<option>001 PB | PEREZ</option>\n|x|"
    nodes = parse_delta(make_delta(("updatePanel", "P1", content), ("pageTitle", "", "T")))

    assert nodes[0].content == content
    assert nodes[1] == DeltaNode("pageTitle", "", "T")


def test_length_counts_utf16_code_units() -> None:
    content = "Edificio 🏢 Ñandú"  # the emoji is 2 UTF-16 code units
    text = make_delta(("updatePanel", "P1", content), ("hiddenField", "__VIEWSTATE", "v"))

    assert text.startswith("17|")
    assert [n.content for n in parse_delta(text)] == [content, "v"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "<html><body>Error</body></html>",
        "10|updatePanel|P1|short|",
        "5|updatePanel|P1|hello",
        "x|updatePanel|P1|hello|",
        "5|updatePanel",
    ],
)
def test_malformed_delta_raises(text: str) -> None:
    with pytest.raises(DeltaParseError):
        parse_delta(text)


def test_apply_delta_updates_panel_and_hidden_fields() -> None:
    nodes = parse_delta(panel_delta("panel_units.html", viewstate="VS-NEW"))
    nodes.append(DeltaNode("hiddenField", "__NEWFIELD", "n"))

    html = apply_delta(load("debt_page.html"), nodes)

    assert [u.value for u in parse_units(html)] == ["9001", "9002", "9003"]
    fields = parse_form(html).fields
    assert fields["__VIEWSTATE"] == "VS-NEW"
    assert fields["__NEWFIELD"] == "n"
    assert fields["ctl00$ContentPlaceHolder1$ddlEdificio"] == "1"


def test_apply_delta_to_unknown_panel_raises() -> None:
    with pytest.raises(DeltaParseError, match="panel"):
        apply_delta(load("debt_page.html"), [DeltaNode("updatePanel", "Nope", "x")])
