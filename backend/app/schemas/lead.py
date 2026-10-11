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
    canonical_stage: CanonicalStage  # MIS funnel bucket (for charts)
    crm_stage: str | None = None  # the lead's current CRM stage, as stored
    crm_stage_label: str | None = None  # the CRM's own name for it, e.g. "PF Paid"
    is_invalid: bool
    is_duplicate: bool
    created_at: datetime | None


class CrmStageCount(BaseModel):
    """One CRM stage in the provider's Leads filter, in CRM pipeline order."""

    value: str
    label: str
    count: int
