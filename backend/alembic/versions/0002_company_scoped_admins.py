"""company-scoped admins and per-company providers

Adds:
- admin_users.brand   (NULL = super-admin; 'fmc'/'av' = company admin)
- providers.brand      (the single company a vendor belongs to; NOT NULL)

Revision ID: 0002_company_scoped_admins
Revises: 0001_initial
Create Date: 2026-06-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_company_scoped_admins"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The 'brand' enum already exists (created in 0001); reference it without recreating.
brand_enum = postgresql.ENUM("fmc", "av", name="brand", create_type=False)


def upgrade() -> None:
    # admin scope — nullable (NULL = super-admin).
    op.add_column("admin_users", sa.Column("brand", brand_enum, nullable=True))

    # provider company — NOT NULL. Safe here because the table is empty on a fresh
    # install. If you already had providers, you'd add nullable, backfill, then alter.
    op.add_column("providers", sa.Column("brand", brand_enum, nullable=False))


def downgrade() -> None:
    op.drop_column("providers", "brand")
    op.drop_column("admin_users", "brand")
