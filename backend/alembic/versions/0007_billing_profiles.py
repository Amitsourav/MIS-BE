"""provider_billing_profiles: a partner's billing details for MIS-made invoices

Revision ID: 0007_billing_profiles
Revises: 0006_lead_chat_reads
Create Date: 2026-10-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_billing_profiles"
down_revision: Union[str, None] = "0006_lead_chat_reads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_billing_profiles",
        sa.Column(
            "provider_id", sa.Uuid(),
            sa.ForeignKey("providers.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("gst_registered", sa.Boolean(), nullable=False),
        sa.Column("legal_name", sa.String(200), nullable=False),
        sa.Column("gstin", sa.String(15), nullable=True),
        sa.Column("pan", sa.String(10), nullable=False),
        sa.Column("address_line1", sa.String(200), nullable=False),
        sa.Column("address_line2", sa.String(200), nullable=True),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("state_code", sa.String(2), nullable=False),
        sa.Column("pincode", sa.String(6), nullable=False),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("bank_account_name", sa.String(200), nullable=False),
        sa.Column("bank_account_number", sa.String(30), nullable=False),
        sa.Column("bank_ifsc", sa.String(11), nullable=False),
        sa.Column("bank_name", sa.String(100), nullable=False),
        sa.Column("bank_branch", sa.String(100), nullable=True),
        sa.Column("invoice_prefix", sa.String(12), nullable=False),
        sa.Column("signatory_name", sa.String(100), nullable=True),
        sa.Column("updated_by_email", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("provider_billing_profiles")
