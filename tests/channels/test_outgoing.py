"""One turn -> the Chatwoot messages to create: one message, split only when too long."""

from app.bot.choices import MAX_TEXT
from app.bot.debt_message import join_blocks
from app.channels.outgoing import MAX_MESSAGE, pack, with_buttons

BLOCK_A = "*EDIFICIO INVENTADO 04-C*\nSaldo total: *$1.000,00*"
BLOCK_B = "*EDIFICIO INVENTADO COC.3*\nEstás al día: no tenés saldo pendiente."


def test_blocks_and_text_go_in_one_message() -> None:
    assert pack([BLOCK_A, BLOCK_B, "¿Te ayudo con algo más?"]) == [
        f"{BLOCK_A}\n\n{BLOCK_B}\n\n¿Te ayudo con algo más?"
    ]
    assert pack(["Hola"]) == ["Hola"]
    assert join_blocks([BLOCK_A], "Listo") == f"{BLOCK_A}\n\nListo"


def test_a_long_turn_is_split_between_blocks() -> None:
    a, b, c = "a" * 1500, "b" * 1500, "c" * 1500
    text = "¿Algo más?"

    messages = pack([a, b, c, text])

    assert messages == [f"{a}\n\n{b}", f"{c}\n\n{text}"]
    assert all(len(m) <= MAX_MESSAGE for m in messages)


def test_exactly_the_limit_fits() -> None:
    a = "a" * (MAX_MESSAGE - 2 - 3)
    assert pack([a, "xyz"]) == [f"{a}\n\nxyz"]
    assert pack([a, "xyzw"]) == [a, "xyzw"]


def test_a_block_longer_than_a_message_is_cut_at_line_ends() -> None:
    lines = [f"• 01/2026 Concepto {n}: $1.000,00" for n in range(200)]
    block = "\n".join(lines)

    messages = pack([block, "¿Algo más?"])

    assert len(messages) > 1
    assert all(len(m) <= MAX_MESSAGE for m in messages)
    # Nothing lost or reordered; no line cut in half.
    assert "\n".join(messages).replace("\n\n", "\n").split("\n") == [*lines, "¿Algo más?"]


def test_a_line_longer_than_a_message_is_cut_by_characters() -> None:
    messages = pack(["x" * 9000])
    assert [len(m) for m in messages] == [MAX_MESSAGE, MAX_MESSAGE, 1000]


def test_with_buttons_puts_the_blocks_in_the_text_when_they_fit() -> None:
    assert with_buttons([BLOCK_A], "¿Te paso con alguien?") == (
        [],
        f"{BLOCK_A}\n\n¿Te paso con alguien?",
    )
    assert with_buttons([], "¿Cuál?") == ([], "¿Cuál?")


def test_with_buttons_sends_the_blocks_first_when_they_do_not_fit() -> None:
    long_block = "x" * MAX_TEXT

    first, text = with_buttons([long_block, BLOCK_B], "¿Te paso con alguien?")

    assert first == [f"{long_block}\n\n{BLOCK_B}"]
    assert text == "¿Te paso con alguien?"
