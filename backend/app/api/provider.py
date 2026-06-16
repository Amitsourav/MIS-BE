"""Provider-facing endpoints (/me/*). Every query is scoped to the caller's
provider_id, taken ONLY from the verified JWT via `require_provider`."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import Principal, require_provider
from app.models import MisLead, ProviderDailyMetric, ProviderSource, SyncState
from app.models.enums import Brand, CanonicalStage
from app.schemas.common import Page
from app.schemas.lead import LeadOut
from app.schemas.metrics import (
    BrandSplitResponse,
    BrandSplitRow,
    OverviewResponse,
    QualityMetrics,
    TrendPoint,
    TrendsResponse,
)
from app.services import metrics as M
from app.services.scorecard import compute_scorecard, load_targets

router = APIRouter(prefix="/me", tags=["provider"])

_DEFAULT_RANGE_DAYS = 30


# --- shared parsing helpers ---
def _brands(brand: str | None) -> list[Brand]:
    if brand is None or brand.lower() == "both":
        return list(Brand)
    return [Brand(brand.lower())]


def _range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    to = date_to or today
    frm = date_from or (to - timedelta(days=_DEFAULT_RANGE_DAYS))
    return frm, to


async def _data_as_of(db: AsyncSession) -> str | None:
    row = await db.execute(select(func.max(SyncState.last_run_at)))
    ts = row.scalar()
    return ts.isoformat() if ts else None


async def _volume_reliability(
    db: AsyncSession, provider_id, brands, date_from, date_to
) -> float:
    """Fraction of days in the range that had at least one delivered lead."""
    total_days = (date_to - date_from).days + 1
    if total_days <= 0:
        return 0.0
    rows = await db.execute(
        select(func.count(func.distinct(ProviderDailyMetric.metric_date))).where(
            ProviderDailyMetric.provider_id == provider_id,
            ProviderDailyMetric.brand.in_(brands),
            ProviderDailyMetric.metric_date >= date_from,
            ProviderDailyMetric.metric_date <= date_to,
            ProviderDailyMetric.delivered > 0,
        )
    )
    active = int(rows.scalar() or 0)
    return round(active / total_days, 4)


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    brand: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> OverviewResponse:
    brands = _brands(brand)
    frm, to = _range(date_from, date_to)
    pid = principal.provider_id

    agg = await M.sum_rollups(db, pid, brands, frm, to)
    tfc, ttq = await M.time_to_averages(db, pid, brands, frm, to)

    # scorecard inputs
    validity = M.safe_div(agg["valid"], agg["delivered"])
    reliability = await _volume_reliability(db, pid, brands, frm, to)
    targets = await load_targets(db, principal.brand)  # vendor's own company targets
    scorecard = compute_scorecard(
        {
            "validity": validity,
            "qualification_rate": M.safe_div(agg["qualified"], agg["valid"]),
            "conversion_rate": M.safe_div(agg["converted"], agg["valid"]),
            "volume_reliability": reliability,
            "dispute_rate": 0.0,  # Phase 2
        },
        targets,
    )

    return OverviewResponse(
        brand=brand or "both",
        date_from=frm,
        date_to=to,
        funnel=M.to_funnel(agg),
        quality=M.to_quality(agg),
        rates=M.to_rates(agg, tfc, ttq),
        scorecard=scorecard,
        data_as_of=await _data_as_of(db),
    )


@router.get("/trends", response_model=TrendsResponse)
async def trends(
    brand: str | None = None,
    granularity: str = Query("day", pattern="^(day|week)$"),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> TrendsResponse:
    brands = _brands(brand)
    frm, to = _range(date_from, date_to)

    rows = await db.execute(
        select(
            ProviderDailyMetric.metric_date,
            ProviderDailyMetric.delivered,
            ProviderDailyMetric.valid,
            ProviderDailyMetric.qualified,
            ProviderDailyMetric.converted,
        )
        .where(
            ProviderDailyMetric.provider_id == principal.provider_id,
            ProviderDailyMetric.brand.in_(brands),
            ProviderDailyMetric.metric_date >= frm,
            ProviderDailyMetric.metric_date <= to,
        )
        .order_by(ProviderDailyMetric.metric_date)
    )

    buckets: dict[str, dict[str, int]] = {}
    for m_date, delivered, valid, qualified, converted in rows.all():
        if granularity == "week":
            iso = m_date.isocalendar()
            key = date.fromisocalendar(iso[0], iso[1], 1).isoformat()
        else:
            key = m_date.isoformat()
        b = buckets.setdefault(
            key, {"delivered": 0, "valid": 0, "qualified": 0, "converted": 0}
        )
        b["delivered"] += delivered
        b["valid"] += valid
        b["qualified"] += qualified
        b["converted"] += converted

    points = [
        TrendPoint(period=k, **v) for k, v in sorted(buckets.items())
    ]
    return TrendsResponse(granularity=granularity, points=points)


@router.get("/brand-split", response_model=BrandSplitResponse)
async def brand_split(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> BrandSplitResponse:
    frm, to = _range(date_from, date_to)
    out_rows = []
    for b in Brand:
        agg = await M.sum_rollups(db, principal.provider_id, [b], frm, to)
        if agg["delivered"] == 0:
            continue
        tfc, ttq = await M.time_to_averages(db, principal.provider_id, [b], frm, to)
        out_rows.append(
            BrandSplitRow(
                brand=b,
                funnel=M.to_funnel(agg),
                quality=M.to_quality(agg),
                rates=M.to_rates(agg, tfc, ttq),
            )
        )
    return BrandSplitResponse(rows=out_rows)


@router.get("/quality", response_model=QualityMetrics)
async def quality(
    brand: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> QualityMetrics:
    frm, to = _range(date_from, date_to)
    agg = await M.sum_rollups(db, principal.provider_id, _brands(brand), frm, to)
    return M.to_quality(agg)


# --- per-lead table ---
def _leads_query(principal: Principal, brand, stage, date_from, date_to, q, all_time=False):
    """Build the scoped leads select (joined to source for display name).

    When `all_time` is True the created_at date window is skipped entirely, so
    the table returns every lead the provider has (the "All leads" filter).
    """
    stmt = (
        select(MisLead, ProviderSource.source_name)
        .outerjoin(
            ProviderSource,
            (ProviderSource.brand == MisLead.brand)
            & (ProviderSource.crm_source_id == MisLead.crm_source_id),
        )
        .where(MisLead.provider_id == principal.provider_id)  # hard scope
    )
    if not all_time:
        frm, to = _range(date_from, date_to)
        start = datetime.combine(frm, time.min, tzinfo=timezone.utc)
        end = datetime.combine(to, time.max, tzinfo=timezone.utc)
        stmt = stmt.where(
            MisLead.created_at >= start, MisLead.created_at <= end
        )
    if brand and brand.lower() != "both":
        stmt = stmt.where(MisLead.brand == Brand(brand.lower()))
    if stage:
        stmt = stmt.where(MisLead.canonical_stage == CanonicalStage(stage))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            (MisLead.full_name.ilike(like)) | (MisLead.phone.ilike(like))
        )
    return stmt


@router.get("/leads", response_model=Page[LeadOut])
async def leads(
    brand: str | None = None,
    stage: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    q: str | None = None,
    all_time: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> Page[LeadOut]:
    base = _leads_query(principal, brand, stage, date_from, date_to, q, all_time)

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar() or 0

    rows = await db.execute(
        base.order_by(MisLead.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        LeadOut(
            id=lead.id,
            serial_no=lead.serial_no,
            full_name=lead.full_name,
            phone=lead.phone,
            brand=lead.brand,
            source_name=source_name,
            canonical_stage=lead.canonical_stage,
            is_invalid=lead.is_invalid,
            is_duplicate=lead.is_duplicate,
            created_at=lead.created_at,
        )
        for lead, source_name in rows.all()
    ]
    return Page(items=items, total=int(total), page=page, page_size=page_size)


@router.get("/leads/export")
async def leads_export(
    brand: str | None = None,
    stage: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    q: str | None = None,
    all_time: bool = Query(False),
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    base = _leads_query(principal, brand, stage, date_from, date_to, q, all_time)
    rows = await db.execute(base.order_by(MisLead.created_at.desc()).limit(50000))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["serial_no", "full_name", "phone", "brand", "source", "stage",
         "is_invalid", "is_duplicate", "created_at"]
    )
    for lead, source_name in rows.all():
        writer.writerow(
            [
                lead.serial_no or "",
                lead.full_name or "",
                lead.phone or "",
                lead.brand.value,
                source_name or "",
                lead.canonical_stage.value,
                lead.is_invalid,
                lead.is_duplicate,
                lead.created_at.isoformat() if lead.created_at else "",
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )
