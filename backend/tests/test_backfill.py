"""Unmapped leads are stored with provider_id=null and get backfilled (claimed +
rolled up) when their CRM source is later mapped to a provider."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models import MisLead
from app.models.enums import Brand, CanonicalStage
from app.services.rollup import claim_unmapped_leads, recompute_cohorts
from tests.factories import make_provider

pytestmark = pytest.mark.asyncio


async def _add_unmapped_lead(db, *, brand, crm_source_id, phone="9000000001"):
    lead = MisLead(
        brand=brand,
        crm_lead_id=uuid.uuid4(),
        provider_id=None,  # unmapped
        crm_source_id=crm_source_id,
        full_name="Orphan Lead",
        phone=phone,
        canonical_stage=CanonicalStage.QUALIFIED,
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        qualified_at=datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc),
    )
    db.add(lead)
    await db.commit()
    return lead


async def test_claim_backfills_and_rolls_up(client, db):
    provider = await make_provider(db, "Acme", "acme@x.com", brand=Brand.FMC)
    source_id = uuid.uuid4()
    await _add_unmapped_lead(db, brand=Brand.FMC, crm_source_id=source_id)
    await _add_unmapped_lead(db, brand=Brand.FMC, crm_source_id=source_id, phone="9000000002")

    claimed = await claim_unmapped_leads(
        db, provider_id=provider.id, brand=Brand.FMC, crm_source_id=source_id
    )
    await db.commit()
    assert claimed == 2

    # Leads now belong to the provider and show up in their scoped overview.
    from app.core.security import create_access_token

    token = create_access_token(
        subject="u", role="provider", provider_id=str(provider.id)
    )
    res = await client.get(
        "/me/overview?from=2026-06-01&to=2026-06-02",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["funnel"]["delivered"] == 2  # rolled up after backfill


async def test_claim_ignores_other_sources(client, db):
    provider = await make_provider(db, "Acme2", "acme2@x.com", brand=Brand.FMC)
    mine = uuid.uuid4()
    other = uuid.uuid4()
    await _add_unmapped_lead(db, brand=Brand.FMC, crm_source_id=mine)
    await _add_unmapped_lead(db, brand=Brand.FMC, crm_source_id=other, phone="9000000003")

    claimed = await claim_unmapped_leads(
        db, provider_id=provider.id, brand=Brand.FMC, crm_source_id=mine
    )
    await db.commit()
    assert claimed == 1  # only the matching source's lead


async def test_claim_with_nothing_to_backfill(client, db):
    provider = await make_provider(db, "Acme3", "acme3@x.com", brand=Brand.FMC)
    claimed = await claim_unmapped_leads(
        db, provider_id=provider.id, brand=Brand.FMC, crm_source_id=uuid.uuid4()
    )
    assert claimed == 0
