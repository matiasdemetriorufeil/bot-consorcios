"""The debt message built by the code, and the check of amounts in the agent's text.
All data is invented."""

from decimal import Decimal
from typing import Any

import pytest

from app.bot.debt_message import (
    amounts_not_in,
    find_amounts,
    parse_amount,
    render_debt_message,
    render_payment_message,
)

HOW_TO = "Pagalo con el código en el banco inventado."


def _data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "unit": "EDIFICIO INVENTADO 04-C",
        "total_debt": "$165.060,00",
        "up_to_date": False,
        "data_date": "30/09/2026 14:05",
        "detail": [
            "08/2026 Expensas ordinarias: $82.530,00",
            "09/2026 Expensas ordinarias: $82.530,00",
        ],
        "more_lines": 0,
        "stale": False,
        "payment_code": "0000111122223333444",
        "payment_how_to": HOW_TO,
    }
    return data | changes


def test_debt_message_with_debt() -> None:
    assert render_debt_message(_data()) == (
        "*EDIFICIO INVENTADO 04-C*\n"
        "Saldo total: *$165.060,00*\n"
        "• 08/2026 Expensas ordinarias: $82.530,00\n"
        "• 09/2026 Expensas ordinarias: $82.530,00\n"
        "Dato al 30/09/2026 a las 14:05.\n"
        "\n"
        "Código de pago Siro: *0000111122223333444*\n"
        f"{HOW_TO}"
    )


def test_debt_message_up_to_date_has_no_total_nor_detail() -> None:
    message = render_debt_message(_data(up_to_date=True, total_debt="$0,00", detail=[]))
    assert "Estás al día" in message
    assert "Saldo total" not in message and "•" not in message
    assert "Código de pago Siro" in message


def test_debt_message_cut_stale_and_without_code() -> None:
    message = render_debt_message(
        _data(more_lines=4, stale=True, payment_code=None, payment_how_to="Pedilo.")
    )
    lines = message.splitlines()
    assert "• y 4 más" in lines
    assert "No se pudo actualizar ahora: es el último dato guardado." in lines
    assert "Código de pago" not in message
    assert lines[-1] == "Pedilo."


def test_debt_message_is_whatsapp_format() -> None:
    message = render_debt_message(_data(more_lines=1, stale=True))
    assert "**" not in message and "#" not in message and "|" not in message


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("$165.060,00", Decimal("165060.00")),
        ("$ 165.060", Decimal("165060")),
        ("$82.530,5", Decimal("82530.5")),
        ("$165060.00", Decimal("165060.00")),
        ("-$1.500,00", Decimal("-1500.00")),
        ("$1.500", Decimal("1500")),
    ],
)
def test_parse_amount(text: str, value: Decimal) -> None:
    assert parse_amount(text) == value


def test_find_amounts_ignores_asterisks_and_trailing_punctuation() -> None:
    text = "Debés *$165.060,00*. Y $82.530, nada más; el 30/09 o 12 cuotas no cuentan."
    assert find_amounts(text) == ["$165.060,00", "$82.530"]


def test_amounts_not_in() -> None:
    blocks = [render_debt_message(_data())]
    assert amounts_not_in("Tu saldo es *$165.060*.", blocks) == []
    assert amounts_not_in("Son $82.530,00 por mes.", blocks) == []
    assert amounts_not_in("Debés $165.000,00.", blocks) == ["$165.000,00"]
    assert amounts_not_in("¿Te ayudo con algo más?", blocks) == []


def test_debt_message_ends_with_the_self_service_link() -> None:
    message = render_debt_message(_data(autogestion_url="https://autogestion.example.com"))
    assert message.splitlines()[-2:] == [
        HOW_TO,
        "Expensas y comprobantes: https://autogestion.example.com",
    ]
    assert "Expensas y comprobantes" not in render_debt_message(_data(autogestion_url=None))


def test_payment_message_has_no_balance() -> None:
    message = render_payment_message(
        {
            "unit": "EDIFICIO INVENTADO 04-C",
            "payment_code": "0000111122223333444",
            "payment_how_to": HOW_TO,
            "autogestion_url": "https://autogestion.example.com",
        }
    )
    assert message == (
        "*EDIFICIO INVENTADO 04-C*\n"
        "Código de pago Siro: *0000111122223333444*\n"
        f"{HOW_TO}\n"
        "Expensas y comprobantes: https://autogestion.example.com"
    )
