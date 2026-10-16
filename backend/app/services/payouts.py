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
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.config import settings
from app.crm import reader
from app.crm.lenders import lender_display
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
# India has no DST, so a fixed offset gives the business date for "overdue".
_IST = timezone(timedelta(hours=5, minutes=30))


def _today() -> date:
    return datetime.now(_IST).date()


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


def _gst(earned: Decimal, pending: Decimal, balance: Decimal) -> dict[str, Decimal]:
    """The GST fields from exact (unrounded) amounts, each rounded once."""
    rate = settings.payout_gst_rate
    k = 1 + rate / 100
    q = lambda d: d.quantize(_CENT, rounding=ROUND_HALF_UP)  # noqa: E731
    return dict(
        gst_rate=q(rate),
        earned_gst=q(earned * rate / 100),
        earned_with_gst=q(earned * k),
        pending_with_gst=q(pending * k),
        balance_with_gst=q(balance * k),
    )


def _overpaid(r: dict[str, Any]) -> Decimal:
    return max(_exact(r.get("payout_paid")) - _exact(r.get("payout_earned")), Decimal("0"))


def _invoice_state(r: dict[str, Any], today: date) -> str:
    """Which pie slice this row's pending money belongs to. Only an invoice
    still 'raised' covers it; a 'paid' latest invoice means anything pending
    came from later releases, and 'cancelled' covers nothing."""
    if (r.get("partner_invoice_status") or "").lower() != "raised":
        return "not_raised"
    due = r.get("partner_invoice_due_date")
    return "overdue" if due is not None and due < today else "raised"


def _summary(rows: list[dict[str, Any]]) -> PayoutSummary:
    """Totals over the exact CRM values, rounded once at the end (so many
    sub-paisa rows can't drift from the true total)."""
    def total(key: str, keep=lambda r: True) -> Decimal:
        return sum((_exact(r.get(key)) for r in rows if keep(r)), Decimal("0")).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )

    today = _today()

    # Pending money by invoice state. An open invoice covers at most its own
    # amount (no amount recorded -> it covers all the row's pending); the rest
    # of that row's pending isn't invoiced yet.
    split = {"not_raised": Decimal("0"), "raised": Decimal("0"), "overdue": Decimal("0")}
    for r in rows:
        pending, state = _exact(r.get("payout_pending")), _invoice_state(r, today)
        amount = r.get("partner_invoice_amount")
        covered = pending if state == "not_raised" or amount is None else min(pending, _exact(amount))
        split[state] += covered
        split["not_raised"] += pending - covered
    raised = split["raised"].quantize(_CENT, rounding=ROUND_HALF_UP)
    overdue = split["overdue"].quantize(_CENT, rounding=ROUND_HALF_UP)

    paid, tds, pending_total = total("payout_paid"), total("payout_tds"), total("payout_pending")
    overpaid = sum((_overpaid(r) for r in rows), Decimal("0")).quantize(_CENT, rounding=ROUND_HALF_UP)
    exact = lambda key: sum((_exact(r.get(key)) for r in rows), Decimal("0"))  # noqa: E731
    exact_overpaid = sum((_overpaid(r) for r in rows), Decimal("0"))

    return PayoutSummary(
        students=len({r["lead_id"] for r in rows}),
        loan_total=total("loan_amount"),
        earned=total("payout_earned"),
        paid=paid,
        pending=pending_total,
        overpaid=overpaid,
        balance=pending_total - overpaid,
        # The pie adds up to earned: overpayment is shown apart, not as a slice.
        cash_transferred=max(paid - overpaid - tds, Decimal("0")),
        tds=tds,
        # Remainder, so the three pending slices always add up to pending.
        invoice_not_raised=pending_total - raised - overdue,
        invoice_raised=raised,
        invoice_overdue=overdue,
        **_gst(exact("payout_earned"), exact("payout_pending"),
               exact("payout_pending") - exact_overpaid),
    )


def _sort_key(r: dict[str, Any]):
    # Last released newest first (nulls last), then a stable tie-break for paging.
    d = r.get("last_disbursed_on")
    return (d is None, -(d.toordinal()) if d else 0, str(r["lead_bank_id"]))


def _item_fields(r: dict[str, Any]) -> dict[str, Any]:
    return dict(
        lead_no=r.get("lead_no"),
        full_name=r.get("full_name"),
        bank_name=lender_display(r.get("bank_name")),
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
        balance=_money(r.get("payout_pending"))
        - _overpaid(r).quantize(_CENT, rounding=ROUND_HALF_UP),
        tds=_money(r.get("payout_tds")),
        net_paid=_money(r.get("payout_paid")) - _money(r.get("payout_tds")),
        paid_on=r.get("payout_paid_on"),
        invoice_number=r.get("partner_invoice_number"),
        invoice_amount=_opt_money(r.get("partner_invoice_amount")),
        invoice_date=r.get("partner_invoice_date"),
        invoice_due_date=r.get("partner_invoice_due_date"),
        invoice_status=r.get("partner_invoice_status"),
        invoice_state=_invoice_state(r, _today()),
        **_gst(_exact(r.get("payout_earned")), _exact(r.get("payout_pending")),
               _exact(r.get("payout_pending")) - _overpaid(r)),
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


async def lead_payout_items(
    provider_brand: Brand | None, source_ids: list[uuid.UUID], lead_id: uuid.UUID
) -> list[PayoutItem]:
    """The caller's payout rows for one student (one per lender file)."""
    rows = await _scoped_rows(provider_brand, source_ids, None, None)
    return [_to_item(r) for r in rows if r["lead_id"] == lead_id]


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
                serial_no=r.get("serial_no"),
                crm_bank_name=r.get("bank_name"),
                crm_source_id=str(r["lead_source_id"]) if r.get("lead_source_id") else None,
                source_name=r.get("source_name"),
            )
            for r in rows
        ],
        total=len(rows),
    )
