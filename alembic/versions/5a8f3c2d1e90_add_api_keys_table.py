"""add api_keys table

Revision ID: 5a8f3c2d1e90
Revises: 3b21914a9970
Create Date: 2026-05-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a8f3c2d1e90'
down_revision: Union[str, Sequence[str], None] = '3b21914a9970'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create api_keys table for merchant API Key authentication."""
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key_id", sa.String(), unique=True, index=True, nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), index=True, nullable=False),
        sa.Column("name", sa.String(), server_default="", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Drop api_keys table."""
    op.drop_table("api_keys")
