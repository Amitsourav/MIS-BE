"""Mapping a CRM source makes its existing leads and payouts visible to the
provider immediately — data is live, so there is no backfill step to run."""
from __future__ import annotations

import uuid

import pytest

from app.core.security import create_access_token, hash_password
from app.models import AdminUser
from app.models.enums import Brand
from tests.factories import lead_row, make_provider, payout_row, provider_headers

pytestmark = pytest.mark.asyncio


async def _fmc_admin_headers(db) -> dict:
    admin = AdminUser(email="fmcadmin@x.com", password_hash=hash_password("pw"), brand=Brand.FMC)
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    token = create_access_token(subject=str(admin.id), role="admin", brand="fmc")
    return {"Authorization": f"Bearer {token}"}


async def test_mapping_a_source_shows_its_leads_and_payouts_at_once(client, db, crm):
    provider = await make_provider(db, "Altera", "alt@x.com")
    src = uuid.uuid4()
    crm.leads[Brand.FMC] = [lead_row(src, phone="9000000001"), lead_row(src, phone="9000000002")]
    crm.payouts = [payout_row(src), payout_row(src, bank="SBI")]
    headers = provider_headers(provider)

    before = await client.get("/me/leads?from=2026-06-01&to=2026-06-02", headers=headers)
    assert before.json()["total"] == 0  # not mapped yet

    res = await client.post(
        f"/admin/providers/{provider.id}/sources",
        headers=await _fmc_admin_headers(db),
        json={"brand": "fmc", "crm_source_id": str(src)},
    )
    assert res.status_code == 201

    after = await client.get("/me/leads?from=2026-06-01&to=2026-06-02", headers=headers)
    assert after.json()["total"] == 2
    pay = await client.get("/provider/payouts", headers=headers)
    assert pay.json()["total"] == 2


async def test_unmapped_sources_stay_hidden(client, db, crm):
    provider = await make_provider(db, "Acme", "acme@x.com")
    crm.leads[Brand.FMC] = [lead_row(uuid.uuid4())]
    crm.payouts = [payout_row(uuid.uuid4())]
    headers = provider_headers(provider)

    assert (await client.get("/me/leads?all_time=true", headers=headers)).json()["total"] == 0
    assert (await client.get("/provider/payouts", headers=headers)).json()["total"] == 0


async def test_mapping_an_already_mapped_source_conflicts(client, db, crm):
    a = await make_provider(db, "A", "a@x.com")
    b = await make_provider(db, "B", "b@x.com")
    headers = await _fmc_admin_headers(db)
    src = str(uuid.uuid4())

    first = await client.post(f"/admin/providers/{a.id}/sources", headers=headers,
                              json={"brand": "fmc", "crm_source_id": src})
    assert first.status_code == 201
    second = await client.post(f"/admin/providers/{b.id}/sources", headers=headers,
                               json={"brand": "fmc", "crm_source_id": src})
    assert second.status_code == 409
