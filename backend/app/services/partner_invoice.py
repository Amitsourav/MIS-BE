"""Make a partner's invoice from their billing details: the lines, the GST and
the PDF. Shared by "Preview" (nothing is sent) and "Raise" (sent to the CRM)."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import HTTPException

from app.config import settings
from app.crm import invoices, reader
from app.deps import Principal
from app.models.billing import BillingProfile
from app.schemas.invoice import InvoiceLineIn
from app.services.billing import Tax, tax_for
from app.services.invoice_pdf import BillTo, PdfLine, build_invoice_pdf


def bill_to() -> BillTo | None:
    """FundMyCampus as printed under "Bill to" (MIS settings). None = not set."""
    if not settings.fmc_bill_to_name:
        return None
    return BillTo(
        name=settings.fmc_bill_to_name,
        gstin=(settings.fmc_bill_to_gstin or "").strip().upper() or None,
        address_lines=[x.strip() for x in (settings.fmc_bill_to_address or "").split("|") if x.strip()],
        state_code=(settings.fmc_bill_to_state_code or "").strip() or None,
    )


def gst_rate_for(profile: BillingProfile) -> Decimal:
    return settings.payout_gst_rate if profile.gst_registered else Decimal("0")


def tax_split(profile: BillingProfile) -> str:
    to = bill_to()
    if not profile.gst_registered:
        return "none"
    return "cgst_sgst" if to and to.state_code == profile.state_code else "igst"


@dataclass
class Prepared:
    source_id: uuid.UUID
    lines: list[dict]  # sent to the CRM: lead_bank_id + exact amount
    total: Decimal  # before GST
    tax: Tax
    pdf: bytes


async def prepare(
    principal: Principal,
    profile: BillingProfile,
    *,
    invoice_number: str,
    invoice_date: date,
    picked: list[InvoiceLineIn],
) -> Prepared:
    to = bill_to()
    if to is None:
        raise HTTPException(503, "Invoicing isn't set up yet. Please contact FundMyCampus.")
    eligible, numbers = await _gather(principal)
    by_id = {uuid.UUID(str(r["lead_bank_id"])): r for r in eligible}
    rows = []
    for line in picked:
        r = by_id.get(line.lead_bank_id)
        if r is None:
            raise HTTPException(422, "One of these students has nothing left to invoice. Reload and try again.")
        remaining = Decimal(str(r["remaining"]))
        amount = line.amount if line.amount is not None else remaining
        if amount > remaining:
            name = r.get("full_name") or "A student"
            raise HTTPException(422, f"{name}: Rs {amount:.2f} is more than the Rs {remaining:.2f} still to invoice.")
        rows.append((r, amount.quantize(Decimal("0.01"))))

    # The CRM takes one source per invoice.
    sources = {uuid.UUID(str(r["lead_source_id"])) for r, _ in rows if r.get("lead_source_id")}
    if len(sources) > 1:
        raise HTTPException(422, "These students are under different FundMyCampus accounts of yours. Raise a separate invoice for each.")
    source_id = sources.pop() if sources else principal.source_ids[0]

    total = sum((a for _, a in rows), Decimal("0"))
    tax = tax_for(total, gst_rate_for(profile), profile.state_code, to.state_code)
    pdf = build_invoice_pdf(
        profile=profile, bill_to=to, invoice_number=invoice_number or "DRAFT",
        invoice_date=invoice_date,
        lines=[
            PdfLine(
                lead_no=numbers.get(uuid.UUID(str(r["lead_id"]))) if r.get("lead_id") else None,
                full_name=r.get("full_name"), bank_name=r.get("bank_name"), amount=a,
            )
            for r, a in rows
        ],
        tax=tax,
    )
    return Prepared(
        source_id=source_id,
        lines=[{"lead_bank_id": r["lead_bank_id"], "amount": a} for r, a in rows],
        total=total, tax=tax, pdf=pdf,
    )


async def _gather(principal: Principal):
    return await asyncio.gather(
        invoices.eligible(principal.brand, principal.is_demo, principal.source_ids),
        reader.lead_numbers(principal.brand, principal.source_ids),
    )
