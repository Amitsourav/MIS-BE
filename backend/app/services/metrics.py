"""Dashboard metrics computed from live lead facts.

Counting rules (unchanged from the old daily rollups): funnel/side counts are
over VALID leads only; invalid and duplicate leads count solely into their own
buckets, so `valid = delivered − invalid − duplicates` holds exactly and every
rate has a sensible (≤1) denominator.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from app.models.enums import CanonicalStage
from app.schemas.metrics import FunnelCounts, QualityMetrics, RateMetrics, TrendPoint
from app.services.funnel import (
    reached_connected,
    reached_contacted,
    reached_converted,
    reached_qualified,
)
from app.services.live import LeadFact

_COUNT_KEYS = (
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


def aggregate(facts: Iterable[LeadFact]) -> dict[str, int]:
    c = dict.fromkeys(_COUNT_KEYS, 0)
    for lead in facts:
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


def time_to_averages(facts: Iterable[LeadFact]) -> tuple[float | None, float | None]:
    """Average seconds to first contact and to qualify, over valid leads."""
    contact_deltas: list[float] = []
    qualify_deltas: list[float] = []
    for lead in facts:
        if not lead.is_valid or lead.created_at is None:
            continue
        if lead.contacted_at is not None and lead.contacted_at >= lead.created_at:
            contact_deltas.append((lead.contacted_at - lead.created_at).total_seconds())
        if lead.qualified_at is not None and lead.qualified_at >= lead.created_at:
            qualify_deltas.append((lead.qualified_at - lead.created_at).total_seconds())
    tfc = round(sum(contact_deltas) / len(contact_deltas), 1) if contact_deltas else None
    ttq = round(sum(qualify_deltas) / len(qualify_deltas), 1) if qualify_deltas else None
    return tfc, ttq


def volume_reliability(facts: Iterable[LeadFact], date_from: date, date_to: date) -> float:
    """Fraction of days in the range that had at least one delivered lead."""
    total_days = (date_to - date_from).days + 1
    if total_days <= 0:
        return 0.0
    active = {
        d for d in (f.cohort_day for f in facts) if d is not None and date_from <= d <= date_to
    }
    return round(len(active) / total_days, 4)


def trend_points(facts: Iterable[LeadFact], granularity: str) -> list[TrendPoint]:
    """Delivered / valid / qualified / converted per day (or ISO week start)."""
    buckets: dict[str, list[LeadFact]] = {}
    for f in facts:
        day = f.cohort_day
        if day is None:
            continue
        if granularity == "week":
            iso = day.isocalendar()
            key = date.fromisocalendar(iso[0], iso[1], 1).isoformat()
        else:
            key = day.isoformat()
        buckets.setdefault(key, []).append(f)
    points = []
    for key in sorted(buckets):
        agg = aggregate(buckets[key])
        points.append(
            TrendPoint(
                period=key,
                delivered=agg["delivered"],
                valid=agg["valid"],
                qualified=agg["qualified"],
                converted=agg["converted"],
            )
        )
    return points
