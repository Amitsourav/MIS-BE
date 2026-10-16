"""Per-lead table schemas."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import CanonicalStage
from app.schemas.payout import OptMoney, PayoutItem


class LeadTrancheOut(BaseModel):
    """One release of money by the lender on this file."""

    tranche_no: int | None
    amount: OptMoney = None
    disbursed_on: date | None = None


class LeadLenderOut(BaseModel):
    """A lender the student's file reached login with. `bank` is the real
    bank only ("PNB", never the "UC PNB" route); None while the file sits
    on an aggregator."""

    bank: str | None
    status: str  # loan_login | sanctioned | pf_paid | disbursed | lost
    status_label: str  # "Logged in" | "Sanctioned" | "PF Paid" | "Disbursed" | "Declined"
    amount: OptMoney = None
    # 'sanctioned' = the lender's sanction; 'applied' = the amount asked for
    # (before a sanction); None when neither is known.
    amount_kind: str | None = None
    application_no: str | None = None
    tranches: list[LeadTrancheOut] = []  # every release so far, tranche 1 first


class LeadOut(BaseModel):
    """Fields exposed to the provider who supplied the lead.

    PII the provider originated (name/phone/stage) is allowed. FMC's own
    serial number, the brand and the CRM source name are NOT shown; the
    partner gets their own running number (`lead_no`) instead. Internal
    notes, transcripts, custom_fields are never included.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_no: int | None  # the partner's own 1, 2, 3... in arrival order
    full_name: str | None
    phone: str | None
    canonical_stage: CanonicalStage  # MIS funnel bucket (for charts)
    crm_stage: str | None = None  # the lead's current CRM stage, as stored
    crm_stage_label: str | None = None  # the CRM's own name for it, e.g. "PF Paid"
    is_invalid: bool
    is_duplicate: bool
    created_at: datetime | None
    counsellor_name: str | None = None  # the FMC counsellor looking after the student
    lenders: list[LeadLenderOut] = []  # empty until the file is logged in with a lender


class StageEventOut(BaseModel):
    """One move of the lead to a CRM stage, from the CRM's stage log."""

    stage: str  # as stored, e.g. "pf_paid"
    label: str  # the CRM's own name, e.g. "PF Paid"
    at: datetime


class LeadDetailOut(LeadOut):
    """The lead page: the Leads row plus how it moved through the CRM and
    the partner's payout on each lender file (FMC only; [] otherwise)."""

    contacted_at: datetime | None = None
    qualified_at: datetime | None = None
    converted_at: datetime | None = None
    stage_history: list[StageEventOut] = []  # oldest first
    payouts: list[PayoutItem] = []


class CrmStageCount(BaseModel):
    """One CRM stage in the provider's Leads filter, in CRM pipeline order."""

    value: str
    label: str
    count: int
