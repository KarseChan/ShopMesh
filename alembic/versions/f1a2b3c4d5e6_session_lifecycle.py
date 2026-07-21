"""session_lifecycle — status, turn tracking, preference extraction state

Revision ID: f1a2b3c4d5e6
Revises: e7f9a1b2c3d4
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "e7f9a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend sessions table
    op.add_column("sessions", sa.Column("status", sa.String(), nullable=False, server_default="active"))
    op.add_column("sessions", sa.Column("last_active_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.add_column("sessions", sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sessions", sa.Column("last_extracted_turn", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("idx_sessions_status", "sessions", ["status"])
    op.create_index("idx_sessions_last_active_at", "sessions", ["last_active_at"])

    # Extend conversation_messages with turn_id
    op.add_column("conversation_messages", sa.Column("turn_id", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("idx_conversation_messages_turn_id", "conversation_messages", ["conversation_id", "turn_id"])

    # Preference extraction state tracking
    op.create_table(
        "preference_extraction_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(), nullable=False, server_default=""),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("last_extracted_turn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_extracted_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("extraction_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("idx_pref_extract_tenant_id", "preference_extraction_state", ["tenant_id"])
    op.create_index("idx_pref_extract_session_id", "preference_extraction_state", ["session_id"])
    op.create_index("idx_pref_extract_user_id", "preference_extraction_state", ["user_id"])


def downgrade() -> None:
    op.drop_table("preference_extraction_state")
    op.drop_index("idx_conversation_messages_turn_id", "conversation_messages")
    op.drop_column("conversation_messages", "turn_id")
    op.drop_index("idx_sessions_last_active_at", "sessions")
    op.drop_index("idx_sessions_status", "sessions")
    op.drop_column("sessions", "last_extracted_turn")
    op.drop_column("sessions", "turn_count")
    op.drop_column("sessions", "last_active_at")
    op.drop_column("sessions", "status")
