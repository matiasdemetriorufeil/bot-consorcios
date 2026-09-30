"""Pure functions that turn ConsorPlus HTML into data. No network access here."""

import re
from dataclasses import dataclass
from decimal import Decimal

from bs4 import BeautifulSoup, Tag

from app.consorplus.allowlist import BUILDING_SELECT, UNIT_SELECT
from app.consorplus.errors import ParseError
from app.consorplus.models import Building, DebtLine, Unit

BUTTON_INPUT_TYPES = frozenset({"submit", "image", "button", "reset"})


@dataclass(frozen=True)
class SelectOption:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True)
class FormState:
    """What a browser would post for the page's form, minus the buttons."""

    action: str
    fields: dict[str, str]
    # name -> value of every submit/image button in the form. They are NOT in `fields`.
    buttons: dict[str, str]


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(tag: Tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split())


def parse_form(html: str) -> FormState:
    form = _soup(html).find("form")
    if form is None:
        raise ParseError("La página no tiene <form>")

    fields: dict[str, str] = {}
    buttons: dict[str, str] = {}
    for element in form.find_all("input"):
        name = element.get("name")
        if not name:
            continue
        input_type = (element.get("type") or "text").lower()
        if input_type in BUTTON_INPUT_TYPES:
            buttons[name] = element.get("value", "")
            continue
        if element.has_attr("disabled"):
            continue
        if input_type in ("checkbox", "radio"):
            if element.has_attr("checked"):
                fields[name] = element.get("value", "on")
            continue
        fields[name] = element.get("value", "")

    for element in form.find_all("button"):
        if element.get("name") and (element.get("type") or "submit").lower() == "submit":
            buttons[element["name"]] = element.get("value", "")

    for element in form.find_all("select"):
        name = element.get("name")
        if not name or element.has_attr("disabled"):
            continue
        option = element.find("option", selected=True) or element.find("option")
        if option is not None:
            fields[name] = option.get("value", _text(option))

    for element in form.find_all("textarea"):
        if element.get("name") and not element.has_attr("disabled"):
            fields[element["name"]] = element.get_text()

    return FormState(action=form.get("action", ""), fields=fields, buttons=buttons)


def parse_select_options(html: str, name: str) -> list[SelectOption]:
    """Options of the <select name=...>, skipping placeholders with an empty value."""
    select = _soup(html).find("select", attrs={"name": name})
    if select is None:
        raise ParseError(f"No se encontró el combo {name!r}")
    return [
        SelectOption(
            value=option["value"], label=_text(option), selected=option.has_attr("selected")
        )
        for option in select.find_all("option")
        if option.get("value", "").strip()
    ]


def selected_value(html: str, name: str) -> str | None:
    selected = [option for option in parse_select_options(html, name) if option.selected]
    return selected[0].value if selected else None


def parse_buildings(html: str) -> list[Building]:
    return [
        Building(code=o.value, name=o.label) for o in parse_select_options(html, BUILDING_SELECT)
    ]


def parse_units(html: str) -> list[Unit]:
    units = []
    for option in parse_select_options(html, UNIT_SELECT):
        label, _, owner = option.label.partition("|")
        units.append(
            Unit(value=option.value, label=label.strip(), owner_name=owner.strip() or None)
        )
    return units


# --- Amounts ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"\d+(?:[.,]\d+)*")
_GROUPED_RE = {sep: re.compile(rf"\d{{1,3}}(?:{re.escape(sep)}\d{{3}})+") for sep in ".,"}


def _drop_thousands(integer: str, sep: str, raw: str) -> str:
    if sep not in integer:
        return integer
    if not _GROUPED_RE[sep].fullmatch(integer):
        raise ParseError(f"Separador de miles mal ubicado en el importe {raw!r}")
    return integer.replace(sep, "")


def parse_amount(text: str) -> Decimal:
    """Parse an amount such as '123456', '123.456', '123.456,50', '1,234.5' or '-1.500'.

    With a single kind of separator, exactly three trailing digits mean thousands
    ('123.456' -> 123456, as ConsorPlus uses the Argentine format); otherwise it is the
    decimal separator ('1650.6' -> 1650.6).
    """
    raw = text
    cleaned = re.sub(r"[\s$ ]", "", text)
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative, cleaned = True, cleaned[1:-1]
    if cleaned.startswith("-"):
        negative, cleaned = not negative, cleaned[1:]
    elif cleaned.endswith("-"):
        negative, cleaned = not negative, cleaned[:-1]
    if not _AMOUNT_RE.fullmatch(cleaned):
        raise ParseError(f"Importe inválido: {raw!r}")

    last_dot, last_comma = cleaned.rfind("."), cleaned.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        decimal_sep = "." if last_dot > last_comma else ","
        thousands_sep = "," if decimal_sep == "." else "."
        integer, fraction = cleaned.rsplit(decimal_sep, 1)
        integer = _drop_thousands(integer, thousands_sep, raw)
    elif last_dot >= 0 or last_comma >= 0:
        sep = "." if last_dot >= 0 else ","
        integer, fraction = cleaned.rsplit(sep, 1)
        if len(fraction) == 3:
            integer, fraction = _drop_thousands(cleaned, sep, raw), ""
        elif sep in integer:
            raise ParseError(f"Importe ambiguo: {raw!r}")
    else:
        integer, fraction = cleaned, ""

    value = Decimal(f"{integer}.{fraction}" if fraction else integer)
    return -value if negative else value


def _optional_amount(text: str) -> Decimal | None:
    return parse_amount(text) if text.strip() else None


# --- Debt table ------------------------------------------------------------------------

DEBT_COLUMNS = {
    "concepto": "concept",
    "periodo": "period",
    "imp.concepto": "concept_amount",
    "saldo adeudado": "balance",
    "deuda acumulada": "accumulated",
}


def _normalize_header(text: str) -> str:
    return " ".join(text.lower().split()).rstrip(".").replace("período", "periodo")


def _find_debt_table(soup: BeautifulSoup) -> tuple[Tag, list[Tag]] | None:
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [_normalize_header(_text(c)) for c in rows[0].find_all(["th", "td"])]
        if "saldo adeudado" in headers:
            return table, rows
    return None


def parse_debt_lines(html: str) -> list[DebtLine]:
    """Debt lines of the 'Detalle Deuda Unidad' table.

    No table means no debt (ConsorPlus hides it when the unit is up to date). A table with
    unexpected columns raises ParseError instead of being read as 'no debt'.
    """
    found = _find_debt_table(_soup(html))
    if found is None:
        return []
    _, rows = found

    headers = [_normalize_header(_text(c)) for c in rows[0].find_all(["th", "td"])]
    missing = set(DEBT_COLUMNS) - set(headers)
    if missing:
        raise ParseError(f"Faltan columnas en la tabla de deuda: {sorted(missing)}")
    index = {DEBT_COLUMNS[h]: i for i, h in enumerate(headers) if h in DEBT_COLUMNS}

    lines = []
    for row in rows[1:]:
        cells = [_text(c) for c in row.find_all(["td", "th"], recursive=False)]
        if not any(cells):
            continue  # empty footer row
        if len(cells) != len(headers):
            raise ParseError(f"Fila de deuda con {len(cells)} celdas (se esperaban {len(headers)})")
        values = {key: cells[i] for key, i in index.items()}
        lines.append(
            DebtLine(
                concept=values["concept"],
                period=values["period"],
                concept_amount=_optional_amount(values["concept_amount"]),
                balance=parse_amount(values["balance"]),
                accumulated=_optional_amount(values["accumulated"]),
            )
        )
    return lines
