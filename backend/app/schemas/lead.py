"""Per-lead table schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import Brand, CanonicalStage


class LeadOut(BaseModel):
    """Fields exposed to the provider who supplied the lead.

    PII the provider originated (serial/name/phone/stage) is allowed.
    Internal notes, transcripts, custom_fields are NOT included here.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    serial_no: int | None
    full_name: str | None
    phone: str | None
    brand: Brand
    source_name: str | None = None
    canonical_stage: CanonicalStage
    is_invalid: bool
    is_duplicate: bool
    created_at: datetime | None
