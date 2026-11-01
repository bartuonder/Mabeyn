"""add full text search to chunks

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import REGCONFIG

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chunks",
        sa.Column(
            "fts_config",
            REGCONFIG(),
            server_default=sa.text("'simple'::regconfig"),
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TABLE chunks ADD COLUMN search_vector tsvector GENERATED ALWAYS AS "
        "(to_tsvector(fts_config, context_header || ' ' || content)) STORED"
    )
    op.execute("CREATE INDEX ix_chunks_search_vector ON chunks USING gin (search_vector)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_search_vector")
    op.drop_column("chunks", "search_vector")
    op.drop_column("chunks", "fts_config")
