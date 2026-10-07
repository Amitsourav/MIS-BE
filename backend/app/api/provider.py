"""Provider-facing endpoints (/me/*, /provider/payouts). Every number is read
live from the CRM on each request, scoped to the caller's mapped CRM sources —
which come ONLY from the verified JWT via `require_provider`."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import Principal, require_provider
from app.models.enums import Brand, CanonicalStage
from app.schemas.common import Page
from app.schemas.lead import LeadOut
from app.schemas.metrics import (
    BrandSplitResponse,
    BrandSplitRow,
    OverviewResponse,
    QualityMetrics,
    TrendsResponse,
)
from app.schemas.payout import PayoutsResponse
from app.services import live
from app.services import metrics as M
from app.services import payouts as P
from app.services.scorecard import compute_scorecard, load_targets

router = APIRouter(prefix="/me", tags=["provider"])

_DEFAULT_RANGE_DAYS = 30
_EXPORT_LIMIT = 50000


# --- shared parsing helpers ---
def _brands(brand: str | None) -> list[Brand]:
    if brand is None or brand.lower() == "both":
        return list(Brand)
    try:
        return [Brand(brand.lower())]
    except ValueError:
        raise HTTPException(422, "brand must be 'fmc', 'av' or 'both'")


def _range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    to = date_to or today
    frm = date_from or (to - timedelta(days=_DEFAULT_RANGE_DAYS))
    return frm, to


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _facts(
    principal: Principal, brand: str | None, frm: date | None, to: date | None
) -> list[live.LeadFact]:
    """Live facts for the caller's own company, if the brand filter includes it."""
    if principal.brand is None or principal.brand not in _brands(brand):
        return []
    return await live.lead_facts(principal.brand, principal.source_ids, frm, to)


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    brand: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> OverviewResponse:
    frm, to = _range(date_from, date_to)
    facts = await _facts(principal, brand, frm, to)
    agg = M.aggregate(facts)
    tfc, ttq = M.time_to_averages(facts)

    targets = await load_targets(db, principal.brand)  # vendor's own company targets
    scorecard = compute_scorecard(
        {
            "validity": M.safe_div(agg["valid"], agg["delivered"]),
            "qualification_rate": M.safe_div(agg["qualified"], agg["valid"]),
            "conversion_rate": M.safe_div(agg["converted"], agg["valid"]),
            "volume_reliability": M.volume_reliability(facts, frm, to),
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
        data_as_of=_now_iso(),  # live
    )


@router.get("/trends", response_model=TrendsResponse)
async def trends(
    brand: str | None = None,
    granularity: str = Query("day", pattern="^(day|week)$"),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    principal: Principal = Depends(require_provider),
) -> TrendsResponse:
    frm, to = _range(date_from, date_to)
    facts = await _facts(principal, brand, frm, to)
    return TrendsResponse(granularity=granularity, points=M.trend_points(facts, granularity))


@router.get("/brand-split", response_model=BrandSplitResponse)
async def brand_split(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    principal: Principal = Depends(require_provider),
) -> BrandSplitResponse:
    frm, to = _range(date_from, date_to)
    facts = await _facts(principal, None, frm, to)
    rows = []
    if facts:  # each vendor belongs to exactly one company
        agg = M.aggregate(facts)
        tfc, ttq = M.time_to_averages(facts)
        rows.append(
            BrandSplitRow(
                brand=principal.brand,
                funnel=M.to_funnel(agg),
                quality=M.to_quality(agg),
                rates=M.to_rates(agg, tfc, ttq),
            )
        )
    return BrandSplitResponse(rows=rows)


@router.get("/quality", response_model=QualityMetrics)
async def quality(
    brand: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    principal: Principal = Depends(require_provider),
) -> QualityMetrics:
    frm, to = _range(date_from, date_to)
    return M.to_quality(M.aggregate(await _facts(principal, brand, frm, to)))


# --- per-lead table ---
async def _filtered_leads(
    principal: Principal, brand, stage, date_from, date_to, q, all_time
) -> list[live.LeadFact]:
    """The caller's leads after every filter, newest first.

    When `all_time` is True the created_at window is skipped entirely, so the
    table returns every lead the provider has (the "All leads" filter).
    """
    if stage is not None:
        try:
            stage = CanonicalStage(stage)
        except ValueError:
            raise HTTPException(422, "Unknown stage")
    frm, to = (None, None) if all_time else _range(date_from, date_to)
    facts = await _facts(principal, brand, frm, to)
    if stage is not None:
        facts = [f for f in facts if f.canonical_stage == stage]
    if q and q.strip():
        needle = q.strip().lower()
        facts = [
            f for f in facts
            if needle in (f.full_name or "").lower() or needle in (f.phone or "")
        ]
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    facts.sort(key=lambda f: (f.created_at or epoch, str(f.crm_lead_id)), reverse=True)
    return facts


def _lead_out(principal: Principal, f: live.LeadFact) -> LeadOut:
    return LeadOut(
        id=f.crm_lead_id,
        serial_no=f.serial_no,
        full_name=f.full_name,
        phone=f.phone,
        brand=f.brand,
        source_name=principal.source_names.get(f.crm_source_id),
        canonical_stage=f.canonical_stage,
        is_invalid=f.is_invalid,
        is_duplicate=f.is_duplicate,
        created_at=f.created_at,
    )


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
) -> Page[LeadOut]:
    facts = await _filtered_leads(principal, brand, stage, date_from, date_to, q, all_time)
    window = facts[(page - 1) * page_size : page * page_size]
    return Page(
        items=[_lead_out(principal, f) for f in window],
        total=len(facts),
        page=page,
        page_size=page_size,
    )


