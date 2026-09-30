"""Normalization of ConsorPlus contact data. All numbers, names and emails are invented."""

import pytest

from app.sync.normalize import (
    clean_document,
    clean_name,
    extract_emails,
    extract_phones,
    name_key,
    normalize_phone,
)

CBA = "+5493515550101"


@pytest.mark.parametrize(
    ("raw", "e164", "needs_review"),
    [
        ("0351-15-5550101", CBA, False),
        ("(0351) 155-550101", CBA, False),
        ("0351 15 555 0101", CBA, False),
        ("351 15 5550101", CBA, False),
        ("351 5550101", CBA, False),
        ("0351-5550101", CBA, False),
        ("3515550101", CBA, False),
        ("+54 9 351 555-0101", CBA, False),
        ("+54 351 555 0101", CBA, False),
        ("+5493515550101", CBA, False),
        ("0054 9 351 5550101", CBA, False),
        ("549 351 5550101", CBA, False),
        ("9 351 5550101", CBA, False),
        ("5550101", CBA, True),
        ("555-0101", CBA, True),
        ("15-5550101", CBA, True),
        ("155550101", CBA, True),
        ("011 15 5555-0101", "+5491155550101", False),
        ("11 5555-0101", "+5491155550101", False),
        ("0261 455-0101", "+5492614550101", False),
        ("03543 15 450101", "+5493543450101", False),
        ("+598 99 555 010", "+59899555010", False),
    ],
)
def test_normalize_phone(raw: str, e164: str, needs_review: bool) -> None:
    phone = normalize_phone(raw)

    assert phone is not None, raw
    assert phone.e164 == e164
    assert phone.needs_review is needs_review
    assert phone.raw == raw.strip()


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "no tiene",
        "-",
        "123",
        "12345",
        "0",
        "15",
        "0000000000",
        "1234567890123456",
        "351 000 0000",
        "+54 9 351 12",
    ],
)
def test_invalid_phones_are_rejected(raw: str) -> None:
    assert normalize_phone(raw) is None


def test_phone_repr_is_redacted() -> None:
    assert "5550101" not in repr(normalize_phone("3515550101"))


def test_extract_phones_splits_cells_and_counts_invalid() -> None:
    phones, invalid = extract_phones("5550101 / 155550202 y 12, 3515550101")

    assert [p.e164 for p in phones] == [CBA, "+5493515550202"]  # duplicate dropped
    assert invalid == 1


def test_extract_phones_empty_cell() -> None:
    assert extract_phones("") == ([], 0)
    assert extract_phones("sin datos") == ([], 0)


@pytest.mark.parametrize(
    ("text", "emails"),
    [
        ("Juan.Perez@Example.com", ["juan.perez@example.com"]),
        ("Inquilina desde 01/02/2024 (ana.lopez@example.com)", ["ana.lopez@example.com"]),
        ("a@example.com; B@example.com.ar", ["a@example.com", "b@example.com.ar"]),
        ("x@example.com, X@EXAMPLE.COM", ["x@example.com"]),
        ("no tiene", []),
        ("juan@@example", []),
        ("", []),
    ],
)
def test_extract_emails(text: str, emails: list[str]) -> None:
    assert extract_emails(text) == emails


def test_clean_name_keeps_text_without_extra_spaces() -> None:
    assert clean_name("  PEREZ,   Juan  Carlos ") == "PEREZ, Juan Carlos"
    assert clean_name("") == ""


def test_name_key_ignores_accents_case_punctuation_titles_and_order() -> None:
    assert name_key("Pérez, Juan") == name_key("JUAN PEREZ") == name_key("Sr. Juan  Pérez")
    assert name_key("JUAN PEREZ") != name_key("JUANA PEREZ")


@pytest.mark.parametrize(
    ("raw", "dni"),
    [
        ("20.100.200", "20100200"),
        ("5100200", "5100200"),
        ("DNI 20100200", "20100200"),
        ("20-20100200-3", "20100200"),
        ("27-05100200-1", "5100200"),
        ("", None),
        ("123", None),
        ("sin dato", None),
    ],
)
def test_clean_document(raw: str, dni: str | None) -> None:
    assert clean_document(raw) == dni
