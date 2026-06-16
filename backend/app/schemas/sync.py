"""Sync status / leaderboard schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import Brand


class SyncStateOut(BaseModel):
    brand: Brand
    last_watermark: datetime | None
    last_run_at: datetime | None
    last_status: str | None


class SyncStatusResponse(BaseModel):
    states: list[SyncStateOut]
    running: bool


class SyncRunResponse(BaseModel):
    triggered: bool
    detail: str


class LeaderboardRow(BaseModel):
    provider_id: uuid.UUID
    provider_name: str
    delivered: int
    valid: int
    qualified: int
    converted: int
    qualification_rate: float
    conversion_rate: float
    score_pct: float
    grade: str
