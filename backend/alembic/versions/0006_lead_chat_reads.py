"""lead_chat_reads: when each partner login last opened a lead's chat

Revision ID: 0006_lead_chat_reads
Revises: 0005_provider_is_demo
Create Date: 2026-10-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_lead_chat_reads"
down_revision: Union[str, None] = "0005_provider_is_demo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lead_chat_reads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "provider_user_id", sa.Uuid(),
            sa.ForeignKey("provider_users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("crm_lead_id", sa.Uuid(), nullable=False),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_user_id", "crm_lead_id", name="uq_lead_chat_reads_user_lead"),
    )


def downgrade() -> None:
    op.drop_table("lead_chat_reads")
