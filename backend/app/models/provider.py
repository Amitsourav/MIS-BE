"""Provider accounts, their login users, admin users, and source mapping."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid

from app.database import Base
from app.models.enums import Brand

# JSONB on Postgres, plain JSON elsewhere (tests on SQLite).
JsonType = JSON().with_variant(JSONB, "postgresql")


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # The company this vendor belongs to. Vendors are walled per company:
    # an FMC admin never sees AV vendors and vice-versa.
    brand: Mapped[Brand] = mapped_column(Enum(Brand, name="brand", values_callable=lambda e: [m.value for m in e]), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    payout_config: Mapped[dict] = mapped_column(JsonType, default=dict)  # Phase 2
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    users: Mapped[list["ProviderUser"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )
    sources: Mapped[list["ProviderSource"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )


class ProviderUser(Base):
    __tablename__ = "provider_users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("providers.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    provider: Mapped["Provider"] = relationship(back_populates="users")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    # Company scope. NULL = super-admin (manages BOTH companies); 'fmc'/'av' =
    # company admin restricted to that one company.
    brand: Mapped[Brand | None] = mapped_column(
        Enum(Brand, name="brand", values_callable=lambda e: [m.value for m in e]), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProviderSource(Base):
    """Maps a CRM `lead_sources.id` (per brand) to exactly one provider."""

    __tablename__ = "provider_sources"
    __table_args__ = (
        UniqueConstraint("brand", "crm_source_id", name="uq_provider_sources_brand_src"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("providers.id", ondelete="CASCADE"), nullable=False
    )
    brand: Mapped[Brand] = mapped_column(Enum(Brand, name="brand", values_callable=lambda e: [m.value for m in e]), nullable=False)
    crm_source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_name: Mapped[str | None] = mapped_column(String, nullable=True)

    provider: Mapped["Provider"] = relationship(back_populates="sources")
