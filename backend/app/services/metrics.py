"""Aggregate rollups over a date range and compute dashboard metrics.

Dashboard reads NEVER scan raw leads for counts — they sum the pre-aggregated
`provider_daily_metrics`. The only raw-lead reads here are the time-to-* averages,
which have no pre-aggregated form.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Brand
from app.models.lead import MisLead
from app.models.metrics import ProviderDailyMetric
from app.schemas.metrics import FunnelCounts, QualityMetrics, RateMetrics

_SUM_COLS = (
    "delivered",
    "invalid",
    "duplicates",
    "valid",
    "contacted",
    "connected",
    "qualified",
    "converted",
    "dnp",
    "lost",
)


def safe_div(numer: float, denom: float) -> float:
    return round(numer / denom, 4) if denom else 0.0


async def sum_rollups(
    db: AsyncSession,
    provider_id: uuid.UUID,
    brands: list[Brand],
    date_from: date,
    date_to: date,
) -> dict[str, int]:
    cols = [func.coalesce(func.sum(getattr(ProviderDailyMetric, c)), 0) for c in _SUM_COLS]
    stmt = select(*cols).where(
        ProviderDailyMetric.provider_id == provider_id,
        ProviderDailyMetric.brand.in_(brands),
        ProviderDailyMetric.metric_date >= date_from,
        ProviderDailyMetric.metric_date <= date_to,
    )
    row = (await db.execute(stmt)).one()
    return {c: int(row[i]) for i, c in enumerate(_SUM_COLS)}


def to_funnel(agg: dict[str, int]) -> FunnelCounts:
    return FunnelCounts(
        delivered=agg["delivered"],
        contacted=agg["contacted"],
        connected=agg["connected"],
        qualified=agg["qualified"],
        converted=agg["converted"],
        dnp=agg["dnp"],
        lost=agg["lost"],
    )


def to_quality(agg: dict[str, int]) -> QualityMetrics:
    delivered = agg["delivered"]
    return QualityMetrics(
        delivered=delivered,
        invalid=agg["invalid"],
        duplicates=agg["duplicates"],
        valid=agg["valid"],
        invalid_rate=safe_div(agg["invalid"], delivered),
        duplicate_rate=safe_div(agg["duplicates"], delivered),
    )


def to_rates(
    agg: dict[str, int],
    time_to_first_contact_sec: float | None = None,
    time_to_qualify_sec: float | None = None,
) -> RateMetrics:
    valid = agg["valid"]
    return RateMetrics(
        contact_rate=safe_div(agg["contacted"], valid),
        qualification_rate=safe_div(agg["qualified"], valid),
        conversion_rate=safe_div(agg["converted"], valid),
        time_to_first_contact_sec=time_to_first_contact_sec,
        time_to_qualify_sec=time_to_qualify_sec,
    )


async def time_to_averages(
    db: AsyncSession,
    provider_id: uuid.UUID,
    brands: list[Brand],
    date_from: date,
    date_to: date,
) -> tuple[float | None, float | None]:
    """Average seconds to first contact and to qualify, over valid leads whose
    created_at falls in the range. Computed in Python for DB portability."""
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end = datetime.combine(date_to, time.max, tzinfo=timezone.utc)
    rows = await db.execute(
        select(
            MisLead.created_at, MisLead.contacted_at, MisLead.qualified_at
        ).where(
            MisLead.provider_id == provider_id,
            MisLead.brand.in_(brands),
            MisLead.is_invalid.is_(False),
            MisLead.is_duplicate.is_(False),
            MisLead.created_at >= start,
            MisLead.created_at <= end,
        )
    )
    contact_deltas: list[float] = []
    qualify_deltas: list[float] = []
    for created, contacted, qualified in rows.all():
        if created is None:
            continue
        if contacted is not None and contacted >= created:
            contact_deltas.append((contacted - created).total_seconds())
        if qualified is not None and qualified >= created:
            qualify_deltas.append((qualified - created).total_seconds())

    tfc = round(sum(contact_deltas) / len(contact_deltas), 1) if contact_deltas else None
    ttq = round(sum(qualify_deltas) / len(qualify_deltas), 1) if qualify_deltas else None
    return tfc, ttq
