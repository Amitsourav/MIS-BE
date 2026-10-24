"""Partner invoices, over the CRM's HTTP API (BE-CRM `/api/v1/mis/invoices`).

A partner raises their invoice to FMC from the Payout page: the CRM stores it
(status `raised`, with the PDF) exactly as if an admin had entered it. Like
partner chat, the MIS only relays through the CRM's API with the `mis` key —
never the database — and every call sends the caller's own source ids (from
the JWT); the CRM 404s anything outside them.

Money from the CRM is before GST except `gst_amount`, `amount`, `net_amount`.

Tests replace these functions with in-memory fakes (see tests/conftest.py), so
always call them as `invoices.eligible(...)`, never `from ... import eligible`.
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from app.crm import chat
from app.crm.chat import ChatError
from app.models.enums import Brand


class InvoiceError(ChatError):
    """The CRM refused an invoice call; `detail` is a sentence for the partner."""


# 409 = duplicate invoice number; 404 / 422 / 429 as for chat.
_PASS_THROUGH = frozenset({404, 409, 422, 429})


def _ids(source_ids: list[uuid.UUID]) -> str:
    return ",".join(str(s) for s in source_ids)


async def _call(brand: Brand, demo: bool, method: str, path: str, **kw: Any) -> Any:
    return await chat._call(
        brand, demo, method, path, what="Invoicing", span="crm_invoice",
        error=InvoiceError, pass_through=_PASS_THROUGH, **kw,
    )


async def eligible(brand: Brand, demo: bool, source_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """Students whose payout can still be invoiced (`remaining` > 0)."""
    if not source_ids:
        return []
    return await _call(brand, demo, "GET", "/mis/invoices/eligible",
                       params={"lead_source_ids": _ids(source_ids)})


async def list_invoices(brand: Brand, demo: bool, source_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """The partner's invoices (raised from the MIS or entered by FMC), newest first."""
    if not source_ids:
        return []
    return await _call(brand, demo, "GET", "/mis/invoices",
                       params={"lead_source_ids": _ids(source_ids)})


async def raise_invoice(
    brand: Brand, demo: bool, source_ids: list[uuid.UUID], *,
    lead_source_id: uuid.UUID, invoice_id: uuid.UUID, invoice_number: str,
    invoice_date: date, gst_rate: Decimal, partner_name: str,
    partner_user_email: str | None, lines: list[dict[str, Any]], pdf: bytes,
) -> dict[str, Any]:
    """Raise an invoice with its PDF. The same `invoice_id` again returns the
    stored invoice (no duplicate, no second notification)."""
    payload = {
        "lead_source_ids": [str(s) for s in source_ids],
        "lead_source_id": str(lead_source_id),
        "mis_invoice_id": str(invoice_id),
        "invoice_number": invoice_number,
        "invoice_date": invoice_date.isoformat(),
        "gst_rate": f"{gst_rate:.2f}",
        "partner_name": partner_name,
        "partner_user_email": partner_user_email,
        "lines": [
            {"lead_bank_id": str(l["lead_bank_id"]),
             **({"amount": f"{l['amount']:.2f}"} if l.get("amount") is not None else {})}
            for l in lines
        ],
    }
    return await _call(
        brand, demo, "POST", "/mis/invoices",
        data={"payload": json.dumps(payload)},
        files={"pdf": ("invoice.pdf", pdf, "application/pdf")},
    )


async def pdf(brand: Brand, demo: bool, source_ids: list[uuid.UUID], invoice_id: uuid.UUID) -> bytes:
    """The invoice's PDF bytes (404 when it isn't theirs or has none)."""
    return await _call(brand, demo, "GET", f"/mis/invoices/{invoice_id}/pdf",
                       params={"lead_source_ids": _ids(source_ids)}, raw=True)
