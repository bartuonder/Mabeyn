"""track tokens, cost and latency per assistant message

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

COUNTERS = ("input_tokens", "output_tokens", "llm_calls")


def upgrade() -> None:
    for name in COUNTERS:
        op.add_column("messages", sa.Column(name, sa.Integer(), server_default="0", nullable=False))
    op.add_column(
        "messages",
        sa.Column("cost_usd", sa.Numeric(12, 6), server_default="0", nullable=False),
    )
    op.add_column(
        "messages",
        sa.Column("cached", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("messages", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.create_index("ix_messages_tenant_created", "messages", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_messages_tenant_created", table_name="messages")
    for name in ("latency_ms", "cached", "cost_usd", *reversed(COUNTERS)):
        op.drop_column("messages", name)
