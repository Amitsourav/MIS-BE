"""Partner charts, computed live from the CRM on each request.

Lead funnel (FMC): of the leads delivered in the window (by created date),
how many REACHED Login, Sanction, PF paid and Disbursed. "Reached" = the lead's
current stage, or any stage it was ever moved to, is that step or a later one,
so a disbursed student counts in every step and the bars only ever narrow.

Monthly payouts (FMC), both bars in the partner's own rupees:
- revenue: what the partner will earn on the whole sanctioned loan (rate x
  sanctioned amount; a hand-agreed amount is its own total), in the month the
  loan was sanctioned. No sanction date -> the month of the first release.
- disbursement: what they have earned on money actually released, in the
  month of each release. The CRM's payout_earned is split across a file's
  releases in proportion to their amounts, so the bars always add up to the
  Payout page's "Earned" to the paisa.
"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.crm import reader
from app.models.enums import Brand
from app.schemas.charts import (
    FunnelStep,
    LeadFunnelResponse,
    MonthlyPayoutPoint,
    PayoutMonthlyResponse,
)
from app.services.live import day_bounds

_CENT = Decimal("0.01")

# FMC lender pipeline, in order. Rank = how far along a stage is.
_FMC_STEPS: list[tuple[str, str]] = [
    ("logged_in", "Login"),
    ("sanctioned", "Sanction"),
    ("pf_paid", "PF paid"),
    ("disbursed", "Disbursed"),
]
_FMC_RANK = {key: i + 1 for i, (key, _) in enumerate(_FMC_STEPS)}


def _reach(row: dict[str, Any]) -> int:
    stages = {s for s in (row.get("logged_stages") or []) if s}
    if row.get("raw_stage"):
        stages.add(str(row["raw_stage"]).strip().lower())
    return max((_FMC_RANK.get(s, 0) for s in stages), default=0)


async def lead_funnel(
    brand: Brand | None,
    source_ids: list[uuid.UUID],
    date_from: date | None,
    date_to: date | None,
) -> LeadFunnelResponse:
    if brand != Brand.FMC:  # the lender pipeline is FundMyCampus only
        return LeadFunnelResponse(
            brand_supported=False, date_from=date_from, date_to=date_to, steps=[]
        )
    start, end = day_bounds(date_from, date_to)
    rows = await reader.lead_reach_rows(brand, source_ids, start, end)
    reached = [_reach(r) for r in rows]
    steps = [FunnelStep(key="all", label="All leads", count=len(rows))]
    for key, label in _FMC_STEPS:
        steps.append(
            FunnelStep(key=key, label=label, count=sum(1 for r in reached if r >= _FMC_RANK[key]))
        )
    return LeadFunnelResponse(
        brand_supported=True, date_from=date_from, date_to=date_to, steps=steps
    )


def _d(value: Any) -> Decimal:
    return Decimal("0") if value is None else Decimal(str(value))


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _revenue(p: dict[str, Any]) -> Decimal:
    """What the partner earns once the whole sanctioned loan is released."""
    rate, loan = p.get("payout_rate"), p.get("loan_amount")
    if (p.get("payout_basis") or "agreed") == "rate" and rate is not None and loan is not None:
        # Never below what is already earned (a loan can't release more than
        # sanctioned, but if the CRM data says so, trust what was earned).
        return max(_q(_d(loan) * _d(rate) / 100), _d(p.get("payout_earned")))
    return _d(p.get("payout_earned"))  # agreed: the fixed amount is the total


def _split_by_release(earned: Decimal, releases: list[dict[str, Any]]) -> list[tuple[date | None, Decimal]]:
    """Split `earned` across releases by amount; the last takes the rounding
    remainder so the parts add up exactly."""
    total = sum((_d(r["disbursed_amount"]) for r in releases), Decimal("0"))
    if total <= 0 or earned == 0:
        return []
    ordered = sorted(releases, key=lambda r: (r.get("disbursed_on") or date.max, r.get("tranche_no") or 0))
    parts, used = [], Decimal("0")
    for i, r in enumerate(ordered):
        share = earned - used if i == len(ordered) - 1 else _q(earned * _d(r["disbursed_amount"]) / total)
        used += share
        parts.append((r.get("disbursed_on"), share))
    return parts


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months_between(first: str, last: str) -> list[str]:
    y, m = int(first[:4]), int(first[5:])
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


async def payout_monthly(
    brand: Brand | None,
    source_ids: list[uuid.UUID],
    date_from: date | None,
    date_to: date | None,
) -> PayoutMonthlyResponse:
    zero = Decimal("0")
    if brand != Brand.FMC:
        return PayoutMonthlyResponse(
            brand_supported=False, months=[], revenue_total=zero, disbursement_total=zero,
            revenue_dated_by_first_release=0, revenue_undated=zero,
        )

    def in_range(d: date | None) -> bool:
        return d is not None and (date_from is None or d >= date_from) and (date_to is None or d <= date_to)

    payouts, releases = await asyncio.gather(
        reader.payout_rows(brand, source_ids, None, None),
        reader.disbursement_rows(brand, source_ids),
    )
    by_file: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in releases:
        by_file[r["lead_bank_id"]].append(r)

    revenue: dict[str, Decimal] = defaultdict(Decimal)
    disbursement: dict[str, Decimal] = defaultdict(Decimal)
    fallback, undated = 0, zero
    for p in payouts:
        by_release = p.get("sanction_date") is None and p.get("first_disbursed_on") is not None
        when = p.get("sanction_date") or p.get("first_disbursed_on")
        rev = _revenue(p)
        if when is None:
            if date_from is None and date_to is None:
                undated += rev
        elif in_range(when):
            revenue[_month(when)] += rev
            fallback += by_release
        for released_on, share in _split_by_release(_d(p.get("payout_earned")), by_file.get(p["lead_bank_id"], [])):
            if in_range(released_on):
                disbursement[_month(released_on)] += share

    keys = sorted(set(revenue) | set(disbursement))
    months = _months_between(keys[0], keys[-1]) if keys else []
    points = [
        MonthlyPayoutPoint(month=m, revenue=_q(revenue.get(m, zero)), disbursement=_q(disbursement.get(m, zero)))
        for m in months
    ]
    return PayoutMonthlyResponse(
        brand_supported=True,
        months=points,
        revenue_total=_q(sum(revenue.values(), zero) + undated),
        disbursement_total=_q(sum(disbursement.values(), zero)),
        revenue_dated_by_first_release=fallback,
        revenue_undated=_q(undated),
    )
