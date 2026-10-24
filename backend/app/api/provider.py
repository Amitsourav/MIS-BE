"""Provider-facing endpoints (/me/*, /provider/payouts). Every number is read
live from the CRM on each request, scoped to the caller's mapped CRM sources —
which come ONLY from the verified JWT via `require_provider`."""
from __future__ import annotations

import asyncio
import uuid
from zoneinfo import ZoneInfo
from datetime import date, datetime, timedelta, timezone

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.csvsafe import csv_response, safe_text
from app.database import get_db
from app.deps import Principal, require_provider
from app.models.enums import Brand, CanonicalStage
from app.schemas.common import Page
from app.crm.stage_map import crm_stage_label, crm_stage_order, crm_stage_value
from app.config import settings
from app.crm import chat, invoices, reader
from app.models import BillingProfile, LeadChatRead, ProviderUser
from app.schemas.billing import BillingProfileIn, BillingProfileOut
from app.schemas.invoice import EligibleStudentOut, InvoiceEligibleOut, InvoiceLineIn, InvoiceOut
from app.schemas.chat import ChatLatestOut, ChatMessageOut, ChatPostIn, ChatThreadOut
from app.schemas.lead import CrmStageCount, LeadDetailOut, LeadOut, StageEventOut
from app.schemas.metrics import (
    BrandSplitResponse,
    BrandSplitRow,
    OverviewResponse,
    QualityMetrics,
    TrendsResponse,
)
from app.schemas.charts import LeadFunnelResponse, PayoutMonthlyResponse
from app.schemas.payout import PayoutsResponse
from app.services import billing, charts, live, partner_invoice
from app.services import metrics as M
from app.services import payouts as P
from app.services.lead_details import LeadDetails, lead_details
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


def _window(
    date_from: date | None, date_to: date | None, all_time: bool
) -> tuple[date | None, date | None]:
    """The created_at window to read: (None, None) for "All time"."""
    return (None, None) if all_time else _range(date_from, date_to)


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
    all_time: bool = Query(False),
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> OverviewResponse:
    frm, to = _window(date_from, date_to, all_time)
    facts = await _facts(principal, brand, frm, to)
    if all_time:
        # Report the span actually covered: first lead (or today) .. today.
        to = datetime.now(timezone.utc).date()
        frm = min((f.cohort_day for f in facts if f.cohort_day), default=to)
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
    all_time: bool = Query(False),
    principal: Principal = Depends(require_provider),
) -> TrendsResponse:
    frm, to = _window(date_from, date_to, all_time)
    facts = await _facts(principal, brand, frm, to)
    return TrendsResponse(granularity=granularity, points=M.trend_points(facts, granularity))


@router.get("/brand-split", response_model=BrandSplitResponse)
async def brand_split(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    all_time: bool = Query(False),
    principal: Principal = Depends(require_provider),
) -> BrandSplitResponse:
    frm, to = _window(date_from, date_to, all_time)
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
    all_time: bool = Query(False),
    principal: Principal = Depends(require_provider),
) -> QualityMetrics:
    frm, to = _window(date_from, date_to, all_time)
    return M.to_quality(M.aggregate(await _facts(principal, brand, frm, to)))


# --- per-lead table ---
async def _filtered_leads(
    principal: Principal, brand, stage, date_from, date_to, q, all_time, crm_stage=None
) -> list[live.LeadFact]:
    """The caller's leads after every filter, newest first.

    `stage` filters on the MIS funnel bucket; `crm_stage` on the exact CRM
    stage (e.g. "pf_paid"), matching what the CRM team sees.

    When `all_time` is True the created_at window is skipped entirely, so the
    table returns every lead the provider has (the "All leads" filter).
    """
    if stage is not None:
        try:
            stage = CanonicalStage(stage)
        except ValueError:
            raise HTTPException(422, "Unknown stage")
    frm, to = _window(date_from, date_to, all_time)
    facts = await _facts(principal, brand, frm, to)
    if stage is not None:
        facts = [f for f in facts if f.canonical_stage == stage]
    wanted = crm_stage_value(crm_stage)
    if wanted is not None:
        facts = [f for f in facts if crm_stage_value(f.raw_stage) == wanted]
    if q and q.strip():
        needle = q.strip().lower()
        facts = [
            f for f in facts
            if needle in (f.full_name or "").lower() or needle in (f.phone or "")
            or needle == str(f.lead_no)  # their lead number, typed exactly
        ]
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    facts.sort(key=lambda f: (f.created_at or epoch, str(f.crm_lead_id)), reverse=True)
    return facts


