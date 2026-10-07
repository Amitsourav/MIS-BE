"""Partner payouts (Payout page), read live from the FMC view
`public.mis_partner_payouts` on every request.

Payout = partner rate × sanctioned loan amount (or a hand-agreed amount),
earned on the PF-paid date. Scoped to the caller's mapped FMC sources in SQL.
The view is small (hundreds of rows), so ordering, the summary and paging are
done here on the scoped rows.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.crm import reader
from app.models.enums import Brand
from app.schemas.payout import PayoutItem, PayoutsResponse, PayoutSummary

# Only FundMyCampus has lender commission; Admitverse has no payouts.
PAYOUT_BRANDS: frozenset[Brand] = frozenset({Brand.FMC})

_CENT = Decimal("0.01")


def _money(value: Any) -> Decimal:
    """Coerce a numeric to a 2-dp Decimal (never via float)."""
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(_CENT)


def _opt_money(value: Any) -> Decimal | None:
    return None if value is None else _money(value)


def _sort_key(r: dict[str, Any]):
    # PF paid newest first (nulls last), then a stable tie-break for paging.
    pf = r.get("pf_paid_on")
    return (pf is None, -(pf.toordinal()) if pf else 0, str(r["lead_bank_id"]))


def _to_item(r: dict[str, Any]) -> PayoutItem:
    return PayoutItem(
        serial_no=r.get("serial_no"),
        full_name=r.get("full_name"),
        bank_name=r.get("bank_name"),
        loan_amount=_opt_money(r.get("loan_amount")),
        pf_paid_on=r.get("pf_paid_on"),
        disbursed_total=_money(r.get("disbursed_total")),
        payout_basis=r.get("payout_basis") or "rate",
        payout_rate=_opt_money(r.get("payout_rate")),
        earned=_money(r.get("payout_earned")),
        paid=_money(r.get("payout_paid")),
        pending=_money(r.get("payout_pending")),
    )


async def _scoped_rows(
    provider_brand: Brand | None,
    source_ids: list[uuid.UUID],
    date_from: date | None,
    date_to: date | None,
) -> list[dict[str, Any]]:
    if provider_brand not in PAYOUT_BRANDS:
        return []
    rows = await reader.payout_rows(provider_brand, source_ids, date_from, date_to)
    return sorted(rows, key=_sort_key)


async def build_payouts_page(
    *,
    provider_brand: Brand | None,
    source_ids: list[uuid.UUID],
    date_from: date | None,
    date_to: date | None,
    page: int,
    page_size: int,
) -> PayoutsResponse:
    """The Payout page for one provider. `summary` covers the whole filtered set,
    not just the page. An unsupported brand (AV) returns an empty, flagged page."""
    supported = provider_brand in PAYOUT_BRANDS
    rows = await _scoped_rows(provider_brand, source_ids, date_from, date_to)
    window = rows[(page - 1) * page_size : page * page_size]
    return PayoutsResponse(
        brand_supported=supported,
        summary=PayoutSummary(
            students=len({r["lead_id"] for r in rows}),
            loan_total=sum((_money(r.get("loan_amount")) for r in rows), Decimal("0.00")),
            earned=sum((_money(r.get("payout_earned")) for r in rows), Decimal("0.00")),
            paid=sum((_money(r.get("payout_paid")) for r in rows), Decimal("0.00")),
            pending=sum((_money(r.get("payout_pending")) for r in rows), Decimal("0.00")),
        ),
        items=[_to_item(r) for r in window],
        page=page,
        page_size=page_size,
        total=len(rows),
        data_as_of=datetime.now(timezone.utc).isoformat() if supported else None,
    )


async def payout_items_for_export(
    *,
    provider_brand: Brand | None,
    source_ids: list[uuid.UUID],
    date_from: date | None,
    date_to: date | None,
) -> list[PayoutItem]:
    """All filtered rows (no paging) for CSV export."""
    rows = await _scoped_rows(provider_brand, source_ids, date_from, date_to)
    return [_to_item(r) for r in rows]
