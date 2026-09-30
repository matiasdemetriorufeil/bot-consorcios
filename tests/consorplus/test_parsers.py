from decimal import Decimal

import pytest

from app.consorplus.allowlist import BUILDING_SELECT, UNIT_SELECT
from app.consorplus.errors import ParseError
from app.consorplus.models import DebtLine, UnitDebt
from app.consorplus.parsers import (
    parse_amount,
    parse_buildings,
    parse_debt_lines,
    parse_form,
    parse_units,
    selected_value,
)
from tests.consorplus.conftest import load


def test_parse_buildings_skips_placeholder() -> None:
    buildings = parse_buildings(load("debt_page.html"))

    assert [(b.code, b.name) for b in buildings] == [
        ("1", "001 EDIFICIO LOS PINOS"),
        ("2", "002 TORRE DEL ARROYO"),
        ("7", "007 CONSORCIO PLAZA FICTICIA"),
    ]


def test_parse_units_splits_owner_from_label() -> None:
    units = parse_units(load("panel_units.html"))

    assert [(u.value, u.label, u.owner_name) for u in units] == [
        ("9001", "001 PB-A", "PEREZ JUANA"),
        ("9002", "002 PB-B", "GOMEZ MARTIN"),
        ("9003", "003 1º A", None),
    ]


def test_unit_repr_hides_owner_name() -> None:
    unit = parse_units(load("panel_units.html"))[0]

    assert "PEREZ" not in repr(unit)


def test_parse_units_of_empty_combo() -> None:
    assert parse_units(load("debt_page.html")) == []


def test_missing_combo_raises() -> None:
    with pytest.raises(ParseError):
        parse_buildings(load("home.html"))


def test_selected_value() -> None:
    html = load("panel_unit_selected.html")

    assert selected_value(html, BUILDING_SELECT) == "1"
    assert selected_value(html, UNIT_SELECT) == "9001"
    assert selected_value(load("debt_page.html"), UNIT_SELECT) is None


def test_parse_debt_lines() -> None:
    lines = parse_debt_lines(load("panel_with_debt.html"))

    assert lines == [
        DebtLine("001 EXP.COMUNES", "05/2026", Decimal(98500), Decimal(48500), Decimal(48500)),
        DebtLine("001 EXP.COMUNES", "06/2026", Decimal(98500), Decimal(98500), Decimal(147000)),
        DebtLine(
            "060 PUNITORIOS MENSUALES",
            "06/2026",
            Decimal("4925.50"),
            Decimal("4925.50"),
            Decimal("151925.50"),
        ),
    ]


def test_no_table_means_no_debt() -> None:
    assert parse_debt_lines(load("panel_no_debt.html")) == []


def test_unexpected_debt_table_raises_instead_of_reporting_no_debt() -> None:
    html = (
        "<table><tr><th>Concepto</th><th>Saldo Adeudado</th></tr>"
        "<tr><td>X</td><td>1</td></tr></table>"
    )

    with pytest.raises(ParseError, match="Faltan columnas"):
        parse_debt_lines(html)


def test_unit_debt_total_and_status() -> None:
    lines = tuple(parse_debt_lines(load("panel_with_debt.html")))

    debt = UnitDebt("1", "9001", lines)
    assert debt.total == Decimal("151925.50")
    assert debt.total == lines[-1].accumulated
    assert not debt.is_up_to_date
    assert UnitDebt("1", "9002", ()).is_up_to_date


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("123456", "123456"),
        (" 123456 ", "123456"),
        ("$ 123.456", "123456"),
        ("123.456,50", "123456.50"),
        ("1.234.567", "1234567"),
        ("1,234,567.89", "1234567.89"),
        ("123456,5", "123456.5"),
        ("1650.60", "1650.60"),
        ("0", "0"),
        ("-1.500", "-1500"),
        ("(250,00)", "-250.00"),
        ("250-", "-250"),
        ("\xa016.506", "16506"),
    ],
)
def test_parse_amount(text: str, expected: str) -> None:
    assert parse_amount(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "abc", "1.2.3", "12.34,5.6", "1..5", "12,34.567,8", "1 2x"])
def test_parse_amount_rejects_garbage(text: str) -> None:
    with pytest.raises(ParseError):
        parse_amount(text)


def test_parse_form_collects_what_a_browser_would_post() -> None:
    html = load("debt_page.html").replace(
        "</form>", load("panel_with_debt.html") + '<input name="off" type="checkbox"/></form>'
    )

    form = parse_form(html)

    assert form.action == "./DetalleDeudaUnidad.aspx"
    assert form.fields["__VIEWSTATE"] == "VS-0"
    assert form.fields["ctl00$ContentPlaceHolder1$gridView$ctl02$chkGridIncluir"] == "on"
    assert "off" not in form.fields
    # Buttons are never posted unless explicitly clicked.
    assert "ctl00$ContentPlaceHolder1$btnExcel" in form.buttons
    assert not set(form.buttons) & set(form.fields)
