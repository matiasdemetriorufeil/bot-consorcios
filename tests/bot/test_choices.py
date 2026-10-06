"""WhatsApp limits for options to tap, and how they look where buttons cannot be shown."""

import pytest

from app.bot.choices import numbered_text, problems, title_limit, with_options


def test_title_limit_depends_on_buttons_or_list() -> None:
    assert [title_limit(n) for n in (2, 3, 4, 10)] == [20, 20, 24, 24]


@pytest.mark.parametrize(
    ("text", "titles", "problem"),
    [
        ("", ["Sí", "No"], "falta el texto"),
        ("x" * 1025, ["Sí", "No"], "1025 caracteres"),
        ("¿Cuál?", ["Sí"], "entre 2 y 10"),
        ("¿Cuál?", [str(n) for n in range(11)], "entre 2 y 10"),
        ("¿Cuál?", ["Sí, pasame con alguien", "No"], "más de 20 caracteres"),
        ("¿Cuál?", ["Sí", "sí"], "repetidas"),
        ("¿Cuál?", ["Sí", " "], "vacías"),
    ],
)
def test_problems(text: str, titles: list[str], problem: str) -> None:
    assert any(problem in p for p in problems(text, titles))


def test_what_fits_has_no_problems() -> None:
    assert problems("¿Te paso con una persona?", ["Sí, pasame", "No, gracias"]) == []
    assert problems("x" * 1024, [f"Opción {n}" for n in range(10)]) == []


def test_numbered_text_and_history_text() -> None:
    assert numbered_text("¿Querés?", ["Sí, pasame", "No, gracias"]) == (
        "¿Querés?\n\n1. Sí, pasame\n2. No, gracias\n\nRespondé con el número o escribí la opción."
    )
    assert with_options("¿Querés?", ["Sí", "No"]) == "¿Querés?\n[Opciones: Sí / No]"
