"""The debt message the person gets, built by the code (never by the model).

render_debt_message turns the data of get_debt (status "ok") into a fixed WhatsApp block:
unit, total (or "estás al día"), detail by period and concept, date of the data, the stale
note, payment code, how to pay and the self-service link. render_payment_message is the same
block without the balance, for get_payment_info ("¿cómo pago?"). The channel sends them, as
is, in the same message as the agent's text and before it (join_blocks), so amounts, dates,
payment codes and links always reach the person exactly as stored. In the first message of a
conversation the agent puts FIRST_GREETING before the first block (the model is told not to
introduce itself again).

amounts_not_in reads the "$" amounts the agent wrote anyway, to log the ones that match no
block (debt_amount_mismatch).
"""

import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

# "$165.060,00", "$ 165.060", "-$1.500,5", "$165060.00".
_AMOUNT = re.compile(r"-?\$\s?\d[\d.,]*")

BLOCK_SEPARATOR = "\n\n"

FIRST_GREETING = (
    "Hola, soy el asistente automático del Estudio Diego Rufeil. Si en algún momento querés "
    "hablar con una persona, decímelo."
)


def render_debt_message(result: Mapping[str, Any]) -> str:
    """The block for one unit, from the data get_debt gathers (texts already formatted)."""
    lines = [f"*{result['unit']}*"]
    if result["up_to_date"]:
        lines.append("Estás al día: no tenés saldo pendiente.")
    else:
        lines.append(f"Saldo total: *{result['total_debt']}*")
        lines += [f"• {item}" for item in result["detail"]]
        if more := result.get("more_lines", 0):
            lines.append(f"• y {more} más")
    day, _, hour = result["data_date"].partition(" ")  # "30/09/2026 14:05"
    lines.append(f"Dato al {day} a las {hour}." if hour else f"Dato al {day}.")
    if result.get("stale"):
        lines.append("No se pudo actualizar ahora: es el último dato guardado.")
    lines.append("")
    return "\n".join(lines + _how_to_pay(result))


def render_payment_message(result: Mapping[str, Any]) -> str:
    """How to pay one unit, with no balance: unit, payment code, how to pay and link."""
    return "\n".join([f"*{result['unit']}*", *_how_to_pay(result)])


def _how_to_pay(result: Mapping[str, Any]) -> list[str]:
    lines = []
    if result.get("payment_code"):
        lines.append(f"Código de pago Siro: *{result['payment_code']}*")
    lines.append(result["payment_how_to"])
    if result.get("autogestion_url"):
        lines.append(f"Expensas y comprobantes: {result['autogestion_url']}")
    return lines


def parse_amount(text: str) -> Decimal | None:
    """ "$165.060,00" -> Decimal("165060.00"). Argentine format first ("." thousands, ","
    decimals); "$165060.50" (a dot and two digits, no comma) is read as decimals."""
    digits = text.replace("$", "").replace(" ", "").rstrip(".,")
    negative = digits.startswith("-")
    digits = digits.lstrip("-")
    if "," in digits:
        digits = digits.replace(".", "").replace(",", ".")
    elif not re.search(r"\.\d{1,2}$", digits):
        digits = digits.replace(".", "")
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


def find_amounts(text: str) -> list[str]:
    """The "$" amounts written in a text, as written (asterisks around them do not matter)."""
    return [m.group(0).rstrip(".,") for m in _AMOUNT.finditer(text)]


def join_blocks(blocks: Iterable[str], text: str) -> str:
    """The blocks and then the agent's text, as one message (a blank line between them)."""
    return BLOCK_SEPARATOR.join([*blocks, text])


def amounts_not_in(text: str, blocks: Iterable[str]) -> list[str]:
    """The "$" amounts of `text` whose value appears in none of `blocks`."""
    known = {v for b in blocks for a in find_amounts(b) if (v := parse_amount(a)) is not None}
    return [a for a in find_amounts(text) if parse_amount(a) not in known]
