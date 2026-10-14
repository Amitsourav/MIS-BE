"""providers.is_demo: sales-demo partners, kept out of the leaderboard

Revision ID: 0005_provider_is_demo
Revises: 0004_password_changed_at
Create Date: 2026-10-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_provider_is_demo"
down_revision: Union[str, None] = "0004_password_changed_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "providers",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("providers", "is_demo")
