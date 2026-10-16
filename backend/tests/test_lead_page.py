"""Partner lead page (GET /me/leads/{id}): one lead with its stage timeline
and payouts — only for the partner who supplied it."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models.enums import Brand
from tests.factories import lead_row, make_provider, map_source, payout_row, provider_headers

pytestmark = pytest.mark.asyncio

D1 = datetime(2026, 6, 1, 10, tzinfo=timezone.utc)
D2 = datetime(2026, 6, 2, 10, tzinfo=timezone.utc)


def _at(day: int) -> datetime:
    return datetime(2026, 6, day, 9, tzinfo=timezone.utc)


async def test_lead_page_shows_the_lead_timeline_and_payouts(client, db, crm):
    p = await make_provider(db, "Acme", "acme@x.com")
    src = await map_source(db, p, name="Acme WhatsApp")
    first = lead_row(src, full_name="Riya", created_at=D1)
    arjun = lead_row(src, full_name="Arjun", created_at=D2, raw_stage="pf_paid", serial_no=8802)
    crm.leads[Brand.FMC] = [first, arjun]
    lid = arjun["crm_lead_id"]
    crm.history[lid] = [
        {"to_stage": "contacted", "at": _at(3)},
        {"to_stage": "logged_in", "at": _at(5)},
        {"to_stage": "PF_PAID ", "at": _at(9)},
    ]
    crm.lead_info[Brand.FMC] = {lid: {
        "lead_source_id": src, "counsellor_name": "Rahul M", "loan_amount_lakh": None,
        "lenders": [{"bank_name": "UC PNB", "bank_status": "pf_paid",
                     "loan_amount": Decimal("2000000"), "application_id": "PNB-77"}],
    }}
    crm.payouts = [payout_row(src, lead_id=lid, bank="UC PNB", serial_no=8802),
                   payout_row(src, lead_id=first["crm_lead_id"])]

    r = await client.get(f"/me/leads/{lid}", headers=provider_headers(p))
    assert r.status_code == 200
    body = r.json()
    assert (body["id"], body["lead_no"], body["full_name"]) == (str(lid), 2, "Arjun")
    assert (body["crm_stage"], body["crm_stage_label"]) == ("pf_paid", "PF Paid")
    assert body["counsellor_name"] == "Rahul M"
    assert body["lenders"][0]["bank"] == "PNB"
    assert [(h["stage"], h["label"]) for h in body["stage_history"]] == [
        ("contacted", "Contacted"), ("logged_in", "Logged In"), ("pf_paid", "PF Paid")]
    assert len(body["payouts"]) == 1  # only this student's file
    assert (body["payouts"][0]["lead_no"], body["payouts"][0]["bank_name"]) == (2, "PNB")
    assert "8802" not in r.text and "UC PNB" not in r.text and "Acme WhatsApp" not in r.text


async def test_another_partners_lead_is_404(client, db, crm):
    a = await make_provider(db, "A", "a@x.com")
    b = await make_provider(db, "B", "b@x.com")
    src_a, src_b = await map_source(db, a), await map_source(db, b)
    hers = lead_row(src_b, full_name="Not Yours")
    crm.leads[Brand.FMC] = [lead_row(src_a), hers]
    crm.history[hers["crm_lead_id"]] = [{"to_stage": "contacted", "at": _at(3)}]

    for lead_id in (hers["crm_lead_id"], uuid.uuid4()):
        r = await client.get(f"/me/leads/{lead_id}", headers=provider_headers(a))
        assert r.status_code == 404 and "Not Yours" not in r.text
    assert all(src_b not in call[2] for call in crm.calls)  # never queried with B's sources

    assert (await client.get("/me/leads/not-a-uuid", headers=provider_headers(a))).status_code == 422
    assert (await client.get(f"/me/leads/{hers['crm_lead_id']}")).status_code == 401


async def test_av_lead_page_has_no_payouts(client, db, crm):
    p = await make_provider(db, "Nik", "nik@x.com", brand=Brand.AV)
    src = await map_source(db, p)
    lead = lead_row(src, raw_stage="qualified")
    crm.leads[Brand.AV] = [lead]
    body = (await client.get(f"/me/leads/{lead['crm_lead_id']}",
                             headers=provider_headers(p))).json()
    assert (body["crm_stage_label"], body["payouts"], body["stage_history"]) == ("Hot", [], [])


async def test_export_route_still_wins_over_lead_id(client, db, crm):
    p = await make_provider(db, "Acme", "acme2@x.com")
    await map_source(db, p)
    r = await client.get("/me/leads/export?all_time=true", headers=provider_headers(p))
    assert r.status_code == 200 and "lead_no" in r.text
