"""Pure normalization of the contact data that comes from ConsorPlus. No I/O here."""

import re
import unicodedata
from dataclasses import dataclass

import phonenumbers

DEFAULT_AREA_CODE = "351"  # Córdoba capital
MAX_RAW_LENGTH = 100  # phones.raw

# Several numbers in one cell: "4123456 / 155551234", "351..., 351...", "... y ...".
_PHONE_SEPARATORS = re.compile(r"[/;,|]|\s+(?:y|o)\s+", re.IGNORECASE)
_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}", re.IGNORECASE)
_TITLES = frozenset({"sr", "sra", "srta", "dr", "dra", "ing", "arq", "lic", "cr", "cra", "esc"})


@dataclass(frozen=True, repr=False)
class NormalizedPhone:
    e164: str
    raw: str
    # The area code was assumed (the number came without one).
    needs_review: bool = False

    def __repr__(self) -> str:
        return f"NormalizedPhone(<redacted>, needs_review={self.needs_review})"


def _valid_ar_mobile(national: str) -> str | None:
    """'+549' + national (area code + number, 10 digits) if phonenumbers accepts it."""
    if len(national) != 10:
        return None
    try:
        number = phonenumbers.parse("+549" + national, "AR")
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def _drop_mobile_prefix(digits: str, raw: str) -> str | None:
    """'351 15 1234567' (12 digits: area + 15 + number) -> '3511234567'."""
    groups = [g for g in re.split(r"\D+", raw) if g]
    first = groups[0].removeprefix("0") if groups else ""
    lengths = [len(first)] if len(groups) > 1 and 2 <= len(first) <= 4 else []
    for length in [*lengths, 3, 4, 2]:
        if digits[length : length + 2] == "15":
            candidate = digits[:length] + digits[length + 2 :]
            if _valid_ar_mobile(candidate):
                return candidate
    return None


def normalize_phone(raw: str) -> NormalizedPhone | None:
    """One phone number as WhatsApp E.164 (+549 + area code + number), or None if invalid.

    Drops the trunk '0' and the mobile '15'. Without area code (6-8 digits) assumes Córdoba
    (351) and flags needs_review.
    """
    text = raw.strip()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    stored_raw = text[:MAX_RAW_LENGTH]

    international = text.startswith("+") or digits.startswith("00")
    digits = digits.removeprefix("00")
    if international and not digits.startswith("54"):
        try:
            number = phonenumbers.parse("+" + digits, None)
        except phonenumbers.NumberParseException:
            return None
        if not phonenumbers.is_valid_number(number):
            return None
        return NormalizedPhone(
            phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164), stored_raw
        )
    if international or (digits.startswith("54") and len(digits) in (12, 13)):
        digits = digits.removeprefix("54")

    digits = digits.removeprefix("0")
    if len(digits) == 11 and digits.startswith("9"):
        digits = digits[1:]

    needs_review = False
    national: str | None
    if len(digits) == 10:
        national = digits
    elif len(digits) == 12:
        national = _drop_mobile_prefix(digits, text)
    elif len(digits) == 9 and digits.startswith("15"):
        national, needs_review = DEFAULT_AREA_CODE + digits[2:], True
    elif 6 <= len(digits) <= 8:
        national, needs_review = DEFAULT_AREA_CODE + digits, True
    else:
        national = None

    e164 = _valid_ar_mobile(national) if national else None
    if e164 is None:
        return None
    return NormalizedPhone(e164, stored_raw, needs_review)


def extract_phones(text: str) -> tuple[list[NormalizedPhone], int]:
    """All the phones of a cell (it may hold several) and how many parts were invalid."""
    phones: list[NormalizedPhone] = []
    invalid = 0
    for part in _PHONE_SEPARATORS.split(text or ""):
        if not re.search(r"\d", part):
            continue
        phone = normalize_phone(part)
        if phone is None:
            invalid += 1
        elif phone.e164 not in {p.e164 for p in phones}:
            phones.append(phone)
    return phones, invalid


def extract_emails(text: str) -> list[str]:
    """Valid addresses found in the text (it sometimes comes mixed with notes), lower-cased."""
    emails: list[str] = []
    for match in _EMAIL.findall(text or ""):
        email = match.lower().strip(".")
        if ".." not in email and email not in emails:
            emails.append(email)
    return emails


def clean_name(name: str) -> str:
    """Name as shown to people: same text, without extra whitespace."""
    return " ".join((name or "").split())


def name_key(name: str) -> str:
    """Comparison key: no accents, case, punctuation, titles or word order."""
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    words = re.sub(r"[^a-z0-9]+", " ", ascii_only).split()
    return " ".join(sorted(w for w in words if w not in _TITLES))


def clean_document(document: str) -> str | None:
    """DNI as digits (7-8). A CUIT/CUIL (11 digits) yields its DNI part. Anything else: None."""
    digits = re.sub(r"\D", "", document or "")
    if len(digits) == 11:
        digits = digits[2:10].lstrip("0")
    if 7 <= len(digits) <= 8:
        return digits
    return None


PAYMENT_CODE_LENGTH = 19


def clean_payment_code(code: str) -> str | None:
    """Siro payment code ("Cód.Electrónico"): exactly 19 digits, else None. No guessing."""
    code = (code or "").strip()
    if len(code) == PAYMENT_CODE_LENGTH and code.isascii() and code.isdigit():
        return code
    return None
