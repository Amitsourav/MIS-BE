"""Targets / grade-band schemas."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.models.enums import Brand


class TargetIn(BaseModel):
    brand: Brand | None = None
    metric_key: str
    target_value: float


class TargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand: Brand | None
    metric_key: str
    target_value: float


class TargetsBulkUpdate(BaseModel):
    targets: list[TargetIn]
