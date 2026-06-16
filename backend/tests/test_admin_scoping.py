"""Company-walled admins: an FMC admin can never see or touch AV data, a
super-admin sees both, and each company runs in its own scope."""
from __future__ import annotations

import uuid

import pytest

from app.core.security import create_access_token
from app.models import AdminUser
from app.core.security import hash_password
from app.models.enums import Brand
from tests.factories import make_provider

pytestmark = pytest.mark.asyncio


async def _make_admin(db, email: str, brand: Brand | None) -> AdminUser:
    admin = AdminUser(email=email, password_hash=hash_password("pw"), brand=brand)
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    return admin


def _token(admin: AdminUser) -> dict:
    brand = admin.brand.value if admin.brand is not None else None
    token = create_access_token(subject=str(admin.id), role="admin", brand=brand)
    return {"Authorization": f"Bearer {token}"}


async def test_company_admin_only_sees_own_company_providers(client, db):
    fmc_admin = await _make_admin(db, "fmc@x.com", Brand.FMC)
    await make_provider(db, "FMC Vendor", "fv@x.com", brand=Brand.FMC)
    await make_provider(db, "AV Vendor", "av@x.com", brand=Brand.AV)

    res = await client.get("/admin/providers", headers=_token(fmc_admin))
    assert res.status_code == 200
    names = {p["name"] for p in res.json()}
    assert names == {"FMC Vendor"}  # never the AV vendor


async def test_super_admin_sees_both_companies(client, db):
    superadmin = await _make_admin(db, "super@x.com", None)
    await make_provider(db, "FMC Vendor", "fv2@x.com", brand=Brand.FMC)
    await make_provider(db, "AV Vendor", "av2@x.com", brand=Brand.AV)

    res = await client.get("/admin/providers", headers=_token(superadmin))
    assert res.status_code == 200
    names = {p["name"] for p in res.json()}
    assert names == {"FMC Vendor", "AV Vendor"}


async def test_company_admin_create_is_forced_to_own_company(client, db):
    fmc_admin = await _make_admin(db, "fmc2@x.com", Brand.FMC)
    # Even if the body asks for AV, a company admin's vendor lands in their company.
    res = await client.post(
        "/admin/providers",
        headers=_token(fmc_admin),
        json={"name": "Sneaky", "brand": "av"},
    )
    assert res.status_code == 201
    assert res.json()["brand"] == "fmc"


async def test_super_admin_must_specify_company(client, db):
    superadmin = await _make_admin(db, "super2@x.com", None)
    res = await client.post(
        "/admin/providers", headers=_token(superadmin), json={"name": "NoBrand"}
    )
    assert res.status_code == 422


async def test_company_admin_cannot_touch_other_companys_provider(client, db):
    fmc_admin = await _make_admin(db, "fmc3@x.com", Brand.FMC)
    av_provider = await make_provider(db, "AV Vendor", "av3@x.com", brand=Brand.AV)

    res = await client.put(
        f"/admin/providers/{av_provider.id}",
        headers=_token(fmc_admin),
        json={"name": "hacked"},
    )
    assert res.status_code == 404  # out-of-scope looks like "not found"


async def test_company_admin_sync_status_scoped(client, db):
    fmc_admin = await _make_admin(db, "fmc4@x.com", Brand.FMC)
    res = await client.get("/admin/sync/status", headers=_token(fmc_admin))
    assert res.status_code == 200
    # Only FMC state is ever returned for an FMC admin (no AV rows leak).
    assert all(s["brand"] == "fmc" for s in res.json()["states"])


async def test_super_admin_can_create_company_admin(client, db):
    superadmin = await _make_admin(db, "super3@x.com", None)
    res = await client.post(
        "/admin/admins",
        headers=_token(superadmin),
        json={"email": "new-fmc-admin@x.com", "brand": "fmc"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["brand"] == "fmc"
    assert body["temp_password"]  # one-time password returned

    # The new admin can actually log in and is scoped to FMC.
    login = await client.post(
        "/auth/login",
        json={"email": "new-fmc-admin@x.com", "password": body["temp_password"]},
    )
    assert login.status_code == 200
    assert login.json()["brand"] == "fmc"


async def test_company_admin_cannot_create_admins(client, db):
    fmc_admin = await _make_admin(db, "fmc5@x.com", Brand.FMC)
    res = await client.post(
        "/admin/admins",
        headers=_token(fmc_admin),
        json={"email": "sneaky-admin@x.com", "brand": "fmc"},
    )
    assert res.status_code == 403


async def test_company_admin_cannot_list_admins(client, db):
    fmc_admin = await _make_admin(db, "fmc6@x.com", Brand.FMC)
    res = await client.get("/admin/admins", headers=_token(fmc_admin))
    assert res.status_code == 403
