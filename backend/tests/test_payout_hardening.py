"""Payout money rules and safety fixes from the October audit: exact rounding,
rates never rounded, the view's 'agreed' default, overpayment visible, safe
CSV, logins that stop working at once, un-mapping, unmapped payouts, login
throttling per account."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import create_access_token, hash_password
from app.models import AdminUser, ProviderUser
from app.models.enums import Brand
from tests.factories import lead_row, make_provider, map_source, payout_row, provider_headers

pytestmark = pytest.mark.asyncio


async def _admin(db, email, brand):
    admin = AdminUser(email=email, password_hash=hash_password("pw"), brand=brand)
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    token = create_access_token(subject=str(admin.id), role="admin",
                                brand=brand.value if brand else None)
    return {"Authorization": f"Bearer {token}"}


def _csv(text: str) -> list[dict]:
    assert text.startswith("﻿")
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


# ---------------- money ----------------
async def test_money_rounds_half_up_and_summary_rounds_once(client, db, crm):
    p = await make_provider(db, "A", "a@x.com")
    src = await map_source(db, p)
    crm.payouts = [payout_row(src, earned="10.005", paid="0") for _ in range(3)]
    body = (await client.get("/provider/payouts", headers=provider_headers(p))).json()
    assert {i["earned"] for i in body["items"]} == {"10.01"}  # half up, not 10.00
    assert body["summary"]["earned"] == "30.02"  # exact 30.015 rounded once, not 3 x 10.00
    assert body["summary"]["pending"] == "30.02"

    crm.payouts = [payout_row(src, earned="0.005", paid="0")]
    one = (await client.get("/provider/payouts", headers=provider_headers(p))).json()
    assert one["items"][0]["earned"] == "0.01"


async def test_rate_is_shown_exactly(client, db, crm):
    p = await make_provider(db, "A", "a2@x.com")
    src = await map_source(db, p)
    crm.payouts = [
        payout_row(src, rate="0.625", pf=datetime(2026, 9, 3).date()),
        payout_row(src, rate="0.6", pf=datetime(2026, 9, 2).date()),
        payout_row(src, rate="0.6000", pf=datetime(2026, 9, 1).date()),
        payout_row(src, rate="1", pf=datetime(2026, 8, 31).date()),
    ]
    body = (await client.get("/provider/payouts", headers=provider_headers(p))).json()
    assert [i["payout_rate"] for i in body["items"]] == ["0.625", "0.60", "0.60", "1.00"]


async def test_missing_basis_defaults_to_agreed_like_the_view(client, db, crm):
    p = await make_provider(db, "A", "a3@x.com")
    src = await map_source(db, p)
    row = payout_row(src, rate=None)
    row["payout_basis"] = None
    crm.payouts = [row]
    body = (await client.get("/provider/payouts", headers=provider_headers(p))).json()
    assert body["items"][0]["payout_basis"] == "agreed"


async def test_overpayment_is_visible_not_hidden_as_zero_pending(client, db, crm):
    p = await make_provider(db, "A", "a4@x.com")
    src = await map_source(db, p)
    crm.payouts = [
        payout_row(src, earned="10000.00", paid="12000.00"),  # overpaid by 2000
        payout_row(src, earned="5000.00", paid="1000.00"),
    ]
    body = (await client.get("/provider/payouts", headers=provider_headers(p))).json()
    by_paid = {i["paid"]: i for i in body["items"]}
    assert by_paid["12000.00"]["pending"] == "0.00"
    assert by_paid["12000.00"]["overpaid"] == "2000.00"
    assert by_paid["1000.00"]["overpaid"] == "0.00"
    s = body["summary"]
    assert (s["earned"], s["paid"], s["pending"], s["overpaid"]) == (
        "15000.00", "13000.00", "4000.00", "2000.00")


# ---------------- CSV ----------------
async def test_payout_csv_is_excel_safe(client, db, crm):
    p = await make_provider(db, "A", "a5@x.com")
    src = await map_source(db, p)
    crm.payouts = [payout_row(src, full_name='=HYPERLINK("http://x","click")', bank="+SBI"),
                   payout_row(src, full_name="प्रिया शर्मा", bank="HDFC")]
    res = await client.get("/provider/payouts/export", headers=provider_headers(p))
    assert res.headers["content-type"] == "text/csv; charset=utf-8"
    rows = _csv(res.text)
    names = {r["full_name"] for r in rows}
    assert "'=HYPERLINK(\"http://x\",\"click\")" in names  # shown as text, never run
    assert "प्रिया शर्मा" in names
    assert {r["bank_name"] for r in rows} == {"'+SBI", "HDFC"}
    assert all(r["earned"] == "10800.00" for r in rows)  # numbers untouched


async def test_leads_csv_is_excel_safe(client, db, crm):
    p = await make_provider(db, "A", "a6@x.com")
    src = await map_source(db, p, name="@src")
    crm.leads[Brand.FMC] = [lead_row(src, full_name="-2+3", phone="9000000001")]
    res = await client.get("/me/leads/export?all_time=true", headers=provider_headers(p))
    row = _csv(res.text)[0]
    assert row["full_name"] == "'-2+3" and "source" not in row
    assert row["phone"] == "9000000001"


# ---------------- logins ----------------
async def _user(db, provider) -> ProviderUser:
    return await db.get(ProviderUser, provider._test_user_id)


async def test_deactivated_login_is_rejected_immediately(client, db, crm):
    p = await make_provider(db, "A", "a7@x.com")
    await map_source(db, p)
    h = provider_headers(p)
    assert (await client.get("/provider/payouts", headers=h)).status_code == 200
    user = await _user(db, p)
    user.is_active = False
    await db.commit()
    for path in ("/provider/payouts", "/provider/payouts/export", "/me/leads", "/me/overview"):
        assert (await client.get(path, headers=h)).status_code == 401, path


async def test_password_reset_signs_out_old_sessions(client, db, crm):
    p = await make_provider(db, "A", "a8@x.com", password="old-password")
    await map_source(db, p)
    old_token = provider_headers(p)
    assert (await client.get("/provider/payouts", headers=old_token)).status_code == 200

    user = await _user(db, p)
    user.password_changed_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    await db.commit()
    assert (await client.get("/provider/payouts", headers=old_token)).status_code == 401

    # a fresh login after the reset works
    user.password_changed_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    user.password_hash = hash_password("new-password-1")
    await db.commit()
    res = await client.post("/auth/login", json={"email": "a8@x.com", "password": "new-password-1"})
    assert res.status_code == 200
    fresh = {"Authorization": f"Bearer {res.json()['access_token']}"}
    assert (await client.get("/provider/payouts", headers=fresh)).status_code == 200


async def test_reset_endpoint_stamps_password_change(client, db, crm):
    p = await make_provider(db, "A", "a9@x.com")
    h = await _admin(db, "f@x.com", Brand.FMC)
    uid = p._test_user_id
    res = await client.post(f"/admin/providers/{p.id}/users/{uid}/reset-password", headers=h)
    assert res.status_code == 200
    await db.refresh(await _user(db, p))
    assert (await _user(db, p)).password_changed_at is not None


async def test_token_for_another_providers_login_is_rejected(client, db, crm):
    a = await make_provider(db, "A", "a10@x.com")
    b = await make_provider(db, "B", "b10@x.com")
    await map_source(db, b)
    forged = create_access_token(subject=str(a._test_user_id), role="provider",
                                 provider_id=str(b.id))  # A's login claiming B
    res = await client.get("/provider/payouts", headers={"Authorization": f"Bearer {forged}"})
    assert res.status_code == 401


async def test_login_locks_an_account_after_10_failures_not_other_accounts(client, db):
    await make_provider(db, "A", "victim@x.com", password="right-pass")
    await make_provider(db, "B", "other@x.com", password="right-pass")
    for _ in range(10):
        r = await client.post("/auth/login", json={"email": "victim@x.com", "password": "nope"})
        assert r.status_code == 401
    locked = await client.post("/auth/login", json={"email": "victim@x.com", "password": "right-pass"})
    assert locked.status_code == 429
    ok = await client.post("/auth/login", json={"email": "other@x.com", "password": "right-pass"})
    assert ok.status_code == 200  # other partners behind the same portal IP unaffected


# ---------------- admin: un-map + unmapped payouts ----------------
async def test_unmapping_a_source_removes_its_data_at_once(client, db, crm):
    wrong = await make_provider(db, "Wrong", "w@x.com")
    right = await make_provider(db, "Right", "r@x.com")
    src = await map_source(db, wrong)
    crm.payouts = [payout_row(src)]
    h = await _admin(db, "fa@x.com", Brand.FMC)
    assert (await client.get("/provider/payouts", headers=provider_headers(wrong))).json()["total"] == 1

    mapping_id = (await client.get(f"/admin/providers/{wrong.id}/sources", headers=h)).json()[0]["id"]
    av = await _admin(db, "aa@x.com", Brand.AV)
    assert (await client.delete(f"/admin/providers/{wrong.id}/sources/{mapping_id}",
                                headers=av)).status_code == 404  # other company
    assert (await client.delete(f"/admin/providers/{right.id}/sources/{mapping_id}",
                                headers=h)).status_code == 404  # wrong provider in path
    res = await client.delete(f"/admin/providers/{wrong.id}/sources/{mapping_id}", headers=h)
    assert res.status_code == 204
    assert (await client.get("/provider/payouts", headers=provider_headers(wrong))).json()["total"] == 0

    remap = await client.post(f"/admin/providers/{right.id}/sources", headers=h,
                              json={"brand": "fmc", "crm_source_id": str(src)})
    assert remap.status_code == 201
    assert (await client.get("/provider/payouts", headers=provider_headers(right))).json()["total"] == 1


async def test_admin_sees_payouts_no_partner_can_see(client, db, crm):
    p = await make_provider(db, "A", "u@x.com")
    mapped = await map_source(db, p)
    orphan = uuid.uuid4()
    crm.sources[Brand.FMC] = [{"crm_source_id": orphan, "name": "MBA Aspire"}]
    crm.payouts = [payout_row(mapped), payout_row(orphan, earned="4870.00", full_name="X")]

    h = await _admin(db, "fa2@x.com", Brand.FMC)
    body = (await client.get("/admin/payouts/unmapped", headers=h)).json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["source_name"] == "MBA Aspire" and item["crm_source_id"] == str(orphan)
    assert item["earned"] == "4870.00" and body["summary"]["earned"] == "4870.00"

    sup = await _admin(db, "s2@x.com", None)
    assert (await client.get("/admin/payouts/unmapped", headers=sup)).status_code == 200
    av = await _admin(db, "aa2@x.com", Brand.AV)
    assert (await client.get("/admin/payouts/unmapped", headers=av)).status_code == 403
    assert (await client.get("/admin/payouts/unmapped",
                             headers=provider_headers(p))).status_code == 403


async def test_unmapped_payouts_keep_internal_serial_and_route(client, db, crm):
    """The admin-only unassigned list is internal: FMC serial + CRM route name."""
    orphan = uuid.uuid4()
    crm.payouts = [payout_row(orphan, bank="UC PNB", serial_no=8862)]
    h = await _admin(db, "fa3@x.com", Brand.FMC)
    item = (await client.get("/admin/payouts/unmapped", headers=h)).json()["items"][0]
    assert item["serial_no"] == 8862
    assert item["crm_bank_name"] == "UC PNB" and item["bank_name"] == "PNB"
