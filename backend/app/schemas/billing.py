"""A partner's billing details for the invoices the MIS makes (FundMyCampus)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BillingProfileIn(BaseModel):
    gst_registered: bool
    legal_name: str = ""
    gstin: str | None = None  # required when gst_registered
    pan: str = ""  # with a GSTIN it may be left out: taken from the GSTIN
    address_line1: str = ""
    address_line2: str | None = None
    city: str = ""
    state_code: str = ""  # 2-digit GST state code
    pincode: str = ""
    email: str = ""
    phone: str = ""
    bank_account_name: str = ""
    bank_account_number: str = ""
    bank_ifsc: str = ""
    bank_name: str = ""
    bank_branch: str | None = None
    invoice_prefix: str = ""
    signatory_name: str | None = None


class BillingProfileOut(BaseModel):
    model_config = {"from_attributes": True}

    gst_registered: bool
    legal_name: str
    gstin: str | None
    pan: str
    address_line1: str
    address_line2: str | None
    city: str
    state_code: str
    state_name: str = ""
    pincode: str
    email: str
    phone: str
    bank_account_name: str
    bank_account_number: str
    bank_ifsc: str
    bank_name: str
    bank_branch: str | None
    invoice_prefix: str
    signatory_name: str | None
    updated_at: datetime | None = None
    # The suggested number for the next invoice dated today (editable).
    next_invoice_number: str = ""
    # How GST will be charged: "cgst_sgst" (same state as FMC), "igst", or "none".
    tax_split: str = "none"
