"""Pre-aggregated daily rollups — what the dashboard reads (never raw leads)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.database import Base
from app.models.enums import Brand


class ProviderDailyMetric(Base):
    __tablename__ = "provider_daily_metrics"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("providers.id"), primary_key=True
    )
    brand: Mapped[Brand] = mapped_column(
        Enum(Brand, name="brand", values_callable=lambda e: [m.value for m in e]),
        primary_key=True,
    )
    metric_date: Mapped[date] = mapped_column(Date, primary_key=True)

    delivered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contacted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    connected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qualified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    converted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dnp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
