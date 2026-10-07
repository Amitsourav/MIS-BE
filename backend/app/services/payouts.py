"""Partner payouts: apply the CRM view snapshot, back-stamp on source mapping,
and build the scoped Payout page / export.

The CRM view `public.mis_partner_payouts` is small (hundreds of rows), so every
sync is a full refresh: upsert what the view has, delete what it no longer has.
Upsert is done in Python (load-then-diff) so it is portable to the SQLite tests.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MisLead, MisPayout, SyncState
from app.models.enums import Brand
from app.schemas.payout import PayoutItem, PayoutsResponse, PayoutSummary

# Only FundMyCampus has lender commission; Admitverse has no payouts.
PAYOUT_BRANDS: frozenset[Brand] = frozenset({Brand.FMC})

_CENT = Decimal("0.01")


def _money(value: Any) -> Decimal:
    """Coerce a DB numeric (Decimal, or float/int on SQLite) to a 2-dp Decimal."""
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(_CENT)


# ---------------- Sync: full refresh from the CRM view ----------------
async def apply_snapshot(
    db: AsyncSession,
    brand: Brand,
    rows: list[dict[str, Any]],
    source_map: dict[uuid.UUID, uuid.UUID],
) -> dict[str, int]:
    """Make `mis_payouts` for `brand` exactly match the view rows.

    `rows` use the view's column names. `provider_id` is stamped from the
    crm_source_id -> provider_id map; unmapped sources are stored with null
    (never skipped) so a later mapping can back-stamp them. Does NOT commit.
    """
    existing = {
        p.crm_lead_bank_id: p
        for p in (
            await db.execute(select(MisPayout).where(MisPayout.brand == brand))
        ).scalars().all()
    }
    now = datetime.now(timezone.utc)
    seen: set[uuid.UUID] = set()
    inserted = updated = unmapped = 0

    for r in rows:
        key = r["lead_bank_id"]
        seen.add(key)
        provider_id = source_map.get(r.get("lead_source_id"))
        if provider_id is None:
            unmapped += 1
        values = {
            "crm_lead_id": r["lead_id"],
            "crm_source_id": r.get("lead_source_id"),
            "provider_id": provider_id,
            "bank_name": r.get("bank_name"),
            "loan_amount": (
                None if r.get("loan_amount") is None else _money(r["loan_amount"])
            ),
            "pf_paid_on": r.get("pf_paid_on"),
            "disbursed_total": _money(r.get("disbursed_total")),
            "payout_basis": r.get("payout_basis") or "rate",
            "payout_rate": (
                None if r.get("payout_rate") is None else _money(r["payout_rate"])
            ),
            "payout_earned": _money(r.get("payout_earned")),
            "payout_paid": _money(r.get("payout_paid")),
            "payout_pending": _money(r.get("payout_pending")),
            "crm_updated_at": r.get("updated_at"),
            "synced_at": now,
        }
        obj = existing.get(key)
        if obj is None:
            db.add(MisPayout(brand=brand, crm_lead_bank_id=key, **values))
            inserted += 1
        else:
            for field, value in values.items():
                setattr(obj, field, value)
            updated += 1

    # Files gone from the view (payout removed / lender file deleted in the CRM).
    stale = set(existing) - seen
    if stale:
        await db.execute(
            delete(MisPayout).where(
                MisPayout.brand == brand, MisPayout.crm_lead_bank_id.in_(stale)
            )
        )
    await db.flush()
    return {
        "inserted": inserted,
        "updated": updated,
        "deleted": len(stale),
        "unmapped": unmapped,
    }


async def claim_unmapped_payouts(
    db: AsyncSession,
    *,
    provider_id: uuid.UUID,
    brand: Brand,
    crm_source_id: uuid.UUID,
) -> int:
    """Back-stamp payout rows stored before their source was mapped. Mirrors
    `rollup.claim_unmapped_leads`. Does NOT commit."""
    res = await db.execute(
        update(MisPayout)
        .where(
            MisPayout.brand == brand,
            MisPayout.crm_source_id == crm_source_id,
            MisPayout.provider_id.is_(None),
        )
        .values(provider_id=provider_id)
    )
    return int(res.rowcount or 0)


# ---------------- Read: scoped Payout page / export ----------------
def _filters(provider_id: uuid.UUID, date_from: date | None, date_to: date | None):
    conds = [MisPayout.provider_id == provider_id]  # hard scope
    if date_from is not None:
        conds.append(MisPayout.pf_paid_on >= date_from)
    if date_to is not None:
        conds.append(MisPayout.pf_paid_on <= date_to)
    return conds


def _items_query(conds):
    return (
        select(MisPayout, MisLead.serial_no, MisLead.full_name)
        .outerjoin(
            MisLead,
            (MisLead.brand == MisPayout.brand)
            & (MisLead.crm_lead_id == MisPayout.crm_lead_id),
        )
        .where(*conds)
        .order_by(
            MisPayout.pf_paid_on.desc().nulls_last(),
            MisPayout.crm_lead_bank_id,  # stable paging tie-break
        )
    )


def _to_item(p: MisPayout, serial_no: int | None, full_name: str | None) -> PayoutItem:
    return PayoutItem(
        serial_no=serial_no,
        full_name=full_name,
        bank_name=p.bank_name,
        loan_amount=None if p.loan_amount is None else _money(p.loan_amount),
        pf_paid_on=p.pf_paid_on,
        disbursed_total=_money(p.disbursed_total),
        payout_basis=p.payout_basis,
        payout_rate=None if p.payout_rate is None else _money(p.payout_rate),
        earned=_money(p.payout_earned),
        paid=_money(p.payout_paid),
        pending=_money(p.payout_pending),
    )


async def _data_as_of(db: AsyncSession) -> str | None:
    state = await db.get(SyncState, Brand.FMC)
    ts = state.last_run_at if state else None
    return ts.isoformat() if ts else None


async def build_payouts_page(
    db: AsyncSession,
    *,
    provider_id: uuid.UUID,
    provider_brand: Brand | None,
    date_from: date | None,
    date_to: date | None,
    page: int,
    page_size: int,
) -> PayoutsResponse:
    """The Payout page for one provider. `summary` covers the whole filtered set,
    not just the page. An unsupported brand (AV) returns an empty, flagged page."""
    if provider_brand not in PAYOUT_BRANDS:
        return PayoutsResponse(
            brand_supported=False,
            summary=PayoutSummary(),
            items=[],
            page=page,
            page_size=page_size,
            total=0,
            data_as_of=None,
        )

    conds = _filters(provider_id, date_from, date_to)
    agg = (
        await db.execute(
            select(
                func.count(),
                func.count(func.distinct(MisPayout.crm_lead_id)),
                func.coalesce(func.sum(MisPayout.loan_amount), 0),
                func.coalesce(func.sum(MisPayout.payout_earned), 0),
                func.coalesce(func.sum(MisPayout.payout_paid), 0),
                func.coalesce(func.sum(MisPayout.payout_pending), 0),
            ).where(*conds)
        )
    ).one()
    total, students, loan_total, earned, paid, pending = agg

    rows = await db.execute(
        _items_query(conds).offset((page - 1) * page_size).limit(page_size)
    )
    return PayoutsResponse(
        brand_supported=True,
        summary=PayoutSummary(
            students=int(students or 0),
            loan_total=_money(loan_total),
            earned=_money(earned),
            paid=_money(paid),
            pending=_money(pending),
        ),
        items=[_to_item(p, s, n) for p, s, n in rows.all()],
        page=page,
        page_size=page_size,
        total=int(total or 0),
        data_as_of=await _data_as_of(db),
    )


async def payout_items_for_export(
    db: AsyncSession,
    *,
    provider_id: uuid.UUID,
    provider_brand: Brand | None,
    date_from: date | None,
    date_to: date | None,
) -> list[PayoutItem]:
    """All filtered rows (no paging) for CSV export."""
    if provider_brand not in PAYOUT_BRANDS:
        return []
    rows = await db.execute(_items_query(_filters(provider_id, date_from, date_to)))
    return [_to_item(p, s, n) for p, s, n in rows.all()]