def _lead_out(f: live.LeadFact, details: LeadDetails | None) -> LeadOut:
    return LeadOut(
        id=f.crm_lead_id,
        lead_no=f.lead_no,
        full_name=f.full_name,
        phone=f.phone,
        canonical_stage=f.canonical_stage,
        crm_stage=crm_stage_value(f.raw_stage),
        crm_stage_label=crm_stage_label(f.brand, f.raw_stage),
        is_invalid=f.is_invalid,
        is_duplicate=f.is_duplicate,
        created_at=f.created_at,
        counsellor_name=details.counsellor_name if details else None,
        lenders=details.lenders if details else [],
    )


@router.get("/leads", response_model=Page[LeadOut])
async def leads(
    brand: str | None = None,
    stage: str | None = None,
    crm_stage: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    q: str | None = None,
    all_time: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_provider),
) -> Page[LeadOut]:
    # The leads and their details come from two CRM queries; run them at
    # once (details for all the caller's leads, then pick this page's).
    facts, details = await asyncio.gather(
        _filtered_leads(principal, brand, stage, date_from, date_to, q, all_time, crm_stage),
        lead_details(principal.brand, principal.source_ids),
    )
    window = facts[(page - 1) * page_size : page * page_size]
    return Page(
        items=[_lead_out(f, details.get(f.crm_lead_id)) for f in window],
        total=len(facts),
        page=page,
        page_size=page_size,
    )


@router.get("/lead-funnel", response_model=LeadFunnelResponse)
async def lead_funnel(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    all_time: bool = Query(False),
    principal: Principal = Depends(require_provider),
) -> LeadFunnelResponse:
    """All leads -> Login -> Sanction -> PF paid -> Disbursed for leads delivered
    in the window: how many reached each step. FMC only."""
    frm, to = _window(date_from, date_to, all_time)
    return await charts.lead_funnel(principal.brand, principal.source_ids, frm, to)


@router.get("/stages", response_model=list[CrmStageCount])
async def stages(
    brand: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    all_time: bool = Query(False),
    principal: Principal = Depends(require_provider),
) -> list[CrmStageCount]:
    """The CRM stages the caller's leads are in right now, with counts, in the
    CRM's pipeline order — for the Leads stage filter."""
    frm, to = _window(date_from, date_to, all_time)
    counts: dict[str, int] = {}
    for f in await _facts(principal, brand, frm, to):
        value = crm_stage_value(f.raw_stage)
        if value is not None:
            counts[value] = counts.get(value, 0) + 1
    return [
        CrmStageCount(value=v, label=crm_stage_label(principal.brand, v), count=n)
        for v, n in sorted(counts.items(), key=lambda kv: crm_stage_order(principal.brand, kv[0]))
    ]


@router.get("/leads/export")
async def leads_export(
    brand: str | None = None,
    stage: str | None = None,
    crm_stage: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    q: str | None = None,
    all_time: bool = Query(False),
    principal: Principal = Depends(require_provider),
) -> StreamingResponse:
    facts = (await _filtered_leads(
        principal, brand, stage, date_from, date_to, q, all_time, crm_stage
    ))[:_EXPORT_LIMIT]
    details = await lead_details(principal.brand, principal.source_ids)

    def lenders_col(d: LeadDetails | None, attr: str) -> str:
        # One cell per field; several lenders are joined in the same order.
        if d is None:
            return ""
        return "; ".join(
            "" if getattr(l, attr) is None else str(getattr(l, attr))
            for l in d.lenders
        )

    rows = []
    for f in facts:
        d = details.get(f.crm_lead_id)
        rows.append([
            f.lead_no or "",
            safe_text(f.full_name or ""),
            f.phone or "",
            crm_stage_label(f.brand, f.raw_stage) or "",
            f.canonical_stage.value,
            f.is_invalid,
            f.is_duplicate,
            f.created_at.isoformat() if f.created_at else "",
            safe_text((d.counsellor_name if d else None) or ""),
            safe_text("; ".join(l.bank or "—" for l in d.lenders) if d else ""),
            lenders_col(d, "status_label"),
            "; ".join("" if l.amount is None else f"{l.amount:.2f}" for l in d.lenders) if d else "",
            safe_text(lenders_col(d, "application_no")),
        ])
    return csv_response(
        ["lead_no", "full_name", "phone", "stage", "funnel_stage", "is_invalid",
         "is_duplicate", "created_at", "counsellor", "lender", "lender_status",
         "loan_amount", "application_no"],
        rows,
        "leads.csv",
    )


