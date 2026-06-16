"""Company-set targets and scorecard bands — all configurable, no benchmarks."""
from __future__ import annotations

import uuid

from sqlalchemy import Enum, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.database import Base
from app.models.enums import Brand


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # null brand = applies to all brands.
    brand: Mapped[Brand | None] = mapped_column(
        Enum(Brand, name="brand", values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    )
    metric_key: Mapped[str] = mapped_column(String, nullable=False)
    target_value: Mapped[float] = mapped_column(Numeric, nullable=False)
