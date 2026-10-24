"""A partner's billing details, set up once and printed on every invoice the
MIS makes for them (FundMyCampus partners only)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.database import Base


class BillingProfile(Base):
    __tablename__ = "provider_billing_profiles"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("providers.id", ondelete="CASCADE"), primary_key=True
    )
    # True = GST-registered: a tax invoice with GST. False = a plain invoice, no GST.
    gst_registered: Mapped[bool] = mapped_column(Boolean, nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    pan: Mapped[str] = mapped_column(String(10), nullable=False)
    address_line1: Mapped[str] = mapped_column(String(200), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    pincode: Mapped[str] = mapped_column(String(6), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    bank_account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    bank_account_number: Mapped[str] = mapped_column(String(30), nullable=False)
    bank_ifsc: Mapped[str] = mapped_column(String(11), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    bank_branch: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    signatory_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_by_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
