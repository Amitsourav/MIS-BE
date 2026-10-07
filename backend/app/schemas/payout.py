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


class PayoutSummary(BaseModel):
    students: int = 0
    loan_total: Money = Decimal("0")
    earned: Money = Decimal("0")
    paid: Money = Decimal("0")
    pending: Money = Decimal("0")


class PayoutItem(BaseModel):
    serial_no: int | None
    full_name: str | None
    bank_name: str | None
    loan_amount: OptMoney  # sanctioned by this lender
    pf_paid_on: date | None  # the date the payout is earned
    disbursed_total: Money  # information only
    payout_basis: str  # 'rate' | 'agreed'
    payout_rate: OptMoney  # percent, e.g. "0.60"; null when basis = 'agreed'
    earned: Money
    paid: Money
    pending: Money


class PayoutsResponse(BaseModel):
    brand_supported: bool
    summary: PayoutSummary
    items: list[PayoutItem]
    page: int
    page_size: int
    total: int
    data_as_of: str | None = None
