"""initial schema — all tables with tenant_id

Revision ID: 3b21914a9970
Revises:
Create Date: 2026-05-29 00:45:56.649987

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '3b21914a9970'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables from scratch."""
    # --- users (new in Phase 1) ---
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), unique=True, index=True, nullable=False),
        sa.Column("username", sa.String(), unique=True, index=True, nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("tenant_id", sa.String(), index=True, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    # --- products (shared, no tenant_id) ---
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.String(), unique=True, index=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), index=True, nullable=False),
        sa.Column("brand", sa.String(), index=True, nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("stock", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("platform_id", sa.String(), index=True, nullable=False),
        sa.Column("promotion_id", sa.String(), nullable=True),
        sa.Column("features", sa.Text(), server_default="", nullable=False),
        sa.Column("embedding_text", sa.Text(), server_default="", nullable=False),
        sa.Column("rating", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("delivery_minutes", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )

    # --- user_profiles (tenant-scoped) ---
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(), index=True, server_default="", nullable=False),
        sa.Column("user_id", sa.String(), index=True, nullable=False),
        sa.Column("category", sa.String(), index=True, nullable=False),
        sa.Column("price_sensitivity", sa.Float(), server_default=sa.text("0.5"), nullable=False),
        sa.Column("preferred_brands", sa.Text(), server_default="", nullable=False),
        sa.Column("visit_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    # --- sessions (tenant-scoped) ---
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(), index=True, server_default="", nullable=False),
        sa.Column("session_id", sa.String(), unique=True, index=True, nullable=False),
        sa.Column("user_id", sa.String(), index=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    # --- orders (tenant-scoped) ---
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(), index=True, server_default="", nullable=False),
        sa.Column("order_id", sa.String(), unique=True, index=True, nullable=False),
        sa.Column("session_id", sa.String(), index=True, nullable=False),
        sa.Column("user_id", sa.String(), index=True, nullable=False),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("total_price", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), server_default="'pending'", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    # --- intent_samples (shared, no tenant_id) ---
    op.create_table(
        "intent_samples",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("intent", sa.String(), index=True, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(), server_default="'manual'", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    # --- conversation_messages (tenant-scoped) ---
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(), index=True, server_default="", nullable=False),
        sa.Column("conversation_id", sa.String(), index=True, nullable=False),
        sa.Column("user_id", sa.String(), index=True, nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table("conversation_messages")
    op.drop_table("intent_samples")
    op.drop_table("orders")
    op.drop_table("sessions")
    op.drop_table("user_profiles")
    op.drop_table("products")
    op.drop_table("users")
