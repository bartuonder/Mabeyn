"""add publishable and secret api keys and the tool identity flag

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing keys could always name a customer, so they become secret keys.
    op.add_column(
        "api_keys", sa.Column("kind", sa.String(16), server_default="secret", nullable=False)
    )
    op.create_check_constraint("apikeykind", "api_keys", "kind IN ('publishable', 'secret')")
    op.add_column(
        "api_keys",
        sa.Column(
            "allowed_origins",
            sa.dialects.postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "tool_definitions",
        sa.Column("requires_identity", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("tool_definitions", "requires_identity")
    op.drop_column("api_keys", "allowed_origins")
    op.drop_constraint("apikeykind", "api_keys", type_="check")
    op.drop_column("api_keys", "kind")
