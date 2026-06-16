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
