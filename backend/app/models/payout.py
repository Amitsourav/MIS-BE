"""Partner payouts — one row per CRM lender file (student × lender) that has
reached PF paid and carries a payout (rate × sanctioned loan amount, or a hand-
agreed amount). Mirrored from the FMC view `public.mis_partner_payouts`;
full-refreshed on every sync."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.database import Base
from app.models.enums import Brand


class MisPayout(Base):
    __tablename__ = "mis_payouts"
    __table_args__ = (
        UniqueConstraint("brand", "crm_lead_bank_id", name="uq_mis_payouts_brand_lead_bank"),
        Index("idx_mis_payouts_provider", "provider_id", "pf_paid_on"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    brand: Mapped[Brand] = mapped_column(Enum(Brand, name="brand", values_callable=lambda e: [m.value for m in e]), nullable=False)
    crm_lead_bank_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    crm_lead_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    crm_source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("providers.id"), nullable=True  # null = unmapped source
    )

    bank_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # Sanctioned by this lender — what the rate applies to.
    loan_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # The date the payout is earned; drives date filters and ordering.
    pf_paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Released by the lender so far — information only, not a payout input.
    disbursed_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    payout_basis: Mapped[str] = mapped_column(String, nullable=False)  # 'rate' | 'agreed'
    payout_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    payout_earned: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    payout_paid: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    payout_pending: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    crm_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
