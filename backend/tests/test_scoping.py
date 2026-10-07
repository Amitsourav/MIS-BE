"""The non-negotiable: Provider A can NEVER read Provider B's data — and every
live CRM query is scoped to the caller's own mapped sources."""
from __future__ import annotations

import uuid

import pytest

from app.core.security import create_access_token
from app.models.enums import Brand
from tests.factories import lead_row, make_provider, map_source, provider_headers

pytestmark = pytest.mark.asyncio

RANGE = "from=2026-06-01&to=2026-06-02"


async def test_provider_cannot_see_other_providers_leads(client, db, crm):
    a = await make_provider(db, "Provider A", "a@x.com")
    b = await make_provider(db, "Provider B", "b@x.com")
    sa, sb = await map_source(db, a), await map_source(db, b)
    crm.leads[Brand.FMC] = [
        lead_row(sa, full_name="Alice A", phone="9111111111"),
        lead_row(sb, full_name="Bob B", phone="9222222222"),
    ]

    res = await client.get(f"/me/leads?{RANGE}", headers=provider_headers(a))
    assert res.status_code == 200
    body = res.json()
    assert {item["full_name"] for item in body["items"]} == {"Alice A"}
    assert body["total"] == 1
    assert "Bob B" not in str(body)
    # The CRM was only ever asked for A's own source.
    assert crm.calls == [("lead_rows", Brand.FMC, [sa])]


async def test_overview_counts_are_scoped(client, db, crm):
    a = await make_provider(db, "Provider A", "a2@x.com")
    b = await make_provider(db, "Provider B", "b2@x.com")
    sa, sb = await map_source(db, a), await map_source(db, b)
    crm.leads[Brand.FMC] = [lead_row(sa, phone=f"91111111{i:02d}") for i in range(3)] + [
        lead_row(sb, phone=f"92222222{i:02d}") for i in range(9)
    ]

    res = await client.get(f"/me/overview?{RANGE}", headers=provider_headers(a))
    assert res.status_code == 200
    body = res.json()
    assert body["funnel"]["delivered"] == 3  # A's number, never B's 9
    assert body["quality"]["valid"] == 3
    assert body["scorecard"]["grade"] in {"A", "B", "C", "D", "F"}


async def test_provider_without_sources_sees_nothing(client, db, crm):
    a = await make_provider(db, "No Sources", "ns@x.com")
    crm.leads[Brand.FMC] = [lead_row(uuid.uuid4())]  # someone else's source

    res = await client.get(f"/me/leads?{RANGE}", headers=provider_headers(a))
    assert res.json()["total"] == 0
    assert crm.calls == [("lead_rows", Brand.FMC, [])]


async def test_other_company_sources_are_never_used(client, db, crm):
    """A vendor's scope is only its own company's sources."""
    a = await make_provider(db, "FMC Vendor", "fv@x.com", brand=Brand.FMC)
    await map_source(db, a)
    res = await client.get(f"/me/overview?brand=av&{RANGE}", headers=provider_headers(a))
    assert res.status_code == 200
    assert res.json()["funnel"]["delivered"] == 0
    assert crm.calls == []  # never queried the AV CRM for an FMC vendor


async def test_unauthenticated_is_rejected(client, db):
    res = await client.get("/me/leads")
    assert res.status_code == 401


async def test_admin_token_cannot_use_provider_routes(client, db):
    admin_token = create_access_token(subject="admin-1", role="admin")
    res = await client.get(
        "/me/leads", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res.status_code == 403
