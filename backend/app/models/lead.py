"""Normalized lead facts synced from both CRMs (read-only sources)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.database import Base
from app.models.enums import Brand, CanonicalStage


class MisLead(Base):
    __tablename__ = "mis_leads"
    __table_args__ = (
        UniqueConstraint("brand", "crm_lead_id", name="uq_mis_leads_brand_lead"),
        Index("idx_mis_leads_provider", "provider_id", "brand", "created_at"),
        Index("idx_mis_leads_stage", "provider_id", "canonical_stage"),
        # Supports the 30-day duplicate-window lookup by (brand, phone, created_at).
        Index("idx_mis_leads_dup", "brand", "phone", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    brand: Mapped[Brand] = mapped_column(Enum(Brand, name="brand", values_callable=lambda e: [m.value for m in e]), nullable=False)
    crm_lead_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("providers.id"), nullable=True  # null = unmapped source
    )
    crm_source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    serial_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)

    raw_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    canonical_stage: Mapped[CanonicalStage] = mapped_column(
        Enum(
            CanonicalStage,
            name="canonical_stage",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    is_invalid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invalid_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    contacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    qualified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    converted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lost_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    crm_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True  # incremental-sync watermark source
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
