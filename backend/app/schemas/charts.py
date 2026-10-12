"""Partner charts: lead pipeline funnel and monthly revenue vs disbursement."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.schemas.payout import Money


class FunnelStep(BaseModel):
    key: str  # 'all' | 'logged_in' | 'sanctioned' | 'pf_paid' | 'disbursed'
    label: str
    count: int  # leads that REACHED this step (or went further)


class LeadFunnelResponse(BaseModel):
    brand_supported: bool
    date_from: date | None  # created-date window (None = all time)
    date_to: date | None
    steps: list[FunnelStep]


class MonthlyPayoutPoint(BaseModel):
    month: str  # YYYY-MM
    revenue: Money  # what the partner will earn on loans sanctioned this month
    disbursement: Money  # what they earned on money released this month


class PayoutMonthlyResponse(BaseModel):
    brand_supported: bool
    months: list[MonthlyPayoutPoint]  # continuous, oldest first
    revenue_total: Money
    disbursement_total: Money
    # Loans with no sanction date in the CRM are placed in the month of their
    # first release instead; this many loans were placed that way.
    revenue_dated_by_first_release: int
    # Loans with neither date: counted in revenue_total, in no month.
    revenue_undated: Money
