"""add conversation status and escalation reason

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("status", sa.String(32), server_default="open", nullable=False),
    )
    op.create_check_constraint(
        "conversationstatus",
        "conversations",
        "status IN ('open', 'escalated', 'closed')",
    )
    op.add_column("conversations", sa.Column("escalation_reason", sa.Text(), nullable=True))
    op.create_index("ix_conversations_status", "conversations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_conversations_status", table_name="conversations")
    op.drop_column("conversations", "escalation_reason")
    op.drop_constraint("conversationstatus", "conversations", type_="check")
    op.drop_column("conversations", "status")
