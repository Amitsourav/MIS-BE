"""Sales-demo partners: flagged is_demo, visible to their own login (so the
portal can show "AI Calling"), kept out of the admin leaderboard."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.security import create_access_token, hash_password
from app.models import AdminUser
from app.models.enums import Brand
from tests.factories import lead_row, make_provider, map_source, provider_headers

pytestmark = pytest.mark.asyncio


async def _admin(db, email="demo-admin@x.com", brand=Brand.FMC):
    admin = AdminUser(email=email, password_hash=hash_password("pw"), brand=brand)
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    token = create_access_token(subject=str(admin.id), role="admin",
                                brand=brand.value if brand else None)
    return {"Authorization": f"Bearer {token}"}


async def test_admin_creates_and_toggles_demo_provider(client, db):
    h = await _admin(db)
    created = (await client.post("/admin/providers", headers=h,
                                 json={"name": "Demo Partner – EduLink", "is_demo": True})).json()
    assert created["is_demo"] is True
    real = (await client.post("/admin/providers", headers=h, json={"name": "Real"})).json()
    assert real["is_demo"] is False

    listed = {p["name"]: p["is_demo"] for p in
              (await client.get("/admin/providers", headers=h)).json()}
    assert listed == {"Demo Partner – EduLink": True, "Real": False}

    off = (await client.put(f"/admin/providers/{created['id']}", headers=h,
                            json={"is_demo": False})).json()
    assert off["is_demo"] is False


async def test_login_and_me_carry_is_demo(client, db):
    demo = await make_provider(db, "Demo", "demo@x.com", password="secret123")
    demo.is_demo = True
    await db.commit()
    real = await make_provider(db, "Altera", "real@x.com", password="secret123")

    login = (await client.post("/auth/login",
                               json={"email": "demo@x.com", "password": "secret123"})).json()
    assert login["is_demo"] is True
    me = (await client.get("/auth/me", headers=provider_headers(demo))).json()
    assert me["is_demo"] is True and me["provider_name"] == "Demo"

    login2 = (await client.post("/auth/login",
                                json={"email": "real@x.com", "password": "secret123"})).json()
    assert login2["is_demo"] is False
    me2 = (await client.get("/auth/me", headers=provider_headers(real))).json()
    assert me2["is_demo"] is False


async def test_demo_provider_left_out_of_leaderboard(client, db, crm):
    real = await make_provider(db, "Real", "r@x.com")
    demo = await make_provider(db, "Demo", "d@x.com")
    demo.is_demo = True
    await db.commit()
    sr, sd = await map_source(db, real), await map_source(db, demo)
    when = datetime(2026, 6, 1, 10, tzinfo=timezone.utc)
    crm.leads[Brand.FMC] = [
        lead_row(sr, phone="9000000001", created_at=when),
        lead_row(sd, phone="9000000002", created_at=when, raw_stage="disbursed"),
    ]
    board = (await client.get("/admin/leaderboard?from=2026-06-01&to=2026-06-10",
                              headers=await _admin(db, "lb@x.com", None))).json()
    assert [r["provider_name"] for r in board] == ["Real"]

    # The demo partner's own portal still works normally.
    ov = await client.get("/me/leads?all_time=true", headers=provider_headers(demo))
    assert ov.status_code == 200 and ov.json()["total"] == 1
