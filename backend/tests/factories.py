"""Small helpers to build test data: MIS rows via the ORM session, and CRM rows
for the in-memory fake CRM (see the `crm` fixture in conftest.py)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.security import create_access_token, hash_password
from app.models import Provider, ProviderSource, ProviderUser
from app.models.enums import Brand


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


async def map_source(
    db, provider: Provider, source_id: uuid.UUID | None = None, name: str = "src"
) -> uuid.UUID:
    """Map a CRM source to the provider (in its own company); returns the id."""
    source_id = source_id or uuid.uuid4()
    db.add(ProviderSource(provider_id=provider.id, brand=provider.brand,
                          crm_source_id=source_id, source_name=name))
    await db.commit()
    return source_id


def provider_headers(provider: Provider) -> dict:
    token = create_access_token(
        subject="u-" + str(provider.id), role="provider", provider_id=str(provider.id)
    )
    return {"Authorization": f"Bearer {token}"}


def lead_row(
    source_id: uuid.UUID,
    *,
    created_at: datetime = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    raw_stage: str | None = "qualified",
    full_name: str = "Test Lead",
    phone: str | None = "9000000001",
    serial_no: int | None = None,
    is_duplicate: bool = False,
    contacted_at: datetime | None = None,
    qualified_at: datetime | None = None,
    converted_at: datetime | None = None,
    lost_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> dict:
    """A row shaped like `app.crm.reader.lead_rows` output (phone normalized,
    duplicate flag and milestones already worked out by the SQL)."""
    return {
        "crm_lead_id": uuid.uuid4(),
        "serial_no": serial_no,
        "full_name": full_name,
        "phone": phone,
        "crm_source_id": source_id,
        "raw_stage": raw_stage,
        "created_at": created_at,
        "crm_updated_at": updated_at or created_at,
        "is_duplicate": is_duplicate,
        "contacted_at": contacted_at,
        "qualified_at": qualified_at,
        "converted_at": converted_at,
        "lost_at": lost_at,
    }


def payout_row(
    source_id: uuid.UUID,
    *,
    lead_id: uuid.UUID | None = None,
    bank: str = "UC PNB",
    loan: str | None = "1800000.00",
    disbursed: str = "400000.00",
    earned: str = "10800.00",
    paid: str = "0.00",
    pf: date | None = date(2026, 8, 20),
    basis: str = "rate",
    rate: str | None = "0.60",
    serial_no: int | None = None,
    full_name: str | None = None,
) -> dict:
    """A row shaped like `app.crm.reader.payout_rows` output (the FMC view
    plus the student's serial/name from the leads table)."""
    pending = max(Decimal(earned) - Decimal(paid), Decimal("0"))
    return {
        "lead_bank_id": uuid.uuid4(),
        "lead_id": lead_id or uuid.uuid4(),
        "lead_source_id": source_id,
        "bank_name": bank,
        "loan_amount": None if loan is None else Decimal(loan),
        "pf_paid_on": pf,
        "disbursed_total": Decimal(disbursed),
        "payout_basis": basis,
        "payout_rate": None if rate is None else Decimal(rate),
        "payout_earned": Decimal(earned),
        "payout_paid": Decimal(paid),
        "payout_pending": pending,
        "updated_at": datetime(2026, 9, 30, 10, tzinfo=timezone.utc),
        "serial_no": serial_no,
        "full_name": full_name,
    }
