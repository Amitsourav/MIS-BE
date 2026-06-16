"""Recompute `provider_daily_metrics` for affected (provider, brand, date) cohorts.

A cohort = leads delivered (created_at) on a given UTC day for a provider+brand.
Funnel/side counts are over VALID leads only; invalid & duplicate leads count
solely into their own buckets so that `valid = delivered − invalid − duplicates`
holds exactly and every rate has a sensible (≤1) denominator.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Brand, CanonicalStage
from app.models.lead import MisLead
from app.models.metrics import ProviderDailyMetric
from app.services.funnel import (
    reached_connected,
    reached_contacted,
    reached_converted,
    reached_qualified,
)

CohortKey = tuple  # (provider_id: UUID, brand: Brand, day: date)


def cohort_day(lead: MisLead) -> date | None:
    """The UTC delivery day used to bucket a lead, or None if undatable."""
    ts = lead.created_at or lead.crm_updated_at
    if ts is None:
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc)
    return ts.date()


def _counts_for_cohort(leads: list[MisLead]) -> dict[str, int]:
    c = {
        "delivered": 0,
        "invalid": 0,
        "duplicates": 0,
        "valid": 0,
        "contacted": 0,
        "connected": 0,
        "qualified": 0,
        "converted": 0,
        "dnp": 0,
        "lost": 0,
    }
    for lead in leads:
        c["delivered"] += 1
        if lead.is_invalid:
            c["invalid"] += 1
            continue
        if lead.is_duplicate:
            c["duplicates"] += 1
            continue
        c["valid"] += 1
        if reached_contacted(lead):
            c["contacted"] += 1
        if reached_connected(lead):
            c["connected"] += 1
        if reached_qualified(lead):
            c["qualified"] += 1
        if reached_converted(lead):
            c["converted"] += 1
        if lead.canonical_stage == CanonicalStage.DNP:
            c["dnp"] += 1
        elif lead.canonical_stage == CanonicalStage.LOST:
            c["lost"] += 1
    return c


async def claim_unmapped_leads(
    db: AsyncSession,
    *,
    provider_id: uuid.UUID,
    brand: Brand,
    crm_source_id: uuid.UUID,
) -> int:
    """Assign previously-unmapped leads (provider_id IS NULL) from a CRM source
    to a provider, then recompute the affected daily rollups.

    Called when an admin maps a source: any leads already pulled from that source
    before it was mapped get retroactively attributed to the vendor. Returns the
    number of leads claimed. Does NOT commit (caller controls the transaction).
    """
    # Find which delivery days will need their rollups rebuilt.
    rows = await db.execute(
        select(MisLead.created_at, MisLead.crm_updated_at).where(
            MisLead.brand == brand,
            MisLead.crm_source_id == crm_source_id,
            MisLead.provider_id.is_(None),
        )
    )
    affected: set[CohortKey] = set()
    claimed = 0
    for created_at, crm_updated_at in rows.all():
        claimed += 1
        day = cohort_day(MisLead(created_at=created_at, crm_updated_at=crm_updated_at))
        if day is not None:
            affected.add((provider_id, brand, day))

    if claimed == 0:
        return 0

    await db.execute(
        update(MisLead)
        .where(
            MisLead.brand == brand,
            MisLead.crm_source_id == crm_source_id,
            MisLead.provider_id.is_(None),
        )
        .values(provider_id=provider_id)
    )
    await db.flush()
    await recompute_cohorts(db, affected)
    return claimed


async def recompute_cohorts(db: AsyncSession, keys: set[CohortKey]) -> None:
    """Recompute and upsert rollups for each affected cohort key."""
    for provider_id, brand, day in keys:
        if provider_id is None or day is None:
            continue
        start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        end = datetime.combine(day, time.max, tzinfo=timezone.utc)

        rows = await db.execute(
            select(MisLead).where(
                MisLead.provider_id == provider_id,
                MisLead.brand == brand,
                MisLead.created_at >= start,
                MisLead.created_at <= end,
            )
        )
        leads = list(rows.scalars().all())
        counts = _counts_for_cohort(leads)

        # Portable upsert: clear the cohort row, then insert the fresh counts.
        await db.execute(
            delete(ProviderDailyMetric).where(
                ProviderDailyMetric.provider_id == provider_id,
                ProviderDailyMetric.brand == brand,
                ProviderDailyMetric.metric_date == day,
            )
        )
        db.add(
            ProviderDailyMetric(
                provider_id=provider_id, brand=brand, metric_date=day, **counts
            )
        )
        await db.flush()
