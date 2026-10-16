"""When each partner login last opened a lead's chat (for "new reply" dots).
The messages themselves live in the CRM; only this read marker is the MIS's."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.database import Base


class LeadChatRead(Base):
    __tablename__ = "lead_chat_reads"
    __table_args__ = (
        UniqueConstraint("provider_user_id", "crm_lead_id", name="uq_lead_chat_reads_user_lead"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("provider_users.id", ondelete="CASCADE"), nullable=False
    )
    crm_lead_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
