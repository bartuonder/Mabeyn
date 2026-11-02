"""add conversation summary and memory extraction columns

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("summarized_messages", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("memory_extracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_conversations_pending_extraction",
        "conversations",
        ["created_at"],
        postgresql_where=sa.text("memory_extracted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_pending_extraction", table_name="conversations")
    op.drop_column("conversations", "memory_extracted_at")
    op.drop_column("conversations", "summarized_messages")
    op.drop_column("conversations", "summary")
