"""Turn live CRM lead rows into canonical lead facts.

Same rules the old sync applied, now per request: stage mapping, phone
validity, duplicate flag (computed in SQL over the whole brand), milestone
timestamps from the stage logs, and the current-stage backfill.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from app.crm import reader
from app.crm.normalize import validate_phone
from app.crm.stage_map import map_stage
from app.models.enums import Brand, CanonicalStage


@dataclass(frozen=True)
class LeadFact:
    brand: Brand
    crm_lead_id: uuid.UUID
    crm_source_id: uuid.UUID | None
    serial_no: int | None
    full_name: str | None
    phone: str | None  # normalized digits
    raw_stage: str | None
    canonical_stage: CanonicalStage
    is_invalid: bool
    invalid_reason: str | None
    is_duplicate: bool
    created_at: datetime | None
    contacted_at: datetime | None
    qualified_at: datetime | None
    converted_at: datetime | None
    lost_at: datetime | None
    crm_updated_at: datetime | None

    @property
    def is_valid(self) -> bool:
        return not self.is_invalid and not self.is_duplicate

    @property
    def cohort_day(self) -> date | None:
        """The UTC delivery day the lead is bucketed into."""
        ts = self.created_at
        if ts is None:
            return None
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc)
        return ts.date()


def build_fact(brand: Brand, row: dict[str, Any]) -> LeadFact:
    raw_stage = row.get("raw_stage")
    canonical = map_stage(brand, raw_stage)
    phone = row.get("phone")
    is_invalid, invalid_reason = validate_phone(phone)

    qualified_at = row.get("qualified_at")
    converted_at = row.get("converted_at")
    # Current stage implies a milestone the logs didn't capture: backfill from
    # the lead's last update, as the sync did.
    if canonical == CanonicalStage.CONVERTED and converted_at is None:
        converted_at = row.get("crm_updated_at")
    if canonical in (CanonicalStage.QUALIFIED, CanonicalStage.IN_PROCESS) and qualified_at is None:
        qualified_at = row.get("crm_updated_at")

    return LeadFact(
        brand=brand,
        crm_lead_id=row["crm_lead_id"],
        crm_source_id=row.get("crm_source_id"),
        serial_no=row.get("serial_no"),
        full_name=row.get("full_name"),
        phone=phone,
        raw_stage=str(raw_stage) if raw_stage is not None else None,
        canonical_stage=canonical,
        is_invalid=is_invalid,
        invalid_reason=invalid_reason,
        is_duplicate=bool(row.get("is_duplicate")),
        created_at=row.get("created_at"),
        contacted_at=row.get("contacted_at"),
        qualified_at=qualified_at,
        converted_at=converted_at,
        lost_at=row.get("lost_at"),
        crm_updated_at=row.get("crm_updated_at"),
    )


def day_bounds(
    date_from: date | None, date_to: date | None
) -> tuple[datetime | None, datetime | None]:
    """[from 00:00 UTC, day-after-to 00:00 UTC) — None means unbounded."""
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc) if date_from else None
    end = (
        datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
        if date_to
        else None
    )
    return start, end


async def lead_facts(
    brand: Brand,
    source_ids: list[uuid.UUID],
    date_from: date | None,
    date_to: date | None,
) -> list[LeadFact]:
    """Live facts for the given sources, delivered (created) in the date range."""
    start, end = day_bounds(date_from, date_to)
    rows = await reader.lead_rows(brand, source_ids, start, end)
    return [build_fact(brand, r) for r in rows]
