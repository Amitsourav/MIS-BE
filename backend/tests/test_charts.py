"""Partner graphs: lead funnel (reached each step) and monthly revenue vs
disbursement in the partner's own rupees."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from app.models.enums import Brand
from tests.factories import lead_row, make_provider, map_source, payout_row, provider_headers

pytestmark = pytest.mark.asyncio

JUL = datetime(2026, 7, 10, 10, tzinfo=timezone.utc)
AUG = datetime(2026, 8, 10, 10, tzinfo=timezone.utc)


def _steps(body):
    return [(s["key"], s["count"]) for s in body["steps"]]


async def test_funnel_counts_leads_that_reached_each_step(client, db, crm):
    p = await make_provider(db, "A", "f@x.com")
    src = await map_source(db, p)
    crm.leads[Brand.FMC] = [
        lead_row(src, phone="9000000001", created_at=JUL, raw_stage="created"),
        lead_row(src, phone="9000000002", created_at=JUL, raw_stage="logged_in"),
        lead_row(src, phone="9000000003", created_at=JUL, raw_stage="sanctioned"),
        lead_row(src, phone="9000000004", created_at=JUL, raw_stage="disbursed"),  # skipped steps
        # lost now, but the logs show it got to PF paid first: still counts
        {**lead_row(src, phone="9000000005", created_at=JUL, raw_stage="lost"),
         "logged_stages": ["contacted", "logged_in", "sanctioned", "pf_paid", "lost"]},
        # moved back from sanction to processing: it did reach sanction
        {**lead_row(src, phone="9000000006", created_at=JUL, raw_stage="processing"),
         "logged_stages": ["sanctioned", "processing"]},
        lead_row(src, phone="9000000007", created_at=AUG, raw_stage="pf_paid"),
    ]
    h = provider_headers(p)
    body = (await client.get("/me/lead-funnel?all_time=true", headers=h)).json()
    assert body["brand_supported"] is True
    assert _steps(body) == [
        ("all", 7), ("logged_in", 6), ("sanctioned", 5), ("pf_paid", 3), ("disbursed", 1),
    ]
    assert [s["label"] for s in body["steps"]] == [
        "All leads", "Login", "Sanction", "PF paid", "Disbursed"]
    counts = [c for _, c in _steps(body)]
    assert counts == sorted(counts, reverse=True)  # never widens

    july = (await client.get("/me/lead-funnel?from=2026-07-01&to=2026-07-31", headers=h)).json()
    assert _steps(july)[0] == ("all", 6) and _steps(july)[3] == ("pf_paid", 2)


async def test_funnel_is_fmc_only_and_scoped(client, db, crm):
    av = await make_provider(db, "AV", "av@x.com", brand=Brand.AV)
    await map_source(db, av)
    body = (await client.get("/me/lead-funnel?all_time=true", headers=provider_headers(av))).json()
    assert body == {"brand_supported": False, "date_from": None, "date_to": None, "steps": []}

    a = await make_provider(db, "A", "a@x.com")
    b = await make_provider(db, "B", "b@x.com")
    sa, sb = await map_source(db, a), await map_source(db, b)
    crm.leads[Brand.FMC] = [lead_row(sa, phone="9000000001"), lead_row(sb, phone="9000000002"),
                            lead_row(sb, phone="9000000003")]
    mine = (await client.get("/me/lead-funnel?all_time=true", headers=provider_headers(a))).json()
    assert _steps(mine)[0] == ("all", 1)
    assert ("lead_reach_rows", Brand.FMC, [sa]) in crm.calls


def _release(row, amount, on, tranche=1):
    return {"lead_bank_id": row["lead_bank_id"], "lead_source_id": row["lead_source_id"],
            "tranche_no": tranche, "disbursed_amount": amount, "disbursed_on": on}


async def test_monthly_revenue_on_sanction_and_disbursement_per_release(client, db, crm):
    from decimal import Decimal
    p = await make_provider(db, "A", "m@x.com")
    src = await map_source(db, p)
    # 20L sanctioned in July at 0.60%; 10L released in Aug, 4L in Oct -> earned 8,400
    a = payout_row(src, loan="2000000.00", rate="0.60", earned="8400.00", disbursed="1400000.00",
                   released=date(2026, 10, 2))
    a["sanction_date"] = date(2026, 7, 5)
    # agreed 10,080 with no sanction date: revenue falls back to its first release month (June)
    b = payout_row(src, basis="agreed", rate=None, loan="2100000.00", earned="10080.00",
                   disbursed="1128186.00", released=date(2026, 6, 20))
    crm.payouts = [a, b]
    crm.releases = [
        _release(a, Decimal("1000000.00"), date(2026, 8, 14), 1),
        _release(a, Decimal("400000.00"), date(2026, 10, 2), 2),
        _release(b, Decimal("1128186.00"), date(2026, 6, 20), 1),
    ]
    body = (await client.get("/provider/payouts/monthly", headers=provider_headers(p))).json()
    assert body["brand_supported"] is True
    assert body["months"] == [
        {"month": "2026-06", "revenue": "10080.00", "disbursement": "10080.00"},
        {"month": "2026-07", "revenue": "12000.00", "disbursement": "0.00"},  # 0.60% x 20L
        {"month": "2026-08", "revenue": "0.00", "disbursement": "6000.00"},   # 8,400 x 10/14
        {"month": "2026-09", "revenue": "0.00", "disbursement": "0.00"},      # gap filled
        {"month": "2026-10", "revenue": "0.00", "disbursement": "2400.00"},   # 8,400 x 4/14
    ]
    assert body["revenue_total"] == "22080.00"
    # the disbursement bars add up to the Payout page's Earned exactly
    page = (await client.get("/provider/payouts", headers=provider_headers(p))).json()
    assert body["disbursement_total"] == page["summary"]["earned"] == "18480.00"
    assert body["revenue_dated_by_first_release"] == 1
    assert body["revenue_undated"] == "0.00"

    aug = (await client.get("/provider/payouts/monthly?date_from=2026-08-01&date_to=2026-08-31",
                            headers=provider_headers(p))).json()
    assert aug["months"] == [{"month": "2026-08", "revenue": "0.00", "disbursement": "6000.00"}]


async def test_monthly_split_rounding_adds_up_to_the_paisa(client, db, crm):
    from decimal import Decimal
    p = await make_provider(db, "A", "r@x.com")
    src = await map_source(db, p)
    row = payout_row(src, earned="100.00", disbursed="3.00", released=date(2026, 9, 3))
    crm.payouts = [row]
    crm.releases = [_release(row, Decimal("1.00"), date(2026, 7, 1), 1),
                    _release(row, Decimal("1.00"), date(2026, 8, 1), 2),
                    _release(row, Decimal("1.00"), date(2026, 9, 3), 3)]
    body = (await client.get("/provider/payouts/monthly", headers=provider_headers(p))).json()
    assert [m["disbursement"] for m in body["months"]] == ["33.33", "33.33", "33.34"]
    assert body["disbursement_total"] == "100.00"


async def test_monthly_undated_loan_and_av_and_admin(client, db, crm):
    from app.core.security import create_access_token, hash_password
    from app.models import AdminUser
    p = await make_provider(db, "A", "u@x.com")
    src = await map_source(db, p)
    row = payout_row(src, basis="agreed", rate=None, earned="5000.00")
    row["first_disbursed_on"] = row["last_disbursed_on"] = None
    crm.payouts = [row]
    body = (await client.get("/provider/payouts/monthly", headers=provider_headers(p))).json()
    assert body["months"] == [] and body["revenue_undated"] == "5000.00"
    assert body["revenue_total"] == "5000.00" and body["disbursement_total"] == "0.00"

    av = await make_provider(db, "AV", "av2@x.com", brand=Brand.AV)
    res = (await client.get("/provider/payouts/monthly", headers=provider_headers(av))).json()
    assert res["brand_supported"] is False and res["months"] == []

    admin = AdminUser(email="ad@x.com", password_hash=hash_password("pw"), brand=Brand.AV)
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    h = {"Authorization": "Bearer " + create_access_token(subject=str(admin.id), role="admin", brand="av")}
    assert (await client.get(f"/admin/providers/{p.id}/payouts/monthly", headers=h)).status_code == 404
    assert (await client.get(f"/admin/providers/{uuid.uuid4()}/payouts/monthly", headers=h)).status_code == 404
