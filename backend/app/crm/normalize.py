"""Pure phone helpers: cleaning to a comparable form and validating.

The live lead query normalizes phones in SQL with the same rules (see
`app.crm.reader._NPHONE`); keep the two in step.
"""
from __future__ import annotations

import re

_DIGITS = re.compile(r"\D+")


def normalize_phone(raw: str | None) -> str | None:
    """Strip to digits, drop a leading country '91'/'0' for Indian numbers.
    Returns a comparable canonical phone string, or None if nothing usable."""
    if not raw:
        return None
    digits = _DIGITS.sub("", raw)
    if not digits:
        return None
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def validate_phone(phone_digits: str | None) -> tuple[bool, str | None]:
    """Return (is_invalid, reason). A valid mobile is 10–15 digits."""
    if not phone_digits:
        return True, "empty_phone"
    n = len(phone_digits)
    if n < 10 or n > 15:
        return True, "bad_phone_length"
    if len(set(phone_digits)) == 1:
        return True, "repeated_digit_phone"
    return False, None
