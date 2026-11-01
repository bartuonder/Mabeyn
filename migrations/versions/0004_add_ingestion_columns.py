"""add ingestion columns and vector index

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("language", sa.String(8), server_default="tr", nullable=False),
    )
    op.add_column("documents", sa.Column("effective_date", sa.Date(), nullable=True))
    op.add_column("documents", sa.Column("raw_content", sa.LargeBinary(), nullable=True))
    op.add_column("chunks", sa.Column("embedding", Vector(1536), nullable=True))
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.drop_column("chunks", "embedding")
    op.drop_column("documents", "raw_content")
    op.drop_column("documents", "effective_date")
    op.drop_column("documents", "language")
