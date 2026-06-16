"""Dashboard / metrics response schemas."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.models.enums import Brand


class FunnelCounts(BaseModel):
    delivered: int = 0
    contacted: int = 0
    connected: int = 0
    qualified: int = 0
    converted: int = 0
    # side states
    dnp: int = 0
    lost: int = 0


class QualityMetrics(BaseModel):
    delivered: int = 0
    invalid: int = 0
    duplicates: int = 0
    valid: int = 0
    invalid_rate: float = 0.0
    duplicate_rate: float = 0.0


class RateMetrics(BaseModel):
    contact_rate: float = 0.0
    qualification_rate: float = 0.0
    conversion_rate: float = 0.0
    # avg seconds; null if not computable
    time_to_first_contact_sec: float | None = None
    time_to_qualify_sec: float | None = None


class Scorecard(BaseModel):
    score_pct: float
    grade: str
    criteria: dict[str, dict]  # key -> {value, rating, weight}


class OverviewResponse(BaseModel):
    brand: str  # 'fmc' | 'av' | 'both'
    date_from: date
    date_to: date
    funnel: FunnelCounts
    quality: QualityMetrics
    rates: RateMetrics
    scorecard: Scorecard | None = None
    data_as_of: str | None = None  # ISO timestamp of last sync


class TrendPoint(BaseModel):
    period: str  # YYYY-MM-DD (day) or ISO week start
    delivered: int = 0
    valid: int = 0
    qualified: int = 0
    converted: int = 0


class TrendsResponse(BaseModel):
    granularity: str
    points: list[TrendPoint]


class BrandSplitRow(BaseModel):
    brand: Brand
    funnel: FunnelCounts
    quality: QualityMetrics
    rates: RateMetrics


class BrandSplitResponse(BaseModel):
    rows: list[BrandSplitRow]
