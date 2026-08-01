"""add items_json + idempotency_key to orders (购物功能 P2)

Revision ID: a1c2e3f40501
Revises: f1a2b3c4d5e6
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c2e3f40501"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("items_json", sa.Text(), server_default="", nullable=False))
    op.add_column("orders", sa.Column("idempotency_key", sa.String(), nullable=True))
    op.create_index("ix_orders_idempotency_key", "orders", ["idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_orders_idempotency_key", table_name="orders")
    op.drop_column("orders", "idempotency_key")
    op.drop_column("orders", "items_json")
