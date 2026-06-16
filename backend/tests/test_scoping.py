"""The non-negotiable: Provider A can NEVER read Provider B's data."""
from __future__ import annotations

from datetime import date

import pytest

from app.core.security import create_access_token
from tests.factories import add_lead, add_rollup, make_provider

pytestmark = pytest.mark.asyncio


def _provider_token(provider) -> dict:
    token = create_access_token(
        subject="user-" + str(provider.id),
        role="provider",
        provider_id=str(provider.id),
    )
    return {"Authorization": f"Bearer {token}"}


async def test_provider_cannot_see_other_providers_leads(client, db):
    a = await make_provider(db, "Provider A", "a@x.com")
    b = await make_provider(db, "Provider B", "b@x.com")

    await add_lead(db, a, full_name="Alice A", phone="9111111111")
    await add_lead(db, b, full_name="Bob B", phone="9222222222")

    res = await client.get("/me/leads", headers=_provider_token(a))
    assert res.status_code == 200
    body = res.json()
    names = {item["full_name"] for item in body["items"]}
    assert names == {"Alice A"}
    assert body["total"] == 1
    assert "Bob B" not in str(body)


async def test_overview_counts_are_scoped(client, db):
    a = await make_provider(db, "Provider A", "a2@x.com")
    b = await make_provider(db, "Provider B", "b2@x.com")
    await add_rollup(db, a, delivered=10, valid=9, qualified=4, converted=1)
    await add_rollup(db, b, delivered=99, valid=99, qualified=99, converted=99)

    res = await client.get(
        "/me/overview?from=2026-06-01&to=2026-06-02", headers=_provider_token(a)
    )
    assert res.status_code == 200
    body = res.json()
    assert body["funnel"]["delivered"] == 10  # A's number, never B's 99
    assert body["quality"]["valid"] == 9
    assert body["scorecard"]["grade"] in {"A", "B", "C", "D", "F"}


async def test_unauthenticated_is_rejected(client, db):
    res = await client.get("/me/leads")
    assert res.status_code == 401


async def test_admin_token_cannot_use_provider_routes(client, db):
    admin_token = create_access_token(subject="admin-1", role="admin")
    res = await client.get(
        "/me/leads", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res.status_code == 403
