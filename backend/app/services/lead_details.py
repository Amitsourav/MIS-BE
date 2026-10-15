"""Per-lead details for the partner's Leads page, read live from the CRM view
`mis_partner_lead_info`: the FMC counsellor on the student and every lender
the file reached login with (real bank only, amount, application number)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.crm import reader
from app.crm.lenders import lender_display
from app.models.enums import Brand
from app.schemas.lead import LeadLenderOut

_CENT = Decimal("0.01")
_LAKH = Decimal("100000")

_STATUS_LABELS = {
    "loan_login": "Logged in",
    "sanctioned": "Sanctioned",
    "pf_paid": "PF Paid",
    "disbursed": "Disbursed",
    "lost": "Declined",
}


@dataclass
class LeadDetails:
    counsellor_name: str | None = None
    lenders: list[LeadLenderOut] = field(default_factory=list)


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _lender(raw: dict[str, Any], applied: Decimal | None) -> LeadLenderOut:
    status = str(raw.get("bank_status") or "")
    sanctioned = _money(raw.get("loan_amount"))
    if sanctioned is not None and sanctioned > 0:
        amount, kind = sanctioned, "sanctioned"
    elif applied is not None and applied > 0:
        amount, kind = applied, "applied"
    else:
        amount, kind = None, None
    return LeadLenderOut(
        bank=lender_display(raw.get("bank_name")),
        status=status,
        status_label=_STATUS_LABELS.get(status, status.replace("_", " ").title()),
        amount=amount,
        amount_kind=kind,
        application_no=(raw.get("application_id") or None),
    )


async def lead_details(
    brand: Brand | None,
    source_ids: list[uuid.UUID],
    lead_ids: list[uuid.UUID] | None = None,
) -> dict[uuid.UUID, LeadDetails]:
    """Details keyed by CRM lead id, for the caller's sources only (and only
    `lead_ids` when given). Leads with nothing to show are simply absent."""
    if brand is None or not source_ids or lead_ids == []:
        return {}
    out: dict[uuid.UUID, LeadDetails] = {}
    for row in await reader.lead_info_rows(brand, source_ids, lead_ids):
        lakh = row.get("loan_amount_lakh")
        applied = _money(Decimal(str(lakh)) * _LAKH) if lakh is not None else None
        out[row["lead_id"]] = LeadDetails(
            counsellor_name=row.get("counsellor_name"),
            lenders=[_lender(l, applied) for l in row.get("lenders") or []],
        )
    return out