@router.get("/leads/export")
async def leads_export(
    brand: str | None = None,
    stage: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    q: str | None = None,
    all_time: bool = Query(False),
    principal: Principal = Depends(require_provider),
) -> StreamingResponse:
    facts = await _filtered_leads(principal, brand, stage, date_from, date_to, q, all_time)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["serial_no", "full_name", "phone", "brand", "source", "stage",
         "is_invalid", "is_duplicate", "created_at"]
    )
    for f in facts[:_EXPORT_LIMIT]:
        writer.writerow(
            [
                f.serial_no or "",
                f.full_name or "",
                f.phone or "",
                f.brand.value,
                principal.source_names.get(f.crm_source_id) or "",
                f.canonical_stage.value,
                f.is_invalid,
                f.is_duplicate,
                f.created_at.isoformat() if f.created_at else "",
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


# --- partner payouts (Payout page) ---
# Mounted under /provider (not /me) to match the Payout page contract. Same
# scoping rule: sources come only from the JWT via `require_provider`.
payouts_router = APIRouter(prefix="/provider", tags=["provider"])


@payouts_router.get("/payouts", response_model=PayoutsResponse)
async def payouts(
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_provider),
) -> PayoutsResponse:
    """Earnings on the caller's students who have paid PF to a lender. Dates
    filter on pf_paid_on; omitted = all time."""
    return await P.build_payouts_page(
        provider_brand=principal.brand,
        source_ids=principal.source_ids,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@payouts_router.get("/payouts/export")
async def payouts_export(
    date_from: date | None = None,
    date_to: date | None = None,
    principal: Principal = Depends(require_provider),
) -> StreamingResponse:
    items = await P.payout_items_for_export(
        provider_brand=principal.brand,
        source_ids=principal.source_ids,
        date_from=date_from,
        date_to=date_to,
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["serial_no", "full_name", "bank_name", "loan_amount", "pf_paid_on",
         "disbursed_total", "payout_basis", "payout_rate", "earned", "paid",
         "pending"]
    )
    for it in items:
        row = it.model_dump(mode="json")
        writer.writerow(
            [
                row["serial_no"] or "",
                row["full_name"] or "",
                row["bank_name"] or "",
                row["loan_amount"] or "",
                row["pf_paid_on"] or "",
                row["disbursed_total"],
                row["payout_basis"],
                row["payout_rate"] or "",
                row["earned"],
                row["paid"],
                row["pending"],
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=payouts.csv"},
    )
