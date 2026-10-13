"""Partner payout (Payout page) schemas. Money is Decimal, serialised as a
2-decimal string — never a float."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer

Money = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.2f}", return_type=str)]
OptMoney = Annotated[
    Decimal | None,
    PlainSerializer(lambda v: None if v is None else f"{v:.2f}", return_type=str | None),
]
# A percent rate exactly as the CRM stores it, at least 2 decimals:
# 0.6 -> "0.60", 0.625 -> "0.625" (never rounded to 0.62).
OptRate = Annotated[
    Decimal | None,
    PlainSerializer(lambda v: None if v is None else format(v, "f"), return_type=str | None),
]


class PayoutSummary(BaseModel):
    students: int = 0
    loan_total: Money = Decimal("0")
    earned: Money = Decimal("0")
    paid: Money = Decimal("0")
    pending: Money = Decimal("0")
    # Paid beyond what was earned (pending can't go below 0, so it shows here).
    overpaid: Money = Decimal("0")
    # Net position, earned - paid (= pending - overpaid): + FMC still owes the
    # partner this much; - FMC has paid this much extra.
    balance: Money = Decimal("0")
    # Where the earned money stands (the Payout page pie); the 5 slices add up
    # to earned. Overpaid is shown apart. Paid - overpaid = cash_transferred + tds;
    # pending = invoice_not_raised + invoice_raised + invoice_overdue.
    cash_transferred: Money = Decimal("0")  # paid (up to earned) minus TDS
    tds: Money = Decimal("0")  # tax deducted at source from what was paid
    invoice_not_raised: Money = Decimal("0")  # pending, partner hasn't invoiced it yet
    invoice_raised: Money = Decimal("0")  # pending on an open invoice, not yet due
    invoice_overdue: Money = Decimal("0")  # pending on an open invoice past its due date


class PayoutItem(BaseModel):
    serial_no: int | None
    full_name: str | None
    bank_name: str | None
    loan_amount: OptMoney  # sanctioned by this lender
    pf_paid_on: date | None  # information only; no longer the earning date
    disbursed_total: Money  # what the bank has released so far: the payout basis
    payout_basis: str  # 'rate' | 'agreed'
    payout_rate: OptRate  # percent, e.g. "0.60"; null when basis = 'agreed'
    earned: Money
    paid: Money
    pending: Money
    overpaid: Money = Decimal("0")
    balance: Money = Decimal("0")  # earned - paid: + still to pay, - paid extra
    first_disbursed_on: date | None = None  # bank's first release
    last_disbursed_on: date | None = None  # latest release: the row's date
    tds: Money = Decimal("0")
    net_paid: Money = Decimal("0")  # paid minus TDS
    paid_on: date | None = None  # FMC's latest payment to the partner
    # The student's most recent partner invoice (one student can be on several).
    invoice_number: str | None = None
    invoice_amount: OptMoney = None
    invoice_date: date | None = None
    invoice_due_date: date | None = None
    invoice_status: str | None = None  # 'raised' | 'paid' | 'cancelled'
    invoice_state: str = "not_raised"  # bucket of this row's pending: not_raised | raised | overdue


class PayoutsResponse(BaseModel):
    brand_supported: bool
    summary: PayoutSummary
    items: list[PayoutItem]
    page: int
    page_size: int
    total: int
    data_as_of: str | None = None


class UnmappedPayoutItem(PayoutItem):
    """A payout on a CRM source no provider is mapped to (admin only)."""

    crm_source_id: str | None
    source_name: str | None


class UnmappedPayoutsResponse(BaseModel):
    summary: PayoutSummary
    items: list[UnmappedPayoutItem]
    total: int
