"""flag suspicious chunks and blocked messages

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chunks", sa.Column("flagged", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column("chunks", sa.Column("injection_score", sa.Float(), nullable=True))
    op.add_column(
        "messages", sa.Column("blocked", sa.Boolean(), server_default=sa.false(), nullable=False)
    )


def downgrade() -> None:
    op.drop_column("messages", "blocked")
    op.drop_column("chunks", "injection_score")
    op.drop_column("chunks", "flagged")
