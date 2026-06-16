"""Pure normalization helpers: phone cleaning/validation, stage-log → timestamp
derivation, and assembling a canonical lead fact from a raw CRM row."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.models.enums import Brand, CanonicalStage
from app.sync.stage_map import map_stage

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


def derive_timestamps(
    brand: Brand, logs: list[dict[str, Any]]
) -> dict[str, datetime | None]:
    """From a lead's ordered stage logs, derive the first time it reached each
    funnel milestone (and last time it was lost)."""
    contacted_at = qualified_at = converted_at = lost_at = None
    for entry in logs:  # assumed ordered by created_at ASC
        stage = map_stage(brand, entry.get("stage"))
        ts = entry.get("created_at")
        if ts is None:
            continue
        if stage == CanonicalStage.CONTACTED and contacted_at is None:
            contacted_at = ts
        elif stage == CanonicalStage.QUALIFIED and qualified_at is None:
            qualified_at = ts
        elif stage == CanonicalStage.CONVERTED and converted_at is None:
            converted_at = ts
        elif stage == CanonicalStage.LOST:
            lost_at = ts  # keep the latest lost timestamp
    return {
        "contacted_at": contacted_at,
        "qualified_at": qualified_at,
        "converted_at": converted_at,
        "lost_at": lost_at,
    }


def build_lead_fact(
    brand: Brand, row: dict[str, Any], logs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Assemble the canonical lead fact (minus provider_id / is_duplicate, which
    the worker sets once it has MIS-side context)."""
    raw_stage = row.get("raw_stage")
    canonical = map_stage(brand, raw_stage)

    phone = normalize_phone(row.get("phone"))
    is_invalid, invalid_reason = validate_phone(phone)

    ts = derive_timestamps(brand, logs)
    # If the current stage already implies a milestone but no log captured it,
    # backfill from the lead's own created/updated where reasonable.
    if canonical == CanonicalStage.CONVERTED and ts["converted_at"] is None:
        ts["converted_at"] = row.get("crm_updated_at")
    if (
        canonical in (CanonicalStage.QUALIFIED, CanonicalStage.IN_PROCESS)
        and ts["qualified_at"] is None
    ):
        ts["qualified_at"] = row.get("crm_updated_at")

    return {
        "brand": brand,
        "crm_lead_id": row.get("crm_lead_id"),
        "crm_source_id": row.get("crm_source_id"),
        "serial_no": row.get("serial_no"),
        "full_name": row.get("full_name"),
        "phone": phone,
        "raw_stage": str(raw_stage) if raw_stage is not None else None,
        "canonical_stage": canonical,
        "is_invalid": is_invalid,
        "invalid_reason": invalid_reason,
        "created_at": row.get("created_at"),
        "contacted_at": ts["contacted_at"],
        "qualified_at": ts["qualified_at"],
        "converted_at": ts["converted_at"],
        "lost_at": ts["lost_at"],
        "crm_updated_at": row.get("crm_updated_at"),
    }
