"""provider_users.password_changed_at: tokens issued before a password reset
stop working at once

Revision ID: 0004_password_changed_at
Revises: 0003_partner_payouts
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_password_changed_at"
down_revision: Union[str, None] = "0003_partner_payouts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no default: existing tokens stay valid until a reset happens.
    op.add_column(
        "provider_users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("provider_users", "password_changed_at")
