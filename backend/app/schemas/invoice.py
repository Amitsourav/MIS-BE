"""Partner invoices raised from the MIS (stored in the CRM; app/crm/invoices.py).

Money is a 2-decimal string. Before GST everywhere except `gst_amount`,
`amount` and `net_amount`. Never carries FMC's serial number, the CRM source
name or an aggregator's name (the CRM leaves them out).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.payout import Money


class EligibleStudentOut(BaseModel):
    lead_bank_id: uuid.UUID
    lead_id: uuid.UUID
    lead_no: int | None = None  # the partner's own lead number
    full_name: str | None = None
    bank_name: str | None = None  # the real bank; None while on an aggregator
    released: Money = Decimal("0")  # what the bank has released so far
    payout_due: Money = Decimal("0")
    payout_paid: Money = Decimal("0")
    on_open_invoices: Money = Decimal("0")  # already on a raised, unpaid invoice
    remaining: Money = Decimal("0")  # what can still be invoiced (before GST)


class InvoiceEligibleOut(BaseModel):
    gst_rate: Money  # percent, e.g. "18.00": the rate the invoice will carry
    items: list[EligibleStudentOut] = []


class InvoiceLineIn(BaseModel):
    lead_bank_id: uuid.UUID
    amount: Decimal | None = Field(default=None, gt=0)  # None = all that's left


class InvoiceLineOut(BaseModel):
    lead_id: uuid.UUID | None = None
    lead_no: int | None = None
    full_name: str | None = None
    bank_name: str | None = None
    amount: Money  # before GST


class InvoiceOut(BaseModel):
    id: uuid.UUID
    invoice_number: str
    invoice_date: date
    due_date: date | None = None
    lines_total: Money  # before GST
    gst_rate: Money = Decimal("0")
    gst_amount: Money = Decimal("0")
    amount: Money  # lines_total + gst_amount: what the partner billed
    tds_amount: Money = Decimal("0")
    net_amount: Money  # amount - TDS
    status: str  # raised | paid | cancelled
    paid_on: date | None = None
    raised_via: str = "crm"  # mis = raised by the partner here; crm = entered by FMC
    has_pdf: bool = False
    created_at: datetime | None = None
    lines: list[InvoiceLineOut] = []
