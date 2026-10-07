"""partner payouts mirrored from the FMC view public.mis_partner_payouts

Revision ID: 0003_partner_payouts
Revises: 0002_company_scoped_admins
Create Date: 2026-10-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_partner_payouts"
down_revision: Union[str, None] = "0002_company_scoped_admins"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The 'brand' enum already exists (created in 0001); reference it without recreating.
brand_enum = postgresql.ENUM("fmc", "av", name="brand", create_type=False)


def upgrade() -> None:
    op.create_table(
        "mis_payouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("brand", brand_enum, nullable=False),
        sa.Column("crm_lead_bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crm_lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crm_source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("providers.id"), nullable=True),
        sa.Column("bank_name", sa.Text(), nullable=True),
        sa.Column("loan_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("pf_paid_on", sa.Date(), nullable=True),
        sa.Column("disbursed_total", sa.Numeric(14, 2), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("payout_basis", sa.Text(), nullable=False),
        sa.Column("payout_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("payout_earned", sa.Numeric(14, 2), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("payout_paid", sa.Numeric(14, 2), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("payout_pending", sa.Numeric(14, 2), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("crm_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("brand", "crm_lead_bank_id",
                            name="uq_mis_payouts_brand_lead_bank"),
    )
    op.create_index("idx_mis_payouts_provider", "mis_payouts",
                    ["provider_id", "pf_paid_on"])


def downgrade() -> None:
    op.drop_index("idx_mis_payouts_provider", table_name="mis_payouts")
    op.drop_table("mis_payouts")
