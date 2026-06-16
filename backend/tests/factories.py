"""Small helpers to build test data directly via the ORM session."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.security import hash_password
from app.models import (
    MisLead,
    Provider,
    ProviderDailyMetric,
    ProviderUser,
)
from app.models.enums import Brand, CanonicalStage


async def make_provider(
    db, name: str, email: str, password: str = "pw", brand: Brand = Brand.FMC
) -> Provider:
    provider = Provider(name=name, brand=brand)
    db.add(provider)
    await db.flush()
    db.add(
        ProviderUser(
            provider_id=provider.id,
            email=email,
            password_hash=hash_password(password),
        )
    )
    await db.commit()
    await db.refresh(provider)
    return provider


async def add_lead(
    db,
    provider: Provider,
    *,
    brand: Brand = Brand.FMC,
    stage: CanonicalStage = CanonicalStage.QUALIFIED,
    full_name: str = "Test Lead",
    phone: str = "9000000001",
    created_at: datetime | None = None,
    is_invalid: bool = False,
    is_duplicate: bool = False,
) -> MisLead:
    lead = MisLead(
        brand=brand,
        crm_lead_id=uuid.uuid4(),
        provider_id=provider.id,
        crm_source_id=uuid.uuid4(),
        full_name=full_name,
        phone=phone,
        canonical_stage=stage,
        is_invalid=is_invalid,
        is_duplicate=is_duplicate,
        created_at=created_at or datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        qualified_at=(
            datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
            if stage == CanonicalStage.QUALIFIED
            else None
        ),
    )
    db.add(lead)
    await db.commit()
    return lead


async def add_rollup(
    db,
    provider: Provider,
    *,
    brand: Brand = Brand.FMC,
    metric_date=None,
    delivered: int = 10,
    valid: int = 9,
    qualified: int = 4,
    converted: int = 1,
) -> None:
    from datetime import date

    db.add(
        ProviderDailyMetric(
            provider_id=provider.id,
            brand=brand,
            metric_date=metric_date or date(2026, 6, 1),
            delivered=delivered,
            invalid=1,
            duplicates=0,
            valid=valid,
            contacted=valid,
            connected=qualified,
            qualified=qualified,
            converted=converted,
            dnp=0,
            lost=0,
        )
    )
    await db.commit()