# Declared after /leads/export so "export" is never read as a lead id.
@router.get("/leads/{lead_id}", response_model=LeadDetailOut)
async def lead_detail(
    lead_id: uuid.UUID,
    principal: Principal = Depends(require_provider),
) -> LeadDetailOut:
    """One lead's page. A lead that isn't the caller's (or doesn't exist)
    is a plain 404, so ids of other partners' leads reveal nothing."""
    if principal.brand is None:
        raise HTTPException(404, "Lead not found")
    # All four are scoped to the caller's sources, so they can run at once;
    # the lead row alone decides 404.
    facts, details, history, payouts = await asyncio.gather(
        live.lead_facts(principal.brand, principal.source_ids, None, None, lead_id),
        lead_details(principal.brand, principal.source_ids, [lead_id]),
        reader.stage_history(principal.brand, principal.source_ids, lead_id),
        P.lead_payout_items(principal.brand, principal.source_ids, lead_id),
    )
    if not facts:
        raise HTTPException(404, "Lead not found")
    f = facts[0]
    return LeadDetailOut(
        **_lead_out(f, details.get(lead_id)).model_dump(),
        contacted_at=f.contacted_at,
        qualified_at=f.qualified_at,
        converted_at=f.converted_at,
        stage_history=[
            StageEventOut(
                stage=crm_stage_value(h["to_stage"]),
                label=crm_stage_label(principal.brand, h["to_stage"]),
                at=h["at"],
            )
            for h in history
            if crm_stage_value(h.get("to_stage")) is not None and h.get("at") is not None
        ],
        payouts=payouts,
    )


# --- partner chat on a lead (messages live in the CRM; app/crm/chat.py) ---
async def _mark_read(db: AsyncSession, principal: Principal, lead_id: uuid.UUID) -> None:
    user_id = uuid.UUID(principal.user_id)
    now = datetime.now(timezone.utc)
    row = (await db.execute(select(LeadChatRead).where(
        LeadChatRead.provider_user_id == user_id, LeadChatRead.crm_lead_id == lead_id,
    ))).scalar_one_or_none()
    if row is None:
        db.add(LeadChatRead(provider_user_id=user_id, crm_lead_id=lead_id, last_read_at=now))
    else:
        row.last_read_at = now
    try:
        await db.commit()
    except IntegrityError:  # the same login opened it twice at once: either wins
        await db.rollback()


def _chat_brand(principal: Principal) -> Brand:
    if principal.brand is None:
        raise HTTPException(404, "Lead not found")
    return principal.brand


