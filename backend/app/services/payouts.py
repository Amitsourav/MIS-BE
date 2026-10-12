"""Partner payouts (Payout page), read live from the FMC view
`public.mis_partner_payouts` on every request.

Payout = partner rate × the amount the bank has actually released, growing
with each tranche (or a hand-agreed amount). The CRM computes payout_earned;
the MIS never recomputes it. Rows are dated by last_disbursed_on (when money
was last released). Scoped to the caller's mapped FMC sources in SQL.
The view is small (hundreds of rows), so ordering, the summary and paging are
done here on the scoped rows.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.crm import reader
from app.models.enums import Brand
from app.schemas.payout import (
    PayoutItem,
    PayoutsResponse,
    PayoutSummary,
    UnmappedPayoutItem,
    UnmappedPayoutsResponse,
)

# Only FundMyCampus has lender commission; Admitverse has no payouts.
PAYOUT_BRANDS: frozenset[Brand] = frozenset({Brand.FMC})

_CENT = Decimal("0.01")


def _exact(value: Any) -> Decimal:
    """The CRM numeric as an exact Decimal (never via float); None -> 0."""
    return Decimal("0") if value is None else Decimal(str(value))


def _money(value: Any) -> Decimal:
    """Exact amount rounded to paise, half up (0.005 -> 0.01), as money is."""
    return _exact(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def _opt_money(value: Any) -> Decimal | None:
    return None if value is None else _money(value)


def _rate(value: Any) -> Decimal | None:
    """The percent rate exactly as stored, shown with at least 2 decimals
    (0.6 -> 0.60, 0.625 -> 0.625). Never rounded: it is a contract term."""
    if value is None:
        return None
    d = _exact(value).normalize()
    return d.quantize(_CENT) if d.as_tuple().exponent > -2 else d


def _overpaid(r: dict[str, Any]) -> Decimal:
    return max(_exact(r.get("payout_paid")) - _exact(r.get("payout_earned")), Decimal("0"))


def _summary(rows: list[dict[str, Any]]) -> PayoutSummary:
    """Totals over the exact CRM values, rounded once at the end (so many
    sub-paisa rows can't drift from the true total)."""
    def total(key: str) -> Decimal:
        return sum((_exact(r.get(key)) for r in rows), Decimal("0")).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )

    return PayoutSummary(
        students=len({r["lead_id"] for r in rows}),
        loan_total=total("loan_amount"),
        earned=total("payout_earned"),
        paid=total("payout_paid"),
        pending=total("payout_pending"),
        overpaid=sum((_overpaid(r) for r in rows), Decimal("0")).quantize(
            _CENT, rounding=ROUND_HALF_UP
        ),
    )


def _sort_key(r: dict[str, Any]):
    # Last released newest first (nulls last), then a stable tie-break for paging.
    d = r.get("last_disbursed_on")
    return (d is None, -(d.toordinal()) if d else 0, str(r["lead_bank_id"]))


def _item_fields(r: dict[str, Any]) -> dict[str, Any]:
    return dict(
        serial_no=r.get("serial_no"),
        full_name=r.get("full_name"),
        bank_name=r.get("bank_name"),
        loan_amount=_opt_money(r.get("loan_amount")),
        pf_paid_on=r.get("pf_paid_on"),
        first_disbursed_on=r.get("first_disbursed_on"),
        last_disbursed_on=r.get("last_disbursed_on"),
        disbursed_total=_money(r.get("disbursed_total")),
        # The CRM view's own default for a missing basis is 'agreed'.
        payout_basis=r.get("payout_basis") or "agreed",
        payout_rate=_rate(r.get("payout_rate")),
        earned=_money(r.get("payout_earned")),
        paid=_money(r.get("payout_paid")),
        pending=_money(r.get("payout_pending")),
        overpaid=_overpaid(r).quantize(_CENT, rounding=ROUND_HALF_UP),
    )


def _to_item(r: dict[str, Any]) -> PayoutItem:
    return PayoutItem(**_item_fields(r))


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
        summary=_summary(rows),
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


async def unmapped_payouts(mapped_source_ids: list[uuid.UUID]) -> UnmappedPayoutsResponse:
    """FMC payouts on sources no provider is mapped to: money in the CRM that
    no partner can see. Admin only."""
    rows = sorted(
        await reader.payout_rows_unmapped(Brand.FMC, mapped_source_ids), key=_sort_key
    )
    return UnmappedPayoutsResponse(
        summary=_summary(rows),
        items=[
            UnmappedPayoutItem(
                **_item_fields(r),
                crm_source_id=str(r["lead_source_id"]) if r.get("lead_source_id") else None,
                source_name=r.get("source_name"),
            )
            for r in rows
        ],
        total=len(rows),
    )
