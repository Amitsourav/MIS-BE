"""Partner chat on a lead (messages live in the CRM; see app/crm/chat.py)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ChatTag = Literal["counsellor", "admin"]


class ChatMessageOut(BaseModel):
    id: str
    author_kind: Literal["partner", "staff"]
    author_name: str
    # partner | counsellor (the lead's counsellor when they wrote) | team
    author_role: str
    body: str | None  # None when staff hid it -> show "Message removed"
    is_hidden: bool = False
    tags: list[str] = []
    created_at: datetime


class ChatThreadOut(BaseModel):
    counsellor_name: str | None = None
    items: list[ChatMessageOut] = []


class ChatPostIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    tags: list[ChatTag] = []  # [] = the counsellor (the CRM's default)
    # Optional: send the same id when retrying, so a double send posts once.
    client_message_id: uuid.UUID | None = None


class ChatLatestOut(BaseModel):
    lead_id: uuid.UUID
    last_message_at: datetime | None = None
    last_staff_message_at: datetime | None = None
    unread: bool  # a staff reply came after this login last opened the chat
