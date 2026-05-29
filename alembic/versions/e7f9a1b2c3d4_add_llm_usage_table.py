"""add llm_usage table

Revision ID: e7f9a1b2c3d4
Revises: 5a8f3c2d1e90
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "e7f9a1b2c3d4"
down_revision = "5a8f3c2d1e90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_cents", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_llm_usage_tenant_date", "llm_usage", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_llm_usage_tenant_date", table_name="llm_usage")
    op.drop_table("llm_usage")
