"""Sync bookkeeping — incremental watermark + last run status per brand."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.enums import Brand


class SyncState(Base):
    __tablename__ = "sync_state"

    brand: Mapped[Brand] = mapped_column(
        Enum(Brand, name="brand", values_callable=lambda e: [m.value for m in e]),
        primary_key=True,
    )
    last_watermark: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True  # max crm_updated_at processed
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String, nullable=True)
