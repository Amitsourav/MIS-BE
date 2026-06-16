"""initial MIS schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

brand_enum = postgresql.ENUM("fmc", "av", name="brand", create_type=False)
stage_enum = postgresql.ENUM(
    "delivered", "contacted", "connected", "qualified", "in_process",
    "converted", "opportunity", "dnp", "lost",
    name="canonical_stage", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    brand_enum.create(bind, checkfirst=True)
    stage_enum.create(bind, checkfirst=True)

    op.create_table(
        "providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("contact_email", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("payout_config", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_table(
        "provider_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("providers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_table(
        "admin_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_table(
        "provider_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("providers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("brand", brand_enum, nullable=False),
        sa.Column("crm_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.UniqueConstraint("brand", "crm_source_id", name="uq_provider_sources_brand_src"),
    )

    op.create_table(
        "mis_leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("brand", brand_enum, nullable=False),
        sa.Column("crm_lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("providers.id"), nullable=True),
        sa.Column("crm_source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("serial_no", sa.Integer(), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("raw_stage", sa.Text(), nullable=True),
        sa.Column("canonical_stage", stage_enum, nullable=False),
        sa.Column("is_invalid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("invalid_reason", sa.Text(), nullable=True),
        sa.Column("is_duplicate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lost_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("crm_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("brand", "crm_lead_id", name="uq_mis_leads_brand_lead"),
    )
    op.create_index("idx_mis_leads_provider", "mis_leads",
                    ["provider_id", "brand", "created_at"])
    op.create_index("idx_mis_leads_stage", "mis_leads",
                    ["provider_id", "canonical_stage"])
    op.create_index("idx_mis_leads_dup", "mis_leads", ["brand", "phone", "created_at"])

    op.create_table(
        "provider_daily_metrics",
        sa.Column("provider_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("providers.id"), primary_key=True),
        sa.Column("brand", brand_enum, primary_key=True),
        sa.Column("metric_date", sa.Date(), primary_key=True),
        sa.Column("delivered", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("invalid", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("duplicates", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("valid", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("contacted", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("connected", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("qualified", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("converted", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("dnp", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("lost", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "sync_state",
        sa.Column("brand", brand_enum, primary_key=True),
        sa.Column("last_watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.Text(), nullable=True),
    )

    op.create_table(
        "targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("brand", brand_enum, nullable=True),
        sa.Column("metric_key", sa.Text(), nullable=False),
        sa.Column("target_value", sa.Numeric(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("targets")
    op.drop_table("sync_state")
    op.drop_table("provider_daily_metrics")
    op.drop_index("idx_mis_leads_dup", table_name="mis_leads")
    op.drop_index("idx_mis_leads_stage", table_name="mis_leads")
    op.drop_index("idx_mis_leads_provider", table_name="mis_leads")
    op.drop_table("mis_leads")
    op.drop_table("provider_sources")
    op.drop_table("admin_users")
    op.drop_table("provider_users")
    op.drop_table("providers")
    stage_enum.drop(op.get_bind(), checkfirst=True)
    brand_enum.drop(op.get_bind(), checkfirst=True)