@router.get("/leads/{lead_id}/messages", response_model=ChatThreadOut)
async def lead_messages(
    lead_id: uuid.UUID,
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> ChatThreadOut:
    """The lead's chat with its counsellor and the company's team, oldest
    first. Opening it marks staff replies as read for this login."""
    out = ChatThreadOut.model_validate(await chat.thread(
        _chat_brand(principal), principal.is_demo, principal.source_ids, lead_id
    ))
    await _mark_read(db, principal, lead_id)
    return out


@router.post("/leads/{lead_id}/messages", response_model=ChatMessageOut, status_code=201)
async def post_lead_message(
    lead_id: uuid.UUID,
    body: ChatPostIn,
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> ChatMessageOut:
    """Message the counsellor and/or admins about this lead. Tag
    "counsellor", "admin" or both; no tag goes to the counsellor."""
    text = body.body.strip()
    if not text:
        raise HTTPException(422, "Message is empty")
    user = await db.get(ProviderUser, uuid.UUID(principal.user_id))
    out = ChatMessageOut.model_validate(await chat.post(
        _chat_brand(principal), principal.is_demo, principal.source_ids, lead_id,
        message_id=body.client_message_id or uuid.uuid4(),
        partner_name=principal.provider_name or "Partner",
        partner_user_email=user.email if user else None,
        body=text,
        tags=list(dict.fromkeys(body.tags)),
    ))
    await _mark_read(db, principal, lead_id)
    return out


@router.get("/messages/latest", response_model=list[ChatLatestOut])
async def messages_latest(
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> list[ChatLatestOut]:
    """Leads with chat messages, and whether a staff reply is unread — for
    the "new reply" dots on the Leads table."""
    if principal.brand is None:
        return []
    rows = await chat.latest(principal.brand, principal.is_demo, principal.source_ids)
    read = dict((await db.execute(
        select(LeadChatRead.crm_lead_id, LeadChatRead.last_read_at).where(
            LeadChatRead.provider_user_id == uuid.UUID(principal.user_id)
        )
    )).all())
    out = []
    for r in rows:
        item = ChatLatestOut(**r, unread=False)
        staff, seen = item.last_staff_message_at, read.get(item.lead_id)
        if seen is not None and seen.tzinfo is None:  # SQLite drops tz; stored UTC
            seen = seen.replace(tzinfo=timezone.utc)
        item.unread = staff is not None and (seen is None or staff > seen)
        out.append(item)
    return out


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
    filter on last_disbursed_on (when the bank last released money); omitted
    = all time."""
    return await P.build_payouts_page(
        provider_brand=principal.brand,
        source_ids=principal.source_ids,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@payouts_router.get("/payouts/monthly", response_model=PayoutMonthlyResponse)
async def payouts_monthly(
    date_from: date | None = None,
    date_to: date | None = None,
    principal: Principal = Depends(require_provider),
) -> PayoutMonthlyResponse:
    """Per month: revenue (rate x sanctioned loan, in the sanction month) and
    disbursement (earned on money released, in each release's month)."""
    return await charts.payout_monthly(
        principal.brand, principal.source_ids, date_from, date_to
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
    rows = []
    for it in items:
        row = it.model_dump(mode="json")
        rows.append(
            [
                row["lead_no"] or "",
                safe_text(row["full_name"] or ""),
                safe_text(row["bank_name"] or ""),
                row["loan_amount"] or "",
                row["pf_paid_on"] or "",
                row["disbursed_total"],
                row["payout_basis"],
                row["payout_rate"] or "",
                row["earned"],
                row["paid"],
                row["pending"],
                row["overpaid"],
                row["balance"],
                row["first_disbursed_on"] or "",
                row["last_disbursed_on"] or "",
                row["tds"],
                row["net_paid"],
                row["paid_on"] or "",
                safe_text(row["invoice_number"] or ""),
                row["invoice_amount"] or "",
                row["invoice_date"] or "",
                row["invoice_due_date"] or "",
                row["invoice_status"] or "",
                row["earned_with_gst"],
                row["pending_with_gst"],
                row["balance_with_gst"],
                row["paid_with_gst"],
            ]
        )
    return csv_response(
        ["lead_no", "full_name", "bank_name", "loan_amount", "pf_paid_on",
         "disbursed_total", "payout_basis", "payout_rate", "earned", "paid",
         "pending", "overpaid", "balance", "first_disbursed_on", "last_disbursed_on",
         "tds", "net_paid", "paid_on", "invoice_number", "invoice_amount", "invoice_date",
         "invoice_due_date", "invoice_status", "earned_with_gst", "pending_with_gst",
         "balance_with_gst", "paid_with_gst"],
        rows,
        "payouts.csv",
    )


# --- partner invoices (stored in the CRM; app/crm/invoices.py) ---
# A partner bills FMC for their payouts: a one-time billing setup (with or
# without GST), then pick students, number and date. The MIS makes the PDF.
# FMC only (Admitverse has no payouts).
_IST = ZoneInfo("Asia/Kolkata")
_NOT_FMC = "Payouts are tracked for FundMyCampus loans"


def _invoice_brand(principal: Principal) -> Brand:
    if principal.brand != Brand.FMC:
        raise HTTPException(404, _NOT_FMC)
    return principal.brand


def _invoice_out(row: dict, numbers: dict[uuid.UUID, int]) -> InvoiceOut:
    out = InvoiceOut.model_validate(row)
    for line in out.lines:
        line.lead_no = numbers.get(line.lead_id) if line.lead_id else None
    return out


@payouts_router.get("/invoices/eligible", response_model=InvoiceEligibleOut)
async def invoice_eligible(
    principal: Principal = Depends(require_provider),
) -> InvoiceEligibleOut:
    """Students whose payout can still be invoiced, with what's left on each
    (before GST), and the GST rate the invoice will carry."""
    gst = settings.payout_gst_rate
    if principal.brand != Brand.FMC:
        return InvoiceEligibleOut(gst_rate=gst, items=[])
    rows, numbers = await asyncio.gather(
        invoices.eligible(principal.brand, principal.is_demo, principal.source_ids),
        reader.lead_numbers(principal.brand, principal.source_ids),
    )
    items = [EligibleStudentOut.model_validate(r) for r in rows]
    for it in items:
        it.lead_no = numbers.get(it.lead_id)
    return InvoiceEligibleOut(gst_rate=gst, items=items)


@payouts_router.get("/invoices", response_model=list[InvoiceOut])
async def invoice_list(
    principal: Principal = Depends(require_provider),
) -> list[InvoiceOut]:
    """The partner's invoices, newest first: raised here or entered by FMC."""
    if principal.brand != Brand.FMC:
        return []
    rows, numbers = await asyncio.gather(
        invoices.list_invoices(principal.brand, principal.is_demo, principal.source_ids),
        reader.lead_numbers(principal.brand, principal.source_ids),
    )
    return [_invoice_out(r, numbers) for r in rows]


def _invoice_form(invoice_number: str, invoice_date: date, lines: str, *, need_number: bool):
    number = invoice_number.strip()
    if need_number and not number:
        raise HTTPException(422, "Enter your invoice number.")
    if len(number) > 60:
        raise HTTPException(422, "The invoice number can be at most 60 characters.")
    if invoice_date > datetime.now(_IST).date():
        raise HTTPException(422, "The invoice date can't be in the future.")
    try:
        picked = TypeAdapter(list[InvoiceLineIn]).validate_python(json.loads(lines))
    except (ValueError, ValidationError):
        raise HTTPException(422, "The chosen students couldn't be read. Reload and try again.")
    if not picked:
        raise HTTPException(422, "Pick at least one student.")
    if len({l.lead_bank_id for l in picked}) != len(picked):
        raise HTTPException(422, "The same student is on this invoice twice.")
    return number, picked


async def _profile_or_422(db: AsyncSession, principal: Principal) -> BillingProfile:
    profile = await db.get(BillingProfile, principal.provider_id)
    if profile is None:
        raise HTTPException(422, "Set up your billing details first.")
    return profile


@payouts_router.post("/invoices/preview")
async def invoice_preview(
    invoice_number: str = Form(""),
    invoice_date: date = Form(...),
    lines: str = Form(...),
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The invoice PDF exactly as it would be sent. Nothing is saved or sent."""
    _invoice_brand(principal)
    number, picked = _invoice_form(invoice_number, invoice_date, lines, need_number=False)
    profile = await _profile_or_422(db, principal)
    prep = await partner_invoice.prepare(
        principal, profile, invoice_number=number, invoice_date=invoice_date, picked=picked
    )
    return Response(prep.pdf, media_type="application/pdf",
                    headers={"Cache-Control": "no-store",
                             "Content-Disposition": 'inline; filename="invoice-preview.pdf"'})


@payouts_router.post("/invoices", response_model=InvoiceOut, status_code=201)
async def invoice_raise(
    invoice_number: str = Form(""),
    invoice_date: date = Form(...),
    lines: str = Form(..., description='JSON: [{"lead_bank_id": "...", "amount": "1234.00"}]; no amount = all that is left'),
    client_invoice_id: uuid.UUID | None = Form(None, description="Send the same id when retrying"),
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> InvoiceOut:
    """Raise an invoice to FMC for the chosen students. The MIS makes the PDF
    from the partner's billing details: payouts + GST when they are
    GST-registered, without GST otherwise. Stored in the CRM as `raised`."""
    brand = _invoice_brand(principal)
    number, picked = _invoice_form(invoice_number, invoice_date, lines, need_number=True)
    profile = await _profile_or_422(db, principal)
    prep = await partner_invoice.prepare(
        principal, profile, invoice_number=number, invoice_date=invoice_date, picked=picked
    )
    user = await db.get(ProviderUser, uuid.UUID(principal.user_id))
    row, numbers = await asyncio.gather(
        invoices.raise_invoice(
            brand, principal.is_demo, principal.source_ids,
            lead_source_id=prep.source_id,
            invoice_id=client_invoice_id or uuid.uuid4(),
            invoice_number=number,
            invoice_date=invoice_date,
            gst_rate=prep.tax.rate,
            partner_name=profile.legal_name,
            partner_user_email=user.email if user else None,
            lines=prep.lines,
            pdf=prep.pdf,
        ),
        reader.lead_numbers(brand, principal.source_ids),
    )
    return _invoice_out(row, numbers)


async def _billing_out(principal: Principal, profile: BillingProfile) -> BillingProfileOut:
    out = BillingProfileOut.model_validate(profile)
    out.state_name = billing.STATES.get(profile.state_code, "")
    out.tax_split = partner_invoice.tax_split(profile)
    try:
        existing = await invoices.list_invoices(principal.brand, principal.is_demo, principal.source_ids)
    except invoices.InvoiceError:
        existing = []  # the suggestion is only a starting point
    out.next_invoice_number = billing.next_invoice_number(
        profile.invoice_prefix, datetime.now(_IST).date(),
        [r.get("invoice_number") for r in existing],
    )
    return out


@payouts_router.get("/billing-profile", response_model=BillingProfileOut | None)
async def billing_profile_get(
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> BillingProfileOut | None:
    """The partner's billing details, or null before the one-time setup."""
    _invoice_brand(principal)
    profile = await db.get(BillingProfile, principal.provider_id)
    return None if profile is None else await _billing_out(principal, profile)


@payouts_router.put("/billing-profile", response_model=BillingProfileOut)
async def billing_profile_put(
    body: BillingProfileIn,
    principal: Principal = Depends(require_provider),
    db: AsyncSession = Depends(get_db),
) -> BillingProfileOut:
    """Set up or change the billing details printed on the partner's invoices.
    With GST: GSTIN (checked), PAN from it, the GSTIN's state. Without GST: PAN."""
    _invoice_brand(principal)
    try:
        clean = billing.check_profile(body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))
    user = await db.get(ProviderUser, uuid.UUID(principal.user_id))
    profile = await db.get(BillingProfile, principal.provider_id)
    if profile is None:
        profile = BillingProfile(provider_id=principal.provider_id, **clean)
        db.add(profile)
    else:
        for k, v in clean.items():
            setattr(profile, k, v)
    profile.updated_by_email = user.email if user else None
    profile.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(profile)
    return await _billing_out(principal, profile)


@payouts_router.get("/invoices/{invoice_id}/pdf")
async def invoice_pdf(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_provider),
) -> Response:
    """The invoice's PDF. 404 when it isn't the caller's or has no PDF."""
    content = await invoices.pdf(
        _invoice_brand(principal), principal.is_demo, principal.source_ids, invoice_id
    )
    return Response(
        content, media_type="application/pdf",
        headers={"Cache-Control": "no-store",
                 "Content-Disposition": f'attachment; filename="invoice-{invoice_id}.pdf"'},
    )
