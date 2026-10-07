"""Partner payouts (Payout page, earned on PF paid), read live from the FMC view:
scoping by mapped sources, AV unsupported, page-independent summary, string
money, PF-date filter and ordering, export, admin company scope."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.core.security import create_access_token, hash_password
from app.models import AdminUser
from app.models.enums import Brand
from tests.factories import make_provider, map_source, payout_row, provider_headers

pytestmark = pytest.mark.asyncio


async def _admin_headers(db, email: str, brand: Brand | None) -> dict:
    admin = AdminUser(email=email, password_hash=hash_password("pw"), brand=brand)
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    token = create_access_token(
        subject=str(admin.id), role="admin", brand=brand.value if brand else None
    )
    return {"Authorization": f"Bearer {token}"}


async def test_provider_sees_only_own_sources_payouts(client, db, crm):
    a = await make_provider(db, "A", "pa@x.com")
    b = await make_provider(db, "B", "pb@x.com")
    sa, sb = await map_source(db, a), await map_source(db, b)
    crm.payouts = [payout_row(sa, bank="SBI"), payout_row(sb, bank="HDFC")]

    res = await client.get("/provider/payouts", headers=provider_headers(a))
    assert res.status_code == 200
    body = res.json()
    assert body["brand_supported"] is True
    assert [i["bank_name"] for i in body["items"]] == ["SBI"]
    assert body["total"] == 1
    assert "HDFC" not in str(body)
    assert crm.calls == [("payout_rows", Brand.FMC, [sa])]


async def test_av_provider_is_unsupported_not_an_error(client, db, crm):
    av = await make_provider(db, "AV Vendor", "av@x.com", brand=Brand.AV)
    await map_source(db, av)
    res = await client.get("/provider/payouts", headers=provider_headers(av))
    assert res.status_code == 200
    body = res.json()
    assert body["brand_supported"] is False
    assert body["items"] == [] and body["total"] == 0
    assert body["summary"]["earned"] == "0.00"
    assert body["data_as_of"] is None
    assert crm.calls == []  # the AV CRM is never asked for payouts

    export = await client.get("/provider/payouts/export", headers=provider_headers(av))
    assert export.status_code == 200
    assert export.text.strip().count("\n") == 0  # header only


async def test_summary_covers_whole_filtered_set_not_the_page(client, db, crm):
    a = await make_provider(db, "A", "pa4@x.com")
    src = await map_source(db, a)
    shared_student = uuid.uuid4()  # one student with two lenders
    crm.payouts = [
        payout_row(src, lead_id=shared_student, loan="1000000.00",
                   earned="6000.00", paid="6000.00", pf=date(2026, 9, 1)),
        payout_row(src, lead_id=shared_student, loan="500000.00",
                   earned="3000.00", pf=date(2026, 9, 20)),
        payout_row(src, loan="250000.00", earned="1500.50",
                   paid="500.25", pf=date(2026, 9, 10)),
        # agreed deal with no sanctioned amount: counts toward earned, not loan_total
        payout_row(src, loan=None, basis="agreed", rate=None, earned="0.00",
                   pf=date(2026, 8, 1)),
    ]

    res = await client.get("/provider/payouts?page_size=1", headers=provider_headers(a))
    body = res.json()
    assert len(body["items"]) == 1
    assert body["total"] == 4
    assert body["summary"] == {
        "students": 3,
        "loan_total": "1750000.00",
        "earned": "10500.50",
        "paid": "6500.25",
        "pending": "4000.25",
    }
    assert body["items"][0]["pf_paid_on"] == "2026-09-20"  # newest PF-paid first

    page3 = (await client.get("/provider/payouts?page=3&page_size=1",
                              headers=provider_headers(a))).json()
    assert page3["items"][0]["pf_paid_on"] == "2026-09-01"
    assert page3["summary"] == body["summary"]


async def test_date_filter_uses_pf_paid_on(client, db, crm):
    a = await make_provider(db, "A", "pa5@x.com")
    src = await map_source(db, a)
    crm.payouts = [payout_row(src, pf=date(2026, 8, 5)), payout_row(src, pf=date(2026, 9, 25))]

    res = await client.get(
        "/provider/payouts?date_from=2026-09-01&date_to=2026-09-30",
        headers=provider_headers(a),
    )
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["pf_paid_on"] == "2026-09-25"


async def test_money_serialises_as_strings(client, db, crm):
    a = await make_provider(db, "A", "pa6@x.com")
    src = await map_source(db, a)
    crm.payouts = [
        payout_row(src, serial_no=8862, full_name="Riya S"),
        payout_row(src, basis="agreed", rate=None, loan=None, earned="25000.00"),  # no lead name
    ]

    body = (await client.get("/provider/payouts", headers=provider_headers(a))).json()
    by_basis = {i["payout_basis"]: i for i in body["items"]}

    rate_row = by_basis["rate"]
    assert rate_row["serial_no"] == 8862 and rate_row["full_name"] == "Riya S"
    assert rate_row["payout_rate"] == "0.60"
    for key in ("loan_amount", "disbursed_total", "earned", "paid", "pending"):
        assert isinstance(rate_row[key], str)
    assert rate_row["loan_amount"] == "1800000.00"
    assert rate_row["disbursed_total"] == "400000.00"
    assert rate_row["pf_paid_on"] == "2026-08-20"
    assert rate_row["earned"] == "10800.00"
    assert set(rate_row) == {
        "serial_no", "full_name", "bank_name", "loan_amount", "pf_paid_on",
        "disbursed_total", "payout_basis", "payout_rate", "earned", "paid", "pending",
    }

    agreed = by_basis["agreed"]
    assert agreed["payout_rate"] is None
    assert agreed["loan_amount"] is None
    assert agreed["full_name"] is None  # row still returned without a name
    assert isinstance(body["summary"]["earned"], str)


async def test_export_returns_all_filtered_rows(client, db, crm):
    a = await make_provider(db, "A", "pa7@x.com")
    src = await map_source(db, a)
    crm.payouts = [payout_row(src) for _ in range(3)]

    res = await client.get("/provider/payouts/export", headers=provider_headers(a))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    lines = res.text.strip().splitlines()
    assert lines[0] == ("serial_no,full_name,bank_name,loan_amount,pf_paid_on,"
                        "disbursed_total,payout_basis,payout_rate,earned,paid,pending")
    assert len(lines) == 4
    assert "10800.00" in lines[1]


async def test_admin_payouts_respect_company_scope(client, db, crm):
    fmc_vendor = await make_provider(db, "FMC V", "fv@x.com", brand=Brand.FMC)
    av_vendor = await make_provider(db, "AV V", "avv@x.com", brand=Brand.AV)
    src = await map_source(db, fmc_vendor)
    crm.payouts = [payout_row(src)]

    fmc_admin = await _admin_headers(db, "fa@x.com", Brand.FMC)
    av_admin = await _admin_headers(db, "aa@x.com", Brand.AV)

    ok = await client.get(f"/admin/providers/{fmc_vendor.id}/payouts", headers=fmc_admin)
    assert ok.status_code == 200 and ok.json()["total"] == 1

    # An AV admin can't reach FMC vendors at all, and their own vendors are unsupported.
    hidden = await client.get(f"/admin/providers/{fmc_vendor.id}/payouts", headers=av_admin)
    assert hidden.status_code == 404
    own = await client.get(f"/admin/providers/{av_vendor.id}/payouts", headers=av_admin)
    assert own.status_code == 200 and own.json()["brand_supported"] is False


async def test_admin_token_cannot_use_provider_payouts(client, db):
    headers = await _admin_headers(db, "x@x.com", None)
    res = await client.get("/provider/payouts", headers=headers)
    assert res.status_code == 403


async def test_crm_outage_is_a_503_not_a_crash(client, db, crm):
    a = await make_provider(db, "A", "pa9@x.com")
    await map_source(db, a)
    crm.down = True
    res = await client.get("/provider/payouts", headers=provider_headers(a))
    assert res.status_code == 503
