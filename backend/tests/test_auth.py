"""Login flow + /auth/me."""
from __future__ import annotations

import pytest

from tests.factories import make_provider

pytestmark = pytest.mark.asyncio


async def test_login_success_and_me(client, db):
    await make_provider(db, "Acme Leads", "vendor@acme.com", password="s3cret")

    res = await client.post(
        "/auth/login", json={"email": "vendor@acme.com", "password": "s3cret"}
    )
    assert res.status_code == 200
    token = res.json()["access_token"]
    assert res.json()["role"] == "provider"

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "vendor@acme.com"
    assert me.json()["provider_name"] == "Acme Leads"


async def test_login_wrong_password(client, db):
    await make_provider(db, "Acme Leads", "vendor2@acme.com", password="s3cret")
    res = await client.post(
        "/auth/login", json={"email": "vendor2@acme.com", "password": "wrong"}
    )
    assert res.status_code == 401


async def test_login_unknown_email(client, db):
    res = await client.post(
        "/auth/login", json={"email": "nobody@nowhere.com", "password": "x"}
    )
    assert res.status_code == 401


async def _admin_headers(db, email, brand):
    from app.core.security import create_access_token, hash_password
    from app.models import AdminUser
    admin = AdminUser(email=email, password_hash=hash_password("pw"), brand=brand)
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    token = create_access_token(subject=str(admin.id), role="admin",
                                brand=brand.value if brand else None)
    return {"Authorization": f"Bearer {token}"}


async def _login(client, email, password):
    return await client.post("/auth/login", json={"email": email, "password": password})


async def test_admin_resets_provider_password(client, db):
    from app.models.enums import Brand
    p = await make_provider(db, "Altera", "alt@x.com", password="old-pass")
    h = await _admin_headers(db, "fa@x.com", Brand.FMC)

    users = (await client.get(f"/admin/providers/{p.id}/users", headers=h)).json()
    assert [u["email"] for u in users] == ["alt@x.com"]
    assert "password_hash" not in users[0]
    uid = users[0]["id"]

    # Generated password: returned once, old one stops working.
    res = await client.post(f"/admin/providers/{p.id}/users/{uid}/reset-password", headers=h)
    assert res.status_code == 200
    new = res.json()["temp_password"]
    assert new and len(new) >= 12
    assert (await _login(client, "alt@x.com", "old-pass")).status_code == 401
    assert (await _login(client, "alt@x.com", new)).status_code == 200

    # Chosen password: not echoed back.
    res = await client.post(f"/admin/providers/{p.id}/users/{uid}/reset-password",
                            headers=h, json={"password": "Chosen-Pass-1"})
    assert res.status_code == 200 and res.json()["temp_password"] is None
    assert (await _login(client, "alt@x.com", "Chosen-Pass-1")).status_code == 200

    too_short = await client.post(f"/admin/providers/{p.id}/users/{uid}/reset-password",
                                  headers=h, json={"password": "short"})
    assert too_short.status_code == 422


async def test_password_reset_is_company_scoped(client, db):
    import uuid as _uuid
    from app.models.enums import Brand
    fmc = await make_provider(db, "F", "f@x.com", password="keep-me")
    other = await make_provider(db, "O", "o@x.com")
    av_admin = await _admin_headers(db, "aa@x.com", Brand.AV)
    fmc_admin = await _admin_headers(db, "fa2@x.com", Brand.FMC)
    uid = (await client.get(f"/admin/providers/{fmc.id}/users", headers=fmc_admin)).json()[0]["id"]

    # Other company's admin can't see or reset it.
    assert (await client.get(f"/admin/providers/{fmc.id}/users", headers=av_admin)).status_code == 404
    r = await client.post(f"/admin/providers/{fmc.id}/users/{uid}/reset-password", headers=av_admin)
    assert r.status_code == 404
    # A user id must belong to the provider in the path.
    r = await client.post(f"/admin/providers/{other.id}/users/{uid}/reset-password", headers=fmc_admin)
    assert r.status_code == 404
    r = await client.post(f"/admin/providers/{fmc.id}/users/{_uuid.uuid4()}/reset-password", headers=fmc_admin)
    assert r.status_code == 404
    # Providers can't call it at all.
    from tests.factories import provider_headers
    r = await client.post(f"/admin/providers/{fmc.id}/users/{uid}/reset-password",
                          headers=provider_headers(fmc))
    assert r.status_code == 403
    assert (await _login(client, "f@x.com", "keep-me")).status_code == 200
