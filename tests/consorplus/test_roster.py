"""'List. Todos Los Datos' (Listado2036.aspx): allowlist, client and parser. Invented data."""

from dataclasses import fields, is_dataclass

import pytest

from app.consorplus.allowlist import (
    CONTENT_PREFIX,
    ROSTER_FROM_SELECT,
    ROSTER_QUERY_BUTTON,
    ROSTER_TO_SELECT,
    check_get,
    check_input_fields,
    check_postback,
)
from app.consorplus.errors import ConsorPlusError, ForbiddenActionError, NotFoundError, ParseError
from app.consorplus.parsers import parse_roster
from tests.consorplus.conftest import BASE_URL, load

ROSTER_URL = BASE_URL + "Listado2036.aspx"
DEBT_URL = BASE_URL + "DetalleDeudaUnidad.aspx"
EXCEL_BUTTON = CONTENT_PREFIX + "Button4"
SENSITIVE = ("ALIAS.FALSO", "0000000000000000000001", "999000111", "11112222", "33334444")


def _all_strings(obj) -> list[str]:
    if is_dataclass(obj):
        return [s for f in fields(obj) for s in _all_strings(getattr(obj, f.name))]
    if isinstance(obj, list | tuple):
        return [s for item in obj for s in _all_strings(item)]
    return [obj] if isinstance(obj, str) else []


# --- Allowlist -------------------------------------------------------------------------


def test_roster_get_is_allowed_only_without_query() -> None:
    check_get(ROSTER_URL)
    check_get(BASE_URL + "listado2036.ASPX")
    with pytest.raises(ForbiddenActionError):
        check_get(ROSTER_URL + "?excel=1")


def test_roster_query_postback_is_allowed() -> None:
    data = {"__EVENTTARGET": ROSTER_QUERY_BUTTON, ROSTER_FROM_SELECT: "7", ROSTER_TO_SELECT: "7"}
    check_postback(ROSTER_URL, ROSTER_QUERY_BUTTON, data, {EXCEL_BUTTON: "Exportar Excel"})
    check_input_fields({ROSTER_FROM_SELECT: "7", ROSTER_TO_SELECT: "7"})


def test_excel_button_is_forbidden() -> None:
    with pytest.raises(ForbiddenActionError):
        check_postback(ROSTER_URL, EXCEL_BUTTON, {"__EVENTTARGET": EXCEL_BUTTON}, {})


def test_excel_button_cannot_ride_along_the_query_postback() -> None:
    data = {"__EVENTTARGET": ROSTER_QUERY_BUTTON, EXCEL_BUTTON: "Exportar Excel"}
    with pytest.raises(ForbiddenActionError, match="Botón"):
        check_postback(ROSTER_URL, ROSTER_QUERY_BUTTON, data, {EXCEL_BUTTON: "Exportar Excel"})


def test_roster_query_button_is_not_allowed_on_the_debt_page() -> None:
    # Same control name as "Listar PDF" on the debt page: the (page, control) pair matters.
    with pytest.raises(ForbiddenActionError):
        check_postback(DEBT_URL, ROSTER_QUERY_BUTTON, {"__EVENTTARGET": ROSTER_QUERY_BUTTON}, {})


def test_other_roster_fields_are_forbidden() -> None:
    with pytest.raises(ForbiddenActionError, match="Campos no permitidos"):
        check_input_fields({CONTENT_PREFIX + "ddlOtroCombo": "1"})


# --- Client ----------------------------------------------------------------------------


def test_list_roster_flow(server, make_client) -> None:
    client = make_client(server)

    # The combo value (internal id "5") differs from the visible building code ("007").
    rows = client.list_roster("5")

    assert [r.unit_value for r in rows] == ["9001", "9002"]
    assert server.pages()[-2:] == [("GET", "listado2036.aspx"), ("POST", "listado2036.aspx")]
    post = server.calls[-1].data
    assert post["__EVENTTARGET"] == ROSTER_QUERY_BUTTON
    assert post[ROSTER_FROM_SELECT] == post[ROSTER_TO_SELECT] == "5"
    assert post["__ASYNCPOST"] == "true"
    assert EXCEL_BUTTON not in post
    assert not any(key.startswith(CONTENT_PREFIX + "btn") for key in post)


def test_list_roster_unknown_building(server, make_client) -> None:
    client = make_client(server)

    with pytest.raises(NotFoundError):
        client.list_roster("7")
    assert ("POST", "listado2036.aspx") not in server.pages()


def test_list_roster_rejects_rows_of_another_building(server, make_client) -> None:
    client = make_client(server)

    with pytest.raises(ConsorPlusError, match="otro edificio"):
        client.list_roster("1")


def test_excel_click_after_query_is_blocked_without_request(server, make_client) -> None:
    client = make_client(server)
    client.list_roster("5")
    calls_before = len(server.calls)

    with pytest.raises(ForbiddenActionError):
        client._postback(EXCEL_BUTTON)
    with pytest.raises(ForbiddenActionError):
        client._async_postback(EXCEL_BUTTON, {}, button=True)

    assert len(server.calls) == calls_before


# --- Parser ----------------------------------------------------------------------------


def test_parse_roster_by_header_name() -> None:
    rows = parse_roster(load("roster_panel.html"))

    assert len(rows) == 2  # the pager row is skipped
    first = rows[0]
    assert (first.unit_value, first.building_code, first.unit_label, first.ph) == (
        "9001",
        "007",
        "01° A",
        "1",
    )
    assert first.building_name == "007 CONSORCIO PLAZA FICTICIA"
    assert first.unit_type == "DPTO"
    assert rows[1].unit_type == "COCH"
    assert first.owner.name == "PEREZ, JUAN CARLOS"
    assert first.owner.phone == "0351-15-5550101"
    assert first.owner.mobile == ""
    assert first.owner.email == "Juan.Perez@example.com"
    assert first.owner.document == "20.100.200"
    assert first.second_owner.name == "GOMEZ MARIA"
    assert first.second_owner.phone == "4550103"
    assert first.tenant.name == "LOPEZ ANA"
    assert first.tenant.document == ""
    assert rows[1].second_owner is None
    assert rows[1].tenant is None


def test_parse_roster_discards_sensitive_columns() -> None:
    rows = parse_roster(load("roster_panel.html"))

    values = " ".join(_all_strings(rows))
    for secret in SENSITIVE:
        assert secret not in values
        assert secret not in repr(rows)


def test_parse_roster_reads_the_payment_code_raw_and_keeps_it_out_of_repr() -> None:
    rows = parse_roster(load("roster_panel.html"))

    assert [r.payment_code for r in rows] == ["0000000000000009001", "12345-6"]
    assert "0000000000000009001" not in repr(rows)


def test_contact_repr_is_redacted() -> None:
    rows = parse_roster(load("roster_panel.html"))

    assert "PEREZ" not in repr(rows[0].owner)
    assert "PEREZ" not in repr(rows[0])


def test_parse_roster_missing_header_is_an_error() -> None:
    html = load("roster_panel.html").replace(">Celular Propietario<", ">Celu<")

    with pytest.raises(ParseError, match="celular propietario"):
        parse_roster(html)


def test_parse_roster_without_grid_is_an_error() -> None:
    with pytest.raises(ParseError, match="grilla"):
        parse_roster("<div><p>Sin datos</p></div>")


def test_parse_roster_row_with_wrong_cell_count_is_an_error() -> None:
    html = load("roster_panel.html").replace("<td>9002</td>", "<td>9002</td><td>extra</td>")

    with pytest.raises(ParseError, match="celdas"):
        parse_roster(html)
